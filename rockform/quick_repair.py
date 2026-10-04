"""Explicit, reversible feature repairs; inferred material stays distinguishable."""
from copy import deepcopy
import uuid
import numpy as np
import trimesh
import manifold3d as manifold
from .model import GeometryError, TaskContext
from .geometry import build_solid, _weld_cut_seams, oriented_patch, boundary_loops, validate_solid


def original_feature(hold):
    source = deepcopy(hold.repair_source if hold.repair_source is not None else hold)
    source.id = uuid.uuid4().hex
    return source


def suggested_trim_height(hold):
    source = hold.repair_source if hold.repair_source is not None else hold
    edges, counts = np.unique(np.sort(source.patch.edges, axis=1), axis=0, return_counts=True)
    damaged = edges[counts != 2]
    if not len(damaged):
        raise GeometryError("This feature has no open boundary to trim.")
    # Heights are above the fitted wall, including the original noise cutoff.
    offset = source.extraction.get("wall_offset_mm", 0.)
    return float(source.patch.vertices[np.unique(damaged), 2].max() + offset + .1)


def fill_base_shift(hold):
    source = hold.repair_source if hold.repair_source is not None else hold
    return 3. - float(source.patch.vertices[:, 2].min())


def repair_trim_height(hold, combined=False):
    """Display old wall-relative cuts at the same physical height above the fill."""
    repair = hold.extraction.get('quick_repair', {})
    if combined:
        if 'trim_height_above_base_mm' in repair:
            return repair['trim_height_above_base_mm']
        if 'trim_height_above_wall_mm' in repair:
            source = hold.repair_source if hold.repair_source is not None else hold
            return max(0., repair['trim_height_above_wall_mm'] + fill_base_shift(source)
                       - float(source.extraction.get('wall_offset_mm', 0.)))
        return 0.
    if 'trim_height_above_wall_mm' in repair:
        return repair['trim_height_above_wall_mm']
    return suggested_trim_height(hold)


def project_to_back(patch, context=None):
    """Union triangular columns down to z=0, resolving overlapping projections.

    Unlike a single extruded outline, this also handles a ragged, overlapping
    rim. No height field replaces the contact surface. Obscured undersides may
    be filled; the preview and saved source make that choice inspectable.
    """
    ctx = context or TaskContext()
    triangles = patch.triangles.copy()
    cross_z = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 2]
    triangles[cross_z < 0] = triangles[cross_z < 0][:, [0, 2, 1]]
    triangles = triangles[np.abs(cross_z) > 1e-10]
    faces = np.array([[0,1,2], [5,4,3], [0,3,4], [0,4,1],
                      [1,4,5], [1,5,2], [2,5,3], [2,3,0]], dtype=np.uint64)
    groups = []
    for offset in range(0, len(triangles), 1000):
        ctx.step(25 + int(35 * offset / max(1, len(triangles))), "Filling observed surfaces down to the wall…")
        pieces = []
        for top in triangles[offset:offset + 1000]:
            if top[:, 2].max() <= 1e-8:
                continue
            bottom = top.copy()
            bottom[:, 2] = 0.
            vertices = np.vstack([top, bottom])
            indices = faces
            if np.any(top[:, 2] == 0.):
                vertices, mapping = np.unique(vertices, axis=0, return_inverse=True)
                indices = mapping[faces].astype(np.uint64)
                indices = indices[(indices[:, 0] != indices[:, 1]) &
                                  (indices[:, 1] != indices[:, 2]) & (indices[:, 2] != indices[:, 0])]
            piece = manifold.Manifold(manifold.Mesh64(vertices, indices))
            if piece.status() != manifold.Error.NoError:
                raise GeometryError("A surface triangle cannot be filled downward. Try trimming the damaged base.")
            pieces.append(piece)
        group = manifold.Manifold.batch_boolean(pieces, manifold.OpType.Add)
        group.num_tri()  # Evaluate here so cancellation is checked between batches.
        groups.append(group)
    ctx.step(65, "Joining the fill and resolving overlapping rim segments…")
    result = manifold.Manifold.batch_boolean(groups, manifold.OpType.Add).simplify(.0001)
    if result.is_empty() or result.status() != manifold.Error.NoError:
        raise GeometryError("The downward fill could not form a solid. Try Trim damaged base.")
    output = result.to_mesh64()
    solid = trimesh.Trimesh(output.vert_properties[:, :3], output.tri_verts, process=False)
    # Adjacent columns may leave coincident, opposite sheets at union seams.
    # Weld and remove only paired opposite faces, never retain an internal sheet.
    solid.merge_vertices(digits_vertex=7)
    _, inverse, counts = np.unique(np.sort(solid.faces, axis=1), axis=0, return_inverse=True, return_counts=True)
    duplicate = counts[inverse] > 1
    if duplicate.any():
        for group_id in np.unique(inverse[duplicate]):
            ids = np.flatnonzero(inverse == group_id)
            if len(ids) != 2 or np.dot(solid.face_normals[ids[0]], solid.face_normals[ids[1]]) > -.999:
                raise GeometryError("The fill contains an ambiguous overlap. Try trimming the damaged base.")
        solid.update_faces(~duplicate)
    solid.update_faces(solid.nondegenerate_faces())
    solid.remove_unreferenced_vertices()
    _stitch_base_seams(solid)
    solid = _connected_fill(solid, ctx)
    _close_back_seams(solid)
    # Once seams share topology, collapse coplanar subdivisions of the base.
    # This avoids spending minutes checking redundant triangles on one plane.
    if solid.is_watertight:
        compact = manifold.Manifold(manifold.Mesh64(solid.vertices, solid.faces.astype(np.uint64))).simplify(.0001)
        if compact.status() == manifold.Error.NoError and not compact.is_empty():
            output = compact.to_mesh64()
            solid = trimesh.Trimesh(output.vert_properties[:, :3], output.tri_verts, process=False)
    return solid


def _close_back_seams(solid):
    """Close seams exposed by union cleanup on the generated back only."""
    if solid.is_watertight:
        return
    from scipy.spatial import cKDTree
    from shapely.geometry import Polygon
    import shapely
    faces = solid.faces.tolist()
    for loop in boundary_loops(solid):
        vertices = solid.vertices[loop]
        if np.abs(vertices[:, 2]).max() > 1e-8:
            continue
        if len(loop) == 3:
            triangles = [loop]
        else:
            polygon = Polygon(vertices[:, :2])
            if not polygon.is_valid:
                continue
            tree = cKDTree(vertices[:, :2])
            triangles = []
            for triangle in shapely.constrained_delaunay_triangles(polygon).geoms:
                distance, ids = tree.query(np.asarray(triangle.exterior.coords)[:3])
                if distance.max() > 1e-7:
                    raise GeometryError("Could not join the generated back-plane seam.")
                triangles.append(loop[ids])
        for face in triangles:
            points = solid.vertices[face]
            if np.cross(points[1] - points[0], points[2] - points[0])[2] > 0:
                face = face[::-1]
            faces.append(face.tolist())
    solid.faces = np.asarray(faces)


def _connected_fill(solid, context=None):
    """Remove union sheets and fully enclosed fragments, never exterior rock."""
    ctx = context or TaskContext()
    components = trimesh.graph.connected_components(solid.face_adjacency, nodes=np.arange(len(solid.faces)), min_len=1)
    if len(components) == 1:
        return solid
    pieces = [solid.submesh([ids], append=True, repair=False) for ids in components]
    # Compute just signed volume; zero-thickness union sheets have no centroid.
    volumes = []
    for part in pieces:
        triangles = part.triangles - part.vertices[0]
        volumes.append(abs(np.einsum('ij,ij->i', triangles[:, 0],
                                    np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6.))
    main = int(np.argmax(volumes))
    largest = pieces[main]
    body = None
    outside_volume = 0.
    for index, (part, volume) in enumerate(zip(pieces, volumes)):
        if index == main:
            continue
        ctx.step(68, "Removing enclosed fill fragments…")
        if volume > .01 and part.is_watertight and largest.is_volume:
            if body is None:
                body = manifold.Manifold(manifold.Mesh64(np.array(largest.vertices, copy=True, order='C'), np.array(largest.faces, dtype=np.uint64, copy=True, order='C')))
            fragment = manifold.Manifold(manifold.Mesh64(np.array(part.vertices, copy=True, order='C'), np.array(part.faces, dtype=np.uint64, copy=True, order='C')))
            remainder = fragment - body
            if (body.status() == manifold.Error.NoError and remainder.status() == manifold.Error.NoError
                    and remainder.is_empty()):
                # The fragment is wholly inside existing material. Removing it
                # changes neither the exterior contact surface nor the volume.
                continue
        outside_volume += volume
    if outside_volume > .01:
        raise GeometryError("The fill leaves separate pieces outside the main body. Select one connected feature or repair the source scan.")
    return largest


def _trim_solid(solid, height):
    # The native constructor requires writable C arrays; union output exposes
    # read-only views even when they are already contiguous.
    body = manifold.Manifold(manifold.Mesh64(np.array(solid.vertices, copy=True, order='C'),
                                          np.array(solid.faces, dtype=np.uint64, copy=True, order='C')))
    cut = body.trim_by_plane([0., 0., 1.], float(height))
    if cut.status() != manifold.Error.NoError or cut.is_empty():
        raise GeometryError("The filled solid could not be trimmed at that height. Lower the cut.")
    mesh = cut.to_mesh64()
    return trimesh.Trimesh(np.array(mesh.vert_properties[:, :3], copy=True),
                           np.array(mesh.tri_verts, copy=True), process=False)


def rebuild_filled_body(hold, patch, context=None):
    """Rebuild from the filled-and-cut body, rather than filling a cut scan."""
    body = hold.repair_body.copy()
    body.vertices *= hold.scale
    rotation = trimesh.transformations.euler_matrix(np.radians(hold.tilt_x), np.radians(hold.tilt_y), 0)
    body.apply_transform(rotation)
    raw_point = trimesh.transform_points(hold.patch.vertices[:1] * hold.scale, rotation)[0]
    body.vertices[:, 2] += patch.vertices[0, 2] - raw_point[2]
    if hold.tilt_x or hold.tilt_y or hold.backing:
        # Shape edits retain the filled body and return it to a flat mounting
        # plane. The original cut is already represented by this saved mesh.
        body = _trim_solid(body, 0.)
        body = project_to_back(body, context)
    return body


def _stitch_base_seams(solid):
    """Join collinear subdivisions on the generated flat base, not scan gaps.

    Removing a zero-area union sheet can expose one long edge opposite several
    shorter edges. Subdivide the existing base triangle to share those vertices.
    This adds no surface or volume and never caps an actual missing patch.
    """
    edges, counts = np.unique(np.sort(solid.edges, axis=1), axis=0, return_counts=True)
    boundary = edges[(counts == 1) & (np.abs(solid.vertices[edges, 2]) < 1e-8).all(axis=1)]
    ids = np.unique(boundary)
    faces = solid.faces.tolist()
    for a, b in boundary:
        start, end = solid.vertices[[a, b]]
        direction = end - start
        if np.dot(direction, direction) < 1e-16:
            continue
        t = (solid.vertices[ids] - start) @ direction / np.dot(direction, direction)
        distance = np.linalg.norm(solid.vertices[ids] - (start + t[:, None] * direction), axis=1)
        interior = ids[(t > 1e-7) & (t < 1 - 1e-7) & (distance < 1e-7)]
        if not len(interior):
            continue
        interior = interior[np.argsort((solid.vertices[interior] - start) @ direction)]
        for index, face in enumerate(faces):
            if a in face and b in face:
                ring = [int(a), *interior.tolist(), int(b)]
                if face[(face.index(int(a)) + 1) % 3] != b:
                    ring.reverse()
                other = next(v for v in face if v not in (a, b))
                faces[index] = [ring[0], ring[1], other]
                faces.extend([[x, y, other] for x, y in zip(ring[1:-1], ring[2:])])
                break
    solid.faces = np.asarray(faces)


def quick_repair(hold, method="fill", trim_height=None, up=None, context=None):
    from .grip import assess_grip
    ctx = context or TaskContext()
    ctx.step(5, "Preparing a separate repair preview…")
    source = original_feature(hold)
    if source.feature_kind != "protrusion":
        raise GeometryError("Quick base repair currently works on raised features. Use scan gap repair for recesses to preserve their cavity.")
    if source.solid is not None:
        raise GeometryError("Select an incomplete raised feature for quick repair.")
    if method == "trim-fill":
        method = "fill-trim"  # Accept the previous API spelling for new previews.
    if method not in ("fill", "trim", "fill-trim"):
        raise GeometryError("Choose Fill down to wall, Trim damaged base, or Fill then trim.")
    repaired = deepcopy(source)
    repaired.id = uuid.uuid4().hex
    repaired.repair_source = source
    repaired.result = repaired.solid = None
    repaired.repair_body = None
    repaired.extraction.pop("geometry_status", None)
    repaired.extraction.pop("geometry_issue", None)
    repaired.extraction.pop("missing_boundaries", None)
    repaired.scale, repaired.tilt_x, repaired.tilt_y = 1., 0., 0.
    repaired.backing, repaired.edge_flare = 0., 0.
    offset = float(source.extraction.get("wall_offset_mm", 0.))
    metadata = dict(method=method, source_hold_id=hold.id, inferred=True,
                    numerical_tolerance_mm=.001 if method != "trim" else 0.)
    contact_margin = float(source.extraction.get("contact_margin_mm", 0.))
    fill_shift = fill_base_shift(source)
    if method in ('trim', 'fill-trim'):
        height = (suggested_trim_height(source) if method == 'trim' else 0.) if trim_height is None else float(trim_height)
        if not np.isfinite(height) or height < 0:
            raise GeometryError("Enter a finite, nonnegative trim height.")
        cut_height = height - (fill_shift if method == 'fill-trim' else offset)
        if repaired.patch.bounds[1, 2] - cut_height < 3.:
            raise GeometryError("That cut would leave less than 3 mm of feature height. Lower the cut or use Fill down to wall.")
        protected = np.vstack([source.protected_points, source.protected_triangles.reshape(-1, 3)])
        if len(protected) and protected[:, 2].min() < cut_height:
            raise GeometryError("This cut would remove protected grip detail. Lower the trim plane or change the protected region.")
        metadata["trim_height_above_wall_mm"] = cut_height + offset
        if method == 'fill-trim':
            metadata.update(trim_height_above_base_mm=height, trim_reference='filled-base')
        if contact_margin and (method == 'trim' or cut_height > source.patch.bounds[0, 2]):
            repaired.extraction["contact_margin_mm"] = 0.
            repaired.extraction.pop("contact_margin_complete", None)
            metadata["removed_contact_margin_mm"] = contact_margin
    if method != "trim":
        repaired.edge_style = "projected-base"
        shift = -cut_height if method == 'fill-trim' else fill_shift
        metadata["fill_base_local_z_mm"] = -fill_shift
        metadata["fill_stock_backing_below_wall_mm" if method == 'fill-trim' else "backing_below_wall_mm"] = float(fill_shift - offset)
        repaired.preparation = ("Quick repair: filled beneath the observed feature, with the flat back 3 mm below the lowest scanned point. "
                                "Amber is inferred support; undercuts can be filled. Missing contact texture is not added. "
                                "Compare with the original feature before keeping.")
        if method == 'fill-trim':
            metadata.update(sequence="fill-then-trim", flat_back_above_wall_mm=cut_height + offset)
            repaired.preparation = (f"Quick repair: filled 3 mm below the lowest scanned point, then trimmed {height:.2f} mm upward from that back. "
                                    "The cut becomes the flat back. The remaining scanned surface is retained; amber is inferred support and fill can cover undercuts. "
                                    "Compare the original in coral to inspect removed rock before keeping.")
    else:
        repaired.edge_style = "natural-base"
        shift = -cut_height
        repaired.preparation = (f"Quick repair: cut the damaged base at {height:.2f} mm above the fitted wall and closed the new underside. "
                                "The remaining scanned surface is retained; no side fill or surrounding plate was added. "
                                "Show the original feature to inspect what was removed before keeping.")
        if contact_margin:
            repaired.preparation += " Trimming removed the surrounding contact margin; the original is saved for comparison."
    try:
        if method == 'fill-trim':
            ctx.step(20, "Filling the original feature before trimming…")
            stock = source.patch.copy()
            stock.vertices[:, 2] += fill_shift
            filled = project_to_back(stock, ctx)
            validate_solid(filled, ctx)
            ctx.step(75, "Trimming the filled solid and closing the cut…")
            plane = cut_height + fill_shift
            repaired.repair_body = _trim_solid(filled, plane) if height else filled.copy()
            repaired.repair_body.vertices[:, 2] -= plane
            parts = repaired.repair_body.split(only_watertight=False)
            main = max(parts, key=lambda part: abs(part.volume))
            offcuts = [part for part in parts if part is not main]
            repaired.repair_body = main
            metadata.update(separated_offcuts=len(offcuts), offcut_volume_mm3=float(sum(abs(part.volume) for part in offcuts)))
            if offcuts:
                repaired.preparation += f" Trimming separated {len(offcuts)} offcut(s); the main body was retained. Compare the original to inspect removed rock."
        if method in ('trim', 'fill-trim') and cut_height > source.patch.bounds[0, 2]:
            repaired.patch = trimesh.intersections.slice_mesh_plane(source.patch, [0,0,1], [0,0,cut_height], cap=False)
            if not len(repaired.patch.faces):
                raise GeometryError("No surface remains above this cut. Lower the trim height.")
            _weld_cut_seams(repaired.patch)
        repaired.patch.vertices[:, 2] += shift
        repaired.frame[2, 3] += shift
        repaired.protected_points[:, 2] += shift
        repaired.protected_triangles[:, :, 2] += shift
        repaired.extraction["quick_repair"] = metadata
        if method == 'fill-trim' and metadata['separated_offcuts']:
            centers = repaired.patch.triangles_center
            distance = trimesh.proximity.closest_point(repaired.repair_body, centers)[1]
            keep = distance <= .001
            # Scan triangles obscured by fill are still source evidence. Keep
            # those inside the body, but exclude rock belonging to the offcuts.
            keep[~keep] = repaired.repair_body.contains(centers[~keep])
            repaired.patch.update_faces(keep)
            repaired.patch.remove_unreferenced_vertices()
            if not len(repaired.patch.faces):
                raise GeometryError("The cut removed the observed feature. Lower the trim height.")
        if method == "trim":
            loops = boundary_loops(repaired.patch)
            if len(loops) != 1 or np.max(np.abs(repaired.patch.vertices[loops[0], 2])) > 1e-5:
                raise GeometryError("The cut has not cleared every damaged boundary.")
        repaired.solid = build_solid(repaired, ctx)
    except GeometryError as error:
        advice = ("Raise the trim height to clear the boundary, or try Fill then trim to close the remaining damaged edges." if method == "trim" else
                  "Adjust the cut height or repair the source scan." if method == "fill-trim" else
                  "Try Fill then trim or repair the source scan.")
        raise GeometryError(f"Quick repair could not produce a valid solid. {advice} Detail: {error}") from error
    if len(repaired.protected_triangles) or len(repaired.protected_points):
        points = np.vstack([repaired.protected_points, repaired.protected_triangles.reshape(-1, 3), repaired.protected_triangles.mean(axis=1)])
        if trimesh.proximity.closest_point(repaired.solid, points)[1].max() > .001:
            raise GeometryError("The repair would remove or cover protected grip detail. Lower the cut or change the protected region.")
    openings = max(0, (2 - repaired.solid.euler_number) // 2)
    metadata["through_openings"] = int(openings)
    if openings:
        repaired.preparation += f" {openings} opening(s) remain through the shape; fill only adds beneath observed surfaces."
    repaired.grip_review = assess_grip(repaired, up, ctx)
    ctx.step(100, "Repair preview ready — inspect before keeping")
    return repaired


def comparison_surface(hold):
    if hold.repair_source is None:
        return None
    source = hold.repair_source.patch.copy()
    source.apply_transform(hold.frame @ np.linalg.inv(hold.repair_source.frame))
    source.vertices *= hold.scale
    rotation = trimesh.transformations.euler_matrix(np.radians(hold.tilt_x), np.radians(hold.tilt_y), 0)
    source.apply_transform(rotation)
    raw = hold.patch.copy()
    raw.vertices *= hold.scale
    raw.apply_transform(rotation)
    oriented, _ = oriented_patch(hold)
    source.vertices[:, 2] += oriented.vertices[0, 2] - raw.vertices[0, 2]
    return source

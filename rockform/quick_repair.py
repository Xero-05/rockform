"""Explicit, reversible feature repairs; inferred material stays distinguishable."""
from copy import deepcopy
import uuid
import numpy as np
import trimesh
import manifold3d as manifold
from .model import GeometryError, TaskContext
from .geometry import build_solid, _weld_cut_seams, oriented_patch, boundary_loops


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
    # Discard only sub-0.01 mm³ numerical slivers, not disconnected rock pieces.
    components = trimesh.graph.connected_components(solid.face_adjacency, nodes=np.arange(len(solid.faces)), min_len=1)
    if len(components) > 1:
        pieces = [solid.submesh([ids], append=True, repair=False) for ids in components]
        largest = max(pieces, key=lambda part: abs(part.volume))
        discarded = [part for part in pieces if part is not largest]
        if sum(abs(part.volume) for part in discarded) > .01:
            raise GeometryError("The fill leaves separate pieces. Select one feature or trim farther above the damaged boundary.")
        solid = largest
    # Once seams share topology, collapse coplanar subdivisions of the base.
    # This avoids spending minutes checking redundant triangles on one plane.
    if solid.is_watertight:
        compact = manifold.Manifold(manifold.Mesh64(solid.vertices, solid.faces.astype(np.uint64))).simplify(.0001)
        if compact.status() == manifold.Error.NoError and not compact.is_empty():
            output = compact.to_mesh64()
            solid = trimesh.Trimesh(output.vert_properties[:, :3], output.tri_verts, process=False)
    return solid


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
    if method not in ("fill", "trim", "trim-fill"):
        raise GeometryError("Choose Fill down to wall, Trim damaged base, or Trim then fill.")
    repaired = deepcopy(source)
    repaired.id = uuid.uuid4().hex
    repaired.repair_source = source
    repaired.result = repaired.solid = None
    repaired.extraction.pop("geometry_status", None)
    repaired.extraction.pop("geometry_issue", None)
    repaired.extraction.pop("missing_boundaries", None)
    repaired.scale, repaired.tilt_x, repaired.tilt_y = 1., 0., 0.
    repaired.backing, repaired.edge_flare = 0., 0.
    offset = float(source.extraction.get("wall_offset_mm", 0.))
    metadata = dict(method=method, source_hold_id=hold.id, inferred=True,
                    numerical_tolerance_mm=.001 if method != "trim" else 0.)
    contact_margin = float(source.extraction.get("contact_margin_mm", 0.))
    if method in ('trim', 'trim-fill'):
        height = (suggested_trim_height(source) if method == 'trim' else 0.) if trim_height is None else float(trim_height)
        if not np.isfinite(height) or height < 0:
            raise GeometryError("Enter a finite, nonnegative trim height above the fitted wall.")
        cut_height = height - offset
        if repaired.patch.bounds[1, 2] - cut_height < 3.:
            raise GeometryError("That cut would leave less than 3 mm of feature height. Lower the cut or use Fill down to wall.")
        if len(source.protected_triangles) and source.protected_triangles[:, :, 2].min() < cut_height:
            raise GeometryError("This cut would remove protected grip detail. Lower the trim plane or change the protected region.")
        ctx.step(20, "Cutting off the damaged lower portion…")
        repaired.patch = trimesh.intersections.slice_mesh_plane(repaired.patch, [0,0,1], [0,0,cut_height], cap=False)
        if not len(repaired.patch.faces):
            raise GeometryError("No surface remains above this cut. Lower the trim height.")
        _weld_cut_seams(repaired.patch)
        metadata["trim_height_above_wall_mm"] = height
        if contact_margin:
            repaired.extraction["contact_margin_mm"] = 0.
            repaired.extraction.pop("contact_margin_complete", None)
            metadata["removed_contact_margin_mm"] = contact_margin
    if method != "trim":
        repaired.edge_style = "projected-base"
        # Combined repair fills below the cut, without restoring trimmed rock.
        # Fill-only also retains contact texture below the fitted wall.
        if method == 'trim-fill':
            shift = 3. - cut_height
            metadata.update(backing_below_trim_mm=3., fill_plane_above_wall_mm=height - 3.)
        else:
            shift = max(offset, 3. - float(repaired.patch.vertices[:, 2].min())) if contact_margin else offset
            metadata["backing_below_wall_mm"] = float(shift - offset)
        repaired.preparation = ("Quick repair: filled straight down to the fitted wall. Amber surfaces are inferred material; "
                                "undercuts below the observed surface can be filled. No surrounding plate was added. "
                                "Compare with the original feature before keeping.")
        if contact_margin:
            repaired.preparation = ("Quick repair: filled beneath the observed feature and retained surrounding rock, "
                                    "with the flat back at least 3 mm below the lowest retained scan point. "
                                    "Amber is inferred support; undercuts can be filled. Missing contact texture is not added. "
                                    "Compare with the original feature before keeping.")
        if method == 'trim-fill':
            repaired.preparation = (f"Quick repair: trimmed the base at {height:.2f} mm above the fitted wall, "
                                    "then filled remaining open edges down to a flat back 3 mm below the cut. "
                                    "The remaining scanned surface is retained; amber is inferred support and fill can cover undercuts. "
                                    "Compare the original in coral to inspect removed rock before keeping.")
    else:
        repaired.edge_style = "natural-base"
        shift = -cut_height
        repaired.preparation = (f"Quick repair: cut the damaged base at {height:.2f} mm above the fitted wall and closed the new underside. "
                                "The remaining scanned surface is retained; no side fill or surrounding plate was added. "
                                "Show the original feature to inspect what was removed before keeping.")
        if contact_margin:
            repaired.preparation += " Trimming removed the surrounding contact margin; the original is saved for comparison."
    repaired.patch.vertices[:, 2] += shift
    repaired.frame[2, 3] += shift
    repaired.protected_points[:, 2] += shift
    repaired.protected_triangles[:, :, 2] += shift
    repaired.extraction["quick_repair"] = metadata
    try:
        if method == "trim":
            loops = boundary_loops(repaired.patch)
            if len(loops) != 1 or np.max(np.abs(repaired.patch.vertices[loops[0], 2])) > 1e-5:
                raise GeometryError("The cut has not cleared every damaged boundary.")
        repaired.solid = build_solid(repaired, ctx)
    except GeometryError as error:
        advice = ("Raise the trim height to clear the boundary, or try Trim then fill to close the remaining damaged edges." if method == "trim" else
                  "Adjust the cut height or repair the source scan." if method == "trim-fill" else
                  "Try Trim then fill or repair the source scan.")
        raise GeometryError(f"Quick repair could not produce a valid solid. {advice} Detail: {error}") from error
    if len(repaired.protected_triangles):
        points = np.vstack([repaired.protected_triangles.reshape(-1, 3), repaired.protected_triangles.mean(axis=1)])
        if trimesh.proximity.closest_point(repaired.solid, points)[1].max() > .001:
            raise GeometryError("The fill would cover protected grip detail. Try trimming or change the protected region.")
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

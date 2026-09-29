from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, MultiPoint, LineString, Point
from shapely.ops import triangulate
from rtree.index import Index, Property
from .model import GeometryError, TaskContext, Hold


def boundary_loops(mesh):
    edges, counts = np.unique(np.sort(mesh.edges, axis=1), axis=0, return_counts=True)
    if np.any(counts > 2):
        raise GeometryError("This selection has overlapping/non-manifold edges. Clean it or select a smaller area.")
    boundary = edges[counts == 1]
    neighbors = {}
    for a, b in boundary:
        neighbors.setdefault(int(a), []).append(int(b))
        neighbors.setdefault(int(b), []).append(int(a))
    if any(len(v) != 2 for v in neighbors.values()):
        raise GeometryError("The boundary branches or touches itself. Adjust the selection boundary.")
    loops, remaining = [], set(neighbors)
    while remaining:
        start = min(remaining)
        loop, previous, current = [start], -1, start
        while True:
            following = next(n for n in neighbors[current] if n != previous)
            if following == start:
                break
            if following in loop:
                raise GeometryError("The selection boundary intersects itself.")
            loop.append(following)
            previous, current = current, following
        remaining.difference_update(loop)
        loops.append(np.array(loop, dtype=int))
    return loops


def _plane_segment(triangle, normal, origin, eps):
    distance = (triangle - origin) @ normal
    points = [p for p, d in zip(triangle, distance) if abs(d) <= eps]
    for a, b in ((0, 1), (1, 2), (2, 0)):
        if distance[a] * distance[b] < -eps * eps:
            points.append(triangle[a] + (triangle[b] - triangle[a]) * distance[a] / (distance[a] - distance[b]))
    return np.asarray(points)


def self_intersections(mesh, context=None, stop_after=1):
    """Check triangle pairs, including folds sharing vertices; ignore valid adjacency."""
    ctx = context or TaskContext()
    tri = mesh.triangles
    # Manifold's mesh exchange uses float32. Respect that precision when comparing planes.
    eps = max(float(np.ptp(mesh.vertices, axis=0).max()) * 1e-6, 1e-6)
    prop = Property()
    prop.dimension = 3
    lo, hi = tri.min(axis=1) - eps, tri.max(axis=1) + eps
    bounds = np.hstack((lo, hi))
    tree = Index(((i, tuple(b), None) for i, b in enumerate(bounds)), properties=prop)
    normals = mesh.face_normals
    face_indices = np.asarray(mesh.faces)
    areas = np.asarray(mesh.area_faces)
    hits = []
    for i, t1 in enumerate(tri):
        if i % 500 == 0:
            ctx.step(75 + int(20 * i / max(len(tri), 1)), "Checking for intersecting surfaces…")
        for j in tree.intersection(tuple(bounds[i])):
            if j <= i:
                continue
            shared = np.intersect1d(face_indices[i], face_indices[j])
            t2, n1, n2 = tri[j], normals[i], normals[j]
            direction = np.cross(n1, n2)
            length = np.linalg.norm(direction)
            hit = False
            # Use the larger triangle's plane: sliver triangle normals amplify float32 error.
            coplanar = (np.max(np.abs((t2 - t1[0]) @ n1)) <= eps if areas[i] >= areas[j]
                        else np.max(np.abs((t1 - t2[0]) @ n2)) <= eps)
            if coplanar:
                axes = [a for a in range(3) if a != np.argmax(np.abs(n1))]
                p1, p2 = Polygon(t1[:, axes]), Polygon(t2[:, axes])
                inter = p1.buffer(-eps).intersection(p2.buffer(-eps))
                hit = inter.area > eps * eps
            else:
                if length < 1e-10:
                    continue
                if len(shared) >= 2:
                    continue  # Noncoplanar planes meet only on their common edge.
                # Do not snap near-plane vertices onto a long intersection segment.
                # This matters for the very narrow triangles formed around drilled seats.
                a = _plane_segment(t1, n2, t2[0], eps * 1e-4)
                b = _plane_segment(t2, n1, t1[0], eps * 1e-4)
                if len(a) == 0 or len(b) == 0:
                    continue
                axis = direction / length
                aa, bb = a @ axis, b @ axis
                overlap = min(aa.max(), bb.max()) - max(aa.min(), bb.min())
                hit = overlap > eps
            if hit:
                hits.append((i, j))
                if len(hits) >= stop_after:
                    return hits
    return hits


def validate_solid(mesh, context=None):
    if len(mesh.faces) == 0 or not np.isfinite(mesh.vertices).all():
        raise GeometryError("The model is empty or contains invalid coordinates.")
    if not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume <= 0:
        raise GeometryError("The model is not a closed, consistently oriented solid. Adjust its boundary or backing plane.")
    if len(trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)), min_len=1)) != 1:
        raise GeometryError("The hold contains disconnected pieces. Select one connected rock feature.")
    if self_intersections(mesh, context):
        raise GeometryError("Surfaces intersect inside this model. Change the boundary/backing plane or reduce the recess depth.")


def frame_for_patch(patch):
    center = patch.vertices.mean(axis=0)
    _, _, vt = np.linalg.svd(patch.vertices - center, full_matrices=False)
    z = vt[-1]
    mean_normal = (patch.face_normals * patch.area_faces[:, None]).sum(axis=0)
    if np.dot(z, mean_normal) < 0:
        z *= -1
    x = vt[0] - z * np.dot(vt[0], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    transform = np.eye(4)
    transform[:3, :3] = np.stack([x, y, z])
    transform[:3, 3] = -transform[:3, :3] @ center
    return transform


def extract_hold(revision, face_indices, name="Hold", context=None, edge_style="wall-ramp"):
    indices = np.unique(np.asarray(face_indices, dtype=int))
    if len(indices) < 4:
        raise GeometryError("Select a larger connected surface patch first.")
    patch = revision.mesh.submesh([indices], append=True, repair=False)
    if len(trimesh.graph.connected_components(patch.face_adjacency, nodes=np.arange(len(patch.faces)), min_len=1)) != 1:
        raise GeometryError("Select a single connected patch. Remove isolated selected areas.")
    frame = frame_for_patch(patch)
    patch.apply_transform(frame)
    protected = revision.mesh.vertices[revision.protected]
    if len(protected):
        protected = trimesh.transform_points(protected, frame)
    hold = Hold(name, revision.id, indices, patch, protected, frame, edge_style=edge_style)
    protected_faces = indices[revision.protected[revision.mesh.faces[indices]].any(axis=1)]
    if len(protected_faces):
        triangles = revision.mesh.triangles[protected_faces]
        hold.protected_triangles = trimesh.transform_points(triangles.reshape(-1, 3), frame).reshape(-1, 3, 3)
    hold.solid = build_solid(hold, context)
    return hold


def _weld_cut_seams(mesh):
    """Join numerically coincident plane-cut vertices without moving the surface."""
    tolerance = max(float(mesh.extents.max()) * 1e-7, 1e-6)
    pairs = cKDTree(mesh.vertices).query_pairs(tolerance, output_type="ndarray")
    parent = np.arange(len(mesh.vertices))
    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    for a, b in pairs:
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)
    mapping = np.array([root(i) for i in range(len(parent))])
    mesh.faces = mapping[mesh.faces]
    mesh.update_faces(mesh.unique_faces())
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()


def prepare_hold(revision, face_indices, name="Hold", context=None, round_boundary=True, minimum_size=0.):
    """Make a reviewable solid, trimming only the border when strict extraction fails.

    Clipping subdivides original triangles; it never replaces the grip surface
    with a height field or caps missing scan patches. The source revision stays intact.
    """
    from .cleanup import split_touching_boundaries
    ctx = context or TaskContext()
    indices = np.unique(np.asarray(face_indices, dtype=int))
    if len(indices) < 4:
        raise GeometryError("Paint a larger area with Select hold, or choose a suggested feature.")
    patch = revision.mesh.submesh([indices], append=True, repair=False)
    components = trimesh.graph.connected_components(patch.face_adjacency, nodes=np.arange(len(patch.faces)), min_len=1)
    note = ""
    if len(components) != 1:
        if not round_boundary:
            raise GeometryError("The selection contains separate pieces. Enable boundary preparation or erase the isolated areas.")
        biggest = max(components, key=lambda ids: patch.area_faces[ids].sum())
        note = f"Kept the largest of {len(components)} selected pieces. "
        indices = indices[np.sort(biggest)]
        patch = revision.mesh.submesh([indices], append=True, repair=False)
    try:
        hold = extract_hold(revision, indices, name, ctx)
        hold.preparation = note + "Selected rock surface retained; a gradual wall return forms the flat back."
        return hold
    except GeometryError as first_error:
        if not round_boundary:
            raise
        reason = str(first_error)
    failures = [reason]
    frame = frame_for_patch(patch)
    patch.apply_transform(frame)
    # A contracted selection outline retains its aspect ratio and asymmetry.
    # A circular fallback used to turn unrelated rock features into small pucks.
    outline = MultiPoint(patch.vertices[:, :2]).convex_hull
    if not isinstance(outline, Polygon) or outline.area < 1e-4:
        raise GeometryError("This selection is too narrow to form a hold. Select a broader feature.")
    if not outline.exterior.is_ccw:
        outline = Polygon(np.asarray(outline.exterior.coords)[::-1])
    center = np.asarray(outline.centroid.coords[0])
    outline_xy = np.asarray(outline.exterior.coords)[:-1]
    protected = revision.mesh.vertices[revision.protected]
    if len(protected):
        protected = trimesh.transform_points(protected, frame)
    protected_faces = indices[revision.protected[revision.mesh.faces[indices]].any(axis=1)]
    protected_triangles = np.empty((0, 3, 3))
    if len(protected_faces):
        protected_triangles = trimesh.transform_points(revision.mesh.triangles[protected_faces].reshape(-1, 3), frame).reshape(-1, 3, 3)
    for attempt, fraction in enumerate([.90, .75, .60, .45]):
        clip_xy = center + (outline_xy - center) * fraction
        if np.ptp(clip_xy, axis=0).min() < minimum_size:
            break
        ctx.step(20 + attempt * 15, "Preparing a simpler selection edge; preserving the scanned surface…")
        edges = np.roll(clip_xy, -1, axis=0) - clip_xy
        normals = np.column_stack([-edges[:, 1], edges[:, 0], np.zeros(len(edges))])
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        origins = np.column_stack([clip_xy, np.zeros(len(clip_xy))])
        cut = trimesh.intersections.slice_mesh_plane(patch, plane_normal=normals, plane_origin=origins, cap=False)
        if len(cut.faces) < 4:
            continue
        cut.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=7)
        _weld_cut_seams(cut)
        cut.update_faces(cut.unique_faces())
        cut.update_faces(cut.nondegenerate_faces())
        cut.remove_unreferenced_vertices()
        split_touching_boundaries(cut)
        hold = Hold(name, revision.id, indices, cut, protected.copy(), frame.copy(), protected_triangles=protected_triangles.copy())
        try:
            hold.solid = build_solid(hold, ctx)
        except GeometryError as error:
            reason = str(error)
            failures.append(reason)
            continue
        hold.preparation = note + (f"Trimmed the selection edge to a {cut.extents[0]:.1f} × {cut.extents[1]:.1f} mm outline following the selected feature. "
                                  "The remaining rock surface is unchanged; added support returns gradually to the wall. Inspect green rock and amber support before keeping.")
        return hold
    if any("artificial ledge" in error for error in failures):
        raise GeometryError("This selection cuts through tall or abrupt rock and would create an artificial ledge. "
                            "Include low surrounding rock or change the backing plane. Boundary trimming did not resolve it.")
    raise GeometryError("This area still cannot form a clean hold after boundary preparation. "
                        "Preview and apply scan repair, then choose a smaller patch away from missing areas or deep undercuts. "
                        f"Detail: {reason}")


def oriented_patch(hold):
    if hold.scale <= 0 or hold.backing <= 0:
        raise GeometryError("Scale and added backing thickness must be positive.")
    patch = hold.patch.copy()
    patch.vertices *= hold.scale
    rotation = trimesh.transformations.euler_matrix(np.radians(hold.tilt_x), np.radians(hold.tilt_y), 0)
    patch.apply_transform(rotation)
    lift = hold.backing - patch.vertices[:, 2].min()
    patch.vertices[:, 2] += lift
    points = hold.protected_points * hold.scale
    if len(points):
        points = trimesh.transform_points(points, rotation)
        points[:, 2] += lift
    return patch, points


def build_solid(hold, context=None):
    ctx = context or TaskContext()
    ctx.step(20, "Closing the hold against a flat back…")
    patch, _ = oriented_patch(hold)
    loops = boundary_loops(patch)
    if len(loops) != 1:
        raise GeometryError("Select a patch with one outer boundary and no missing interior patches. Repair small scan gaps first.")
    boundary = loops[0]
    xy = patch.vertices[boundary, :2]
    polygon = Polygon(xy)
    if not polygon.is_valid or polygon.area < 1e-4:
        raise GeometryError("This boundary overlaps when projected onto the back. Adjust the backing tilt or select a smaller feature.")
    if not np.isfinite(hold.edge_flare) or not 0 <= hold.edge_flare <= 100:
        raise GeometryError("Edge taper width must be between 0 and 100 mm. Use 0 for straight sides.")
    # Consistent CCW ring, preserving each original perimeter vertex.
    if not polygon.exterior.is_ccw:
        boundary = boundary[::-1]
        xy = xy[::-1]
        polygon = Polygon(xy)
    if hold.edge_style not in ("wall-ramp", "legacy"):
        raise GeometryError("Unknown edge style. Choose Use gradual wall return to rebuild this hold.")
    if hold.edge_style == "wall-ramp":
        solid = _tapered_solid(patch, boundary, polygon, support_width(hold, patch, boundary), ctx, eased=False)
        if support_ledge_area(hold, solid) >= 25.:
            raise GeometryError("The cut edge would create an artificial ledge. Include more low rock around the feature, "
                                "or change the backing tilt. A narrow taper cannot fix a cut through tall rock.")
        return solid
    if hold.edge_flare > 0:
        return _tapered_solid(patch, boundary, polygon, hold.edge_flare, ctx)
    vertices = np.vstack([patch.vertices, np.column_stack([xy, np.zeros(len(xy))])])
    faces = patch.faces.tolist()
    count, base = len(boundary), len(patch.vertices)
    for i in range(count):
        j = (i + 1) % count
        faces.extend([[int(boundary[i]), base + i, base + j], [int(boundary[i]), base + j, int(boundary[j])]])
    # GEOS constrained triangulation retains collinear boundary vertices (Shapely 2.1+).
    import shapely
    triangles = shapely.constrained_delaunay_triangles(polygon)
    tree = cKDTree(xy)
    for triangle in triangles.geoms:
        coordinates = np.asarray(triangle.exterior.coords)[:3]
        distances, ids = tree.query(coordinates)
        if distances.max() > 1e-6:
            raise GeometryError("Could not triangulate the back without altering the boundary.")
        faces.append((ids + base).tolist())
    solid = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    trimesh.repair.fix_normals(solid, multibody=True)
    validate_solid(solid, ctx)
    return solid


def support_width(hold, patch=None, boundary=None):
    """A 1:2 fall to the wall replaces the fixed-width, eased upper shelf."""
    if hold.edge_style == "legacy":
        return hold.edge_flare
    if patch is None:
        patch, _ = oriented_patch(hold)
    if boundary is None:
        boundary = boundary_loops(patch)[0]
    return max(hold.edge_flare, 2. * float(patch.vertices[boundary, 2].max()))


def generated_faces(hold, solid=None):
    """Identify added surfaces, excluding the back, by preserved triangle centers."""
    solid = hold.solid if solid is None else solid
    patch, _ = oriented_patch(hold)
    distance = cKDTree(patch.triangles_center).query(solid.triangles_center)[0]
    return (distance > 1e-5) & (solid.triangles[:, :, 2].max(axis=1) > 1e-6)


def support_ledge_area(hold, solid=None):
    """Area of connected, finger-scale added surfaces steeper than 40° to the wall.

    This is a geometric rejection rule, not a prediction of friction or usability.
    All in-plane orientations are screened, so rotating a hold cannot evade it.
    """
    solid = hold.solid if solid is None else solid
    steep = generated_faces(hold, solid) & (solid.face_normals[:, 2] < np.cos(np.radians(40)))
    ids = np.flatnonzero(steep)
    if not len(ids):
        return 0.
    edges = solid.face_adjacency[steep[solid.face_adjacency].all(axis=1)]
    groups = trimesh.graph.connected_components(edges, nodes=ids, min_len=1)
    return max((float(solid.area_faces[g].sum()) for g in groups
                if np.ptp(solid.triangles[g].reshape(-1, 3), axis=0).max() >= 10.), default=0.)


def _tapered_solid(patch, boundary, polygon, width, ctx, eased=True):
    """Add a wall return, or reproduce the legacy eased skirt when requested.

    Each projected annulus is disjoint, including concave outlines. Constrained
    triangulation shares every seam vertex with its neighboring band.
    """
    import shapely
    top = patch.vertices[boundary]
    closed_xy = np.vstack([top[:, :2], top[0, :2]])
    line = LineString(closed_xy)
    distances = np.r_[0., np.cumsum(np.linalg.norm(np.diff(closed_xy, axis=0), axis=1))]
    heights = np.r_[top[:, 2], top[0, 2]]
    vertices, faces = patch.vertices.tolist(), patch.faces.tolist()
    inner, inner_ids = polygon, np.asarray(boundary)
    for fraction in (.25, .5, .75, 1.):
        ctx.step(30 + int(fraction * 25), "Blending the selected rock edge down to the wall…")
        outer = polygon.buffer(width * fraction, quad_segs=3)
        if not eased:
            # Buffering drops collinear vertices. Without resampling, a single
            # long outer edge fans over multiple boundary peaks and invents
            # steep folds despite a shallow intended ramp.
            outer = shapely.segmentize(outer, max_segment_length=5.)
        xy = np.asarray(outer.exterior.coords)[:-1]
        along = shapely.line_locate_point(line, shapely.points(xy))
        fall = (1. + np.cos(np.pi * fraction)) / 2. if eased else 1. - fraction
        z = np.interp(along, distances, heights) * fall
        outer_ids = np.arange(len(vertices), len(vertices) + len(xy))
        vertices.extend(np.column_stack([xy, z]).tolist())
        band = Polygon(outer.exterior.coords, [inner.exterior.coords])
        if not band.is_valid:
            raise GeometryError("The tapered edge overlaps. Reduce edge taper width or simplify the selection.")
        lookup_xy = np.vstack([np.asarray(vertices)[inner_ids, :2], xy])
        lookup_ids = np.r_[inner_ids, outer_ids]
        tree = cKDTree(lookup_xy)
        for triangle in shapely.constrained_delaunay_triangles(band).geoms:
            if not band.covers(triangle.representative_point()):
                continue
            distances_to_vertex, ids = tree.query(np.asarray(triangle.exterior.coords)[:3])
            if distances_to_vertex.max() > 1e-6:
                raise GeometryError("Could not close the tapered edge. Reduce its width or adjust the selection.")
            faces.append(lookup_ids[ids].tolist())
        inner, inner_ids = outer, outer_ids
    if not eased:
        # A triangulated rounded corner can connect two nonadjacent zero-height
        # rim vertices. Lift an interior midpoint on such chords; otherwise the
        # top and back touch along an internal edge and create a non-manifold fin.
        points = np.asarray(vertices)
        triangles = np.asarray(faces)
        edges, counts = np.unique(np.sort(np.vstack([triangles[:, [0, 1]], triangles[:, [1, 2]],
                                                    triangles[:, [2, 0]]]), axis=1), axis=0, return_counts=True)
        chords = edges[(counts == 2) & (points[edges, 2] <= 1e-8).all(axis=1)]
        def ramp_point(xy):
            along = line.project(Point(xy))
            distance = polygon.distance(Point(xy))
            height = np.interp(along, distances, heights) * max(0., 1. - distance / width)
            return [float(xy[0]), float(xy[1]), max(float(height), 1e-5)]
        splits = {}
        for a, b in chords:
            splits[(int(a), int(b))] = len(vertices)
            vertices.append(ramp_point((points[a, :2] + points[b, :2]) / 2))
        repaired = []
        for face in faces:
            perimeter = []
            for a, b in zip(face, face[1:] + face[:1]):
                perimeter.append(a)
                middle = splits.get(tuple(sorted((a, b))))
                if middle is not None:
                    perimeter.append(middle)
            if len(perimeter) == 3:
                repaired.append(face)
            else:
                center = len(vertices)
                vertices.append(ramp_point(points[face, :2].mean(axis=0)))
                repaired.extend([[a, b, center] for a, b in zip(perimeter, perimeter[1:] + perimeter[:1])])
        faces = repaired
    tree = cKDTree(np.asarray(vertices)[inner_ids, :2])
    for triangle in shapely.constrained_delaunay_triangles(inner).geoms:
        if not inner.covers(triangle.representative_point()):
            continue
        distances_to_vertex, ids = tree.query(np.asarray(triangle.exterior.coords)[:3])
        if distances_to_vertex.max() > 1e-6:
            raise GeometryError("Could not triangulate the flat back of the tapered hold.")
        faces.append(inner_ids[ids].tolist())
    solid = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    _weld_cut_seams(solid)
    trimesh.repair.fix_normals(solid, multibody=True)
    validate_solid(solid, ctx)
    return solid


def demo_scan(kind="ledge", resolution=35):
    """Deterministic synthetic fixtures, clearly distinct from real phone scans."""
    x, y = np.meshgrid(np.linspace(-140, 140, resolution), np.linspace(-140, 140, resolution))
    if kind == "ledge":
        z = 38 * np.exp(-(x / 65) ** 4) * (np.tanh((y + 20) / 9) - np.tanh((y - 30) / 18)) / 2
    elif kind == "rounded":
        z = 65 * np.exp(-((x / 62) ** 2 + (y / 48) ** 2))
    else:
        z = 40 * np.exp(-((x / 80) ** 4 + (y / 65) ** 4)) - 22 * np.exp(-((x / 30) ** 2 + (y / 23) ** 2))
    z += 0.45 * np.sin(x / 8) * np.cos(y / 11)
    faces = []
    for row in range(resolution - 1):
        for col in range(resolution - 1):
            a = row * resolution + col
            faces.extend([[a, a + 1, a + resolution + 1], [a, a + resolution + 1, a + resolution]])
    return trimesh.Trimesh(np.column_stack([x.ravel(), y.ravel(), z.ravel()]), faces, process=False)

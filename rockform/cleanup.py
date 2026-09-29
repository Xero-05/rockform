from dataclasses import dataclass, field
import numpy as np
import trimesh
import pyvista as pv
import shapely
from shapely.geometry import Polygon
from scipy.spatial import cKDTree
from .model import Revision, TaskContext, GeometryError


@dataclass
class CleanupOptions:
    repair: bool = True
    fragment_percent: float = 0.2
    smooth_iterations: int = 0
    reduction: float = 0.0
    gap_ids: list[int] = field(default_factory=list)
    max_gap_diameter: float = 10.0
    auto_fill_small_gaps: bool = False


@dataclass
class CleanupPreview:
    revision: Revision
    removed: trimesh.Trimesh | None
    summary: str
    added: trimesh.Trimesh | None = None
    warnings: list[str] = field(default_factory=list)


def split_touching_boundaries(mesh):
    """Separate face fans meeting only at a vertex, without moving the surface."""
    edges, counts = np.unique(np.sort(mesh.edges, axis=1), axis=0, return_counts=True)
    boundary = edges[counts == 1]
    vertices, degree = np.unique(boundary, return_counts=True)
    trouble = vertices[degree > 2]
    faces = mesh.faces.copy()
    points = mesh.vertices.tolist()
    split_count = 0
    # Only branching boundary vertices need inspection. Most scans have very few.
    for vertex in trouble:
        incident = np.flatnonzero((faces == vertex).any(axis=1))
        neighbors = {}
        for face_id in incident:
            for other in faces[face_id]:
                if other != vertex:
                    neighbors.setdefault(int(other), []).append(int(face_id))
        pairs = [(ids[0], i) for ids in neighbors.values() for i in ids[1:]]
        groups = trimesh.graph.connected_components(np.asarray(pairs, dtype=int).reshape(-1, 2), nodes=incident, min_len=1)
        for group in groups[1:]:
            new_id = len(points)
            points.append(mesh.vertices[vertex].tolist())
            block = faces[group]
            block[block == vertex] = new_id
            faces[group] = block
            split_count += 1
    if split_count:
        mesh.vertices = np.asarray(points)
        mesh.faces = faces
    return split_count


def gap_info(mesh):
    # Report simple closed boundary loops even if a different region is tangled.
    edges, counts = np.unique(np.sort(mesh.edges, axis=1), axis=0, return_counts=True)
    boundary = edges[counts == 1]
    if not len(boundary):
        return []
    neighbors = {}
    for a, b in boundary:
        neighbors.setdefault(int(a), []).append(int(b))
        neighbors.setdefault(int(b), []).append(int(a))
    components = trimesh.graph.connected_components(boundary, nodes=np.unique(boundary), min_len=1)
    loops = []
    for component in components:
        if any(len(neighbors[int(v)]) != 2 for v in component):
            continue
        start = int(component[0]); loop = [start]; previous = -1; current = start
        while True:
            following = next(v for v in neighbors[current] if v != previous)
            if following == start:
                break
            loop.append(following); previous, current = current, following
        loops.append(np.asarray(loop, dtype=int))
    # Determine each connected component's outermost loop, never offer it for filling.
    vertex_component = np.full(len(mesh.vertices), -1)
    components = trimesh.graph.connected_components(mesh.edges_unique, nodes=np.arange(len(mesh.vertices)))
    for i, ids in enumerate(components):
        vertex_component[ids] = i
    groups = {}
    info = []
    for i, loop in enumerate(loops):
        points = mesh.vertices[loop]
        perimeter = np.linalg.norm(points - np.roll(points, 1, axis=0), axis=1).sum()
        diameter = float(np.linalg.norm(np.ptp(points, axis=0)))
        group = int(vertex_component[loop[0]])
        groups.setdefault(group, []).append((perimeter, i))
        info.append(dict(id=i, vertices=loop, center=points.mean(axis=0), diameter=diameter, outer=False))
    for items in groups.values():
        info[max(items)[1]]["outer"] = True
    return info


def _fill_selected_gaps(mesh, options, allowed, protected, selected_centers=()):
    new_faces = []
    warnings = []
    filled = 0
    for gap in gap_info(mesh):
        chosen = any(np.linalg.norm(gap["center"] - center) < 1e-5 for center in selected_centers)
        if not chosen and not (options.auto_fill_small_gaps and not gap["outer"] and gap["diameter"] <= options.max_gap_diameter):
            continue
        loop = gap["vertices"]
        if gap["outer"]:
            warnings.append("The outer scan boundary was left open. Only small interior gaps can be repaired.")
            continue
        if gap["diameter"] > options.max_gap_diameter:
            warnings.append("A selected gap is larger than the repair limit and was left open.")
            continue
        if protected[loop].any() or not allowed[loop].all():
            warnings.append("A gap touches a protected or excluded area and was left open.")
            continue
        points = mesh.vertices[loop]
        center = points.mean(axis=0)
        _, _, vt = np.linalg.svd(points - center)
        projected = (points - center) @ vt[:2].T
        if np.max(np.abs((points - center) @ vt[2])) > max(0.25, gap["diameter"] * 0.1):
            warnings.append("An uneven gap was left open; select a different hold area or rescan it.")
            continue
        polygon = Polygon(projected)
        if not polygon.is_valid:
            warnings.append("A tangled gap was left open; use a different area or rescan it.")
            continue
        for triangle in shapely.constrained_delaunay_triangles(polygon).geoms:
            _, ids = cKDTree(projected).query(np.asarray(triangle.exterior.coords)[:3])
            new_faces.append(loop[ids])
        filled += 1
    if new_faces:
        mesh.faces = np.vstack([mesh.faces, new_faces])
    added = trimesh.Trimesh(mesh.vertices.copy(), np.asarray(new_faces), process=False) if new_faces else None
    if added is not None:
        added.remove_unreferenced_vertices()
    return added, filled, list(dict.fromkeys(warnings))


def clean_revision(revision, options, selected_vertices=None, context=None):
    ctx = context or TaskContext()
    mesh = revision.mesh.copy()
    source = revision.mesh
    protected = revision.protected.copy()
    allowed = np.ones(len(mesh.vertices), dtype=bool) if selected_vertices is None else selected_vertices.copy()
    allowed &= ~protected
    ctx.step(10, "Inspecting scan data…")
    selected_centers = [g["center"] for g in gap_info(source) if g["id"] in options.gap_ids] if options.gap_ids else []
    removed_faces = []
    split_count = 0
    if options.repair:
        valid = mesh.nondegenerate_faces()
        editable = allowed[mesh.faces].all(axis=1)
        keep = valid | ~editable
        mesh.update_faces(keep)
        # Duplicate faces are always redundant; retain one copy.
        mesh.update_faces(mesh.unique_faces())
        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh)
        mesh.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=8)
        mesh.update_faces(mesh.unique_faces())
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
        trimesh.repair.fix_normals(mesh, multibody=True)
    ctx.step(25, "Finding disconnected fragments…")
    tree = cKDTree(source.vertices)
    _, nearest = tree.query(mesh.vertices)
    protected, allowed = revision.protected[nearest], allowed[nearest]
    if options.fragment_percent > 0 and len(mesh.faces):
        components = trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)), min_len=1)
        areas = [mesh.area_faces[c].sum() for c in components]
        threshold = max(areas) * options.fragment_percent / 100
        keep = np.ones(len(mesh.faces), dtype=bool)
        for ids, area in zip(components, areas):
            verts = np.unique(mesh.faces[ids])
            if area < threshold and allowed[verts].all() and not protected[verts].any():
                removed_faces.extend(ids)
                keep[ids] = False
        removed = mesh.submesh([np.array(removed_faces, dtype=int)], append=True) if removed_faces else None
        mesh.update_faces(keep)
    else:
        removed = None
    if not len(mesh.faces):
        raise GeometryError("Cleanup would remove the whole scan. Lower the fragment threshold.")
    if options.repair:
        split_count = split_touching_boundaries(mesh)
    # Vertex duplication changes indexing; transfer masks using exact positions.
    _, nearest = tree.query(mesh.vertices)
    allowed = np.ones(len(mesh.vertices), dtype=bool) if selected_vertices is None else selected_vertices[nearest].copy()
    protected = revision.protected[nearest]
    if revision.protected.any():
        distance, _ = cKDTree(source.vertices[revision.protected]).query(mesh.vertices)
        protected |= distance < 1e-8
    allowed &= ~protected
    ctx.step(40, "Smoothing editable areas…")
    if options.smooth_iterations:
        pinned = ~allowed | protected
        edges, counts = np.unique(np.sort(mesh.edges, axis=1), axis=0, return_counts=True)
        pinned[np.unique(edges[counts != 2])] = True
        # Pin high-angle feature edges as well as explicit grip protection.
        feature_pairs = mesh.face_adjacency_edges[mesh.face_adjacency_angles > np.radians(35)]
        pinned[np.unique(feature_pairs)] = True
        operator = trimesh.smoothing.laplacian_calculation(mesh, pinned_vertices=np.flatnonzero(pinned))
        for i in range(options.smooth_iterations):
            ctx.step(40 + int(20 * i / options.smooth_iterations), "Smoothing while retaining protected edges…")
            trimesh.smoothing.filter_taubin(mesh, iterations=2, laplacian_operator=operator)
    ctx.step(65, "Reducing mesh density…")
    if options.reduction > 0:
        editable_faces = (allowed & ~protected)[mesh.faces].all(axis=1)
        part = mesh.submesh([np.flatnonzero(editable_faces)], append=True)
        fixed = mesh.submesh([np.flatnonzero(~editable_faces)], append=True)
        if len(part.faces):
            poly = pv.PolyData(part.vertices, np.column_stack([np.full(len(part.faces), 3), part.faces]).ravel())
            reduced = poly.decimate_pro(options.reduction, preserve_topology=True, splitting=False,
                                       boundary_vertex_deletion=False, feature_angle=25)
            simpler = trimesh.Trimesh(reduced.points, reduced.faces.reshape(-1, 4)[:, 1:], process=False)
            mesh = trimesh.util.concatenate([simpler, fixed]) if len(fixed.faces) else simpler
            mesh.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=8)
            split_touching_boundaries(mesh)
    mesh.remove_unreferenced_vertices()
    # Never modify protected vertices; simplification retains the fixed patch and its boundary.
    _, nearest = cKDTree(source.vertices).query(mesh.vertices)
    protection = revision.protected[nearest]
    if revision.protected.any():
        distance, _ = cKDTree(source.vertices[revision.protected]).query(mesh.vertices)
        protection |= distance < 1e-8
    if protection.any():
        mesh.vertices[protection] = source.vertices[nearest[protection]]
    allowed = np.ones(len(mesh.vertices), dtype=bool) if selected_vertices is None else selected_vertices[nearest].copy()
    allowed &= ~protection
    # Fill last so the yellow preview uses the final surface positions, even
    # when smoothing or simplification is part of the same cleanup operation.
    added, filled, warnings = _fill_selected_gaps(mesh, options, allowed, protection, selected_centers)
    trimesh.repair.fix_normals(mesh, multibody=True)
    revision_out = Revision(mesh, "Cleaned scan", protected=protection)
    ctx.step(100, "Cleanup preview ready")
    gaps = gap_info(mesh)
    large = sum(not g["outer"] and g["diameter"] > options.max_gap_diameter for g in gaps)
    if large:
        warnings.append(f"{large} larger interior gaps remain. Avoid these areas when selecting a hold, or rescan them.")
    summary = (f"{len(source.faces):,} → {len(mesh.faces):,} triangles. "
               f"{len(removed_faces):,} fragment triangles removed; {split_count} touching boundaries separated; "
               f"{filled} small gaps filled. Original retained.")
    return CleanupPreview(revision_out, removed, summary, added, warnings)

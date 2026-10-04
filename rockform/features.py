"""Extract natural features relative to measured surrounding wall geometry."""
from dataclasses import dataclass
import numpy as np
import trimesh
from scipy.spatial import cKDTree
from .model import GeometryError, TaskContext, Hold
from .geometry import frame_for_patch, boundary_loops, _weld_cut_seams, build_solid


DEFAULT_CONTACT_MARGIN_MM = 10.


def _contact_patch(revision, patch, reference, margin, ctx, allow_incomplete=False):
    """Retain the original scan inside an offset of the feature's root.

    Clip triangles in the wall plane and lift their subdivisions back onto the
    original triangle planes. This preserves texture and undercuts, rather than
    reconstructing a height field. The outline also includes overhanging parts
    of the seed, so adding context never cuts away the observed grip.
    """
    import shapely
    from shapely.geometry import Polygon, LineString

    ctx.step(28, "Preserving surrounding rock for hand contact…")
    seed_triangles = shapely.polygons(patch.triangles[:, :, :2])
    try:
        roots = [Polygon(patch.vertices[loop, :2]) for loop in boundary_loops(patch)]
        if not roots:
            raise GeometryError("The observed feature has no root outline in the wall plane.")
        root = max(roots, key=lambda polygon: polygon.area)
        if not root.is_valid or root.area < 1e-4:
            raise GeometryError("The feature's root overlaps in the wall plane. Repair the scan before retaining surrounding rock.")
    except GeometryError:
        if not allow_incomplete:
            raise
        # A damaged rim may branch or overlap in projection. Offset the
        # observed footprint; clipping still retains only actual scan faces.
        root = shapely.union_all(seed_triangles[shapely.is_valid(seed_triangles)])
        if root.is_empty or root.area < 1e-4:
            raise GeometryError("The observed feature is too narrow to retain surrounding rock.")
    outside = ~shapely.covers(root, seed_triangles)
    outline = shapely.union_all([root.buffer(margin, quad_segs=8),
                                *seed_triangles[outside]])
    local = revision.mesh.copy()
    local.apply_transform(reference.frame)
    # A wall-plane footprint can also cover a deep recess or a perpendicular
    # rock face. Keep context within the requested depth below the observed
    # root, rather than pulling an arbitrarily deep sheet into the hand area.
    local = trimesh.intersections.slice_mesh_plane(local, [0, 0, 1],
        [0, 0, float(patch.bounds[0, 2]) - margin], cap=False)
    triangles = local.triangles
    lo, hi = outline.bounds[:2], outline.bounds[2:]
    nearby = np.flatnonzero((triangles[:, :, :2].max(axis=1) >= lo).all(axis=1) &
                           (triangles[:, :, :2].min(axis=1) <= hi).all(axis=1))
    projected = shapely.polygons(triangles[nearby, :, :2])
    inside = shapely.covers(outline, projected)
    retained = triangles[nearby[inside]].tolist()
    for count, index in enumerate(nearby[~inside]):
        if count % 250 == 0:
            ctx.step(30, "Clipping the contact margin without changing scanned texture…")
        triangle = triangles[index]
        xy = triangle[:, :2]
        matrix = np.column_stack([xy, np.ones(3)])
        if abs(np.linalg.det(matrix)) > 1e-12 * max(float(np.ptp(xy, axis=0).max()) ** 2, 1.):
            clipped = Polygon(xy).intersection(outline)
            if clipped.is_empty or clipped.area < 1e-12:
                continue
            # Polygon intersections can be disconnected on a concave root.
            for part in shapely.get_parts(clipped):
                if part.geom_type != "Polygon":
                    continue
                for face in shapely.constrained_delaunay_triangles(part).geoms:
                    face_xy = np.asarray(face.exterior.coords)[:3]
                    weights = np.linalg.solve(matrix.T, np.column_stack([face_xy, np.ones(3)]).T).T
                    points = weights @ triangle
                    if np.dot(np.cross(points[1] - points[0], points[2] - points[0]), local.face_normals[index]) < 0:
                        points = points[::-1]
                    retained.append(points.tolist())
        else:
            # A vertical scanned triangle projects to a line. Clip along that
            # line in 3D, retaining its full depth instead of discarding it.
            distances = np.linalg.norm(xy[:, None] - xy[None, :], axis=2)
            a, b = np.unravel_index(distances.argmax(), distances.shape)
            direction = xy[b] - xy[a]
            length = np.linalg.norm(direction)
            if length < 1e-10:
                continue
            direction /= length
            clipped = LineString([xy[a], xy[b]]).intersection(outline)
            for part in shapely.get_parts(clipped):
                if part.geom_type != "LineString" or part.length < 1e-10:
                    continue
                ends = np.asarray(part.coords) @ direction
                points = triangle.copy()
                for sign, limit in ((1., ends.min()), (-1., ends.max())):
                    result = []
                    d = sign * (points[:, :2] @ direction - limit)
                    for i in range(len(points)):
                        j = (i + 1) % len(points)
                        if d[i] >= -1e-10:
                            result.append(points[i])
                        if (d[i] > 0) != (d[j] > 0) and abs(d[i] - d[j]) > 1e-12:
                            result.append(points[i] + (points[j] - points[i]) * d[i] / (d[i] - d[j]))
                    points = np.asarray(result)
                    if len(points) < 3:
                        break
                retained.extend([points[[0, i, i + 1]].tolist() for i in range(1, len(points) - 1)])
    if not retained:
        raise GeometryError("No surrounding scan surface is available for this contact margin.")
    retained = np.asarray(retained)
    expanded = trimesh.Trimesh(retained.reshape(-1, 3), np.arange(retained.size // 3).reshape(-1, 3), process=False)
    _weld_cut_seams(expanded)
    expanded.update_faces(expanded.nondegenerate_faces())
    expanded.update_faces(expanded.unique_faces())
    expanded.remove_unreferenced_vertices()
    # Separate overlapping/disconnected scan sheets cannot become this hold.
    pieces = list(expanded.split(only_watertight=False, repair=False))
    expanded = min(pieces, key=lambda piece: trimesh.proximity.closest_point(piece, patch.triangles_center[:1])[1][0])
    issue = ""
    try:
        loops = boundary_loops(expanded)
        if len(loops) != 1:
            raise GeometryError("The surrounding contact area contains missing scan data. Repair the scan or reduce Surrounding rock.")
        rim = expanded.vertices[loops[0], :2]
        if shapely.distance(outline.boundary, shapely.points(rim)).max() > 1e-4:
            raise GeometryError("The contact margin reaches an open scan edge, gap or deeper rock face. Include more surrounding rock, reduce Surrounding rock, or inspect the retained area for repair.")
    except GeometryError as error:
        if not allow_incomplete:
            raise
        issue = str(error)
    return expanded, issue


@dataclass
class WallReference:
    frame: np.ndarray
    tolerance: float
    inlier_fraction: float
    noise: float


def wall_reference(mesh, context=None):
    """Fit the dominant wall, not a best-fit plane through the protrusion.

    Uniform spatial samples keep dense scanner tessellation from dominating.
    Deterministic RANSAC tolerates protrusions and recesses; broad planar support
    is required in both directions before this plane is used for extraction.
    """
    ctx = context or TaskContext()
    ctx.step(10, "Finding the surrounding wall plane…")
    frame = frame_for_patch(mesh)
    points = trimesh.transform_points(mesh.triangles_center, frame)
    spacing = max(3., float(np.ptp(points[:, :2], axis=0).max()) / 100.)
    _, sample_ids = np.unique(np.floor(points[:, :2] / spacing).astype(int), axis=0, return_index=True)
    sample = points[sample_ids]
    if len(sample) < 30:
        raise GeometryError("Include more surrounding wall to establish the feature's base plane.")
    if len(sample) > 4000:
        sample = sample[np.linspace(0, len(sample) - 1, 4000).astype(int)]
    xy1 = np.column_stack([sample[:, :2], np.ones(len(sample))])
    extent = np.ptp(sample[:, :2], axis=0)
    rng = np.random.default_rng(731)
    best, best_score = None, -1.
    for attempt in range(400):
        if attempt % 50 == 0:
            ctx.step(10 + attempt // 40, "Separating wall surface from raised and recessed features…")
        ids = rng.choice(len(sample), 3, replace=False)
        if abs(np.linalg.det(xy1[ids])) < spacing * spacing:
            continue
        plane = np.linalg.solve(xy1[ids], sample[ids, 2])
        if np.linalg.norm(plane[:2]) > .7:
            continue
        residual = sample[:, 2] - xy1 @ plane
        inliers = np.abs(residual) < 1.5
        if inliers.sum() < 20:
            continue
        coverage = np.ptp(sample[inliers, :2], axis=0) / np.maximum(extent, 1e-6)
        if coverage.min() < .55:
            continue
        score = inliers.sum() - np.minimum(np.abs(residual[inliers]), 1.5).sum() * .2
        if score > best_score:
            best, best_score = plane, score
    if best is None:
        raise GeometryError("A surrounding wall plane could not be established. Include flatter rock around the feature.")
    for _ in range(4):
        residual = sample[:, 2] - xy1 @ best
        inliers = np.abs(residual) < 2.
        best = np.linalg.lstsq(xy1[inliers], sample[inliers, 2], rcond=None)[0]
    noise = float(np.median(np.abs(residual[inliers])) * 1.4826)
    if inliers.mean() < .18:
        raise GeometryError("Too little surrounding wall supports a reliable base plane. Capture a wider area.")
    normal = np.array([-best[0], -best[1], 1.])
    normal /= np.linalg.norm(normal)
    x = np.array([1., 0., best[0]])
    x /= np.linalg.norm(x)
    y = np.cross(normal, x)
    relative = np.eye(4)
    relative[:3, :3] = np.stack([x, y, normal])
    relative[:3, 3] = -relative[:3, :3] @ [0., 0., best[2]]
    return WallReference(relative @ frame, max(1., min(3., noise * 3.)), float(inliers.mean()), noise)


def raised_patches(revision, reference, context=None, kind="protrusion"):
    """Cut the full-resolution scan where it rejoins the wall, without a skirt."""
    ctx = context or TaskContext()
    ctx.step(25, "Tracing protrusions where they meet the wall…")
    local = revision.mesh.copy()
    local.apply_transform(reference.frame)
    sign = 1. if kind == "protrusion" else -1.
    cut = trimesh.intersections.slice_mesh_plane(local, [0, 0, sign], [0, 0, sign * reference.tolerance], cap=False)
    if not len(cut.faces):
        return []
    _weld_cut_seams(cut)
    pieces = list(cut.split(only_watertight=False, repair=False))
    pieces.sort(key=lambda piece: piece.area, reverse=True)
    return pieces


def protrusion_hold(revision, patch, reference, name="Hold", context=None, kind="protrusion", allow_incomplete=False,
                    contact_margin_mm=DEFAULT_CONTACT_MARGIN_MM):
    ctx = context or TaskContext()
    patch = patch.copy()
    if not np.isfinite(contact_margin_mm) or not 0 <= contact_margin_mm <= 50.:
        raise GeometryError("Surrounding rock must be between 0 and 50 mm.")
    contact_margin = 0.
    contact_issue = ""
    issue = ""
    loops = []
    cut_height = reference.tolerance if kind == "protrusion" else -reference.tolerance
    try:
        loops = boundary_loops(patch)
        if len(loops) != 1:
            raise GeometryError("This feature has missing scan data inside its outline. Preview scan repair or rescan the gap.")
        edge = patch.vertices[loops[0], 2]
        if np.max(np.abs(edge - cut_height)) > 1e-4:
            raise GeometryError("The feature reaches an open scan edge before returning to the wall. Its base or side is incomplete; include more of the feature in the scan.")
    except GeometryError as error:
        issue = f"This feature has broken boundaries or missing scan data. Detail: {error}"
        if not allow_incomplete:
            raise GeometryError(issue) from error
    if kind == "protrusion" and contact_margin_mm > 0:
        patch, contact_issue = _contact_patch(revision, patch, reference, contact_margin_mm, ctx, allow_incomplete)
        contact_margin = float(contact_margin_mm)
        # Re-evaluate the retained surface: a hole in the height-cut seed can
        # be an observed valley below the cut, rather than missing scan data.
        issue = contact_issue
        try:
            loops = boundary_loops(patch)
        except GeometryError:
            loops = []
    # Store a rigidly translated scan in the wall frame. Root-only extraction
    # closes at zero; a contact band keeps its measured rim heights unchanged.
    frame = reference.frame.copy()
    frame[2, 3] -= cut_height
    patch.vertices[:, 2] -= cut_height
    if not issue and not contact_margin:
        patch.vertices[loops[0], 2] = 0.
    center = patch.bounds.mean(axis=0)[:2]
    frame[:2, 3] -= center
    patch.vertices[:, :2] -= center
    world_centers = trimesh.transform_points(patch.triangles_center, np.linalg.inv(frame))
    # Original IDs remain tied to the revision for selection/protection/reopen.
    # Closest triangle centers are ambiguous at narrow cut slivers. A surface
    # proximity query gives the actual original face, including subdivisions.
    _, _, source_ids = trimesh.proximity.closest_point(revision.mesh, world_centers)
    indices = np.unique(source_ids)
    protected_faces = indices[revision.protected[revision.mesh.faces[indices]].any(axis=1)]
    triangles = revision.mesh.triangles[protected_faces]
    protected = trimesh.transform_points(revision.mesh.vertices[revision.protected], frame)
    hold = Hold(name, revision.id, indices, patch, protected, frame, backing=0., edge_flare=0.,
                edge_style="natural-base", feature_kind=kind,
                extraction=dict(wall_offset_mm=cut_height, wall_noise_mm=reference.noise,
                                wall_support_fraction=reference.inlier_fraction,
                                contact_margin_mm=contact_margin, requested_contact_margin_mm=float(contact_margin_mm)))
    if contact_margin:
        hold.extraction["contact_margin_complete"] = not bool(contact_issue)
    if issue and loops:
        outer = max(range(len(loops)), key=lambda i: np.linalg.norm(np.ptp(patch.vertices[loops[i]], axis=0)))
        boundaries = []
        for i, loop in enumerate(loops):
            points = patch.vertices[loop]
            if i != outer or np.max(np.abs(points[:, 2])) > 1e-4:
                boundaries.append(dict(kind="incomplete base or side" if i == outer else "interior gap",
                                       span_mm=np.ptp(points, axis=0).tolist()))
        hold.extraction["missing_boundaries"] = boundaries
    if len(triangles):
        hold.protected_triangles = trimesh.transform_points(triangles.reshape(-1, 3), frame).reshape(-1, 3, 3)
    if contact_margin:
        hold.backing = 3.
        hold.edge_style = "wall-ramp"
    if kind == "recess":
        hold.backing = 3.
        hold.edge_style = "wall-ramp"
        hold.preparation = ("Isolated a recess below the surrounding wall. Preserved its inward surface and added "
                            "3 mm below the deepest point, with an outside return to make a closed pocket prototype. "
                            "This supporting material is needed for the cavity; inspect the amber surfaces.")
    elif contact_margin:
        hold.preparation = (f"Preserved the raised feature and {contact_margin:.1f} mm of surrounding scanned rock "
                            "for contact around its base. Retained texture and grip geometry are unchanged. "
                            "Added 3 mm beneath the lowest retained point and a gradual outside return to the flat back. "
                            "Green is scanned rock; amber is generated support.")
    else:
        hold.preparation = (f"Isolated the raised feature at its natural root ({reference.tolerance:.1f} mm above the fitted wall, "
                            "to exclude scan noise). Closed its underside with a flat back. No added backing or surrounding plate.")
    if not issue:
        try:
            hold.solid = build_solid(hold, ctx)
        except GeometryError as error:
            if not allow_incomplete:
                raise
            issue = str(error)
    if issue:
        hold.extraction.update(geometry_status="needs-repair", geometry_issue=issue)
        hold.preparation = ("Feature recognized from the available scanned surface. Needs repair before mounting or STL export. "
                            + issue + " Missing boundaries are highlighted; missing grip geometry has not been invented.")
        if contact_margin:
            hold.preparation += (f" Retained available scanned rock within the requested {contact_margin:.1f} mm contact margin. "
                                 "Inspect incomplete coverage at scan edges, gaps or deeper rock; no missing texture was added.")
        details = hold.extraction.get("missing_boundaries", [])
        if details:
            hold.preparation += " Detected: " + "; ".join(b['kind'] + " (" +
                " × ".join(f"{v:.1f}" for v in b['span_mm']) + " mm)" for b in details) + "."
    return hold


def feature_regions(revision, context=None, sensitivity=50.):
    ctx = context or TaskContext()
    reference = wall_reference(revision.mesh, ctx)
    reference.tolerance *= 1.25 - .005 * np.clip(sensitivity, 0, 100)
    regions = []
    for kind in ("protrusion", "recess"):
        for patch in raised_patches(revision, reference, ctx, kind):
            # Noise islands are not useful seeds, regardless of tessellation.
            if patch.area >= 80. and patch.extents[2] >= 6.:
                regions.append((kind, patch))
    regions.sort(key=lambda value: value[1].area * value[1].extents[2], reverse=True)
    return reference, regions


def prepare_feature(revision, face_indices, name="Hold", context=None, allow_incomplete=False,
                    contact_margin_mm=DEFAULT_CONTACT_MARGIN_MM):
    """Use the painted region as a seed; trace the complete natural feature."""
    ctx = context or TaskContext()
    indices = np.unique(np.asarray(face_indices, dtype=int))
    if len(indices) < 4:
        raise GeometryError("Select the protrusion or recess first, including some surrounding wall.")
    reference, regions = feature_regions(revision, ctx)
    selected = trimesh.transform_points(revision.mesh.triangles_center[indices], reference.frame)
    ranked = []
    for kind, patch in regions:
        distance = cKDTree(patch.triangles_center).query(selected)[0]
        overlap = np.count_nonzero(distance < 3.)
        if overlap >= 3:
            ranked.append((overlap, kind, patch))
    if not ranked:
        raise GeometryError("The selection does not contain a distinct raised or recessed feature relative to the wall. "
                            "Select the feature itself and include surrounding wall in the scan. "
                            "Turn off Isolate natural feature only if you want a backed surface-patch prototype.")
    # Do not silently switch to another, less-selected feature if this one fails.
    _, kind, patch = max(ranked, key=lambda value: value[0])
    hold = protrusion_hold(revision, patch, reference, name, ctx, kind, allow_incomplete=allow_incomplete,
                           contact_margin_mm=contact_margin_mm)
    hold.preparation += " The selection identified the feature; its outline follows the natural root and chosen contact margin."
    return hold

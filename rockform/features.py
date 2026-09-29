"""Extract natural features relative to measured surrounding wall geometry."""
from dataclasses import dataclass
import numpy as np
import trimesh
from scipy.spatial import cKDTree
from .model import GeometryError, TaskContext, Hold
from .geometry import frame_for_patch, boundary_loops, _weld_cut_seams, build_solid


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


def protrusion_hold(revision, patch, reference, name="Hold", context=None, kind="protrusion"):
    ctx = context or TaskContext()
    patch = patch.copy()
    try:
        loops = boundary_loops(patch)
    except GeometryError as error:
        raise GeometryError(f"This feature has broken boundaries or missing scan data. Preview scan repair or rescan the gap. Detail: {error}") from error
    if len(loops) != 1:
        raise GeometryError("This feature has missing scan data inside its outline. Preview scan repair or rescan the gap.")
    edge = patch.vertices[loops[0], 2]
    cut_height = reference.tolerance if kind == "protrusion" else -reference.tolerance
    if np.max(np.abs(edge - cut_height)) > 1e-4:
        raise GeometryError("The feature reaches an open scan edge before returning to the wall. Include more surrounding rock in the scan.")
    # Place the natural root contour exactly on the mounting plane; this is a
    # rigid translation of retained triangles, not a reconstruction of the grip.
    frame = reference.frame.copy()
    frame[2, 3] -= cut_height
    patch.vertices[:, 2] -= cut_height
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
                                wall_support_fraction=reference.inlier_fraction))
    if len(triangles):
        hold.protected_triangles = trimesh.transform_points(triangles.reshape(-1, 3), frame).reshape(-1, 3, 3)
    if kind == "recess":
        hold.backing = 3.
        hold.edge_style = "wall-ramp"
        hold.preparation = ("Isolated a recess below the surrounding wall. Preserved its inward surface and added "
                            "3 mm below the deepest point, with an outside return to make a closed pocket prototype. "
                            "This supporting material is needed for the cavity; inspect the amber surfaces.")
    else:
        hold.preparation = (f"Isolated the raised feature at its natural root ({reference.tolerance:.1f} mm above the fitted wall, "
                            "to exclude scan noise). Closed its underside with a flat back. No added backing or surrounding plate.")
    hold.solid = build_solid(hold, ctx)
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


def prepare_feature(revision, face_indices, name="Hold", context=None):
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
    hold = protrusion_hold(revision, patch, reference, name, ctx, kind)
    hold.preparation += " The selection identified the feature; its outline follows the wall intersection."
    return hold

from dataclasses import dataclass, field
import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from .model import GeometryError, TaskContext, Hold


@dataclass
class Candidate:
    faces: np.ndarray
    score: float
    explanation: str
    prepared_hold: Hold | None = None


@dataclass
class ScreeningSummary:
    rejected_geometry: int = 0
    rejected_grip: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def text(self):
        return (f"{self.rejected_grip} regions lacked a suitable natural feature or wall return; "
                f"{self.rejected_geometry} could not form a clean shape.")


def suggest_holds(revision, up, minimum_size=50., maximum_size=180., sensitivity=50., context=None, round_boundary=True):
    """Screen the actual retained surface, separately from solid validity."""
    from .geometry import prepare_hold
    from .grip import assess_grip
    ctx = context or TaskContext()
    candidates = detect_candidates(revision, up, minimum_size, maximum_size, sensitivity, ctx)
    usable, summary = [], ScreeningSummary()
    for i, candidate in enumerate(candidates):
        ctx.step(30 + int(65 * i / max(1, len(candidates))), f"Checking suggested hold {i + 1} of {len(candidates)}…")
        try:
            candidate.prepared_hold = prepare_hold(revision, candidate.faces, context=ctx,
                round_boundary=round_boundary, minimum_size=minimum_size)
        except GeometryError as error:
            summary.rejected_geometry += 1
            summary.reasons.append(str(error))
            continue
        review = assess_grip(candidate.prepared_hold, up, ctx)
        candidate.prepared_hold.grip_review = review
        if not review['eligible']:
            summary.rejected_grip += 1
            summary.reasons.extend(review['reasons'])
            continue
        size = candidate.prepared_hold.solid.extents
        candidate.score = review['contact_area_mm2'] * review['relief_mm']
        candidate.explanation = f"{size[0]:.0f} × {size[1]:.0f} mm · {review['relief_mm']:.0f} mm natural relief · review contact"
        usable.append(candidate)
    usable.sort(key=lambda candidate: candidate.score, reverse=True)
    ctx.step(100, f"{len(usable)} broad-feature candidates for review")
    return usable[:10], summary


def detect_candidates(revision, up, minimum_size=50., maximum_size=180., sensitivity=50., context=None):
    ctx = context or TaskContext()
    mesh = revision.mesh
    if len(mesh.faces) < 10:
        raise GeometryError("Import a larger surface scan to find candidate holds.")
    ctx.step(10, "Measuring surface shape…")
    points = mesh.triangles_center
    normals = mesh.face_normals
    # Bounded samples for coarse search, evaluated against original face centers.
    stride = max(1, len(points) // 18000)
    indices = np.arange(0, len(points), stride)
    sample, sn = points[indices], normals[indices]
    tree = cKDTree(sample)
    _, neighbors = tree.query(sample, k=min(24, len(sample)))
    baseline = np.median(sample[neighbors], axis=1)
    curvature = 1 - np.linalg.norm(sn[neighbors].mean(axis=1), axis=1)
    relief = np.abs(np.einsum('ij,ij->i', sample - baseline, sn))
    upward = np.maximum(0, sn @ (up / max(np.linalg.norm(up), 1e-8)))
    def normalize(values):
        return np.clip(values / max(np.percentile(values, 95), 1e-5), 0, 1)
    score = 0.45 * normalize(curvature) + 0.4 * normalize(relief) + 0.15 * upward
    # Plain planes should produce no fabricated hold candidates.
    if np.percentile(curvature, 95) < 1e-5 and np.percentile(relief, 95) < 0.02:
        return []
    # Start at broad relief peaks, so the selection includes low surrounding rock
    # instead of being centered on a high-curvature flank and cutting the grip off.
    from .geometry import frame_for_patch
    frame = frame_for_patch(mesh)
    heights = sample @ frame[2, :3]
    broad = np.mean(heights[neighbors], axis=1)
    broad -= np.median(broad)
    boundary_edges = mesh.edges_unique[np.bincount(mesh.edges_unique_inverse) == 1]
    away = np.ones(len(sample), dtype=bool)
    if len(boundary_edges):
        away = cKDTree(mesh.vertices[np.unique(boundary_edges)]).query(sample)[0] >= minimum_size / 2
    peaks = np.flatnonzero((np.abs(broad) >= 8.) & away)
    peaks = peaks[np.argsort(np.abs(broad[peaks]))[::-1]]
    separated = []
    for index in peaks:
        if all(np.linalg.norm(sample[index] - sample[old]) >= maximum_size * .5 for old in separated):
            separated.append(index)
        if len(separated) == 3:
            break
    sorted_ids = np.r_[separated, np.argsort(score)[::-1]].astype(int)
    peak_set = set(separated)
    all_tree = cKDTree(points @ frame[:2, :3].T)
    chosen, centers = [], []
    threshold = 0.8 - np.clip(sensitivity, 0, 100) * 0.0055
    adjacency = mesh.face_adjacency
    for step, sample_id in enumerate(sorted_ids[:min(1000, len(sorted_ids))]):
        if len(chosen) >= 10:
            break
        if sample_id not in peak_set and score[sample_id] < threshold:
            continue
        ctx.step(30 + int(60 * step / min(1000, len(sorted_ids))), "Grouping distinct grip features…")
        center = sample[sample_id]
        radius = maximum_size / 2
        if any(np.linalg.norm(center - previous) < radius * 1.4 for previous in centers):
            continue
        ids = np.asarray(all_tree.query_ball_point(center @ frame[:2, :3].T, radius), dtype=int)
        if len(ids) < 6:
            continue
        # Keep only the connected selected component containing the seed.
        mapping = np.full(len(mesh.faces), -1, dtype=int)
        mapping[ids] = np.arange(len(ids))
        edges = adjacency[(mapping[adjacency] >= 0).all(axis=1)]
        local = mapping[edges]
        graph = coo_matrix((np.ones(len(local)), (local[:, 0], local[:, 1])), shape=(len(ids), len(ids)))
        _, labels = connected_components(graph, directed=False)
        seed = np.argmin(np.linalg.norm(points[ids] - center, axis=1))
        ids = ids[labels == labels[seed]]
        if len(ids) < 6:
            continue
        size = np.ptp(mesh.vertices[np.unique(mesh.faces[ids])], axis=0).max()
        if not minimum_size <= size <= maximum_size * 1.1:
            continue
        chosen.append(Candidate(ids, float(score[sample_id]),
            f"{size:.0f} mm feature · surface relief and changing slopes · review grip shape"))
        centers.append(center)
    ctx.step(100, f"Found {len(chosen)} candidate regions")
    return chosen

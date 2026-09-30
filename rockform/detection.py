from dataclasses import dataclass, field
import numpy as np
from scipy.spatial import cKDTree
from .model import GeometryError, TaskContext, Hold


@dataclass
class Candidate:
    faces: np.ndarray
    score: float
    explanation: str
    prepared_hold: Hold | None = None
    feature_patch: object = None
    reference: object = None
    feature_kind: str = "manual"


@dataclass
class ScreeningSummary:
    rejected_geometry: int = 0
    rejected_grip: int = 0
    reasons: list[str] = field(default_factory=list)
    needs_repair: int = 0

    @property
    def text(self):
        return (f"{self.needs_repair} recognized features need repair; "
                f"{self.rejected_grip} regions lacked a suitable natural feature or wall return; "
                f"{self.rejected_geometry} could not form a clean shape.")


def suggest_holds(revision, up, minimum_size=50., maximum_size=300., sensitivity=50., context=None, round_boundary=True):
    """Screen the actual retained surface, separately from solid validity."""
    from .geometry import prepare_hold
    from .features import protrusion_hold
    from .grip import assess_grip
    ctx = context or TaskContext()
    candidates = detect_candidates(revision, up, minimum_size, maximum_size, sensitivity, ctx)
    usable, summary = [], ScreeningSummary()
    for i, candidate in enumerate(candidates):
        ctx.step(30 + int(65 * i / max(1, len(candidates))), f"Checking suggested hold {i + 1} of {len(candidates)}…")
        try:
            if candidate.feature_patch is not None:
                candidate.prepared_hold = protrusion_hold(revision, candidate.feature_patch, candidate.reference,
                                                         context=ctx, kind=candidate.feature_kind, allow_incomplete=True)
                candidate.faces = candidate.prepared_hold.face_indices
            else:
                candidate.prepared_hold = prepare_hold(revision, candidate.faces, context=ctx,
                    round_boundary=round_boundary, minimum_size=minimum_size)
        except GeometryError as error:
            summary.rejected_geometry += 1
            summary.reasons.append(str(error))
            continue
        review = assess_grip(candidate.prepared_hold, up, ctx)
        candidate.prepared_hold.grip_review = review
        complete = candidate.prepared_hold.solid is not None
        # Missing data can interrupt the contact slope. Still identify measured
        # broad relief, but keep both the failed grip screen and repair status.
        recognized_incomplete = not complete and review['relief_mm'] >= 8.
        if not review['eligible'] and not recognized_incomplete:
            summary.rejected_grip += 1
            summary.reasons.extend(review['reasons'])
            continue
        size = (candidate.prepared_hold.solid if complete else candidate.prepared_hold.patch).extents
        candidate.score = max(review['contact_area_mm2'], 1.) * review['relief_mm']
        kind = "Raised feature" if candidate.feature_kind == "protrusion" else "Recess" if candidate.feature_kind == "recess" else "Surface patch"
        state = "Ready to preview" if complete else "Needs repair"
        candidate.explanation = f"{kind} · {state} · {size[0]:.0f} × {size[1]:.0f} mm · {review['relief_mm']:.0f} mm relief"
        usable.append(candidate)
    usable.sort(key=lambda candidate: (candidate.prepared_hold.solid is not None, candidate.score), reverse=True)
    summary.needs_repair = sum(c.prepared_hold.solid is None for c in usable[:10])
    ctx.step(100, f"{len(usable)} broad-feature candidates for review")
    return usable[:10], summary


def detect_candidates(revision, up, minimum_size=50., maximum_size=300., sensitivity=50., context=None):
    from .features import feature_regions
    import trimesh
    ctx = context or TaskContext()
    if len(revision.mesh.faces) < 10:
        raise GeometryError("Import a larger scan with wall surface around the feature.")
    reference, regions = feature_regions(revision, ctx, sensitivity)
    candidates = []
    tree = cKDTree(revision.mesh.triangles_center)
    for kind, patch in regions:
        size = float(patch.extents[:2].max())
        if not minimum_size <= size <= maximum_size:
            continue
        world = trimesh.transform_points(patch.triangles_center, np.linalg.inv(reference.frame))
        ids = np.unique(tree.query(world)[1])
        candidates.append(Candidate(ids, float(patch.area * patch.extents[2]),
            f"{kind} · {size:.0f} mm natural outline", feature_patch=patch,
            reference=reference, feature_kind=kind))
        if len(candidates) == 20:
            break
    return candidates

"""Generate a hold of a given type whose measured holding area matches a requested difficulty."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import numpy as np
import trimesh
from ..model import TaskContext
from . import shapes
from .holding import GripCone, pinch_cones, holding_area, difficulty_from_area, pole_figure, pinch_span_factor, grip_quality

LEVEL_WORDS = [  # longest phrases first
    (r"very easy|beginner|trivial", 0.), (r"very hard|brutal|extreme|desperate", 1.),
    (r"easy|positive|friendly", .2), (r"moderate|medium|intermediate", .5), (r"hard|difficult|tough", .8)]
# Pull directions all the way around the hold in the wall plane (0 = straight down, +90 = pulling right,
# -90 = pulling left, 180 = pulling up, an undercling), and from flat on the wall to 45 degrees away from it.
PULL_AROUND = np.linspace(-180., 180., 25)
PULL_OUTWARD = np.linspace(0., 45., 7)
PULL_CONE = 30.     # the reported difficulty range covers pulls within this angle of straight down
GRAB_STYLES = {"down": 0., "side_right": 90., "side_left": -90., "undercling": 180.}
GRAB_NAMES = {"down": "Down-pull", "side_right": "Side-pull (pulling right)",
              "side_left": "Side-pull (pulling left)", "undercling": "Undercling (pulling up)"}
SIZE_WORDS = [(r"extra small|tiny|mini|xs", shapes.SIZES["XS"]), (r"extra large|huge|giant|xl", shapes.SIZES["XL"]),
              (r"small", shapes.SIZES["S"]), (r"large|big", shapes.SIZES["L"])]
TYPE_WORDS = [(r"jugs?|buckets?|handles?", "jug"), (r"crimps?|crimpers?", "crimp"), (r"edges?|ledges?|rails?", "edge"),
              (r"slopers?|slopey|domes?|cobbles?", "sloper"), (r"pinch(es)?", "pinch"),
              (r"pockets?|monos?|(one|two|three|[123])[- ]finger", "pocket")]


@dataclass
class HoldRequest:
    hold_type: str = "jug"
    difficulty: float | None = None   # absolute 0-10 holding-area score
    level: float | None = None        # 0-1 within this type's achievable range (from words)
    seed: str | int = 0
    wall_angle: float = 0.            # degrees past vertical; positive = overhang
    cone: GripCone = field(default_factory=GripCone)
    size: float = 1.                  # body scale, see shapes.SIZES
    max_mm: float | None = None       # if set, choose the scale so the largest dimension is about this
    fingers: int | None = None        # pocket finger count; default follows hardness

    def cones(self):
        return pinch_cones() if self.hold_type == "pinch" else [self.cone]


def parse_description(text, seed=0):
    """Read a short description such as 'hard crimp on a 30 degree overhang, seed 12'."""
    t = text.lower()
    hold_type = next((name for pattern, name in TYPE_WORDS if re.search(rf"\b({pattern})\b", t)), None)
    if hold_type is None:
        raise ValueError(f"Name a hold type: {', '.join(shapes.HOLD_TYPES)}.")
    request = HoldRequest(hold_type, seed=seed)
    if m := re.search(r"(?:difficulty|level|score|grade)\s*(?:of\s*)?(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*/\s*10\b", t):
        request.difficulty = float(m.group(1) or m.group(2))
    else:
        request.level = next((v for pattern, v in LEVEL_WORDS if re.search(rf"\b({pattern})\b", t)), None)
    if m := re.search(r"(-?\d+(?:\.\d+)?)\s*(?:deg(?:ree)?s?|°)", t):
        request.wall_angle = float(m.group(1)) * (-1. if "slab" in t else 1.)
    elif re.search(r"\broof\b", t):
        request.wall_angle = 90.
    if m := re.search(r"\bseed\s*[:=]?\s*(\S+)", t):
        request.seed = m.group(1).strip(".,;")
    if hold_type == "pocket" and (m := re.search(r"\b(mono|one|two|three|[123])[- ]?finger|\bmono\b", t)):
        request.fingers = {"mono": 1, "one": 1, "two": 2, "three": 3}.get(m.group(1) or "mono", None) \
            or int(m.group(1))
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*)?(mm|cm|in(?:ch(?:es)?)?\b|\")", t):
        request.max_mm = float(m.group(1)) * {"mm": 1., "cm": 10.}.get(m.group(2), 25.4)
    else:
        request.size = next((v for pattern, v in SIZE_WORDS if re.search(rf"\b({pattern})\b", t)), 1.)
    return request


@dataclass
class GeneratedHold:
    mesh: trimesh.Trimesh
    params: dict
    request: HoldRequest
    target: float
    holding_area_mm2: float
    difficulty: float
    holding_faces: np.ndarray
    achievable: tuple[float, float]
    notes: list[str] = field(default_factory=list)
    pull: dict = field(default_factory=dict)

    @property
    def difficulty_range(self):
        return self.pull["min"], self.pull["max"]

    def grip_map(self, around_deg=0., outward_deg=0., rotation_deg=0.):
        """Per-face grip weight for one pull direction (default straight down), over every way of holding it.
        `rotation_deg` turns the hold on the wall (counter-clockwise seen from the climber); gravity stays put."""
        cones = list(self.request.cones()) + ([self.request.cone] if self.request.hold_type == "pinch" else [])
        return grip_quality(self.mesh, cones, self.request.wall_angle + outward_deg, rotation_deg - around_deg)

    def counted_map(self, around_deg=0., outward_deg=0., rotation_deg=0.):
        """Per-face weight of the surface one hand uses for this pull direction."""
        return measure(self.mesh, self.request, around_deg, outward_deg, rotation_deg)[2]

    def report(self, with_pole_figure=True):
        out = dict(type=self.request.hold_type, seed=str(self.request.seed), wall_angle_deg=self.request.wall_angle,
                   size_scale=round(self.request.size, 3),
                   target_difficulty=round(self.target, 3), difficulty=round(self.difficulty, 3),
                   holding_area_mm2=round(self.holding_area_mm2, 1),
                   achievable_difficulty=[round(v, 3) for v in self.achievable],
                   difficulty_range_by_pull=[round(v, 3) for v in self.difficulty_range], pull_angles=self.pull,
                   size_mm=np.round(self.mesh.extents, 1).tolist(), volume_cm3=round(self.mesh.volume / 1000., 2),
                   cones=[c.as_dict() for c in self.request.cones()], shape=self.params, notes=self.notes,
                   metric="Holding-area score: 0 = ~6000 mm^2 usable surface, 10 = ~120 mm^2 (log scale).")
        if with_pole_figure:
            out["pole_figure"] = pole_figure(self.mesh, self.request.cones())
        return out


def grip_modes(mesh, request, around_deg=0., outward_deg=0., rotation_deg=0.):
    """Holding area and face weights for each way of using the hold: {mode: (area, weights)}.
    A pinch can be squeezed or, if its top allows, hung from like a ledge. `rotation_deg` turns the hold
    on the wall; pulling at `around_deg` is the same as turning it the other way."""
    theta, phi = request.wall_angle + outward_deg, rotation_deg - around_deg
    if request.hold_type != "pinch":
        return {"hang": holding_area(mesh, request.cones(), theta, phi)}
    area, weights = holding_area(mesh, request.cones(), theta, phi)
    return {"squeeze": (area * pinch_span_factor(mesh.extents[0]), weights),
            "hang": holding_area(mesh, [request.cone], theta, phi)}


def measure(mesh, request, around_deg=0., outward_deg=0., rotation_deg=0.):
    """Score one pull direction (default straight down) on the requested wall, using whichever way of
    holding it is easiest: a climber who can hang a pinch's top will not squeeze it."""
    modes = grip_modes(mesh, request, around_deg, outward_deg, rotation_deg)
    area, weights = max(modes.values(), key=lambda m: m[0])
    return area, difficulty_from_area(area), weights


def best_mode(mesh, request, around_deg=0., outward_deg=0., rotation_deg=0.):
    modes = grip_modes(mesh, request, around_deg, outward_deg, rotation_deg)
    return max(modes, key=lambda k: modes[k][0])


def pull_range(mesh, request, around=PULL_AROUND, outward=PULL_OUTWARD, cone_deg=PULL_CONE, rotation_deg=0.):
    """Difficulty for each pull direction. Pulling at angle a around the hold equals turning the hold by -a
    on the wall; pulling out from the wall by o equals steepening the wall by o. The reported range covers
    pulls within `cone_deg` of straight down; `styles` rates each way of grabbing the hold."""
    grid = np.empty((len(outward), len(around)))
    for i, o in enumerate(outward):
        for j, a in enumerate(around):
            grid[i, j] = measure(mesh, request, a, o, rotation_deg)[1]
    s, o = np.meshgrid(np.radians(around), np.radians(outward))
    within = np.degrees(np.arccos(np.cos(s) * np.cos(o))) <= cone_deg + 1e-6
    styles = {name: measure(mesh, request, a, 0., rotation_deg)[1] for name, a in GRAB_STYLES.items()}
    best = int(np.argmin(grid[0]))
    return dict(around_deg=np.asarray(around).tolist(), outward_deg=np.asarray(outward).tolist(),
                difficulty=np.round(grid, 3).tolist(), cone_deg=float(cone_deg), min=float(grid[within].min()),
                max=float(grid[within].max()), median=float(np.median(grid[within])),
                styles={k: round(v, 3) for k, v in styles.items()},
                best_angle_deg=float(around[best]), best_difficulty=float(grid[0, best]),
                rotation_deg=float(rotation_deg))


def from_profile(request: HoldRequest, points, width, outline=2.5, texture=.9):
    """Rate a hold lofted from an edited side profile (see shapes.build_profile), gripped like
    `request.hold_type`. Nothing is searched: its difficulty is whatever the shape measures."""
    mesh, params = shapes.build_profile(points, width, outline, texture, request.seed)
    params["type"] = request.hold_type
    area, difficulty, mask = measure(mesh, request)
    return GeneratedHold(mesh, params, request, difficulty, area, difficulty, mask, (difficulty, difficulty), [],
                         pull_range(mesh, request))


def generate(request: HoldRequest, tolerance=0.1, context=None):
    """Search the shape's hardness until the measured difficulty reaches the target."""
    ctx = context or TaskContext()
    cache = {}

    def evaluate(h):
        if h not in cache:
            mesh, params = shapes.build(request.hold_type, h, request.seed, size, request.fingers)
            cache[h] = (mesh, params, *measure(mesh, request))
        return cache[h]

    size = request.size
    if request.max_mm:
        # Body dimensions scale with size; one correction step lands close to the requested largest dimension.
        for _ in range(2):
            size *= request.max_mm / shapes.build(request.hold_type, .5, request.seed, size,
                                                  request.fingers)[0].extents.max()
        request.size = size = float(np.clip(size, .3, 2.))
    lo, hi = shapes.HARDNESS_RANGE
    ctx.step(5, "Measuring the range this hold type can reach…")
    d_lo, d_hi = evaluate(lo)[3], evaluate(hi)[3]
    achievable = (min(d_lo, d_hi), max(d_lo, d_hi))
    notes = []
    if request.difficulty is not None:
        target = float(request.difficulty)
    else:
        target = achievable[0] + (.5 if request.level is None else request.level) * (achievable[1] - achievable[0])
    if not achievable[0] - tolerance <= target <= achievable[1] + tolerance:
        notes.append(f"A {request.hold_type} with seed {request.seed} reaches difficulty "
                     f"{achievable[0]:.1f}-{achievable[1]:.1f}; using the closest shape to {target:.1f}.")
    increasing = d_hi >= d_lo
    for i in range(16):
        ctx.step(10 + 5 * i, "Adjusting the shape to the requested difficulty…")
        mid = (lo + hi) / 2
        d = evaluate(mid)[3]
        if abs(d - target) <= tolerance / 2:
            break
        if (d < target) == increasing:
            lo = mid
        else:
            hi = mid
    best = min(cache, key=lambda h: abs(cache[h][3] - target))
    mesh, params, area, difficulty, mask = cache[best]
    if abs(difficulty - target) > tolerance and not notes:
        notes.append(f"Closest measured difficulty is {difficulty:.2f}; the shape changes in steps near this target.")
    ctx.step(95, "Rating the hold across pull angles…")
    return GeneratedHold(mesh, params, request, target, area, difficulty, mask, achievable, notes, pull_range(mesh, request))

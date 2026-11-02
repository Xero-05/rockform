"""Plan a boulder route from generated holds and write it as a Rock Wall Builder layout.

    python -m rockform.holdgen.route --grade V3 --wall-angle 20 --out route-v3

Wall frame, same as the Blender Rock Wall Builder: u across the wall (0 = centre line), v up the wall
(0 = bottom edge), millimeters. Holds are bolted on its bolt grid, and `spin` turns a hold
counter-clockwise as seen by the climber, which is holdgen's `rotation_deg`.

The planner is a prototype heuristic, not a grading model: the grade sets the hand-hold difficulty and
the move length, the wall angle lowers the hold difficulty it asks for, and each hand hold is turned so
its best pull points where the climber's weight will hang from it. Holds are then rated as placed, for
that pull, and the same formula turns them back into an estimated grade.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, asdict, fields
import json
import math
from pathlib import Path
import numpy as np
from ..model import GeometryError, default_profiles
from .drill import drill
from .generate import HoldRequest, generate, measure
from .library import MATCH, USES
from . import shapes

# grade: (hand-hold difficulty as placed, hand move length in mm, foot-hold level 0-1 within its type)
GRADES = {"V0": (1.5, 430., 0.), "V1": (2.4, 470., .1), "V2": (3.2, 500., .2), "V3": (4.0, 530., .3),
          "V4": (4.7, 560., .4), "V5": (5.4, 600., .55), "V6": (6.0, 640., .7), "V7": (6.6, 680., .85),
          "V8": (7.2, 720., 1.)}
# Hold types tried first for a target difficulty band (as placed, 0-10). Later entries are fallbacks.
TYPE_BANDS = [(2.5, ("jug",)), (4.0, ("jug", "edge", "pinch", "sloper")),
              (5.5, ("edge", "sloper", "pinch", "pocket")), (7.0, ("edge", "pocket", "crimp", "sloper")),
              (99., ("crimp", "pocket", "edge"))]
STEEP_PER_15 = .5          # route score added per 15 degrees of overhang; holds are asked to be easier by as much
REACH_PER_100 = .35        # route score added per 100 mm of move beyond 430 mm
BODY_DROP = 700.           # the climber's hips hang this far below the hand they are pulling on
START_V, FOOT_V, TOP_MARGIN, EDGE_MARGIN, GAP = 1300., 350., 250., 200., 30.
ROUTE_COLORS = [(.90, .25, .25), (.25, .55, .95), (.30, .75, .35), (.95, .75, .15), (.70, .40, .85)]
LAYOUT_FORMAT, LAYOUT_VERSION = "rock_wall_layout", 1     # the Blender add-on's layout file
ROLE_COLORS = {"START": "#33bf4d", "HAND": "#4080e6", "FOOT": "#f2bf26", "FINISH": "#e64040"}


@dataclass
class Wall:
    width: float = 2000.
    height: float = 3000.
    angle: float = 0.             # degrees past vertical; positive = overhang
    bolt_spacing: float = 200.
    thickness: float = 18.
    panel_width: float = 1220.
    panel_height: float = 2440.

    def bolt_axes(self):
        """Bolt-hole grid, as the add-on lays it out (centred on the wall)."""
        g = self.bolt_spacing
        ni, nj = int(self.width // g), int(self.height // g)
        us = -self.width / 2 + (self.width - (ni - 1) * g) / 2 + np.arange(ni) * g
        vs = (self.height - (nj - 1) * g) / 2 + np.arange(nj) * g
        return us, vs

    def snap(self, u, v):
        us, vs = self.bolt_axes()
        return float(us[np.abs(us - u).argmin()]), float(vs[np.abs(vs - v).argmin()])


@dataclass
class PlacedHold:
    role: str                     # START, HAND, FOOT or FINISH
    u: float
    v: float
    spin: float                   # degrees, counter-clockwise seen by the climber
    pull_deg: float               # expected pull: 0 = straight down, +90 = pulling right
    hold_type: str
    seed: str
    difficulty: float             # holding-area score for that pull, as placed
    radius: float                 # footprint radius around the bolt, mm
    size_mm: list
    style: str                    # the grab style closest to the expected pull
    hole_mm: float = 0.           # bore of the hole drilled through it, 0 = none
    hole_at: list = field(default_factory=lambda: [0., 0.])   # hole from the footprint centre, mm
    library: str = ""             # id of the library hold it is, "" = generated for this route
    name: str = ""                # the library hold's name
    file: str = ""
    report: dict = field(default_factory=dict, repr=False)
    hold: object = field(default=None, repr=False)            # the GeneratedHold, its mesh drilled
    moved: bool = False           # position or turn set by hand after planning

    def record(self):
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in ("report", "hold")}


def wrap(deg):
    return (deg + 180.) % 360. - 180.


def grab_style(pull_deg):
    a = abs(wrap(pull_deg))
    return "down" if a <= 45 else "undercling" if a >= 135 else "side_right" if pull_deg > 0 else "side_left"


def steep_bonus(wall_angle):
    return STEEP_PER_15 * max(wall_angle, 0.) / 15.


def move_cost(difficulty, move_mm, wall_angle):
    return difficulty + REACH_PER_100 * max(move_mm - 430., 0.) / 100. + steep_bonus(wall_angle)


def grade_score(grade):
    d, move, _ = GRADES[grade]
    return move_cost(d, move, 0.)


def expected_pull(hands, i):
    """Pull on hand hold i of a route's start-to-finish sequence. Moving to the next hold, the hips sit
    between this hold and the next one, below this hand; start and finish are matched, pulled straight down."""
    if i == 0 or i == len(hands) - 1:
        return 0.
    return math.degrees(math.atan2((hands[i + 1].u - hands[i].u) / 2, BODY_DROP))


def face_the_pull(placed):
    """The turn, in the add-on's 15 degree steps, that points a hold's easiest pull along its expected pull.
    Pulling at angle a equals turning the hold by -a."""
    return 15. * round(wrap(placed.pull_deg - placed.hold.pull["best_angle_deg"]) / 15.)


def rerate(holds, summary, wall):
    """Re-rate a route after its holds moved or turned: expected pulls, difficulty as placed, move scores and
    the estimated grade. Hand holds keep their start-to-finish order from the list."""
    hands = [h for h in holds if h.role != "FOOT"]
    costs = []
    for i, h in enumerate(hands):
        h.pull_deg = round(expected_pull(hands, i), 1)
        h.style = grab_style(h.pull_deg)
        h.difficulty = float(measure(h.hold.mesh, h.hold.request, h.pull_deg, 0., h.spin)[1])
        if i > 0:
            costs.append(move_cost(h.difficulty, math.hypot(h.u - hands[i - 1].u, h.v - hands[i - 1].v), wall.angle))
    for h in holds:
        if h.role == "FOOT":
            h.difficulty = float(measure(h.hold.mesh, h.hold.request, 0., 0., h.spin)[1])
    score, estimate = estimate_grade(costs)
    summary.update(estimated_grade=estimate, route_score=round(score, 2), hand_moves=len(costs),
                   move_costs=[round(c, 2) for c in costs], adjusted=sum(h.moved for h in holds))
    return summary


def placement_problems(routes, wall, gap=0.):
    """Holds that overlap or go past the wall's edge, over several routes: {id(hold): message}."""
    every = [h for holds in routes for h in holds]
    problems = {}
    for i, a in enumerate(every):
        if abs(a.u) + a.radius > wall.width / 2 or a.v - a.radius < 0 or a.v + a.radius > wall.height:
            problems[id(a)] = "goes past the edge of the wall"
        for b in every[:i]:
            if math.hypot(a.u - b.u, a.v - b.v) < a.radius + b.radius + gap:
                problems[id(a)] = problems[id(b)] = "may touch another hold"
    return problems


def estimate_grade(costs):
    """Route score from its hand moves (the crux counts as much as the average), and the nearest V grade."""
    costs = np.asarray(costs, float)
    crux = max((costs[i:i + 2].mean() for i in range(max(len(costs) - 1, 1))), default=0.)
    score = .5 * crux + .5 * costs.mean()
    names = list(GRADES)
    scores = np.array([grade_score(g) for g in names])
    return float(score), names[int(np.abs(scores - score).argmin())]


def _footprint_radius(mesh, centre):
    return float(np.hypot(*(mesh.vertices[:, :2] - centre).T).max())


def hardware(kind, bore=None):
    """Rockform's prototype bolt (hand holds) or screw (foot holds), optionally with another hole size."""
    profile = default_profiles()[kind]
    if bore:
        profile.shaft_diameter = bore - profile.clearance
    return profile


def _types_for(target, rng, previous):
    band = next(types for limit, types in TYPE_BANDS if target <= limit)
    order = list(rng.permutation(band))
    # Avoid three of the same type in a row.
    if len(previous) >= 2 and previous[-1] == previous[-2] and previous[-1] in order and len(order) > 1:
        order.remove(previous[-1])
        order.append(previous[-1])
    return order + [t for t in shapes.HOLD_TYPES if t not in order]


def library_hold(library, target, pull_deg, wall_angle, profile, used, use, notes):
    """The library hold that lands nearest `target` for this pull, drilled (unless it has its own hole) and turned
    so its easiest pull points along `pull_deg`: (GeneratedHold, spin, as-placed difficulty), or None.
    'prefer' takes it only within MATCH of the target; 'only' takes the nearest. Each library hold is used once
    per route, unless 'only' has run out of them."""
    if library is None or use == "off" or not library.holds:
        return None
    ranked = sorted((h.id in used, abs(library.quick_rating(h.id, wall_angle, pull_deg)[1] - target), h.id)
                    for h in library.holds)
    for again, gap, hold_id in ranked:
        if again and use != "only":
            break
        if use == "prefer" and gap > MATCH:
            break
        try:
            hold = library.rated(hold_id, wall_angle, profile)
        except GeometryError as error:
            notes.append(f"library hold {library.get(hold_id).name} skipped: {error}")
            continue
        if again:
            notes.append(f"library hold {library.get(hold_id).name} is used more than once: the library ran out")
        spin = 15. * round(wrap(pull_deg - hold.pull["best_angle_deg"]) / 15.)
        used.add(hold_id)
        return hold, spin, float(measure(hold.mesh, hold.request, pull_deg, 0., spin)[1])
    return None


def make_hold(target, pull_deg, wall_angle, seed, rng, previous, profile, tries=4, size=1.):
    """Generate a hold, drill it, turn it so its easiest pull points along `pull_deg`, and rate the drilled
    shape there. Tries other types, and corrects the generator's target once since turning and drilling
    change the score. Holds that cannot take the bolt at their centre are skipped.
    Returns (GeneratedHold with the drilled mesh, spin, as-placed difficulty, notes)."""
    best, notes = None, []
    for hold_type in _types_for(target, rng, previous)[:tries]:
        ask = target
        for _ in range(2):
            hold = generate(HoldRequest(hold_type, difficulty=float(np.clip(ask, 0., 10.)), seed=seed,
                                        wall_angle=wall_angle, size=size))
            hold.hole = (hold.mesh.bounds[0, :2] + hold.mesh.bounds[1, :2]) / 2
            if profile is not None:
                try:
                    hold.mesh, hold.hole = drill(hold.mesh, profile)
                    hold.hole_mm = profile.bore
                except GeometryError as error:
                    notes.append(f"{hold_type} skipped: {error}")
                    break
            # Pulling at angle a equals turning the hold by -a, so its easiest pull lands on pull_deg
            # when it is turned by pull_deg - (its easiest pull unturned). The add-on turns in 15 degree steps.
            spin = 15. * round(wrap(pull_deg - hold.pull["best_angle_deg"]) / 15.)
            placed = measure(hold.mesh, hold.request, pull_deg, 0., spin)[1]
            if best is None or abs(placed - target) < abs(best[2] - target):
                best = (hold, spin, placed)
            if abs(placed - target) <= .3:
                return (*best, notes)
            ask += target - placed
    if best is None:
        raise GeometryError(f"No hold type near difficulty {target:.1f} takes a bolt at its centre "
                            f"({'; '.join(notes)}).")
    return (*best, notes)


def plan_route(grade="V3", wall=None, seed=0, moves=None, avoid=(), foot_holds=True, bolt=10.5, progress=print,
               library=None, use_library="prefer"):
    """Lay out start, hand, foot and finish holds for `grade` on `wall`, each drilled for its bolt (hands)
    or screw (feet, `bolt` = 0 for no holes). `avoid` is a list of (u, v, radius) already on the wall.
    With a HoldLibrary, `use_library` 'prefer' takes a library hold for a hand hold when one lands within MATCH
    of the difficulty asked for, and 'only' takes every hold, feet too, from the library.
    Returns (holds, summary dict)."""
    wall = wall or Wall()
    if grade not in GRADES:
        raise ValueError(f"Choose a grade from {', '.join(GRADES)}.")
    if use_library not in USES:
        raise ValueError(f"Choose how to use the library: {', '.join(USES)}.")
    use = use_library if library is not None else "off"
    if use == "only" and not library.holds:
        raise ValueError("The hold library is empty: import or save holds first, or let the route generate them.")
    used = set()
    target, move_len, foot_level = GRADES[grade]
    hold_target = max(target - steep_bonus(wall.angle), .5)
    bolt_profile = hardware("bolt", bolt) if bolt else None
    screw_profile = hardware("screw") if bolt else None
    rng = np.random.default_rng(shapes.seed_to_int(seed) + 9091)
    taken = [tuple(map(float, a)) for a in avoid]
    usable = wall.width / 2 - EDGE_MARGIN
    top = wall.height - TOP_MARGIN
    notes = []

    def free(u, v, r):
        return abs(u) <= usable and v <= top and all(math.hypot(u - a, v - b) >= r + c + GAP for a, b, c in taken)

    def nearest_free(u, v, r):
        """The bolt nearest (u, v) where a hold of radius r fits, within two bolts' distance."""
        g = wall.bolt_spacing
        steps = sorted(((du, dv) for du in range(-2, 3) for dv in range(-2, 3)), key=lambda d: (math.hypot(*d), -d[1]))
        for du, dv in steps:
            spot = wall.snap(u + du * g, v + dv * g)
            if free(*spot, r):
                return spot
        return None

    # Hand positions: the moves share the height between the start and the top, zig-zagging so each
    # move is about the grade's move length. A planned hold is kept 80 mm clear of what is on the wall.
    start = None
    for _ in range(40):
        start = nearest_free(rng.uniform(-.4, .4) * usable, START_V, 80.)
        if start:
            break
    if start is None:
        raise ValueError("No free bolt near the start height: clear some holds or use a wider wall.")
    n = max(2, round((top - start[1]) / (.9 * move_len)))
    if moves:
        n = min(n, moves)
    rise = min((top - start[1]) / n, .95 * move_len)
    hands = [start]
    side = 1. if start[0] < 0 else -1.
    for k in range(n):
        u0, v0 = hands[-1]
        for attempt in range(40):
            dv = rise * rng.uniform(.9, 1.1)
            du = side * math.sqrt(max((move_len * rng.uniform(.9, 1.1)) ** 2 - dv ** 2, 0.)) * (1 if attempt < 20 else -1)
            spot = nearest_free(u0 + du, min(v0 + dv, top), 80.)
            if spot and spot[1] > v0 + 150 and all(math.hypot(spot[0] - a, spot[1] - b) > 250 for a, b in hands):
                break
        else:
            notes.append(f"Stopped after {k} moves: no free bolt for the next move.")
            break
        hands.append(spot)
        side = -side
    if len(hands) < 3:
        raise ValueError("The wall is too short, narrow or full for a route at this grade.")

    holds, costs, types = [], [], []
    seed_base = shapes.seed_to_int(seed)
    for i, (u, v) in enumerate(hands):
        role = "START" if i == 0 else "FINISH" if i == len(hands) - 1 else "HAND"
        if role == "HAND":
            # Moving to the next hold, the hips sit between this hold and the next one, below this hand.
            pull = math.degrees(math.atan2((hands[i + 1][0] - u) / 2, BODY_DROP))
            want = hold_target * rng.uniform(.85, 1.15)
        else:
            pull = 0.        # matched with both hands: a comfortable start, a finish about as hard as the route
            want = max(hold_target - 1., .5) if role == "START" else .9 * hold_target
        hseed = f"{seed_base}-{i}"
        why = []
        picked = library_hold(library, want, pull, wall.angle, bolt_profile, used, use, why)
        if picked is None:
            if use == "only":
                raise ValueError(f"No library hold can be placed for hold {i + 1} ({'; '.join(why) or 'none fit'}).")
            hold, spin, placed, more = make_hold(want, pull, wall.angle, hseed, rng, types, bolt_profile)
            why += more
        else:
            hold, spin, placed = picked
        notes += [f"hold {i + 1}: {w}" for w in why]
        r = _footprint_radius(hold.mesh, hold.hole)
        if not free(u, v, r):
            moved = nearest_free(u, v, r)
            if moved is None:
                notes.append(f"hold {i + 1} overlaps a hold already on the wall at u={u:.0f}, v={v:.0f}")
            else:
                u, v = moved
                hands[i] = moved
        taken.append((u, v, r))
        types.append(hold.request.hold_type)
        if i > 0:
            costs.append(move_cost(placed, math.hypot(u - hands[i - 1][0], v - hands[i - 1][1]), wall.angle))
        holds.append(PlacedHold(role, u, v, spin, round(pull, 1), hold.request.hold_type, str(hold.request.seed),
                                placed, r, np.round(hold.mesh.extents, 1).tolist(), grab_style(pull),
                                float(getattr(hold, "hole_mm", 0.)), hole_at=_offset(hold), report=_report(hold),
                                hold=hold, **_from_library(hold)))
        progress(f"{role:<6} {hold.request.hold_type:<6} at u={u:5.0f} v={v:5.0f} spin {spin:4.0f}  "
                 f"pull {pull:4.0f}  difficulty {placed:.1f} (asked {want:.1f})"
                 + (f"  library: {hold.params['name']}" if "library" in hold.params else ""))

    if foot_holds:
        # Feet about a metre below each hand from the third hold on, and a pair under the start.
        spots = [(hands[0][0] - 200, FOOT_V), (hands[0][0] + 200, FOOT_V)]
        spots += [(u - 150 * np.sign(u - hands[0][0] or 1.), v - 1000.) for u, v in hands[2:-1]]
        ftype = "jug" if foot_level < .3 else "edge"
        for k, (u, v) in enumerate(spots):
            if v < 150:
                continue
            hold = None
            if use == "only":
                hold = library_foot(library, wall.angle, screw_profile, used, notes)
                if hold is None:
                    break
            for attempt in range(0 if hold else 4):
                fseed = f"{seed_base}-f{k}" + (f"-{attempt}" if attempt else "")
                hold = generate(HoldRequest(ftype, level=foot_level, seed=fseed, wall_angle=wall.angle,
                                            size=shapes.SIZES["XS"]))
                hold.hole = (hold.mesh.bounds[0, :2] + hold.mesh.bounds[1, :2]) / 2
                try:
                    if screw_profile:
                        hold.mesh, hold.hole = drill(hold.mesh, screw_profile)
                        hold.hole_mm = screw_profile.bore
                    break
                except GeometryError as error:
                    notes.append(f"foot {k + 1} seed {fseed}: {error}")
                    hold = None
            if hold is None:
                continue
            r = _footprint_radius(hold.mesh, hold.hole)
            spot = nearest_free(u, v, r)
            if spot is None:
                continue
            taken.append((*spot, r))
            placed = measure(hold.mesh, hold.request)[1]
            holds.append(PlacedHold("FOOT", *spot, 0., 0., hold.request.hold_type, str(hold.request.seed), placed, r,
                                    np.round(hold.mesh.extents, 1).tolist(), "down",
                                    float(getattr(hold, "hole_mm", 0.)), hole_at=_offset(hold),
                                    report=_report(hold), hold=hold, **_from_library(hold)))
        progress(f"FEET   {sum(h.role == 'FOOT' for h in holds)} foot holds")

    score, estimate = estimate_grade(costs)
    summary = dict(requested_grade=grade, estimated_grade=estimate, route_score=round(score, 2),
                   requested_score=round(grade_score(grade), 2), wall_angle_deg=wall.angle, seed=str(seed),
                   hand_moves=len(costs), move_costs=[round(c, 2) for c in costs],
                   hand_hold_target=round(hold_target, 2), move_length_mm=move_len,
                   hand_hole=bolt_profile.fit_summary() if bolt_profile else None,
                   foot_hole=screw_profile.fit_summary() if screw_profile else None, notes=notes,
                   use_library=use, library_holds=sum(bool(h.library) for h in holds),
                   model="Prototype heuristic: score = hold difficulty as placed + reach + overhang; "
                         "the crux pair of moves counts as much as the average. Not calibrated on climbers.")
    # Rate the final positions the same way manual adjustments are rated (a hand nudged clear of another
    # hold after its pull was chosen gets the pull it now has).
    rerate(holds, summary, wall)
    for note in notes:
        progress("  note: " + note)
    return holds, summary


def library_foot(library, wall_angle, profile, used, notes):
    """The smallest library hold not used yet, drilled for the foot screw, or None when none is left."""
    for entry in sorted((h for h in library.holds if h.id not in used), key=lambda h: max(h.size_mm[:2])):
        try:
            hold = library.rated(entry.id, wall_angle, profile)
        except GeometryError as error:
            notes.append(f"library hold {entry.name} skipped as a foot: {error}")
            continue
        used.add(entry.id)
        return hold
    notes.append("The library ran out of holds for feet.")
    return None


def _from_library(hold):
    return dict(library=hold.params["library"], name=hold.params["name"]) if "library" in hold.params else {}


def _report(hold):
    return hold.report(with_pole_figure=False)


def _offset(hold):
    """Where the hole is, from the footprint centre the add-on centres scans on."""
    return np.round(hold.hole - (hold.mesh.bounds[0, :2] + hold.mesh.bounds[1, :2]) / 2, 3).tolist()


def write_route(holds, summary, wall, out, route_name="Generated route", color=None):
    """Write STL + JSON per hold, route.json, a Rock Wall Builder layout.json and a route.svg preview."""
    out = Path(out)
    (out / "holds").mkdir(parents=True, exist_ok=True)
    color = list(color or ROUTE_COLORS[0])
    key_base = "".join(ch if ch.isalnum() else "_" for ch in route_name).strip("_") or "route"
    layout_holds, scans, records = [], {}, []
    for n, h in enumerate(holds):
        mesh = h.hold.mesh
        stem = f"{n + 1:02d}_{h.role.lower()}_{h.hold_type}_d{h.difficulty:.1f}"
        path = out / "holds" / f"{stem}.stl"
        mesh.export(path)
        h.file = f"holds/{stem}.stl"        # relative to layout.json, so the folder can be moved or shared
        record = h.record()
        (out / "holds" / f"{stem}.json").write_text(json.dumps(dict(placement=record, hold=h.report), indent=2),
                                                    encoding="utf-8")
        records.append(record)
        key = f"{key_base}_{n + 1:02d}_{h.hold_type}"
        w, hh, d = h.size_mm
        # The hole is already in the STL: 'drilled' tells the add-on not to cut another.
        scans[key] = dict(file=h.file, axis="+Z", scale=1.0, max_tris=0, bolt=0., drilled=h.hole_mm,
                          bolt_at=h.hole_at)
        layout_holds.append(dict(
            params=dict(type="SCAN", scan=key, w=w, h=hh, d=d, rough=0., seed=0, crimp=0., pocket_dia=0.,
                        pocket_depth=0., bolt=h.hole_mm),
            role=h.role, route=1, on_wall=True, u=h.u, v=h.v, spin=h.spin,
            holdgen=dict(type=h.hold_type, seed=h.seed, difficulty=round(h.difficulty, 2), pull_deg=h.pull_deg,
                         style=h.style, grade=summary["requested_grade"], library=h.library, name=h.name)))
    layout = dict(format=LAYOUT_FORMAT, version=LAYOUT_VERSION,
                  wall=dict(width=wall.width, height=wall.height, angle=wall.angle, bolt_spacing=wall.bolt_spacing,
                            thickness=wall.thickness, panel_width=wall.panel_width, panel_height=wall.panel_height),
                  routes=[dict(name=route_name, color=color, holdgen=summary)], scans=scans, holds=layout_holds)
    (out / "layout.json").write_text(json.dumps(layout, indent=2), encoding="utf-8")
    (out / "route.json").write_text(json.dumps(dict(name=route_name, wall=asdict(wall), **summary, holds=records),
                                               indent=2), encoding="utf-8")
    (out / "route.svg").write_text(route_svg(holds, summary, wall, route_name), encoding="utf-8")
    return out / "layout.json"


def write_wall(routes, wall, out):
    """Write several routes on one wall: each in its own folder (as write_route), plus a layout.json
    holding all of them. `routes` is a list of (name, colour, holds, summary)."""
    out = Path(out)
    combined = None
    for n, (name, color, holds, summary) in enumerate(routes, start=1):
        folder = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_") or f"route_{n}"
        layout = json.loads(write_route(holds, summary, wall, out / folder, name, color).read_text(encoding="utf-8"))
        for rec in layout["holds"]:
            rec["route"] = n
        for scan in layout["scans"].values():
            scan["file"] = f"{folder}/{scan['file']}"        # relative to the wall's layout.json
        if combined is None:
            combined = layout
        else:
            combined["routes"] += layout["routes"]
            combined["scans"].update(layout["scans"])
            combined["holds"] += layout["holds"]
    (out / "layout.json").write_text(json.dumps(combined, indent=2), encoding="utf-8")
    return out / "layout.json"


def route_svg(holds, summary, wall, title):
    """Front view of the wall, as the climber sees it: bolt grid, holds by role, difficulty labels."""
    s, pad = .2, 40
    W, H = wall.width * s, wall.height * s

    def xy(u, v):
        return pad + (u + wall.width / 2) * s, pad + H - v * s

    us, vs = wall.bolt_axes()
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W + 2 * pad + 190:.0f}" height="{H + 2 * pad:.0f}" '
             f'font-family="sans-serif" font-size="11">',
             f'<rect x="{pad}" y="{pad}" width="{W:.0f}" height="{H:.0f}" fill="#c9a77c" stroke="#7a5c3a"/>']
    parts += [f'<circle cx="{xy(u, v)[0]:.1f}" cy="{xy(u, v)[1]:.1f}" r="1.5" fill="#3a2a1a"/>' for u in us for v in vs]
    hands = [h for h in holds if h.role != "FOOT"]
    path = " ".join(f"{'M' if i == 0 else 'L'}{xy(h.u, h.v)[0]:.1f},{xy(h.u, h.v)[1]:.1f}" for i, h in enumerate(hands))
    parts.append(f'<path d="{path}" fill="none" stroke="#333" stroke-width="1.5" stroke-dasharray="5 4"/>')
    for h in holds:
        x, y = xy(h.u, h.v)
        r = max(h.radius * s, 4)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{ROLE_COLORS[h.role]}" stroke="#222"/>')
        if h.role != "FOOT":
            # Arrow along the expected pull.
            a = math.radians(h.pull_deg)
            parts.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + 22 * math.sin(a):.1f}" '
                         f'y2="{y + 22 * math.cos(a):.1f}" stroke="#b00" stroke-width="2"/>')
        parts.append(f'<text x="{x + r + 3:.1f}" y="{y + 4:.1f}">{h.hold_type} {h.difficulty:.1f}</text>')
    lx = W + 2 * pad
    lines = [title, f"asked {summary['requested_grade']}, estimate {summary['estimated_grade']}",
             f"score {summary['route_score']} (target {summary['requested_score']})",
             f"wall {wall.angle:g} deg, {summary['hand_moves']} hand moves", "",
             "red line: expected pull"] + [k.title() for k in ROLE_COLORS]
    for i, text in enumerate(lines):
        if text.title() in (k.title() for k in ROLE_COLORS):
            parts.append(f'<circle cx="{lx + 6}" cy="{pad + 16 * i - 4}" r="5" fill="{ROLE_COLORS[text.upper()]}"/>')
            parts.append(f'<text x="{lx + 16}" y="{pad + 16 * i}">{text}</text>')
        else:
            parts.append(f'<text x="{lx}" y="{pad + 16 * i}">{text}</text>')
    return "\n".join(parts + ["</svg>"]) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m rockform.holdgen.route", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grade", default="V3", choices=list(GRADES))
    parser.add_argument("--wall-angle", type=float, default=0., help="degrees past vertical; positive = overhang")
    parser.add_argument("--wall-width", type=float, default=2000.)
    parser.add_argument("--wall-height", type=float, default=3000.)
    parser.add_argument("--bolt-spacing", type=float, default=200.)
    parser.add_argument("--seed", default="0")
    parser.add_argument("--moves", type=int, help="limit the number of hand moves (default: climb to the top)")
    parser.add_argument("--no-feet", action="store_true")
    parser.add_argument("--avoid", type=Path, help="JSON list of [u, v, radius] holds already on the wall")
    parser.add_argument("--name", default=None, help="route name (default: '<grade> <seed>')")
    parser.add_argument("--color", type=float, nargs=3, help="route colour, 0-1 RGB")
    parser.add_argument("--bolt", type=float, default=10.5, help="bolt hole diameter through hand holds (feet get a screw hole), 0 for no holes")
    parser.add_argument("--library", type=Path, nargs="?", const=True, default=None,
                        help="use holds from a hold library (default folder if none is given)")
    parser.add_argument("--use-library", choices=USES, default="prefer",
                        help="prefer: library holds that land near the difficulty asked for; only: library holds only")
    parser.add_argument("--validate", action="store_true", help="run the (slower) solid check on every hold")
    parser.add_argument("--out", type=Path, default=Path("generated-route"))
    args = parser.parse_args(argv)
    wall = Wall(args.wall_width, args.wall_height, args.wall_angle, args.bolt_spacing)
    avoid = json.loads(args.avoid.read_text(encoding="utf-8")) if args.avoid else ()
    library = None
    if args.library:
        from .library import HoldLibrary
        library = HoldLibrary(None if args.library is True else args.library)
    holds, summary = plan_route(args.grade, wall, args.seed, args.moves, avoid, not args.no_feet, args.bolt,
                                library=library, use_library=args.use_library)
    if args.validate:
        from ..geometry import validate_solid
        for h in holds:
            validate_solid(h.hold.mesh)
        summary["solids_validated"] = True
    name = args.name or f"{args.grade} seed {args.seed}"
    layout = write_route(holds, summary, wall, args.out, name, args.color)
    print(f"Route '{name}': asked {summary['requested_grade']}, estimated {summary['estimated_grade']} "
          f"(score {summary['route_score']}, {summary['hand_moves']} hand moves, {len(holds)} holds)")
    print(f"LAYOUT {layout.resolve()}")


if __name__ == "__main__":
    main()

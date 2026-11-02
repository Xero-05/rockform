"""Procedural hold shapes: closed side profiles (Y-Z) lofted across the wall (X).

Adapted from ekamveer241/Climbing-Holds `hold_generator_v3.py`. A closed profile
can overhang, so incut jug lips, pocket roofs and hooked crimp ledges are possible.
Its Easy/Medium/Hard tables are kept as anchors of one continuous `hardness`
(0 = Easy, 0.5 = Medium, 1 = Hard), so generation can search for a measured difficulty.
Dimensions are millimeters with the flat back on Z = 0.
"""
from __future__ import annotations

import math
import zlib
import numpy as np
import trimesh

NU, NF, NB = 56, 64, 6   # columns across X, samples along the front curve, samples along the back

HOLD_TYPES = ("jug", "crimp", "edge", "sloper", "pinch", "pocket")
HOLD_INFO = {
    "jug": "Big incut bucket you can wrap your hand into",
    "crimp": "Small flat ledge, 6-20 mm deep, fingertips only",
    "edge": "Deeper ledge (20-40 mm) that fits four fingers",
    "sloper": "Smooth rounded cobble with no positive edge",
    "pinch": "Parallel-sided loaf squeezed between thumb and fingers",
    "pocket": "Rounded block with a 1-, 2- or 3-finger hole",
}

# Easy, Medium, Hard anchors. W across, H up, D standoff; see the reference repository for sources.
SPECS = {
    "jug": [dict(W=150, H=125, D=78, tl=36, bd=48, lean=35, p=2.5),
            dict(W=128, H=110, D=66, tl=30, bd=40, lean=28, p=2.5),
            dict(W=102, H=92, D=50, tl=24, bd=30, lean=18, p=2.5)],
    "crimp": [dict(W=105, H=38, D=20, inc=6, r=6.0, p=3.8),
              dict(W=88, H=32, D=13, inc=2, r=4.5, p=3.8),
              dict(W=70, H=28, D=7, inc=-3, r=3.5, p=3.8)],
    "edge": [dict(W=125, H=62, D=40, inc=12, r=8.0, p=3.4),
             dict(W=110, H=55, D=30, inc=5, r=7.0, p=3.4),
             dict(W=95, H=48, D=22, inc=-5, r=9.0, p=3.4)],
    "sloper": [dict(W=150, H=125, D=85, nt=3.4, nb=2.4, p=2.2),
               dict(W=135, H=110, D=78, nt=2.7, nb=2.3, p=2.2),
               dict(W=120, H=100, D=70, nt=2.1, nb=2.1, p=2.2)],
    "pinch": [dict(W=62, H=125, D=48, p=8.0),
              dict(W=78, H=145, D=55, p=5.5),
              dict(W=90, H=175, D=65, p=3.2)],
    "pocket": [dict(W=135, H=110, D=58, d=44, hy=30, a=45, p=2.6),
               dict(W=125, H=105, D=44, d=29, hy=26, a=35, p=2.6),
               dict(W=110, H=95, D=30, d=17, hy=22, a=20, p=2.6)],
}
HARDNESS_RANGE = (-0.2, 1.2)    # slight extrapolation beyond the published anchors
MINIMUM = dict(D=5., H=24., tl=12., bd=12., d=10., hy=18., r=2., nt=1.6, nb=1.6, p=2.)
FINGER_W = {1: 24., 2: 44., 3: 64.}
# Size presets scale a hold's body. Finger-sized features keep their size: a crimp's edge depth, a pinch's
# gap and a pocket's hole are set by difficulty and the hand, not by how big the hold is.
SIZES = {"XS": .5, "S": .7, "M": 1., "L": 1.25, "XL": 1.5}
SIZE_KEYS = {"jug": ("W", "H", "D", "tl", "bd"), "crimp": ("W", "H"), "edge": ("W", "H"),
             "sloper": ("W", "H", "D"), "pinch": ("H", "D"), "pocket": ("W", "H", "D")}


def seed_to_int(seed):
    text = str(seed).strip()
    try:
        return int(text)
    except ValueError:
        return zlib.crc32(text.encode("utf-8"))


def spec(hold_type, hardness):
    """Piecewise-linear interpolation through the Easy/Medium/Hard anchors."""
    easy, medium, hard = SPECS[hold_type]
    h = float(np.clip(hardness, *HARDNESS_RANGE))
    a, b, t = (easy, medium, h / .5) if h < .5 else (medium, hard, (h - .5) / .5)
    out = {k: a[k] + (b[k] - a[k]) * t for k in a}
    return {k: max(v, MINIMUM.get(k, -math.inf)) for k, v in out.items()}


def _round_poly(pts, seg=5):
    """pts = [(y, z, radius)]; returns a dense polyline with rounded corners."""
    out = [(pts[0][0], pts[0][1])]
    for i in range(1, len(pts) - 1):
        (y0, z0, _), (y1, z1, r), (y2, z2, _) = pts[i - 1], pts[i], pts[i + 1]
        v1, v2 = np.array([y0 - y1, z0 - z1]), np.array([y2 - y1, z2 - z1])
        l1, l2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if r <= 1e-6 or l1 < 1e-9 or l2 < 1e-9:
            out.append((y1, z1))
            continue
        u1, u2 = v1 / l1, v2 / l2
        ang = math.acos(float(np.clip(u1 @ u2, -1., 1.)))
        if ang < 1e-3 or ang > math.pi - 1e-3:
            out.append((y1, z1))
            continue
        t = r / math.tan(ang / 2)
        if t > .5 * min(l1, l2):
            t = .5 * min(l1, l2)
            r = t * math.tan(ang / 2)
        bis = (u1 + u2) / np.linalg.norm(u1 + u2)
        c = np.array([y1, z1]) + bis * r / math.sin(ang / 2)
        a1 = math.atan2(y1 + u1[0] * t - c[0], z1 + u1[1] * t - c[1])
        a2 = math.atan2(y1 + u2[0] * t - c[0], z1 + u2[1] * t - c[1])
        d = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        n = max(2, int(math.ceil(abs(d) / (math.pi / 2) * seg)))
        a = a1 + d * np.arange(n + 1) / n
        out.extend(zip(c[0] + r * np.sin(a), c[1] + r * np.cos(a)))
    out.append((pts[-1][0], pts[-1][1]))
    return np.asarray(out)


def _resample(poly, n):
    """n points equally spaced by arc length along a polyline."""
    d = np.concatenate([[0.], np.cumsum(np.linalg.norm(np.diff(poly, axis=0), axis=1))])
    s = np.linspace(0., d[-1], n)
    return np.column_stack([np.interp(s, d, poly[:, 0]), np.interp(s, d, poly[:, 1])])


# Profile builders: local standoff D -> [(y, z, corner radius)] from the bottom wall corner,
# over the front, to the top wall corner.
def _jug(S):
    H, tl, bd, lean = S["H"], S["tl"], S["bd"], math.radians(S["lean"])

    def f(D, x):
        yA = -H / 2
        yc = yA + .80 * H                        # lip crest
        zin = D - tl
        zb = max(10., zin - bd * math.tan(lean))
        # One lip only: the bowl's back ramps into the wall below the crest, with no raised rear rim.
        yT = yc - .4 * bd
        return [(yA, 0, 0), (yA + .10 * H, .92 * D, .45 * D), (yc, D, .45 * tl), (yc, zin, .45 * tl),
                (yc - bd, zb, .50 * bd), (yT, 0, 0)]
    return f


def _ledge(S):
    """Crimp and edge: sloped belly, rounded lip, flat ledge top tilted by `inc` (+ = incut)."""
    H, inc, rl = S["H"], math.radians(S["inc"]), S["r"]

    def f(D, x):
        yA, yT = -H / 2, H / 2
        yE = yT + D * math.tan(inc)
        return [(yA, 0, 0), (yA + .30 * (yE - yA), .80 * D, .30 * H), (yE, D, min(rl, .45 * D)), (yT, 0, 0)]
    return f


def _sloper(S):
    b, nt, nb = S["H"] / 2, S["nt"], S["nb"]
    th = math.pi * (1 - np.arange(161) / 160)
    c, s = np.cos(th), np.abs(np.sin(th))
    n = np.where(c > 0, nt, nb)
    y = b * np.sign(c) * np.abs(c) ** (2 / n)
    y[0], y[-1] = -b, b

    def f(D, x):
        z = D * s ** (2 / n)
        z[0] = z[-1] = 0.
        return list(zip(y, z, np.zeros_like(y)))
    return f


def _pinch(S, crown):
    """Loaf with long ramps at both ends, so its top and bottom are steep slopes rather than ledges and
    squeezing the sides is the way to hold it."""
    L = S["H"]

    def f(D, x):
        return [(-L / 2, 0, 0), (-.18 * L, .96 * D, .25 * L), (crown * L, D, 0),
                (.18 * L, .96 * D, .25 * L), (L / 2, 0, 0)]
    return f


def _pocket(S, pocket):
    """Block with one finger pocket. The pocket floor runs down into the hold (incut), so a finger hooks
    instead of sliding out, but stays above the sloped belly. The top ramps to the wall more steeply than
    45 degrees, so the block's top is no ledge and the pocket is the grip; the block grows taller when it
    needs room for that ramp. Heights are measured up from the bottom wall corner, then centred."""
    Dn, d, hy, a = S["D"], pocket["d"], pocket["hy"], math.radians(pocket["a"])
    wx, xc = pocket["wx"], pocket["xc"]
    belly, ramp = .20 * S["H"], 1.15 * Dn          # ramp rise > run: its normal is ~49 degrees off vertical
    lowest = belly + 6.                             # opening bottom, clear of the belly
    H = max(S["H"], lowest + hy + 6. + ramp)
    y0n = lowest + (H - ramp - 6. - hy - lowest) * pocket["yp"]   # spare height places the pocket
    y1n = y0n + hy
    shift = -H / 2

    def f(D, x):
        pts = [(shift, 0, 0), (shift + belly, D, .25 * S["H"])]
        t = (x - xc) / (wx / 2)
        sd = math.sqrt(max(0., 1 - t * t)) if abs(t) < 1 else 0.
        if sd > .06:
            de = min(d, D - 9.) * sd
            h2 = hy / 2 * min(1., .35 + sd)
            y0, y1 = (y0n + y1n) / 2 - h2, (y0n + y1n) / 2 + h2
            room = y0 - belly * (D - de) / D - 4.       # floor must stay above the belly at that depth
            dy = max(0., min(de * math.tan(a), room))
            pts += [(shift + y0, D, 4. * sd), (shift + y0 - dy, D - de, .45 * hy * sd),
                    (shift + y1 - dy, D - de, .45 * hy * sd), (shift + y1, D, 4. * sd)]
        top = max(H - 1.15 * D, y1n + 4.)
        return pts + [(shift + top, D, .15 * D), (shift + H, 0, 0)]
    return f


class Variation:
    """All random draws for one seed, taken up front so they do not depend on hardness."""

    def __init__(self, seed):
        rng = np.random.default_rng(seed_to_int(seed))
        self.scale = rng.uniform(.93, 1.07, 8)
        self.angle = rng.uniform(-3., 3., 3)
        self.phase = rng.uniform(0, 2 * math.pi, 4)
        self.dz_amp = rng.uniform(.04, .10)
        self.shear = rng.uniform(-1, 1, 2) * (.10, .06)
        self.bumps = np.column_stack([rng.uniform(0, 2 * math.pi, 5), rng.uniform(.04, .12, 5),
                                      rng.uniform(0, 2 * math.pi, 5), rng.uniform(.3, 1., 5)])
        self.crown = rng.uniform(-.06, .06)
        self.finger = rng.uniform()
        self.pocket = rng.uniform(-1, 1, 2)


def _fingers(hardness, draw):
    # Discrete finger count follows the anchors: 3 (easy), 2-3 (medium), 1-2 (hard).
    if hardness < 1 / 3:
        return 3
    return (2 if draw < .5 else 3) if hardness < 2 / 3 else (1 if draw < .5 else 2)


def _texture(hardness):
    """Surface bump amplitude: rougher on harder holds, as in the reference generator."""
    return float(np.interp(hardness, (0., .5, 1.), (.5, .9, 1.4)))


def _shape(hold_type, hardness, seed, size, fingers):
    """Dimensions, parameters and profile builder of one generated hold, plus the X its middle section is
    taken at (a pocket's hole centre, else 0)."""
    if hold_type not in SPECS:
        raise ValueError(f"Unknown hold type {hold_type!r}; choose one of {', '.join(HOLD_TYPES)}.")
    var = Variation(seed)
    S = spec(hold_type, hardness)
    for key, k in zip(("W", "H", "D", "tl", "bd", "d", "hy", "r"), var.scale):
        if key in S:
            S[key] *= k
    for key, k in zip(("lean", "inc", "a"), var.angle):
        if key in S:
            S[key] += k
    for key in SIZE_KEYS[hold_type]:
        S[key] = max(S[key] * size, MINIMUM.get(key, 0.))
    params = dict(type=hold_type, hardness=float(hardness), seed=str(seed), size=float(size),
                  **{k: round(float(v), 3) for k, v in S.items()})

    x0 = 0.
    if hold_type == "jug":
        prof = _jug(S)
    elif hold_type in ("crimp", "edge"):
        prof = _ledge(S)
    elif hold_type == "sloper":
        prof = _sloper(S)
    elif hold_type == "pinch":
        prof = _pinch(S, var.crown)
    else:
        fingers = fingers or _fingers(hardness, var.finger)
        pocket = dict(d=min(S["d"], S["D"] - 9.), hy=S["hy"], a=S["a"], wx=FINGER_W[fingers],
                      xc=.10 * var.pocket[0] * S["W"], yp=.5 + .4 * var.pocket[1])
        prof = _pocket(S, pocket)
        params.update(fingers=fingers, pocket_depth=round(pocket["d"], 3))
        x0 = pocket["xc"]
    return var, S, params, prof, x0


def build(hold_type, hardness, seed=0, size=1., fingers=None):
    """Return (trimesh, parameters) for a closed solid with outward normals. `size` scales the body
    (1 = the published dimensions; see SIZES). `fingers` fixes a pocket's finger count (default: from
    hardness)."""
    var, S, params, prof, _x0 = _shape(hold_type, hardness, seed, size, fingers)
    amp = _texture(hardness) * min(1., S["D"] / 25.)
    return _loft(prof, S["W"], S["H"], S["D"], S["p"], amp, var), params


def _simplify(poly, tolerance):
    """Ramer-Douglas-Peucker: the indices of the points that keep a polyline within `tolerance`."""
    keep, stack = {0, len(poly) - 1}, [(0, len(poly) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        d = poly[b] - poly[a]
        rel = poly[a + 1:b] - poly[a]
        dist = np.abs(d[0] * rel[:, 1] - d[1] * rel[:, 0]) / max(np.linalg.norm(d), 1e-9)
        k = int(np.argmax(dist))
        if dist[k] > tolerance:
            keep.add(a + 1 + k)
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return sorted(keep)


def section(hold_type, hardness, seed=0, size=1., fingers=None):
    """The middle side section of a generated hold (through a pocket's hole), as the profile editor's
    starting point: control points [y, z, corner radius] in mm from the bottom wall corner over the front
    to the top one, plus the width, outline exponent and texture that `build_profile` lofts it with.
    Smooth profiles (a sloper's superellipse) are thinned to a few fully rounded corners."""
    var, S, params, prof, x0 = _shape(hold_type, hardness, seed, size, fingers)
    pts = np.array(prof(S["D"], x0), float)
    if len(pts) > 12:
        poly = pts[:, :2]
        idx = _simplify(poly, 2.)
        pts = np.column_stack([poly[idx], np.zeros(len(idx))])
        for i in range(1, len(pts) - 1):        # the largest radius _round_poly can fit at each corner
            v1, v2 = pts[i - 1, :2] - pts[i, :2], pts[i + 1, :2] - pts[i, :2]
            l1, l2 = np.linalg.norm(v1), np.linalg.norm(v2)
            ang = math.acos(float(np.clip(v1 @ v2 / (l1 * l2), -1., 1.)))
            pts[i, 2] = .5 * min(l1, l2) * math.tan(ang / 2) if ang < math.pi - 1e-3 else 0.
    return dict(points=np.round(pts, 2).tolist(), width=round(float(S["W"]), 1), outline=round(float(S["p"]), 2),
                texture=round(_texture(hardness), 2), type=hold_type)


def _crosses(poly):
    """True if two non-neighbouring segments of a polyline cross."""
    a, d = poly[:-1], np.diff(poly, axis=0)
    i, j = np.triu_indices(len(a), 2)

    def cross(u, v):
        return u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    r, den = a[j] - a[i], cross(d[i], d[j])
    with np.errstate(divide="ignore", invalid="ignore"):
        t, u = cross(r, d[j]) / den, cross(r, d[i]) / den
    eps = 1e-9
    return bool(((np.abs(den) > 1e-12) & (t > eps) & (t < 1 - eps) & (u > eps) & (u < 1 - eps)).any())


def build_profile(points, width, outline=2.5, texture=.9, seed=0):
    """Return (trimesh, parameters) for a hold lofted from an edited side profile: `points` are
    [y, z, corner radius] in mm from the bottom wall corner over the front to the top wall corner (see
    `section`). The ends are put on the wall and the profile is centred between them. `outline` is the
    across-the-wall exponent (low = oval, high = boxy) and `texture` the bump amplitude (generated holds use
    0.5-1.4). The seed adds the same depth wobble, shear and bumps as a generated hold's."""
    pts = np.array(points, float)
    if pts.ndim != 2 or pts.shape[1] != 3 or len(pts) < 3:
        raise ValueError("A profile needs at least three points: two on the wall and one in front of it.")
    pts[[0, -1], 1] = 0.
    pts[1:-1, 1] = np.maximum(pts[1:-1, 1], 1.)
    pts[:, 2] = np.maximum(pts[:, 2], 0.)
    H, Dn = pts[-1, 0] - pts[0, 0], float(pts[:, 1].max())
    if H < 10.:
        raise ValueError("The top wall corner must be at least 10 mm above the bottom one.")
    if not 10. <= width <= 1000.:
        raise ValueError("The width must be between 10 and 1000 mm.")
    pts[:, 0] -= (pts[0, 0] + pts[-1, 0]) / 2
    if _crosses(_round_poly(pts)):
        raise ValueError("The profile crosses itself. Move the points so the outline does not cross.")
    outline = float(np.clip(outline, 1.2, 10.))
    profile = dict(points=np.round(pts, 2).tolist(), width=round(float(width), 1), outline=round(outline, 2),
                   texture=round(float(texture), 2))
    params = dict(type="custom", seed=str(seed), size=1., W=round(float(width), 3), H=round(float(H), 3),
                  D=round(Dn, 3), p=outline, profile=profile)

    def prof(D, x):
        return [(y, z * D / Dn, r) for y, z, r in pts]
    amp = max(float(texture), 0.) * min(1., Dn / 25.)
    return _loft(prof, float(width), H, Dn, outline, amp, Variation(seed)), params


def _loft(prof, W, H, Dn, p, amp, var):
    """Sweep `prof(D, x)` across the wall (X), narrowing to the sides with exponent `p`, with the seed's
    depth wobble, shear and bumps of amplitude `amp`. Returns a closed trimesh with outward normals."""
    ax = W / 2.
    ang, freq, phase, weight = var.bumps.T
    fx, fy, bump_amp = freq * np.cos(ang), freq * np.sin(ang), amp * weight
    columns = []
    for i in range(1, NU):
        u = -math.cos(math.pi * i / NU)
        x = ax * u
        k = (1. - abs(u) ** p) ** (1. / p)
        D = Dn * (1. + var.dz_amp * math.sin(1.7 * u + var.phase[0]))
        pts = [(k * y, k * z, k * r) for y, z, r in prof(D, x)]
        yz = _resample(_round_poly(pts), NF)
        w = np.sin(math.pi * np.arange(NF) / (NF - 1))
        texture = (bump_amp * np.sin(fx * x + np.outer(yz[:, 0], fy) + phase)).sum(axis=1)
        yz[1:-1, 1] = np.maximum(yz[1:-1, 1] + (w * k * texture)[1:-1], .3)
        yz[:, 0] += .5 * H * (var.shear[0] * u + var.shear[1] * (u * u - .4)) * math.sqrt(k)
        back_y = yz[-1, 0] + (yz[0, 0] - yz[-1, 0]) * np.arange(1, NB + 1) / (NB + 1.)
        col = np.vstack([yz, np.column_stack([back_y, np.zeros(NB)])])
        columns.append(np.column_stack([np.full(len(col), x), col]))
    NS = NF + NB
    verts = np.vstack([[-ax, 0., 0.], *columns, [ax, 0., 0.]])
    last = len(verts) - 1

    def V(i, j):
        return 0 if i == 0 else last if i == NU else 1 + (i - 1) * NS + j % NS

    faces = []
    for i in range(NU):
        for j in range(NS):
            a, b, c, d = V(i, j), V(i + 1, j), V(i + 1, j + 1), V(i, j + 1)
            faces.extend(t for t in ((a, b, c), (a, c, d)) if len(set(t)) == 3)
    mesh = trimesh.Trimesh(verts, np.array(faces), process=False)
    if mesh.volume < 0:
        mesh.invert()
    return mesh

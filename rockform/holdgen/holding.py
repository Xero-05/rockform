"""Holding-area difficulty metric, ported from the `HoldingArea` geometry-nodes group.

Frame (millimeters, same as Rockform holds): X across the wall, Y up the wall,
Z out of the wall, flat back on Z = 0. The climber's pull is fixed in the world;
the hold is rotated on the wall (phi, about Z) and the wall is tilted (theta,
about X; positive = overhang), as in the Blender pole-figure script.

A face counts as holding area when its normal, projected into the plane of the
pulling arc, lies between the arc start and end vectors, and its angle out of
that plane is within the cutoff. The flat back against the wall never counts.

Two refinements beyond the node group, both switchable to reproduce it exactly:
- grip quality: a face's weight depends on where its normal sits in the arc. Outward
  slopes hold only by friction (weight 0 at v1), a flat top holds well (0.6), and an
  incut surface the fingers curl into holds best (1 from 30 degrees incut). This is why
  a jug lip out-grips a sloper of the same area (`quality=None` = every face counts fully);
- hand window: only the best `hand_width` band counts, since one hand cannot use a
  whole wide hold (None = the whole hold).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def arc_vector(degrees):
    """Unit vector in the Y-Z plane: 0 = straight up, + = out of the wall, - = into the wall."""
    a = np.radians(degrees)
    return np.array([0., np.cos(a), np.sin(a)])


@dataclass
class GripCone:
    """Pulling arc and out-of-plane cutoff (the node group's three inputs)."""
    start: np.ndarray = field(default_factory=lambda: arc_vector(45.))   # debug vector 1: up and out
    end: np.ndarray = field(default_factory=lambda: arc_vector(-80.))    # debug vector 2: up and into the wall
    cutoff_deg: float = 30.
    pull: np.ndarray = field(default_factory=lambda: np.array([0., -1., 0.]))
    # (degrees from the arc start, weight): sloping 0, flat (45 from start) 0.6, 30 degrees incut 1.
    quality: tuple | None = ((0., 0.), (45., .6), (75., 1.), (180., 1.))
    hand_width: float | None = 90.
    window_axis: int = 0      # 0 = band across the wall, 1 = band up the wall (pinch sides)

    def as_dict(self):
        return dict(start=np.round(self.start, 6).tolist(), end=np.round(self.end, 6).tolist(),
                    cutoff_deg=float(self.cutoff_deg), pull=np.round(self.pull, 6).tolist(),
                    quality=None if self.quality is None else [list(q) for q in self.quality], hand_width=self.hand_width,
                    window_axis=self.window_axis)


def pinch_cones(cutoff_deg=40., thumb_width=35.):
    """One cone per side of a pinch: side normals from 30 degrees below to 60 degrees above horizontal.

    The thumb side limits a pinch, so each side counts only a thumb-pad-length band up the wall.
    """
    def side(sign):
        down, up = np.radians(-30.), np.radians(60.)
        return GripCone(np.array([sign * np.cos(down), np.sin(down), 0.]),
                        np.array([sign * np.cos(up), np.sin(up), 0.]), cutoff_deg,
                        np.array([-sign, -1., 0.]) / np.sqrt(2.), quality=None, hand_width=thumb_width, window_axis=1)
    return side(-1.), side(1.)


def orientation(theta_deg=0., phi_deg=0.):
    """Rotation applied to the hold: phi on the wall about Z, then wall steepness theta about X."""
    t, p = np.radians(theta_deg), np.radians(phi_deg)
    rx = np.array([[1., 0., 0.], [0., np.cos(t), -np.sin(t)], [0., np.sin(t), np.cos(t)]])
    rz = np.array([[np.cos(p), -np.sin(p), 0.], [np.sin(p), np.cos(p), 0.], [0., 0., 1.]])
    return rx @ rz


def wall_faces(mesh, tolerance=0.05):
    """Faces lying on the back plane; they are bolted to the wall, not gripped."""
    return (mesh.triangles[:, :, 2].max(axis=1) <= mesh.bounds[0, 2] + tolerance) & (mesh.face_normals[:, 2] < -0.99)


def holding_weights(normals, cone):
    """Per-face weight in [0, 1]; zero outside the arc or beyond the out-of-plane cutoff."""
    start, end = _unit(cone.start), _unit(cone.end)
    plane = _unit(np.cross(start, end))
    out_of_plane = normals @ plane
    projected = normals - out_of_plane[:, None] * plane
    length = np.linalg.norm(projected, axis=1)
    projected = projected / np.maximum(length, 1e-12)[:, None]
    # Between start and end (arcs below 180 degrees): same turning sense on both sides.
    inside = (np.cross(start, projected) @ plane >= -1e-9) & (np.cross(projected, end) @ plane >= -1e-9)
    within = np.degrees(np.arcsin(np.clip(np.abs(out_of_plane), 0., 1.))) <= cone.cutoff_deg
    weight = (inside & within & (length > 1e-9)).astype(float)
    if cone.quality is not None:
        from_start = np.degrees(np.arccos(np.clip(projected @ start, -1., 1.)))
        at, value = np.array(cone.quality, dtype=float).T
        weight *= np.interp(from_start, at, value)
    return weight


def _best_window(position, value, width):
    if width is None or len(position) == 0:
        return np.ones(len(position), dtype=bool)
    order = np.argsort(position)
    p, total = position[order], np.concatenate([[0.], np.cumsum(value[order])])
    ends = np.searchsorted(p, p + width, side="right")
    i = int(np.argmax(total[ends] - total[:-1]))
    keep = np.zeros(len(position), dtype=bool)
    keep[order[i:ends[i]]] = True
    return keep


def holding_area(mesh, cones=None, theta_deg=0., phi_deg=0.):
    """Return (weighted area mm^2, per-face weight). Several cones (pinch sides) report the weakest side times their count."""
    cones = [GripCone()] if cones is None else list(cones)
    rotation = orientation(theta_deg, phi_deg)
    normals = mesh.face_normals @ rotation.T
    centers = mesh.triangles_center @ rotation.T
    back = wall_faces(mesh)
    areas, weights = [], np.zeros(len(mesh.faces))
    for cone in cones:
        w = holding_weights(normals, cone) * ~back
        value = w * mesh.area_faces
        w = w * _best_window(centers[:, cone.window_axis], value, cone.hand_width)
        areas.append(float((w * mesh.area_faces).sum()))
        weights = np.maximum(weights, w)
    return min(areas) * len(cones), weights


def grip_quality(mesh, cones=None, theta_deg=0., phi_deg=0.):
    """Per-face grip weight before the hand window, for heat-map display (best cone per face)."""
    cones = [GripCone()] if cones is None else list(cones)
    normals = mesh.face_normals @ orientation(theta_deg, phi_deg).T
    back = wall_faces(mesh)
    return np.max([holding_weights(normals, cone) * ~back for cone in cones], axis=0)


def pinch_span_factor(gap_mm, comfortable=50., limit=100.):
    """Thumb-to-finger span penalty for pinches: 1 up to a comfortable gap, 0 at the limit."""
    return float(np.clip((limit - gap_mm) / (limit - comfortable), 0., 1.))


def pole_figure(mesh, cones=None, thetas=np.linspace(0., 90., 10), phis=np.linspace(-90., 90., 37)):
    """Holding area over wall steepness (rows) and rotation on the wall (columns)."""
    grid = np.array([[holding_area(mesh, cones, t, p)[0] for p in phis] for t in thetas])
    return dict(thetas_deg=np.asarray(thetas).tolist(), phis_deg=np.asarray(phis).tolist(), area_mm2=grid.tolist())


# Log-linear calibration: a large incut jug lip (~6000 mm^2) is 0, a few-millimeter crimp (~80 mm^2) is 10.
EASY_AREA, HARD_AREA = 6000., 80.


def difficulty_from_area(area):
    if area <= 0:
        return 10.
    score = 10. * np.log(EASY_AREA / area) / np.log(EASY_AREA / HARD_AREA)
    return float(np.clip(score, 0., 10.))


def area_for_difficulty(score):
    return float(EASY_AREA * (HARD_AREA / EASY_AREA) ** (np.clip(score, 0., 10.) / 10.))

"""Bolt and screw holes through generated holds, with no dependency on the scan app's geometry stack.

The hold's back is flat on Z = 0 and every hole runs straight along Z, so finding the surface above a hole
only needs vertical rays (NumPy, no spatial index). The cut itself is a boolean difference: `manifold3d`
on the desktop, or a function installed in `BOOLEAN` where that package is unavailable (the browser app
passes the meshes to manifold's WebAssembly build). This lets the route planner run under Pyodide.
"""
from __future__ import annotations

import numpy as np
import trimesh
from ..model import GeometryError

BOOLEAN = None      # optional replacement: BOOLEAN(solid, cutter) -> solid minus cutter


def top_heights(mesh, xy, chunk=64):
    """Highest surface point above each (x, y) and how many surface crossings a vertical ray makes there
    (2 for a simple solid: its front and its back). NaN where the ray misses the hold."""
    xy = np.asarray(xy, float).reshape(-1, 2)
    tri = mesh.triangles
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    v0, v1 = (b - a)[:, :2], (c - a)[:, :2]
    det = v0[:, 0] * v1[:, 1] - v0[:, 1] * v1[:, 0]
    usable = np.abs(det) > 1e-12                     # faces seen edge-on from above have no area to hit
    a, b, c, v0, v1, det = a[usable], b[usable], c[usable], v0[usable], v1[usable], det[usable]
    lo, hi = np.minimum(np.minimum(a, b), c)[:, :2], np.maximum(np.maximum(a, b), c)[:, :2]
    heights, counts = np.full(len(xy), np.nan), np.zeros(len(xy), int)
    for start in range(0, len(xy), chunk):
        p = xy[start:start + chunk]
        near = ((p[:, None, :] >= lo[None] - 1e-9) & (p[:, None, :] <= hi[None] + 1e-9)).all(axis=2)
        for k, i in enumerate(range(start, start + len(p))):
            f = np.nonzero(near[k])[0]
            if not len(f):
                continue
            d = p[k] - a[f, :2]
            s = (d[:, 0] * v1[f, 1] - d[:, 1] * v1[f, 0]) / det[f]
            t = (v0[f, 0] * d[:, 1] - v0[f, 1] * d[:, 0]) / det[f]
            inside = (s >= -1e-9) & (t >= -1e-9) & (s + t <= 1 + 1e-9)
            if not inside.any():
                continue
            f, s, t = f[inside], s[inside], t[inside]
            z = a[f, 2] + s * (b[f, 2] - a[f, 2]) + t * (c[f, 2] - a[f, 2])
            # A ray through a shared edge or vertex meets the same crossing in several faces.
            z = np.unique(np.round(z, 6))
            heights[i], counts[i] = z.max(), len(z)
    return heights, counts


def seat_cutter(x, y, profile, floor, top):
    """One solid of revolution for the clearance hole and its head seat (counterbore or countersink),
    from below the back to above the surface."""
    bore, seat = profile.bore / 2, profile.seat_diameter / 2
    top = top + 3.
    if profile.recess_depth <= 0:
        outline = [(0., -3.), (bore, -3.), (bore, top), (0., top)]
    elif profile.head_shape == "counterbore":
        outline = [(0., -3.), (bore, -3.), (bore, floor), (seat, floor), (seat, top), (0., top)]
    else:
        outline = [(0., -3.), (bore, -3.), (bore, floor), (seat, floor + profile.recess_depth), (seat, top), (0., top)]
    cutter = trimesh.creation.revolve(np.array(outline), sections=64)
    cutter.apply_translation([x, y, 0.])
    return cutter


def difference(solid, cutter):
    if BOOLEAN is not None:
        return BOOLEAN(solid, cutter)
    from ..mounts import _boolean          # desktop: manifold3d, double precision
    return _boolean([solid, cutter], "difference")


def seat_floor(mesh, profile, x, y):
    """Floor height of a head seat at (x, y) and the surface height above it, or the reason it does not fit."""
    radius = profile.seat_diameter / 2
    angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    rings = [[[x, y]]] + [np.column_stack([x + r * np.cos(angles), y + r * np.sin(angles)])
                          for r in (radius / 2, radius, radius + profile.edge_allowance)]
    heights, hits = top_heights(mesh, np.vstack(rings))
    # A ray down through a closed solid crosses its front and its back; more means an undercut above the seat.
    if np.isnan(heights).any() or (hits > 2).any():
        return None, "an undercut or the hold's edge is in the way of the bolt seat"
    seat = heights[:1 + 2 * len(angles)]
    floor = float(seat.min() - profile.recess_depth)
    if floor < profile.floor_allowance:
        return None, (f"{seat.min():.1f} mm of material where "
                      f"{profile.recess_depth + profile.floor_allowance:.1f} mm is needed for the bolt seat")
    return (floor, float(seat.max())), None


def drill(mesh, profile, step=4.):
    """Cut a hole and head seat as near the footprint centre as it fits: a jug's centre is often under its
    lip. Returns (drilled mesh, hole (x, y)); the Blender add-on and the route views put the hold's origin,
    its bolt, there. Raises GeometryError when no spot within a third of the hold fits."""
    lo, hi = mesh.bounds
    centre = (lo[:2] + hi[:2]) / 2
    half = (hi[:2] - lo[:2]) / 3
    grid = np.stack(np.meshgrid(np.arange(-half[0], half[0] + 1e-9, step), np.arange(-half[1], half[1] + 1e-9, step)),
                    -1).reshape(-1, 2)
    reason = None
    for offset in grid[np.argsort(np.hypot(*grid.T))][:120]:
        x, y = centre + offset
        fit, why = seat_floor(mesh, profile, x, y)
        reason = reason or why
        if fit:
            return difference(mesh, seat_cutter(float(x), float(y), profile, *fit)), np.array([x, y])
    raise GeometryError(f"no spot near the centre takes the bolt seat ({reason})")


"""
Rock Wall Builder - parametric climbing holds + parametric wall + arrange tools,
your own scans, route tags, layouts, setter's sheet, STL export and manufacturing checks.

How to use
  1. Blender -> Scripting workspace -> Text Editor -> New -> paste -> Run Script.
     (Press "Quick Start" at the top of the panel to see a finished wall straight away.)
  2. In the 3D Viewport press N and open the "Rock Wall" tab.
  3. Work down the sub-panels:
       Wall            size, overhang angle, panel size, bolt-hole grid -> Build / Update Wall
       Hold            category (natural rock or gym), type, size, roughness, bolt hole
       Seeds and Sets  seed + shuffle, or generate a whole set of different holds
       Arrange         drag holds around the wall, or auto-arrange them
       Scans           bring in your own 3D scans as holds
       Tags and Routes route list, hold roles (start/hand/foot/finish), colour by route
       Generate Route  a whole route of holds from Rockform's hold generator, rated as placed
       Manufacturing   print / cast checks that say WHY a hold fails and how to fix it
       Layouts+Export  save/load layouts, setter's sheet (HTML + CSV), STL/OBJ per hold
       Selected Hold   exact position / spin of the active hold, re-roll its seed

Units: the scene is set to millimetres (1 Blender unit = 1 mm).

Frames
  Wall : local X = across, local Y = up the wall, local Z = out of the wall.
         The wall object is rotated so it stands up; "Wall angle" leans it over.
  Hold : X = width, Y = height, Z = thickness (sticks out of the wall).
         The flat back sits on Z = 0 and the origin is the bolt position.
  Holds placed on the wall are PARENTED to it, so their Location X / Y are
  positions on the wall surface, and moving or tilting the wall carries them along.
"""

bl_info = {
    "name": "Rock Wall Builder",
    "version": (3, 10, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Rock Wall",
    "description": "Parametric climbing holds and wall, scans, routes, layouts, exports, manufacturing checks",
    "category": "Add Mesh",
}

import csv
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
from math import radians, degrees, pi

import bpy
import numpy as np
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector, geometry


# =============================================================================
# 1. Hold types, presets, colours
# =============================================================================
NATURAL_ITEMS = [
    ("JUG", "Jug", "Big, deep, easy positive hold"),
    ("CRIMP", "Crimp", "Thin flat edge with an adjustable edge depth"),
    ("SLOPER", "Sloper", "Smooth rounded dome with no edge"),
    ("PINCH", "Pinch", "Narrow and thick, squeezed between thumb and fingers"),
    ("POCKET", "Pocket", "Rounded hold with a finger pocket cut into it"),
    ("POCKET2", "Two-finger pocket", "Wide, shallow slot pocket for two fingers"),
    ("RAIL", "Rail", "Long, thin, nearly rectangular bar to pull along"),
    ("UNDERCLING", "Undercling", "Lip you pull up under, with the shelf below it"),
    ("VOLUME", "Volume", "Big, smooth, shallow shape that adds features to the wall"),
    ("FOOTCHIP", "Footchip", "Tiny hold for feet"),
]

# Man-made holds, like the polyurethane ones in a climbing gym: smooth, symmetric and clean.
GYM_ITEMS = [
    ("G_BUCKET", "Bucket jug", "Big smooth bowl you can grab with the whole hand"),
    ("G_DOME", "Dome sloper", "Smooth round dome with no edge"),
    ("G_EDGE", "Edge", "Straight flat-topped edge with rounded corners"),
    ("G_BLADE", "Blade pinch", "Thin upright blade to squeeze"),
    ("G_RAMP", "Ramp", "Wedge that is thick at the top and slopes down"),
    ("G_MONO", "Mono pocket", "Single round finger pocket"),
    ("G_JIB", "Jib", "Tiny round screw-on hold for feet or hands"),
    ("G_BARREL", "Barrel volume", "Half-cylinder volume"),
    ("G_TRIANGLE", "Triangle volume", "Large flat-topped triangle"),
    ("G_PYRAMID", "Pyramid volume", "Square pyramid volume"),
    ("G_HEXAGON", "Hexagon volume", "Large flat-topped hexagon"),
]
TYPE_ITEMS = NATURAL_ITEMS + GYM_ITEMS
CATEGORY_ITEMS = [
    ("NATURAL", "Natural rock", "Lumpy holds that look like real rock, different with every seed"),
    ("GYM", "Gym (man-made)", "Smooth, moulded holds like a climbing gym's: clean shapes, jibs and volumes"),
]
# Big volumes are left out of random mixes (add them by hand, they take a lot of wall)
VOLUME_TYPES = ("VOLUME", "G_BARREL", "G_TRIANGLE", "G_PYRAMID", "G_HEXAGON")


def category_of(t):
    return "GYM" if str(t).startswith("G_") else "NATURAL"


# Starting size (mm) and shape rules for each type.
#   rough_mul : multiplies the roughness slider
#   ledge     : a flat shelf is cut into the upper part
#   ledge_pos : 0-1, how far up the hold the shelf starts
#   edge_frac : shelf depth as a fraction of thickness (crimp uses its own slider)
# Optional shape keys (see SHAPE_DEFAULTS):
#   fp_exp        outline squareness: 2 = ellipse, 6 = nearly a rectangle
#   prof          height profile: 1 = dome, below 1 = flatter top
#   ledge_dir     +1 cuts the upper part into a shelf, -1 cuts the lower part (undercling)
#   pocket_aspect pocket height / width: below 1 = a wide slot
#   gym           man-made hold: no noise, the seed only nudges the proportions
#   sides, rot, corner   regular-polygon outline (3 = triangle ...), turned by rot degrees, corner 0-1 rounds it
#   zmode         height shape: dome (default), box (flat top), ridge (half-cylinder), ramp, cone (pyramid)
#   bowl          (width fraction, depth fraction) of a round pocket pressed into the top
SHAPE_DEFAULTS = dict(fp_exp=2.0, prof=1.0, ledge_dir=1, pocket_aspect=1.0, zmode="dome", sides=0, rot=90.0,
                      corner=0.0, bowl=None)
TYPE_PRESETS = {
    "JUG":    dict(w=160, h=110, d=70, rough_mul=1.0, ledge=True,  ledge_pos=0.20, edge_frac=0.40),
    "CRIMP":  dict(w=140, h=45,  d=35, rough_mul=0.6, ledge=True,  ledge_pos=0.10, edge_frac=0.0),
    "SLOPER": dict(w=170, h=120, d=50, rough_mul=0.5, ledge=False, ledge_pos=0.0,  edge_frac=0.0),
    "PINCH":  dict(w=50,  h=110, d=70, rough_mul=1.0, ledge=False, ledge_pos=0.0,  edge_frac=0.0),
    "POCKET": dict(w=130, h=110, d=50, rough_mul=1.0, ledge=False, ledge_pos=0.0,  edge_frac=0.0),
    "POCKET2": dict(w=140, h=100, d=50, rough_mul=0.8, ledge=False, ledge_pos=0.0, edge_frac=0.0,
                    pocket_aspect=0.55),
    "RAIL":   dict(w=320, h=40,  d=30, rough_mul=0.4, ledge=False, ledge_pos=0.0,  edge_frac=0.0,
                   fp_exp=6.0, prof=0.45),
    "UNDERCLING": dict(w=180, h=100, d=60, rough_mul=0.8, ledge=True, ledge_pos=0.15, edge_frac=0.45,
                       ledge_dir=-1),
    "VOLUME": dict(w=400, h=400, d=120, rough_mul=0.25, ledge=False, ledge_pos=0.0, edge_frac=0.0,
                   fp_exp=2.6, prof=0.75),
    "FOOTCHIP": dict(w=45, h=35, d=20, rough_mul=0.9, ledge=False, ledge_pos=0.0, edge_frac=0.0,
                     fp_exp=2.2),
}
_GYM = dict(gym=True, rough_mul=0.0, ledge=False, ledge_pos=0.0, edge_frac=0.0)
TYPE_PRESETS.update({
    "G_BUCKET": dict(_GYM, w=200, h=150, d=90, fp_exp=2.4, prof=0.55, bowl=(0.62, 0.55)),
    "G_DOME": dict(_GYM, w=160, h=160, d=70, fp_exp=2.0, prof=1.0),
    "G_EDGE": dict(_GYM, w=160, h=40, d=30, fp_exp=8.0, prof=5.0, zmode="box"),
    "G_BLADE": dict(_GYM, w=45, h=130, d=60, fp_exp=5.0, prof=2.2, zmode="ridge"),
    "G_RAMP": dict(_GYM, w=200, h=120, d=60, fp_exp=4.0, prof=4.0, zmode="ramp"),
    "G_MONO": dict(_GYM, w=120, h=100, d=50, fp_exp=2.2, prof=0.7, bowl=(0.42, 0.6)),
    "G_JIB": dict(_GYM, w=45, h=45, d=25, fp_exp=2.0, prof=0.8, bolt=8.0),
    "G_BARREL": dict(_GYM, w=320, h=180, d=90, fp_exp=8.0, prof=2.0, zmode="ridge"),
    "G_TRIANGLE": dict(_GYM, w=380, h=330, d=110, sides=3, rot=90.0, corner=0.18, prof=3.0, zmode="box"),
    "G_PYRAMID": dict(_GYM, w=300, h=300, d=120, sides=4, rot=45.0, corner=0.05, prof=1.0, zmode="cone"),
    "G_HEXAGON": dict(_GYM, w=340, h=300, d=100, sides=6, rot=0.0, corner=0.1, prof=3.0, zmode="box"),
})

TYPE_COLORS = {
    "JUG": (0.80, 0.33, 0.22),
    "CRIMP": (0.20, 0.45, 0.80),
    "SLOPER": (0.25, 0.68, 0.35),
    "PINCH": (0.88, 0.75, 0.20),
    "POCKET": (0.60, 0.35, 0.78),
    "POCKET2": (0.78, 0.40, 0.65),
    "RAIL": (0.25, 0.62, 0.70),
    "UNDERCLING": (0.85, 0.50, 0.20),
    "VOLUME": (0.50, 0.55, 0.62),
    "FOOTCHIP": (0.70, 0.70, 0.30),
    "SCAN": (0.45, 0.60, 0.50),
    "G_BUCKET": (0.90, 0.25, 0.25),
    "G_DOME": (0.20, 0.60, 0.90),
    "G_EDGE": (0.95, 0.80, 0.15),
    "G_BLADE": (0.60, 0.30, 0.85),
    "G_RAMP": (0.95, 0.50, 0.15),
    "G_MONO": (0.15, 0.75, 0.55),
    "G_JIB": (0.95, 0.40, 0.65),
    "G_BARREL": (0.25, 0.45, 0.85),
    "G_TRIANGLE": (0.90, 0.35, 0.20),
    "G_PYRAMID": (0.30, 0.70, 0.30),
    "G_HEXAGON": (0.85, 0.65, 0.15),
}
NATURAL_COLOR = (0.55, 0.50, 0.45)


def hold_color(p):
    """Base colour for a hold: by type or natural grey, with a small shade change per seed."""
    rng = np.random.default_rng(int(p["seed"]) + 777)
    shade = 0.85 + 0.30 * float(rng.random())
    base = TYPE_COLORS[p["type"]] if p.get("color") == "TYPE" else NATURAL_COLOR
    return tuple(min(1.0, c * shade) for c in base) + (1.0,)


# =============================================================================
# 2. Pure geometry (numpy only - no Blender calls)
# =============================================================================
def seeded_noise(points, seed, octaves=3, base_freq=1.2):
    """Smooth 3D noise made from random waves. The same seed always gives the same field.

    The waves (direction, frequency, phase) are drawn from numpy's generator seeded
    with `seed`, so different seeds give unrelated lumps. Output has a standard
    deviation of about 0.5.
    """
    rng = np.random.default_rng(int(seed) + 12345)
    total = np.zeros(len(points))
    power = 0.0
    for o in range(octaves):
        freq = base_freq * (2.0 ** o)
        amp = 0.42 ** o
        for _ in range(5):
            d = rng.normal(size=3)
            d /= np.linalg.norm(d)
            f = freq * (0.8 + 0.4 * float(rng.random()))
            phase = float(rng.uniform(0.0, 2.0 * pi))
            total += amp * np.sin(2.0 * pi * f * (points @ d) + phase)
            power += 0.5 * amp * amp
    return 0.5 * total / np.sqrt(power)


def hold_geometry(p, rings=64, segs=112):
    """Build a hold as a closed hemisphere with a flat back.

    p keys: type, w, h, d (mm), rough, seed, crimp, pocket_dia, pocket_depth.
    Width, height and thickness come out exactly as asked (before the pocket/ledge
    cuts, which only remove material). Returns (verts Nx3, faces, info).
    """
    pre = dict(SHAPE_DEFAULTS, **TYPE_PRESETS[p["type"]])
    w, h, d = float(p["w"]), float(p["h"]), float(p["d"])
    rough = float(p["rough"]) * pre["rough_mul"]

    # Unit hemisphere: vertex 0 is the apex (+Z), then `rings` rings of `segs` vertices.
    phi = (np.arange(1, rings + 1) / rings) * (pi / 2.0)
    theta = np.arange(segs) * (2.0 * pi / segs)
    PH, TH = np.meshgrid(phi, theta, indexing="ij")
    ring = np.stack([np.sin(PH) * np.cos(TH), np.sin(PH) * np.sin(TH), np.cos(PH)], axis=-1)
    dirs = np.vstack([[0.0, 0.0, 1.0], ring.reshape(-1, 3)])

    # Lumpy shape from the seeded noise
    radius = np.clip(1.0 + rough * seeded_noise(dirs, p["seed"]), 0.25, None)
    V = dirs * radius[:, None]
    fp_exp, prof = float(pre["fp_exp"]), float(pre["prof"])
    if pre.get("gym"):              # man-made: no noise; the seed only nudges the proportions a little
        rj = np.random.default_rng(int(p["seed"]) + 999)
        fp_exp *= 0.92 + 0.16 * float(rj.random())
        prof *= 0.92 + 0.16 * float(rj.random())
    rho = np.hypot(dirs[:, 0], dirs[:, 1])                   # 0 at the apex, 1 at the base
    th = np.arctan2(dirs[:, 1], dirs[:, 0])
    sides = int(pre["sides"])
    if sides >= 3:                                           # regular polygon outline, corners rounded
        a = 2.0 * pi / sides
        u = np.mod(th - radians(pre["rot"]), a) - a / 2.0
        sq = (1.0 - pre["corner"]) * np.cos(a / 2.0) / np.cos(u) + pre["corner"]
        V[:, 0] *= sq
        V[:, 1] *= sq
    elif abs(fp_exp - 2.0) > 1e-9:                           # squarer outline (superellipse)
        sq = (np.abs(np.cos(th)) ** fp_exp + np.abs(np.sin(th)) ** fp_exp) ** (-1.0 / fp_exp)
        V[:, 0] *= sq
        V[:, 1] *= sq
    zmode = pre["zmode"]
    if zmode == "dome":
        if prof != 1.0:                                      # flatter (or pointier) top
            V[:, 2] = radius * np.clip(dirs[:, 2], 0.0, None) ** prof
    else:
        if zmode == "box":                                   # flat top with rounded shoulders
            f = np.clip(1.0 - rho ** prof, 0.0, None) ** (1.0 / prof)
        elif zmode == "ridge":                               # half-cylinder along the hold's height
            xn = np.abs(V[:, 0]) / max(float(np.abs(V[:, 0]).max()), 1e-9)
            f = np.clip(1.0 - xn ** prof, 0.0, None) ** (1.0 / prof)
        elif zmode == "ramp":                                # thick at the top, sloping down
            yn = (V[:, 1] - V[:, 1].min()) / max(float(np.ptp(V[:, 1])), 1e-9)
            f = np.clip(1.0 - rho ** prof, 0.0, None) ** (1.0 / prof) * (0.2 + 0.8 * yn)
        else:                                                # cone / pyramid
            f = np.clip(1.0 - rho, 0.0, None) ** prof
        V[:, 2] = radius * f
    base_start = V.shape[0] - segs
    V[base_start:, 2] = 0.0                                  # flat back

    # No undercuts: going down the hold the outline may widen but never shrink, so
    # the shape is a clean "heightfield" that a one-piece mould can release.
    grid = V[1:, :2].reshape(rings, segs, 2)
    rr = np.hypot(grid[..., 0], grid[..., 1])
    grid = grid * (np.maximum.accumulate(rr, axis=0) / np.maximum(rr, 1e-9))[..., None]
    V[1:, :2] = grid.reshape(-1, 2)

    # Stretch to exactly width x height x thickness, centred on the bolt position
    V[:, 2] *= d / max(float(V[:, 2].max()), 1e-9)
    for axis, size in ((0, w), (1, h)):
        lo, hi = float(V[:, axis].min()), float(V[:, axis].max())
        V[:, axis] = (V[:, axis] - (lo + hi) / 2.0) * (size / max(hi - lo, 1e-9))

    # Put the bolt position (origin) at the centre of the footprint, so the hole has
    # material all round it. The overall sizes stay exactly as asked.
    bx, by = V[base_start:, 0], V[base_start:, 1]
    bx2, by2 = np.roll(bx, -1), np.roll(by, -1)
    cross = bx * by2 - bx2 * by
    area2 = float(cross.sum())
    if abs(area2) > 1e-6:
        V[:, 0] -= float(((bx + bx2) * cross).sum()) / (3.0 * area2)
        V[:, 1] -= float(((by + by2) * cross).sum()) / (3.0 * area2)

    max_z = float(V[:, 2].max())

    # Pocket: smooth bowl pressed into the front
    if p["type"] in ("POCKET", "POCKET2") or pre["bowl"]:
        asp = pre["pocket_aspect"]
        if pre["bowl"]:                                      # gym bowl / mono: size comes from the type
            rad = pre["bowl"][0] * min(w, h) / 2.0
            depth = pre["bowl"][1] * max_z
        else:
            wide = 1.7 if p["type"] == "POCKET2" else 1.0    # the slot is wider than a round pocket
            rad = min(float(p["pocket_dia"]) / 2.0 * wide, 0.45 * w, 0.45 * h / asp)
            depth = min(float(p["pocket_depth"]), 0.9 * max_z)
        cy = 0.05 * h
        dist = np.hypot(V[:, 0], (V[:, 1] - cy) / asp)
        t = np.clip(1.0 - dist / rad, 0.0, 1.0)
        t = t * t * (3.0 - 2.0 * t)
        V[:, 2] = np.where(V[:, 2] > 0.0, np.maximum(V[:, 2] - depth * t, 0.0), V[:, 2])

    # Ledge / crimp edge: cut the upper part back, leaving a flat shelf.
    # Done after the noise so the edge depth stays exact.
    if pre["ledge"]:
        edge = float(p["crimp"]) if p["type"] == "CRIMP" else pre["edge_frac"] * d
        edge = min(edge, 0.9 * max_z)
        ledge_y = pre["ledge_dir"] * pre["ledge_pos"] * (h / 2.0)
        cut = (V[:, 1] > ledge_y) if pre["ledge_dir"] > 0 else (V[:, 1] < ledge_y)
        V[cut, 2] = np.minimum(V[cut, 2], max_z - edge)

    V[:, 2] = np.maximum(V[:, 2], 0.0)

    # Faces (wound so the normals point outward)
    faces = []
    for j in range(segs):
        faces.append((0, 1 + j, 1 + (j + 1) % segs))
    for k in range(rings - 1):
        r0 = 1 + k * segs
        r1 = r0 + segs
        for j in range(segs):
            j2 = (j + 1) % segs
            faces.append((r0 + j, r1 + j, r1 + j2, r0 + j2))
    last = 1 + (rings - 1) * segs
    faces.append(tuple(last + j for j in range(segs - 1, -1, -1)))   # flat back

    info = {"radius": float(np.hypot(V[:, 0], V[:, 1]).max()), "maxz": max_z}
    return V, faces, info


def cylinder_geometry(radius, z0, z1, segs=32):
    """Closed cylinder along Z (used as the bolt-hole cutter)."""
    ang = np.arange(segs) * (2.0 * pi / segs)
    x, y = radius * np.cos(ang), radius * np.sin(ang)
    V = np.vstack([np.stack([x, y, np.full(segs, z0)], 1), np.stack([x, y, np.full(segs, z1)], 1)])
    faces = []
    for j in range(segs):
        j2 = (j + 1) % segs
        faces.append((j, j2, segs + j2, segs + j))
    faces.append(tuple(segs + j for j in range(segs)))
    faces.append(tuple(range(segs - 1, -1, -1)))
    return V, faces


def _breaks(total, step):
    pts = [0.0]
    x = step
    while x < total - 1e-6:
        pts.append(x)
        x += step
    pts.append(total)
    return pts


def bolt_axes(W, H, g):
    """Coordinates of the bolt-hole grid on the wall (centred on the wall)."""
    if g <= 0:
        return np.array([]), np.array([])
    ni, nj = int(W // g), int(H // g)
    if ni < 1 or nj < 1:
        return np.array([]), np.array([])
    us = -W / 2.0 + (W - (ni - 1) * g) / 2.0 + np.arange(ni) * g
    vs = (H - (nj - 1) * g) / 2.0 + np.arange(nj) * g
    return us, vs


def snap_bolts(u, v, W, H, g):
    """Snap a wall position to the nearest bolt hole."""
    us, vs = bolt_axes(W, H, g)
    if len(us) == 0 or len(vs) == 0:
        return u, v
    i = min(max(int(round((u - us[0]) / g)), 0), len(us) - 1)
    j = min(max(int(round((v - vs[0]) / g)), 0), len(vs) - 1)
    return float(us[i]), float(vs[j])


def _orient_outward(verts, faces, centre):
    """Flip any face whose normal points toward the centre (fine for a convex slab)."""
    out = []
    c = np.asarray(centre, dtype=float)
    for f in faces:
        pts = np.array([verts[i] for i in f], dtype=float)
        n = np.zeros(3)
        for a in range(len(pts)):
            b = (a + 1) % len(pts)
            n += np.cross(pts[a], pts[b])
        if float(np.dot(n, pts.mean(axis=0) - c)) < 0.0:
            f = tuple(reversed(f))
        out.append(tuple(f))
    return out


def wall_geometry(W, H, T, panel_w, panel_h, grid, hole_r=6.0):
    """Wall slab with panel seams (alternating materials) and bolt-hole discs.

    Local frame: X across (centred), Y up from 0 to H, Z out of the wall; the
    climbing face is Z = 0 and the slab is T thick behind it.
    Returns (verts, faces, material_index, body_face_count).
    Material 0/1 = the two panel shades, 2 = bolt holes.
    """
    us = [-W / 2.0 + x for x in _breaks(W, max(panel_w, 100.0))]
    vs = _breaks(H, max(panel_h, 100.0))
    nu, nv = len(us), len(vs)

    verts = [(u, v, 0.0) for v in vs for u in us] + [(u, v, -T) for v in vs for u in us]
    F = lambda i, j: j * nu + i
    B = lambda i, j: nu * nv + j * nu + i

    faces, mats = [], []
    for j in range(nv - 1):
        for i in range(nu - 1):
            faces.append((F(i, j), F(i + 1, j), F(i + 1, j + 1), F(i, j + 1)))
            mats.append((i + j) % 2)
            faces.append((B(i, j), B(i, j + 1), B(i + 1, j + 1), B(i + 1, j)))
            mats.append(0)
    for i in range(nu - 1):
        faces.append((F(i, 0), F(i + 1, 0), B(i + 1, 0), B(i, 0)))
        faces.append((F(i, nv - 1), F(i + 1, nv - 1), B(i + 1, nv - 1), B(i, nv - 1)))
        mats += [0, 0]
    for j in range(nv - 1):
        faces.append((F(0, j), F(0, j + 1), B(0, j + 1), B(0, j)))
        faces.append((F(nu - 1, j), F(nu - 1, j + 1), B(nu - 1, j + 1), B(nu - 1, j)))
        mats += [0, 0]
    faces = _orient_outward(verts, faces, (0.0, H / 2.0, -T / 2.0))
    body = len(faces)

    # Bolt holes: small dark discs just in front of the face (counter-clockwise, normal +Z)
    hu, hv = bolt_axes(W, H, grid)
    seg = 12
    for v in hv:
        for u in hu:
            c = len(verts)
            verts.append((float(u), float(v), 1.0))
            for s in range(seg):
                a = 2.0 * pi * s / seg
                verts.append((float(u) + hole_r * np.cos(a), float(v) + hole_r * np.sin(a), 1.0))
            for s in range(seg):
                faces.append((c, c + 1 + s, c + 1 + (s + 1) % seg))
                mats.append(2)
    return verts, faces, mats, body


# =============================================================================
# 3. Layout planning (pure Python/numpy)
# =============================================================================
def plan_layout(radii, W, H, g, existing, mode, seed, gap, margin, snap, rand_spin):
    """Choose wall positions for holds so they do not overlap.

    radii    : footprint radius of each hold to place (mm)
    existing : [(u, v, r)] holds already on the wall that stay where they are
    mode     : 'RANDOM' (dart throwing) or 'GRID' (tidy rows, shuffled)
    Returns (list of (u, v, spin_deg) in the same order as radii, number that did
    not fit without overlap).
    """
    rng = np.random.default_rng(int(seed))
    n = len(radii)
    placed_xy = [(float(u), float(v)) for u, v, _ in existing]
    placed_r = [float(r) for _, _, r in existing]
    out = [None] * n
    fails = 0

    def clearance(u, v, r):
        if not placed_r:
            return 1e9
        P = np.array(placed_xy)
        R = np.array(placed_r)
        return float((np.hypot(P[:, 0] - u, P[:, 1] - v) - (R + r + gap)).min())

    def spin():
        return float(15 * int(rng.integers(-6, 7))) if rand_spin else 0.0

    cells = []
    slot = {}
    if mode == "GRID" and n:
        rmax = max(radii)
        cell = 2.0 * rmax + gap
        cols = max(1, int((W - 2.0 * margin) // cell))
        rows = max(1, int((H - 2.0 * margin) // cell))
        for r_ in range(rows):
            for c_ in range(cols):
                cells.append((((c_ - (cols - 1) / 2.0) * cell),
                              H / 2.0 + ((rows - 1) / 2.0 - r_) * cell))
        for i, s in enumerate(rng.permutation(n)):
            slot[i] = int(s)

    for i in sorted(range(n), key=lambda k: -radii[k]):
        r = float(radii[i])
        if mode == "GRID" and slot.get(i, 10 ** 9) < len(cells):
            u, v = cells[slot[i]]
            if snap:
                u, v = snap_bolts(u, v, W, H, g)
            out[i] = (float(u), float(v), spin())
            placed_xy.append((float(u), float(v)))
            placed_r.append(r)
            continue

        ulo, uhi = -W / 2.0 + margin + r, W / 2.0 - margin - r
        vlo, vhi = margin + r, H - margin - r
        if uhi < ulo:
            ulo = uhi = 0.0
        if vhi < vlo:
            vlo = vhi = H / 2.0
        best, best_c = None, -1e18
        for _ in range(400):
            u = float(rng.uniform(ulo, uhi))
            v = float(rng.uniform(vlo, vhi))
            if snap:
                u, v = snap_bolts(u, v, W, H, g)
            c = clearance(u, v, r)
            if c >= 0.0:
                best, best_c = (u, v), c
                break
            if c > best_c:
                best, best_c = (u, v), c
        if best_c < 0.0:
            fails += 1
        out[i] = (best[0], best[1], spin())
        placed_xy.append(best)
        placed_r.append(r)
    return out, fails


# =============================================================================
# 3b. Scans, manufacturing checks, exports (pure Python/numpy - no Blender calls)
# =============================================================================
# Rotations that turn the scan's own axis (the face you climb on) into +Z.
SCAN_AXES = {
    "+Z": np.eye(3),
    "-Z": np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]]),
    "+Y": np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]]),
    "-Y": np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]),
    "+X": np.array([[0, 0, -1.0], [0, 1.0, 0], [1.0, 0, 0]]),
    "-X": np.array([[0, 0, 1.0], [0, 1.0, 0], [-1.0, 0, 0]]),
}


def scan_transform(V, axis, scale):
    """Orient and scale a scan to the hold frame.

    The chosen scan axis becomes +Z (out of the wall), the size is multiplied by
    `scale` (to millimetres), the footprint is centred on the origin (the bolt
    position) and the lowest point is put on Z = 0 (the wall).
    Returns (new vertices, (width, height, thickness)).
    """
    W = (np.asarray(V, dtype=float) @ SCAN_AXES[axis].T) * float(scale)
    lo, hi = W.min(axis=0), W.max(axis=0)
    W[:, 0] -= (lo[0] + hi[0]) / 2.0
    W[:, 1] -= (lo[1] + hi[1]) / 2.0
    W[:, 2] -= lo[2]
    dims = hi - lo
    return W, (float(dims[0]), float(dims[1]), float(dims[2]))


def guess_scan_scale(max_extent):
    """Guess the unit of a scan from its largest size: metres, centimetres or millimetres."""
    if max_extent <= 1.5:
        return 1000.0
    if max_extent <= 40.0:
        return 10.0
    return 1.0


def triangulate(faces):
    """Fan-triangulate polygon faces (tuples of vertex indices)."""
    out = []
    for f in faces:
        for k in range(1, len(f) - 1):
            out.append((f[0], f[k], f[k + 1]))
    return np.array(out, dtype=np.int64)


# ---- manufacturing checks ---------------------------------------------------
MFG_RULES = {
    "PRINT": dict(
        label="3D printing (FDM, back flat on the bed)",
        bolt_thickness=8.0,     # min material under the bolt head (mm)
        wall=6.0,               # min material between bolt hole and outer edge (mm)
        min_edge=1.5,           # smallest crimp edge that prints cleanly (mm)
        overhang_deg=45.0,      # steeper than this (from vertical) needs supports
        overhang_warn=0.03, overhang_fail=0.15,
    ),
    "CAST": dict(
        label="casting (one-piece mould, hold lifted off the front)",
        bolt_thickness=10.0, wall=8.0, min_edge=2.5,
        overhang_deg=3.0,       # any face leaning back more than this is an undercut
        overhang_warn=0.002, overhang_fail=0.02,
    ),
}
SHARP_DEG = 80.0           # fold between neighbouring faces counted as a sharp edge
FLAT_BACK_MIN = 0.85       # share of the footprint that must be a flat mounting face
PLA_DENSITY, PU_DENSITY = 1.24, 1.10   # g/cm3

POCKET_TYPES = ("POCKET", "POCKET2")

# How each check is measured on the mesh, and where the problem shows up when highlighted.
HOW = {
    "EMPTY": "Counted the faces of the mesh.",
    "NOT_WATERTIGHT": "Counted how many faces share every edge. In a closed solid each edge has exactly two. "
                      "Edges with one or three-plus faces are shown in pink.",
    "NO_FLAT_BACK": "Worked out the normal (the direction a face points) of every triangle. Faces pointing "
                    "straight at the wall (within 11 degrees) and lying on the wall plane count as the back. "
                    "Their area is compared with the hold's footprint seen from the front.",
    "NO_BOLT": "Read the bolt hole setting.",
    "BOLT_MISSING": "Looked for a ring of near-vertical faces at the bolt position.",
    "BOLT_THICKNESS": "Took the lowest height of the front surface in a ring just outside the hole: that is the "
                      "material under the bolt head. The bolt area is shown in purple.",
    "BOLT_WALL": "Measured from the bolt centre to the nearest outer edge of the flat back, minus the hole "
                 "radius. The bolt area is shown in purple.",
    "EDGE_TOO_SMALL": "Compared the Edge depth setting with the smallest feature this method can reproduce.",
    "OVERHANG": "Worked out the normal of every triangle and its lean = how far it tips back toward the wall "
                "(0 = facing out or straight up). Faces leaning more than the limit are counted by area and shown "
                "orange (a little over) or red (well over).",
    "UNDERCUT": "Worked out the normal of every triangle and its lean = how far it tips back toward the wall. "
                "Any face leaning back past the limit is an undercut that traps the mould. Shown orange "
                "(slight) or red (steep).",
    "BED_SIZE": "Compared the hold's bounding box with the print bed in both flat orientations.",
    "SHARP_EDGES": "Measured the angle between the normals of the two faces that share each edge. Edges folding "
                   "more than 80 degrees are added up by length and shown in cyan.",
    "WEIGHT": "Added up the signed volume of every triangle.",
}


def _weld(V, T):
    """Merge vertices at the same spot so scans with split vertices are judged fairly."""
    uniq, inv = np.unique(np.round(np.asarray(V, dtype=float), 3), axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    return uniq, inv[np.asarray(T, dtype=np.int64)]


def check_manufacturing(V, T, p, method, bed, bolt_dia, is_scan=False):
    """Judge whether a hold can be 3D printed or cast, and say why not.

    V, T      : vertices (mm, hold frame: back on Z = 0, bolt at the origin) and triangles
    p         : the hold's generator settings, or None for a scan
    method    : 'PRINT' or 'CAST'
    bed       : (x, y, z) printer build volume in mm
    bolt_dia  : bolt hole diameter in mm (0 = none)
    Returns (issues, metrics). Each issue is a dict with level (FAIL / WARN / INFO),
    code, title, why (the numbers and the rule), fix (what to change) and params
    (generator settings that would fix it, applied by "Apply Suggested Fixes").
    """
    rules = MFG_RULES[method]
    label = rules["label"]
    V, T = _weld(V, T)
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    issues = []

    def add(level, code, title, why, fix, params=None):
        issues.append(dict(level=level, code=code, title=title, why=why, fix=fix, params=params or {},
                           how=HOW.get(code, "")))

    if len(T) == 0:
        add("FAIL", "EMPTY", "Empty mesh", "The mesh has no faces, so there is nothing to make.",
            "Check that the object is a mesh with geometry.")
        return issues, {}

    P = V[T]
    if float(np.einsum("ij,ij->i", P[:, 0], np.cross(P[:, 1], P[:, 2])).sum()) < 0:
        T = T[:, ::-1]                    # inside-out mesh (common with scans): flip it
        P = V[T]
    cr = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(cr, axis=1)
    area = ln / 2.0
    n = cr / np.maximum(ln, 1e-12)[:, None]
    nz = n[:, 2]
    back = (nz < -0.98) & (P[:, :, 2].mean(axis=1) < 0.5)
    front_area = float(area[~back].sum())
    proj_area = float((area * np.maximum(nz, 0.0)).sum())
    back_area = float(area[back].sum())
    flat_frac = back_area / max(proj_area, 1e-9)
    extent = V.max(axis=0) - V.min(axis=0)
    volume = abs(float(np.einsum("ij,ij->i", P[:, 0], np.cross(P[:, 1], P[:, 2])).sum()) / 6.0)

    # --- edges: watertightness and sharp folds ---
    NV = len(V)
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    tri_id = np.tile(np.arange(len(T)), 3)
    lo, hi = e.min(axis=1), e.max(axis=1)
    key = lo.astype(np.int64) * NV + hi
    order = np.argsort(key, kind="stable")
    ks = key[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    counts = np.diff(np.r_[starts, len(ks)])
    open_edges = int((counts == 1).sum())
    multi_edges = int((counts > 2).sum())
    pair = starts[counts == 2]
    t1, t2 = tri_id[order[pair]], tri_id[order[pair + 1]]
    fold = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", n[t1], n[t2]), -1.0, 1.0)))
    ea, eb = lo[order[pair]], hi[order[pair]]
    elen = np.linalg.norm(V[ea] - V[eb], axis=1)
    em = 0.5 * (V[ea] + V[eb])
    off_hole = np.hypot(em[:, 0], em[:, 1]) > (bolt_dia / 2.0 + 1.5 if bolt_dia > 0 else 0.0)
    sharp_len = float(elen[(fold > SHARP_DEG) & ~(back[t1] | back[t2]) & off_hole].sum())

    # --- bolt: thickness under the head and wall beside the hole ---
    r_xy = np.hypot(V[:, 0], V[:, 1])
    hr = bolt_dia / 2.0
    r_min = None
    bt = T[back]                      # nearest outer edge of the flat back to the bolt centre
    if len(bt):
        be = np.concatenate([bt[:, [0, 1]], bt[:, [1, 2]], bt[:, [2, 0]]])
        bk = np.minimum(be[:, 0], be[:, 1]).astype(np.int64) * NV + np.maximum(be[:, 0], be[:, 1])
        uk, cnt = np.unique(bk, return_counts=True)
        bnd = uk[cnt == 1]
        A_, B_ = V[bnd // NV, :2], V[bnd % NV, :2]
        mid = 0.5 * (A_ + B_)
        keep = np.hypot(mid[:, 0], mid[:, 1]) > hr + 1.5          # skip the hole's own edge
        if np.any(keep):
            A_, B_ = A_[keep], B_[keep]
            D = B_ - A_
            tt = np.clip(-np.einsum("ij,ij->i", A_, D) / np.maximum(np.einsum("ij,ij->i", D, D), 1e-12), 0.0, 1.0)
            r_min = float(np.hypot(*(A_ + tt[:, None] * D).T).min())
    if r_min is None:                 # no flat back found: fall back to the vertex outline
        ang = np.arctan2(V[:, 1], V[:, 0])
        bins = ((ang + np.pi) / (2.0 * np.pi) * 72).astype(int) % 72
        outline = [float(r_xy[bins == b].max()) for b in range(72) if np.any(bins == b)]
        r_min = min(outline) if outline else 0.0
    front_v = np.unique(T[nz > 0.05].ravel())
    ring = (r_xy[front_v] >= hr - 0.6) & (r_xy[front_v] <= hr + 4.0)
    t_bolt = float(V[front_v][ring][:, 2].min()) if np.any(ring) else None

    cen = P.mean(axis=1)
    rc = np.hypot(cen[:, 0], cen[:, 1])
    has_hole = bool(bolt_dia > 0 and np.count_nonzero(
        (np.abs(nz) < 0.3) & (np.abs(rc - hr) < 0.3 * hr + 0.5) & (area > 0.0)) >= 8)

    over_deg0 = np.degrees(np.arcsin(np.clip(-nz, 0.0, 1.0)))
    over_area0 = float(area[(over_deg0 > rules["overhang_deg"]) & ~back].sum())
    metrics = dict(extent=extent, volume=volume, flat_frac=flat_frac, open_edges=open_edges,
                   sharp_len=sharp_len, r_min=r_min, t_bolt=t_bolt, area=front_area, has_hole=has_hole,
                   over_area=over_area0, over_frac=over_area0 / max(front_area, 1e-9),
                   max_lean=float(over_deg0[~back].max()) if np.any(~back) else 0.0,
                   bolt_dia=float(bolt_dia))
    w, h, d = float(extent[0]), float(extent[1]), float(extent[2])
    gen = p is not None
    ptype = p["type"] if gen else None

    # 1. Watertight
    if open_edges or multi_edges:
        add("FAIL", "NOT_WATERTIGHT", "Mesh is not a closed solid",
            f"{open_edges} edges have only one face and {multi_edges} edges have three or more. "
            "Slicers and mould software must know which side of the surface is solid; holes "
            "produce missing layers, failed slices or broken moulds.",
            ("Very lumpy shapes can fold the surface onto itself: lower Roughness from "
             f"{float(p['rough']):.2f} to about {max(0.0, float(p['rough']) * 0.5):.2f} and rebuild the hold."
             if gen else
             "In Edit Mode: select all, Mesh > Clean Up > Fill Holes, then Mesh > Merge by Distance. "
             "If that fails, add a Remesh modifier (Voxel, 1 mm) and apply it."),
            {"rough": round(max(0.0, float(p["rough"]) * 0.5), 2)} if gen else None)

    # 2. Flat mounting back
    if flat_frac < FLAT_BACK_MIN:
        add("FAIL", "NO_FLAT_BACK", "No flat mounting back",
            f"Only {flat_frac * 100:.0f}% of the hold's footprint is a flat face on the wall plane "
            f"(at least {FLAT_BACK_MIN * 100:.0f}% is needed). A curved or missing back makes the hold rock "
            "when mounted, and tightening the bolt then levers and cracks it.",
            "Cut the back flat at Z = 0: add a Boolean (Intersect) with a box whose underside is the flat "
            "back, or Bisect and fill. If the wrong side is flat, set 'Climbing face' in the Scans panel "
            "to the correct axis and import again.")

    # 3. Bolt hole present, thick enough, far enough from the edge
    if bolt_dia <= 0:
        add("FAIL", "NO_BOLT", "No bolt hole",
            "The hold has no mounting hole, so there is nothing to fasten it to the wall.",
            "Set the Bolt hole to about 12 mm and rebuild the hold." if gen else
            "Use 'Add Bolt Hole to Scan' in the Scans panel.",
            {"bolt": 12.0})
    elif not has_hole:
        add("FAIL", "BOLT_MISSING", "Bolt hole was not cut",
            f"The hold is set to have a {bolt_dia:.0f} mm bolt hole, but the mesh has no hole at the "
            "origin. The boolean that cuts it can fail on very dense or non-manifold meshes.",
            "Rebuild the hold (Update Selected), or cut the hole yourself with a Boolean modifier "
            "and a cylinder along Z at the origin.")
    else:
        if t_bolt is not None and t_bolt < rules["bolt_thickness"]:
            deficit = rules["bolt_thickness"] - t_bolt
            if gen and ptype in POCKET_TYPES:
                new_depth = max(5.0, float(p["pocket_depth"]) - deficit)
                fix = (f"Make the pocket shallower: reduce Pocket depth by {deficit:.1f} mm to about "
                       f"{new_depth:.0f} mm, or make the hold thicker by {deficit:.0f} mm.")
                params = {"pocket_depth": new_depth}
            elif gen:
                new_d = min(float(p["d"]) * rules["bolt_thickness"] / max(t_bolt, 1.0) * 1.1,
                            2.5 * float(p["d"]))
                fix = (f"Make the hold thicker: raise Thickness from {float(p['d']):.0f} mm to about "
                       f"{new_d:.0f} mm (the material at the bolt grows in proportion).")
                params = {"d": new_d}
            else:
                fix = f"Thicken the scan around the bolt by {deficit:.0f} mm, or choose a bolt position on a thicker part."
                params = {}
            add("FAIL", "BOLT_THICKNESS", "Too thin where the bolt goes",
                f"The material around the bolt hole is only {t_bolt:.1f} mm thick; {label} needs at least "
                f"{rules['bolt_thickness']:.0f} mm there. The bolt head and clamping force go through this "
                "section, and thinner material cracks or pulls through.", fix, params)
        wall = r_min - hr
        if wall < rules["wall"]:
            x = 2.0 * (r_min - rules["wall"])
            factor = (rules["wall"] + hr) / max(r_min, 1.0)
            if x >= 8.0:
                fix = f"Use a smaller bolt hole: {x:.0f} mm or less."
                params = {"bolt": float(int(x))}
            elif gen:
                fix = (f"Make the hold bigger: scale width and height by about {factor:.2f}x "
                       f"(to {w * factor:.0f} x {h * factor:.0f} mm).")
                params = {"w": w * factor, "h": h * factor}
            else:
                fix = f"Scale the scan up by about {factor:.2f}x, or use a smaller bolt hole."
                params = {}
            add("FAIL", "BOLT_WALL", "Bolt hole too close to the edge",
                f"The hole is {bolt_dia:.0f} mm wide, and the nearest outer edge of the hold is {r_min:.1f} mm "
                f"from the bolt centre. That leaves {wall:.1f} mm of material beside the hole; "
                f"{label} needs at least {rules['wall']:.0f} mm or the edge splits when the bolt is tightened.",
                fix, params)

    # 4. Smallest crimp edge
    if gen and ptype == "CRIMP" and float(p["crimp"]) < rules["min_edge"]:
        add("FAIL", "EDGE_TOO_SMALL", "Crimp edge too shallow to make",
            f"The crimp edge is {float(p['crimp']):.1f} mm deep. {label} cannot reliably reproduce "
            f"features smaller than {rules['min_edge']:.1f} mm: printed edges round off, and in casting "
            "a thin lip tears or traps air.",
            f"Set Edge depth to {rules['min_edge']:.1f} mm or more (try {rules['min_edge'] + 1.0:.1f} mm).",
            {"crimp": rules["min_edge"] + 1.0})

    # 5. Overhangs (printing) / undercuts (casting)
    over_deg = np.degrees(np.arcsin(np.clip(-nz, 0.0, 1.0)))
    over_area = float(area[(over_deg > rules["overhang_deg"]) & ~back].sum())
    frac = over_area / max(front_area, 1e-9)
    if frac > rules["overhang_warn"]:
        level = "FAIL" if frac > rules["overhang_fail"] else "WARN"
        if gen:
            new_rough = round(max(0.0, float(p["rough"]) * 0.5), 2)
            rough_fix = f"Lower Roughness from {float(p['rough']):.2f} to about {new_rough:.2f}."
            extra = " Pocket side walls also overhang: make the pocket shallower or narrower." \
                if ptype in POCKET_TYPES else ""
            params = {"rough": new_rough}
        else:
            rough_fix = "Smooth the scan (Smooth modifier or Sculpt > Smooth) to remove small overhanging lumps."
            extra, params = "", {}
        if method == "PRINT":
            add(level, "OVERHANG", "Needs support material",
                f"{frac * 100:.1f}% of the surface ({over_area:.0f} mm2) leans out more than "
                f"{rules['overhang_deg']:.0f} degrees from vertical. Printed back-down, those areas hang in "
                "mid-air and need supports, which leave rough scars on the grip surface or sag.",
                rough_fix + extra + " Or print with tree supports and sand the grip faces.", params)
        else:
            add(level, "UNDERCUT", "Undercuts will lock the mould",
                f"{frac * 100:.1f}% of the surface ({over_area:.0f} mm2) faces back toward the wall by more than "
                f"{rules['overhang_deg']:.0f} degrees (an undercut). A one-piece rigid mould cannot release "
                "the casting past these lumps, and a silicone mould would tear.",
                rough_fix + extra + " Or cast in a two-piece mould split along the widest outline.", params)

    # 6. Fits the printer
    if method == "PRINT":
        bx, by, bz = (float(b) for b in bed)
        fits = ((w <= bx and h <= by) or (w <= by and h <= bx)) and d <= bz
        if not fits:
            s1 = min(bx / w, by / h, bz / d)
            s2 = min(by / w, bx / h, bz / d)
            s = min(1.0, max(s1, s2) * 0.98)
            add("FAIL", "BED_SIZE", "Too big for the printer",
                f"The hold is {w:.0f} x {h:.0f} x {d:.0f} mm but the print bed is {bx:.0f} x {by:.0f} x "
                f"{bz:.0f} mm. It will not fit in the build volume in either orientation.",
                f"Scale the hold to about {s * 100:.0f}% ({w * s:.0f} x {h * s:.0f} x {d * s:.0f} mm), split it "
                "into two parts, or print on a larger machine / cast it.",
                {"w": w * s, "h": h * s, "d": d * s} if gen else {})

    # 7. Sharp edges
    if sharp_len > 20.0:
        add("WARN" if is_scan else "INFO", "SHARP_EDGES", "Crisp edges",
            f"{sharp_len:.0f} mm of edge turns more than {SHARP_DEG:.0f} degrees between neighbouring faces. "
            "Crisp edges scrape skin and chip in use; real holds round them to at least 1 mm.",
            "Add a Bevel modifier (width 1 mm, 2 segments) before exporting"
            + (", or soften the scan with a Smooth modifier." if is_scan else
               ". The generator does not round the ledge edges yet."))

    # 8. Weight
    cm3 = volume / 1000.0
    add("INFO", "WEIGHT", "Size and weight",
        f"Volume {cm3:.0f} cm3: about {cm3 * PLA_DENSITY:.0f} g if printed solid in PLA, about "
        f"{cm3 * PU_DENSITY:.0f} g cast in polyurethane resin. A 20% infill print weighs far less.",
        "No change needed.")

    rank = {"FAIL": 0, "WARN": 1, "INFO": 2}
    issues.sort(key=lambda i: rank[i["level"]])
    return issues, metrics


PROBLEM_CATS = {          # key: (label, rgb)
    "OVERHANG_HI": ("Steep overhang / undercut", (0.85, 0.05, 0.05)),
    "OVERHANG_LO": ("Slight overhang / undercut", (1.00, 0.50, 0.05)),
    "SHARP": ("Sharp edge", (0.05, 0.85, 0.95)),
    "OPEN": ("Hole or fold in the surface", (0.95, 0.15, 0.85)),
    "BOLT": ("Thin material around the bolt", (0.55, 0.25, 0.95)),
}
ANGLE_CATS = {
    "A_UP": ("Faces outward: no problem", (0.15, 0.70, 0.25)),
    "A_FINE": ("Leans back, within the limit", (0.90, 0.85, 0.15)),
    "A_LO": ("Over the limit", (1.00, 0.50, 0.05)),
    "A_HI": ("Far over the limit", (0.85, 0.05, 0.05)),
}
ALL_CATS = dict(PROBLEM_CATS, **ANGLE_CATS)


def problem_faces(V, T, method, bolt_dia, codes=(), mode="PROBLEMS"):
    """Say which triangles cause manufacturing trouble, from face normals and edge folds.

    mode 'PROBLEMS' marks only trouble spots; mode 'ANGLES' colours every front face by how far
    it leans back (the measurement behind overhangs and undercuts).
    Returns (V, T, labels): the (possibly flipped) triangles and one category key per triangle
    ('' = nothing to mark).
    """
    rules = MFG_RULES[method]
    V = np.asarray(V, dtype=np.float64)
    T = np.asarray(T, dtype=np.int64)
    if len(T) == 0:
        return V, T, np.zeros(0, dtype=object)
    P = V[T]
    if float(np.einsum("ij,ij->i", P[:, 0], np.cross(P[:, 1], P[:, 2])).sum()) < 0:
        T = T[:, ::-1]
        P = V[T]
    cr = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(cr, axis=1)
    n = cr / np.maximum(ln, 1e-12)[:, None]
    nz = n[:, 2]
    back = (nz < -0.98) & (P[:, :, 2].mean(axis=1) < 0.5)
    lean = np.degrees(np.arcsin(np.clip(-nz, 0.0, 1.0)))
    thr = rules["overhang_deg"]
    far = thr + (25.0 if method == "PRINT" else 10.0)
    labels = np.full(len(T), "", dtype=object)
    if mode == "ANGLES":
        front = ~back
        labels[front & (lean <= 0.0)] = "A_UP"
        labels[front & (lean > 0.0) & (lean <= thr)] = "A_FINE"
        labels[front & (lean > thr) & (lean <= far)] = "A_LO"
        labels[front & (lean > far)] = "A_HI"
        return V, T, labels

    labels[~back & (lean > thr)] = "OVERHANG_LO"
    labels[~back & (lean > far)] = "OVERHANG_HI"

    # sharp folds and open edges, from the edge-to-face map
    Vw, Tw = _weld(V, T)
    NV = len(Vw)
    e = np.concatenate([Tw[:, [0, 1]], Tw[:, [1, 2]], Tw[:, [2, 0]]])
    tri_id = np.tile(np.arange(len(T)), 3)
    lo, hi = e.min(axis=1), e.max(axis=1)
    key = lo.astype(np.int64) * NV + hi
    order = np.argsort(key, kind="stable")
    ks = key[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    counts = np.diff(np.r_[starts, len(ks)])
    pair = starts[counts == 2]
    t1, t2 = tri_id[order[pair]], tri_id[order[pair + 1]]
    fold = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", n[t1], n[t2]), -1.0, 1.0)))
    ea, eb = lo[order[pair]], hi[order[pair]]
    em = 0.5 * (Vw[ea] + Vw[eb])
    off_hole = np.hypot(em[:, 0], em[:, 1]) > (bolt_dia / 2.0 + 1.5 if bolt_dia > 0 else 0.0)
    bad = (fold > SHARP_DEG) & ~(back[t1] | back[t2]) & off_hole
    for t in (t1[bad], t2[bad]):
        labels[t] = np.where(labels[t] == "", "SHARP", labels[t])
    odd = np.isin(key, ks[starts][counts != 2])
    labels[np.unique(tri_id[odd])] = "OPEN"

    if any(c in ("BOLT_THICKNESS", "BOLT_WALL") for c in codes) and bolt_dia > 0:
        cen = P.mean(axis=1)
        zone = (np.hypot(cen[:, 0], cen[:, 1]) < bolt_dia / 2.0 + rules["wall"]) & ~back & (nz > 0.0)
        labels[zone] = "BOLT"
    return V, T, labels


def mfg_status(issues):
    levels = {i["level"] for i in issues}
    return "FAIL" if "FAIL" in levels else ("WARN" if "WARN" in levels else "OK")


FIX_LIMITS = dict(w=(15.0, 600.0), h=(15.0, 600.0), d=(5.0, 300.0), bolt=(0.0, 30.0),
                  rough=(0.0, 1.0), crimp=(1.0, 30.0), pocket_depth=(5.0, 60.0))


def merge_fixes(p, issues):
    """Combine the suggested generator changes from all issues into one settings dict.

    Where two fixes pull the same setting in different directions, strength wins:
    sizes and crimp edge take the largest suggestion, everything else the smallest.
    """
    proposals = {}
    for it in issues:
        for k, v in (it.get("params") or {}).items():
            proposals.setdefault(k, []).append(float(v))
    new = dict(p)
    for k, vals in proposals.items():
        v = max(vals) if k in ("crimp", "d", "w", "h") else min(vals)
        lo, hi = FIX_LIMITS.get(k, (-1e9, 1e9))
        new[k] = min(max(v, lo), hi)
    return new, proposals


# ---- mesh export --------------------------------------------------------------
def stl_bytes(V, T, name="hold"):
    """Binary STL (millimetres) for vertices V and triangles T."""
    tri = np.asarray(V, dtype=np.float64)[np.asarray(T, dtype=np.int64)]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)[:, None]
    rec = np.zeros(len(tri), dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"], rec["v"] = nrm, tri
    header = name.encode("ascii", "replace")[:80].ljust(80, b" ")
    return header + np.uint32(len(tri)).tobytes() + rec.tobytes()


def obj_text(V, T, name="hold"):
    lines = [f"# {name}", f"o {name}"]
    lines += [f"v {x:.5f} {y:.5f} {z:.5f}" for x, y, z in np.asarray(V, dtype=float)]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in np.asarray(T, dtype=int)]
    return "\n".join(lines) + "\n"


# ---- setter's sheet -----------------------------------------------------------
ROLE_ITEMS = [
    ("NONE", "None", "Untagged"),
    ("START", "Start", "Start hold"),
    ("HAND", "Hand", "Hand hold"),
    ("FOOT", "Foot", "Foot hold"),
    ("FINISH", "Finish", "Finish hold"),
]
ROLE_NAMES = {k: n for k, n, _ in ROLE_ITEMS}


def type_label(t):
    return dict((k, n) for k, n, _ in TYPE_ITEMS).get(t, "Scan" if t == "SCAN" else str(t).title())


def col_letters(i):
    s, i = "", i + 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def bolt_ref(u, v, W, H, g):
    """Nearest bolt hole as a grid reference (column letter from the left, row number
    from the bottom) and the hold's offset from that hole in mm."""
    us, vs = bolt_axes(W, H, g)
    if len(us) == 0 or len(vs) == 0:
        return "-", 0.0, 0.0
    i = min(max(int(round((u - us[0]) / g)), 0), len(us) - 1)
    j = min(max(int(round((v - vs[0]) / g)), 0), len(vs) - 1)
    return f"{col_letters(i)}{j + 1}", float(u - us[i]), float(v - vs[j])


def _hex(c):
    return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, x)) * 255)) for x in c[:3])


def build_sheet(wall, holds, routes, title="Setter's sheet"):
    """Printable HTML setter's sheet and a CSV, as plain data in, strings out.

    wall   : dict w, h, t, angle, panel_w, panel_h, grid
    holds  : dicts with name, type, w, h, d, bolt, u, v, spin, role, route, status, seed, scan
    routes : dicts with name, color (r, g, b)
    """
    import csv
    import html as _html
    import io
    from math import cos

    esc = _html.escape
    W, H, g, ang = float(wall["w"]), float(wall["h"]), float(wall["grid"]), float(wall["angle"])

    def route_of(h):
        r = int(h["route"])
        return routes[r - 1] if 1 <= r <= len(routes) else None

    def sort_key(h):
        r = int(h["route"])
        return (r if r > 0 else 999, -h["v"], h["u"])

    rows = []
    for n, h in enumerate(sorted(holds, key=sort_key), 1):
        ref, dx, dy = bolt_ref(h["u"], h["v"], W, H, g)
        r = route_of(h)
        rows.append(dict(
            num=n, route=r["name"] if r else "Unassigned", route_i=int(h["route"]),
            role=ROLE_NAMES.get(h["role"], "-"), name=h["name"], type=type_label(h["type"]),
            w=h["w"], h=h["h"], d=h["d"], bolt=h["bolt"], ref=ref, dx=dx, dy=dy,
            across=h["u"] + W / 2.0, up=h["v"], height=h["v"] * cos(np.radians(ang)),
            spin=h["spin"], status=h.get("status") or "-",
            color=_hex(r["color"]) if r else _hex(TYPE_COLORS.get(h["type"], TYPE_COLORS["SCAN"])),
            raw_role=h["role"],
        ))

    # ---- CSV
    out = io.StringIO()
    cw = csv.writer(out)
    cw.writerow(["number", "route", "role", "hold", "type", "width_mm", "height_mm", "thickness_mm",
                 "bolt_hole_mm", "bolt_grid_ref", "offset_from_hole_x_mm", "offset_from_hole_y_mm",
                 "across_from_left_mm", "up_wall_mm", "height_above_floor_mm", "spin_deg", "manufacturing"])
    for r in rows:
        cw.writerow([r["num"], r["route"], r["role"], r["name"], r["type"], round(r["w"]), round(r["h"]),
                     round(r["d"]), round(r["bolt"], 1), r["ref"], round(r["dx"], 1), round(r["dy"], 1),
                     round(r["across"]), round(r["up"]), round(r["height"]), round(r["spin"]), r["status"]])
    csv_text = out.getvalue()

    # ---- parts list
    parts = {}
    for h in holds:
        k = (h["scan"] or type_label(h["type"]), round(h["w"]), round(h["h"]), round(h["d"]))
        parts[k] = parts.get(k, 0) + 1

    # ---- wall map (SVG, 1 unit = 1 mm, y flipped so up is up)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-W / 2 - 150:.0f} -150 {W + 300:.0f} {H + 300:.0f}" '
           f'style="width:100%;max-width:760px;height:auto;border:1px solid #bbb;background:#fff">',
           f'<rect x="{-W / 2:.0f}" y="0" width="{W:.0f}" height="{H:.0f}" fill="#efe3cf" stroke="#555" stroke-width="6"/>']
    us, vs = bolt_axes(W, H, g)
    for u in us:
        for v in vs:
            svg.append(f'<circle cx="{u:.0f}" cy="{H - v:.0f}" r="5" fill="#9a8f80"/>')
    fs = max(30, int(min(W, H) / 60))
    for i, u in enumerate(us):
        svg.append(f'<text x="{u:.0f}" y="-40" font-size="{fs}" text-anchor="middle" fill="#444">{col_letters(i)}</text>')
    for j, v in enumerate(vs):
        svg.append(f'<text x="{-W / 2 - 35:.0f}" y="{H - v + fs / 3:.0f}" font-size="{fs}" text-anchor="end" fill="#444">{j + 1}</text>')
    for r, h in zip(rows, sorted(holds, key=sort_key)):
        cx, cy = h["u"], H - h["v"]
        svg.append(f'<g transform="rotate({-h["spin"]:.0f} {cx:.0f} {cy:.0f})">'
                   f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{max(h["w"], 20) / 2:.0f}" ry="{max(h["h"], 20) / 2:.0f}" '
                   f'fill="{r["color"]}" fill-opacity="0.8" stroke="#222" stroke-width="3"/></g>')
        tag = {"START": "S", "FINISH": "F", "FOOT": "ft"}.get(h["role"], "")
        svg.append(f'<text x="{cx:.0f}" y="{cy + fs / 3:.0f}" font-size="{fs}" text-anchor="middle" font-weight="bold" '
                   f'style="paint-order:stroke;stroke:#fff;stroke-width:7">{r["num"]}{(" " + tag) if tag else ""}</text>')
    svg.append("</svg>")

    # ---- HTML
    def table(items):
        head = ("<tr><th>#</th><th>Role</th><th>Hold</th><th>Size W x H x T (mm)</th><th>Bolt hole</th>"
                "<th>Grid ref</th><th>Across from left</th><th>Up wall</th><th>Height above floor</th>"
                "<th>Spin</th><th>Status</th></tr>")
        body = ""
        for r in items:
            off = "on hole" if abs(r["dx"]) < 1 and abs(r["dy"]) < 1 else f'off by {r["dx"]:+.0f}, {r["dy"]:+.0f}'
            body += (f'<tr><td>{r["num"]}</td><td>{esc(r["role"])}</td><td>{esc(r["name"])} ({esc(r["type"])})</td>'
                     f'<td>{r["w"]:.0f} x {r["h"]:.0f} x {r["d"]:.0f}</td><td>{r["bolt"]:.0f} mm</td>'
                     f'<td><b>{esc(r["ref"])}</b> <small>{off}</small></td><td>{r["across"]:.0f}</td>'
                     f'<td>{r["up"]:.0f}</td><td>{r["height"]:.0f}</td><td>{r["spin"]:.0f} deg</td>'
                     f'<td>{esc(r["status"])}</td></tr>')
        return f"<table>{head}{body}</table>"

    sections = []
    for ri, route in enumerate(routes, 1):
        items = [r for r in rows if r["route_i"] == ri]
        if not items:
            continue
        starts = sum(1 for r in items if r["raw_role"] == "START")
        finishes = sum(1 for r in items if r["raw_role"] == "FINISH")
        warn = []
        if starts == 0:
            warn.append("no start hold")
        elif starts > 2:
            warn.append(f"{starts} start holds (usually 1 or 2)")
        if finishes == 0:
            warn.append("no finish hold")
        note = f' <span class="warn">Check: {", ".join(warn)}.</span>' if warn else ""
        sections.append(f'<h2><span class="dot" style="background:{_hex(route["color"])}"></span>'
                        f'{esc(route["name"])} <small>({len(items)} holds)</small>{note}</h2>{table(items)}')
    loose = [r for r in rows if r["route_i"] == 0 or r["route_i"] > len(routes)]
    if loose:
        sections.append(f"<h2>Unassigned holds <small>({len(loose)})</small></h2>{table(loose)}")

    parts_rows = "".join(f"<tr><td>{esc(str(k[0]))}</td><td>{k[1]} x {k[2]} x {k[3]}</td><td>{c}</td></tr>"
                         for k, c in sorted(parts.items(), key=lambda kv: (str(kv[0][0]), -kv[1])))
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}}
h1{{margin:0 0 4px}} h2{{margin-top:28px;font-size:18px}} small{{color:#666;font-weight:normal}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin-top:6px}}
th,td{{border:1px solid #ccc;padding:4px 6px;text-align:left}} th{{background:#f0f0f0}}
.dot{{display:inline-block;width:14px;height:14px;border-radius:7px;margin-right:8px;border:1px solid #333}}
.warn{{color:#b00020;font-size:13px}} .meta td{{border:none;padding:2px 14px 2px 0}}
@media print{{h2{{page-break-after:avoid}} table{{page-break-inside:auto}} tr{{page-break-inside:avoid}}}}
</style></head><body>
<h1>{esc(title)}</h1>
<table class="meta"><tr><td><b>Wall</b></td><td>{W:.0f} x {H:.0f} mm, {ang:.0f} deg overhang, {wall["t"]:.0f} mm panels</td></tr>
<tr><td><b>Panels</b></td><td>{wall["panel_w"]:.0f} x {wall["panel_h"]:.0f} mm</td></tr>
<tr><td><b>Bolt grid</b></td><td>{g:.0f} mm; columns A, B, C... from the left, rows 1, 2, 3... from the bottom</td></tr>
<tr><td><b>Holds</b></td><td>{len(rows)} in {len([r for r in routes])} routes</td></tr></table>
<h2>Wall map</h2>{"".join(svg)}
<h2>Parts list</h2><table><tr><th>Hold</th><th>Size (mm)</th><th>Quantity</th></tr>{parts_rows}</table>
{"".join(sections)}
<p><small>Positions are measured along the wall surface. Height above floor = up wall x cos(overhang angle).
Spin is counter-clockwise looking at the wall. S = start, F = finish, ft = foot only.</small></p>
</body></html>"""
    return page, csv_text


# =============================================================================
# 4. Blender helpers (materials, objects, collections)
# =============================================================================
def setup_units(scene):
    us = scene.unit_settings
    us.system = "METRIC"
    us.scale_length = 0.001
    us.length_unit = "MILLIMETERS"


def get_material(name, rgb, roughness=0.8):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        try:
            mat.use_nodes = True       # always on in newer Blender; harmless there
        except Exception:
            pass
        if mat.node_tree is not None:
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
                bsdf.inputs["Roughness"].default_value = roughness
        mat.diffuse_color = (*rgb, 1.0)
    return mat


def get_rock_material():
    """Rock material whose colour comes from each object's own colour."""
    mat = bpy.data.materials.get("RW_Hold")
    if mat is not None:
        return mat
    mat = get_material("RW_Hold", NATURAL_COLOR, 0.85)
    try:
        nt = mat.node_tree
        bsdf = nt.nodes.get("Principled BSDF")
        info = nt.nodes.new("ShaderNodeObjectInfo")
        nt.links.new(info.outputs["Color"], bsdf.inputs["Base Color"])
    except Exception as exc:
        print("Rock Wall Builder: object colour node not set up:", exc)
    return mat


def get_collection(context, name):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if name not in context.scene.collection.children:
        context.scene.collection.children.link(col)
    return col


def _fill_mesh(mesh, verts, faces, mats=None, smooth=True):
    mesh.from_pydata([tuple(map(float, v)) for v in verts], [], [tuple(int(i) for i in f) for f in faces])
    mesh.update()
    if mats is not None:
        mesh.polygons.foreach_set("material_index", [int(m) for m in mats])
    if smooth:
        try:
            mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        except Exception:
            try:
                for poly in mesh.polygons:
                    poly.use_smooth = True
            except Exception:
                pass


def prepare_viewports(context):
    """Material Preview shading and a clip range that suits a wall measured in mm."""
    try:
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type != "VIEW_3D":
                        continue
                    space.clip_start = 10.0
                    space.clip_end = 200000.0
                    if space.shading.type not in {"MATERIAL", "RENDERED"}:
                        space.shading.type = "MATERIAL"
    except Exception as exc:
        print("Rock Wall Builder: could not prepare viewport:", exc)


def frame_objects(context, objs):
    """Select the objects and zoom every 3D view to them."""
    try:
        for o in context.view_layer.objects:
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        context.view_layer.objects.active = objs[0]
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is None:
                    continue
                with context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_selected()
    except Exception as exc:
        print("Rock Wall Builder: could not frame view:", exc)


# ---- wall -------------------------------------------------------------------
def find_wall(scene):
    for o in scene.objects:
        if o.get("rh_wall"):
            return o
    return None


def build_wall(context, pr, frame=False):
    """Create the wall, or rebuild it in place (holds on it stay attached)."""
    scene = context.scene
    setup_units(scene)
    W, H, T = float(pr.wall_width), float(pr.wall_height), float(pr.wall_thickness)
    g = float(pr.bolt_spacing)
    verts, faces, mats, _ = wall_geometry(W, H, T, pr.panel_width, pr.panel_height, g)

    wall = find_wall(scene)
    if wall is None:
        mesh = bpy.data.meshes.new("ClimbWall")
        _fill_mesh(mesh, verts, faces, mats, smooth=False)
        wall = bpy.data.objects.new("ClimbWall", mesh)
        get_collection(context, "RockWall").objects.link(wall)
        wall["rh_wall"] = True
    else:
        mesh = wall.data
        mesh.clear_geometry()
        _fill_mesh(mesh, verts, faces, mats, smooth=False)

    if len(mesh.materials) == 0:
        mesh.materials.append(get_material("RW_PanelA", (0.74, 0.58, 0.40), 0.9))
        mesh.materials.append(get_material("RW_PanelB", (0.66, 0.50, 0.34), 0.9))
        mesh.materials.append(get_material("RW_BoltHole", (0.04, 0.04, 0.04), 0.9))

    wall["rh_w"], wall["rh_h"], wall["rh_g"] = W, H, g
    wall["rh_angle"] = float(pr.wall_angle)
    wall["rh_t"], wall["rh_pw"], wall["rh_ph"] = float(T), float(pr.panel_width), float(pr.panel_height)
    # Stand the wall up (+90 deg about X); the overhang angle tilts the top outward.
    wall.rotation_euler = (radians(90.0 + pr.wall_angle), wall.rotation_euler.y, wall.rotation_euler.z)

    prepare_viewports(context)
    if frame:
        frame_objects(context, [wall])
    return wall


# ---- holds ------------------------------------------------------------------
def hold_name(p):
    k = p["type"]
    base = ("Gym" + k[2:].title()) if k.startswith("G_") else k.title()
    return f"{base}_s{int(p['seed'])}"


def params_from_props(pr):
    return dict(
        type=pr.hold_type, w=float(pr.width), h=float(pr.height), d=float(pr.depth),
        rough=float(pr.roughness), seed=int(pr.seed), crimp=float(pr.crimp_edge),
        pocket_dia=float(pr.pocket_diameter), pocket_depth=float(pr.pocket_depth),
        bolt=float(pr.bolt_hole), color=pr.color_mode,
    )


def _tag_hold(obj, p, info):
    obj["rh_hold"] = True
    obj["rh_params"] = json.dumps(p)
    obj["rh_radius"] = float(info["radius"])
    try:
        clear_overlays(None, obj)
    except Exception:
        pass
    for stale in ("rh_mfg", "rh_mfg_issues", "rh_mfg_method", "rh_mfg_metrics", "rh_mfg_map"):     # the shape changed: old check no longer applies
        if stale in obj:
            del obj[stale]
    recolor(obj)


def cut_bolt_hole(context, obj, diameter, depth):
    """Boolean a through-hole at the hold's origin (its bolt position). Returns True on success."""
    cutter = cm = mod = None
    try:
        verts, faces = cylinder_geometry(diameter / 2.0, -20.0, depth + 20.0)
        cm = bpy.data.meshes.new("rw_cutter")
        _fill_mesh(cm, verts, faces, smooth=False)
        cutter = bpy.data.objects.new("rw_cutter", cm)
        (obj.users_collection[0] if obj.users_collection else context.scene.collection).objects.link(cutter)
        cutter.parent = obj

        mod = obj.modifiers.new("BoltHole", "BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        mod.solver = "EXACT"
        context.view_layer.update()

        evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
        new_mesh = bpy.data.meshes.new_from_object(evaluated)
        if len(new_mesh.polygons) == 0:
            raise RuntimeError("boolean returned an empty mesh")
        old = obj.data
        name = old.name
        obj.modifiers.remove(mod)
        mod = None
        obj.data = new_mesh
        bpy.data.meshes.remove(old)
        new_mesh.name = name
        return True
    except Exception as exc:
        print("Rock Wall Builder: bolt hole failed:", exc)
        try:
            if mod is not None and mod.name in obj.modifiers:
                obj.modifiers.remove(mod)
        except Exception:
            pass
        return False
    finally:
        if cutter is not None:
            bpy.data.objects.remove(cutter, do_unlink=True)
        if cm is not None and cm.users == 0:
            bpy.data.meshes.remove(cm)


def create_hold(context, p, collection):
    verts, faces, info = hold_geometry(p)
    mesh = bpy.data.meshes.new(hold_name(p))
    _fill_mesh(mesh, verts, faces)
    obj = bpy.data.objects.new(hold_name(p), mesh)
    collection.objects.link(obj)
    mesh.materials.append(get_rock_material())
    _tag_hold(obj, p, info)
    if p["bolt"] > 0:
        cut_bolt_hole(context, obj, p["bolt"], info["maxz"])
    return obj


def rebuild_hold(context, obj, p):
    """Regenerate a hold's shape in place (keeps its place on the wall)."""
    verts, faces, info = hold_geometry(p)
    mesh = obj.data
    if mesh.users > 1:
        mesh = mesh.copy()
        obj.data = mesh
    mesh.clear_geometry()
    _fill_mesh(mesh, verts, faces)
    if len(mesh.materials) == 0:
        mesh.materials.append(get_rock_material())
    _tag_hold(obj, p, info)
    obj.name = hold_name(p)
    if p["bolt"] > 0:
        cut_bolt_hole(context, obj, p["bolt"], info["maxz"])


def hold_radius(obj):
    return float(obj.get("rh_radius", 50.0))


def attach_to_wall(obj, wall, u, v, spin_deg):
    obj.parent = wall
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.location = (float(u), float(v), 0.0)
    obj.rotation_euler = (0.0, 0.0, radians(spin_deg))
    obj.scale = (1.0, 1.0, 1.0)


def all_holds(scene):
    return [o for o in scene.objects if o.get("rh_hold")]


def holds_on_wall(scene, wall):
    return [o for o in all_holds(scene) if o.parent == wall]


def arrange_holds(context, holds, wall, pr, layout, seed):
    """Place `holds` on the wall around whatever is already there. Returns how many did not fit."""
    W, H, g = float(wall["rh_w"]), float(wall["rh_h"]), float(wall["rh_g"])
    moving = {o.name for o in holds}
    existing = [(o.location.x, o.location.y, hold_radius(o))
                for o in holds_on_wall(context.scene, wall) if o.name not in moving]
    plan, fails = plan_layout(
        [hold_radius(o) for o in holds], W, H, g, existing, layout, seed,
        float(pr.min_gap), float(pr.edge_margin), bool(pr.snap_bolt), False,
    )
    mode = pr.spin_mode
    step = SPIN_STEP
    rng = np.random.default_rng(int(seed) + 4242)
    full = np.arange(-180.0, 180.0, step)
    near = full[np.abs(full) <= 90.0]
    for o, (u, v, _s) in zip(holds, plan):
        if mode == "FIXED":
            s = float(pr.spin_angle)
        elif mode == "RANDOM":
            s = float(near[int(rng.integers(0, len(near)))])
        elif mode == "ANY":
            s = float(full[int(rng.integers(0, len(full)))])
        elif mode == "KEEP":
            s = degrees(o.rotation_euler.z) if o.parent == wall else 0.0
        else:
            s = 0.0
        attach_to_wall(o, wall, u, v, s)
    return fails


# =============================================================================
# 4b. Scans, tags, layouts, exports and manufacturing (Blender side)
# =============================================================================
ROLE_COLORS = {"NONE": (0.55, 0.55, 0.55), "START": (0.20, 0.75, 0.30), "HAND": (0.25, 0.50, 0.90),
               "FOOT": (0.95, 0.75, 0.15), "FINISH": (0.90, 0.25, 0.25)}
CHECK_COLORS = {"OK": (0.20, 0.70, 0.30), "WARN": (0.95, 0.75, 0.10), "FAIL": (0.85, 0.15, 0.15)}
UNSET_COLOR = (0.55, 0.55, 0.55)
ROUTE_DEFAULTS = [(0.90, 0.25, 0.25), (0.25, 0.55, 0.95), (0.30, 0.75, 0.35), (0.95, 0.75, 0.15),
                  (0.70, 0.40, 0.85), (0.95, 0.55, 0.20), (0.25, 0.75, 0.75), (0.90, 0.45, 0.70)]
LAYOUT_FORMAT = "rock_wall_layout"
LAYOUT_VERSION = 1
REPORT_NAME = "Manufacturing Report"
_SUPPRESS = False            # True while a layout loads, so wall sliders do not rebuild the wall


def hold_params(obj):
    """The generator settings stored on a hold (empty dict if unreadable)."""
    try:
        return json.loads(obj["rh_params"])
    except Exception:
        return {}


# ---- colours ------------------------------------------------------------------
def object_color(obj, scene):
    """Viewport colour of a hold, from the chosen colour mode."""
    pr = scene.rock_wall
    mode = pr.color_mode
    if mode == "ROUTE":
        i = int(obj.rh_route)
        rgb = tuple(pr.routes[i - 1].color) if 1 <= i <= len(pr.routes) else UNSET_COLOR
    elif mode == "ROLE":
        rgb = ROLE_COLORS.get(obj.rh_role, UNSET_COLOR)
    elif mode == "CHECK":
        rgb = CHECK_COLORS.get(str(obj.get("rh_mfg", "")), UNSET_COLOR)
    else:
        p = hold_params(obj) or {"type": "JUG", "seed": 0}
        p["color"] = mode
        return hold_color(p)
    return tuple(rgb[:3]) + (1.0,)


def recolor(obj, scene=None):
    try:
        obj.color = object_color(obj, scene or bpy.context.scene)
    except Exception as exc:
        print("Rock Wall Builder: could not colour", getattr(obj, "name", "?"), exc)


def recolor_all(scene):
    for o in all_holds(scene):
        recolor(o, scene)


# ---- scan library ---------------------------------------------------------------
def safe_key(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "scan"


def scan_templates():
    return [m for m in bpy.data.meshes if m.get("rh_scan_template")]


def get_scan_template(key):
    for m in scan_templates():
        if m.get("rh_scan") == key:
            return m
    return None


_scan_items_cache = []


def scan_enum_items(self, context):
    global _scan_items_cache
    items = [(m["rh_scan"], m["rh_scan"],
              "{:.0f} x {:.0f} x {:.0f} mm".format(*[float(x) for x in m["rh_dims"]]))
             for m in scan_templates()]
    _scan_items_cache = items or [("NONE", "(no scans yet)", "Import a scan first")]
    return _scan_items_cache


def read_scan_file(context, path):
    """Import a scan file with Blender's own importer; return raw vertices and triangles
    (in the file's own units and axes) and remove the temporary objects again."""
    ext = os.path.splitext(path)[1].lower()
    tries = {".stl": [("wm", "stl_import"), ("import_mesh", "stl")],
             ".obj": [("wm", "obj_import"), ("import_scene", "obj")],
             ".ply": [("wm", "ply_import"), ("import_mesh", "ply")]}
    if ext not in tries:
        raise ValueError("Use an STL, OBJ or PLY file")
    before = set(bpy.data.objects.keys())
    done, last = False, None
    for group, name in tries[ext]:
        op = getattr(getattr(bpy.ops, group, None), name, None)
        if op is None:
            continue
        try:
            if name == "obj_import":          # OBJ files default to Y-up: keep the file's axes as they are
                try:
                    res = op(filepath=path, forward_axis="Y", up_axis="Z")
                except TypeError:
                    res = op(filepath=path)
            elif (group, name) == ("import_scene", "obj"):
                res = op(filepath=path, axis_forward="Y", axis_up="Z")
            else:
                res = op(filepath=path)
            if "FINISHED" in res:
                done = True
                break
        except Exception as exc:
            last = exc
    if not done:
        raise RuntimeError(f"Blender could not import {os.path.basename(path)}: {last}")
    new = [bpy.data.objects[n] for n in bpy.data.objects.keys()
           if n not in before and bpy.data.objects[n].type == "MESH"]
    if not new:
        raise RuntimeError("The file contained no mesh")
    Vs, Ts, off = [], [], 0
    try:
        for o in new:
            V, T = mesh_arrays(o.data)
            mw = np.array(o.matrix_world)
            Vs.append(V @ mw[:3, :3].T + mw[:3, 3])
            Ts.append(T + off)
            off += len(V)
    finally:
        for o in list(bpy.data.objects):
            if o.name in before:
                continue
            me = o.data if o.type == "MESH" else None
            bpy.data.objects.remove(o, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
    return np.vstack(Vs), np.vstack(Ts)


def mesh_arrays(mesh):
    """Vertices (N,3) and triangles (M,3) of a mesh as numpy arrays."""
    try:
        mesh.calc_loop_triangles()
    except Exception:
        pass
    n = len(mesh.vertices)
    co = np.empty(n * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    m = len(mesh.loop_triangles)
    tr = np.empty(m * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tr)
    return co.reshape(-1, 3).astype(np.float64), tr.reshape(-1, 3).astype(np.int64)


def _scratch_object(context, name, mesh):
    obj = bpy.data.objects.new(name, mesh)
    context.scene.collection.objects.link(obj)
    return obj


def _drop_object(obj):
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)


def decimate_mesh(context, mesh, target_tris):
    """Reduce a heavy scan to roughly `target_tris` triangles. Returns the (new) mesh."""
    n = len(mesh.polygons)
    if target_tris <= 0 or n <= target_tris:
        return mesh
    tmp = _scratch_object(context, "rw_decimate", mesh)
    try:
        mod = tmp.modifiers.new("Decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = max(0.01, min(1.0, target_tris / float(n)))
        context.view_layer.update()
        new_mesh = bpy.data.meshes.new_from_object(tmp.evaluated_get(context.evaluated_depsgraph_get()))
        if len(new_mesh.polygons) == 0:
            raise RuntimeError("decimate returned an empty mesh")
    finally:
        _drop_object(tmp)
    bpy.data.meshes.remove(mesh)
    return new_mesh


def make_scan_template(context, path, key, axis, unit, max_tris, bolt):
    """Import a scan file, orient and scale it to the hold frame and store it in the library.
    Returns the template mesh."""
    V, T = read_scan_file(context, path)
    raw_size = float(np.ptp(V, axis=0).max())
    if isinstance(unit, (int, float)):
        scale = float(unit)
    else:
        scale = {"MM": 1.0, "CM": 10.0, "M": 1000.0}.get(unit) or guess_scan_scale(raw_size)
    W, dims = scan_transform(V, axis, scale)
    if min(dims) < 1.0:
        raise RuntimeError("The scan is under 1 mm in some direction: check the unit setting")

    mesh = bpy.data.meshes.new("scan_" + key)
    mesh.from_pydata(W.tolist(), [], T.tolist())
    mesh.update()
    mesh = decimate_mesh(context, mesh, int(max_tris))
    mesh.name = "scan_" + key
    try:
        mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    except Exception:
        pass
    if len(mesh.materials) == 0:
        mesh.materials.append(get_rock_material())

    bolt_done = 0.0
    if bolt > 0:
        tmp = _scratch_object(context, "rw_scan_tmp", mesh)
        try:
            ok = cut_bolt_hole(context, tmp, bolt, dims[2])
            mesh = tmp.data              # the new mesh if the cut worked, the original if not
            bolt_done = float(bolt) if ok else 0.0
        finally:
            _drop_object(tmp)
        mesh.name = "scan_" + key

    V2, _ = mesh_arrays(mesh)
    mesh["rh_scan_template"] = True
    mesh["rh_scan"] = key
    mesh["rh_dims"] = [float(x) for x in dims]
    mesh["rh_radius"] = float(np.hypot(V2[:, 0], V2[:, 1]).max())
    mesh["rh_bolt"] = bolt_done
    mesh["rh_file"] = path
    mesh["rh_axis"] = axis
    mesh["rh_scale"] = float(scale)
    mesh["rh_tris"] = int(max_tris)
    mesh.use_fake_user = True
    return mesh


def load_scan_template(context, key, info, base=""):
    """Make a library scan from a layout file's scan entry. A relative file path is relative to the layout
    file's folder (`base`). A 'drilled' scan already has its bolt hole (diameter in mm) at 'bolt_at' (mm from
    its footprint centre): the hold's origin moves onto that hole instead of a new hole being cut."""
    drilled = float(info.get("drilled", 0.0))
    path = str(info["file"])
    if base and not os.path.isabs(path):
        path = os.path.join(base, path)
    mesh = make_scan_template(context, path, key, info.get("axis", "+Z"), float(info.get("scale", 1.0)),
                              int(info.get("max_tris", 0)), 0.0 if drilled else float(info.get("bolt", 0.0)))
    if drilled:
        dx, dy = [float(c) for c in info.get("bolt_at", (0.0, 0.0))]
        mesh.transform(Matrix.Translation((-dx, -dy, 0.0)))
        V, _ = mesh_arrays(mesh)
        mesh["rh_radius"] = float(np.hypot(V[:, 0], V[:, 1]).max())
        mesh["rh_bolt"] = mesh["rh_drilled"] = drilled
        mesh["rh_bolt_at"] = [dx, dy]
    return mesh


def scan_params(mesh):
    d = mesh["rh_dims"]
    return dict(type="SCAN", scan=mesh["rh_scan"], w=float(d[0]), h=float(d[1]), d=float(d[2]),
                rough=0.0, seed=0, crimp=0.0, pocket_dia=0.0, pocket_depth=0.0,
                bolt=float(mesh.get("rh_bolt", 0.0)), color="TYPE")


def create_scan_hold(context, mesh, collection):
    """A new hold that shares the template's mesh (copies cost almost no memory)."""
    obj = bpy.data.objects.new(mesh["rh_scan"], mesh)
    collection.objects.link(obj)
    _tag_hold(obj, scan_params(mesh), {"radius": float(mesh["rh_radius"])})
    return obj


def replace_template(old, new):
    """Swap a template mesh for a new one in every object that uses it."""
    for o in bpy.data.objects:
        if o.data == old:
            o.data = new
    for k in ("rh_scan_template", "rh_scan", "rh_dims", "rh_radius", "rh_file", "rh_axis", "rh_scale", "rh_tris",
              "rh_drilled", "rh_bolt_at"):
        if k in old:
            v = old[k]
            new[k] = v.to_list() if hasattr(v, "to_list") else v
    new["rh_bolt"] = float(old.get("rh_bolt", 0.0))
    name = old.name
    new.use_fake_user = True
    bpy.data.meshes.remove(old)
    new.name = name


# ---- layouts (save / load) -------------------------------------------------------
def layout_to_dict(context):
    scene = context.scene
    pr = scene.rock_wall
    wall = find_wall(scene)
    if wall is not None:
        wd = dict(width=wall["rh_w"], height=wall["rh_h"], angle=wall["rh_angle"], bolt_spacing=wall["rh_g"],
                  thickness=wall.get("rh_t", pr.wall_thickness), panel_width=wall.get("rh_pw", pr.panel_width),
                  panel_height=wall.get("rh_ph", pr.panel_height))
    else:
        wd = dict(width=pr.wall_width, height=pr.wall_height, angle=pr.wall_angle, bolt_spacing=pr.bolt_spacing,
                  thickness=pr.wall_thickness, panel_width=pr.panel_width, panel_height=pr.panel_height)
    holds, scans = [], {}
    for o in all_holds(scene):
        p = hold_params(o)
        p.pop("color", None)
        rec = dict(params=p, role=o.rh_role, route=int(o.rh_route))
        if o.get("rh_holdgen"):
            rec["holdgen"] = json.loads(o["rh_holdgen"])
        if wall is not None and o.parent == wall:
            rec.update(on_wall=True, u=o.location.x, v=o.location.y, spin=degrees(o.rotation_euler.z))
        else:
            rec.update(on_wall=False, x=o.location.x, y=o.location.y, z=o.location.z)
        holds.append(rec)
        if p.get("type") == "SCAN" and p.get("scan") not in scans:
            m = get_scan_template(p["scan"])
            if m is not None:
                scans[p["scan"]] = dict(file=m.get("rh_file", ""), axis=m.get("rh_axis", "+Z"),
                                        scale=float(m.get("rh_scale", 1.0)), max_tris=int(m.get("rh_tris", 0)),
                                        bolt=float(m.get("rh_bolt", 0.0)))
                if m.get("rh_drilled"):
                    scans[p["scan"]].update(drilled=float(m["rh_drilled"]),
                                            bolt_at=[float(c) for c in m.get("rh_bolt_at", (0.0, 0.0))])
    routes = [dict(name=r.name, color=[float(c) for c in r.color], **({"holdgen": json.loads(r.holdgen)}
                                                                       if r.holdgen else {})) for r in pr.routes]
    return dict(format=LAYOUT_FORMAT, version=LAYOUT_VERSION, wall=wd, routes=routes, scans=scans, holds=holds)


def remove_all_holds(scene):
    n = 0
    for o in all_holds(scene):
        mesh = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        n += 1
    return n


def apply_layout(context, data, clear=True, base=""):
    """Rebuild the wall, routes and holds from a layout dict. Returns (holds made, list of problems)."""
    global _SUPPRESS
    if not isinstance(data, dict) or data.get("format") != LAYOUT_FORMAT:
        raise ValueError("This is not a Rock Wall layout file")
    if int(data.get("version", 0)) > LAYOUT_VERSION:
        raise ValueError("This layout was saved by a newer version of the add-on")
    scene = context.scene
    pr = scene.rock_wall
    problems = []

    wd = data.get("wall", {})
    _SUPPRESS = True
    try:
        for attr, key in (("wall_width", "width"), ("wall_height", "height"), ("wall_angle", "angle"),
                          ("wall_thickness", "thickness"), ("panel_width", "panel_width"),
                          ("panel_height", "panel_height"), ("bolt_spacing", "bolt_spacing")):
            if key in wd:
                setattr(pr, attr, float(wd[key]))
    finally:
        _SUPPRESS = False
    wall = build_wall(context, pr, frame=True)

    pr.routes.clear()
    for i, r in enumerate(data.get("routes", [])):
        item = pr.routes.add()
        item.name = str(r.get("name", f"Route {i + 1}"))
        c = list(r.get("color", ROUTE_DEFAULTS[i % len(ROUTE_DEFAULTS)]))[:3]
        item.color = (c + [0.5, 0.5, 0.5])[:3]
        item.holdgen = json.dumps(r["holdgen"]) if r.get("holdgen") else ""
    pr.route_index = 0

    if clear:
        remove_all_holds(scene)

    for key, info in data.get("scans", {}).items():
        if get_scan_template(key) is not None:
            continue
        path = str(info.get("file", ""))
        if base and path and not os.path.isabs(path):
            path = os.path.join(base, path)
        if not os.path.isfile(path):
            problems.append(f"scan '{key}': file not found ({path or 'no path saved'})")
            continue
        try:
            load_scan_template(context, key, info, base)
        except Exception as exc:
            problems.append(f"scan '{key}': {exc}")

    col = get_collection(context, "RockHolds")
    base = params_from_props(pr)
    made = []
    for n, rec in enumerate(data.get("holds", [])):
        try:
            p = dict(rec["params"])
            if p.get("type") == "SCAN":
                tmpl = get_scan_template(p.get("scan"))
                if tmpl is None:
                    problems.append(f"hold {n + 1}: scan '{p.get('scan')}' is missing")
                    continue
                obj = create_scan_hold(context, tmpl, col)
            else:
                if p.get("type") not in TYPE_PRESETS:
                    problems.append(f"hold {n + 1}: unknown type '{p.get('type')}'")
                    continue
                full = dict(base)
                for k in ("w", "h", "d", "rough", "crimp", "pocket_dia", "pocket_depth", "bolt"):
                    if k in p:
                        full[k] = float(p[k])
                full["type"], full["seed"] = p["type"], int(p.get("seed", 0))
                obj = create_hold(context, full, col)
            if rec.get("on_wall", True) and wall is not None:
                attach_to_wall(obj, wall, float(rec["u"]), float(rec["v"]), float(rec.get("spin", 0.0)))
            else:
                obj.location = (float(rec.get("x", 0.0)), float(rec.get("y", 0.0)), float(rec.get("z", 0.0)))
            if rec.get("role") in ROLE_NAMES:
                obj.rh_role = rec["role"]
            obj.rh_route = max(0, int(rec.get("route", 0)))
            if rec.get("holdgen"):
                obj["rh_holdgen"] = json.dumps(rec["holdgen"])
            made.append(obj)
        except Exception as exc:
            problems.append(f"hold {n + 1}: {exc}")
    recolor_all(scene)
    return made, problems


# ---- generated routes (Rockform hold generator) -------------------------------------
# The generator runs in Rockform's own Python (it needs trimesh), as
#   python -m rockform.holdgen.route --grade V3 --wall-angle 20 ... --out <folder>
# and writes STLs plus a layout file in this add-on's format. Its holds come in as scans, so the
# manufacturing checks, setter's sheet and exports treat them like any other hold.
HOLDGEN_MODULE = "rockform.holdgen.route"


def holdgen_python(pr):
    """python.exe of a Rockform checkout: the setting, a .venv next to this script, or ROCKFORM_PYTHON."""
    if pr.hg_python.strip():
        return bpy.path.abspath(pr.hg_python.strip())
    here = [os.path.dirname(os.path.abspath(__file__))]
    here += [os.path.dirname(bpy.path.abspath(t.filepath)) for t in bpy.data.texts if t.filepath]
    for folder in here:
        for repo in (folder, os.path.dirname(folder)):
            for exe in (os.path.join(repo, ".venv", "Scripts", "python.exe"), os.path.join(repo, ".venv", "bin", "python")):
                if os.path.isfile(exe):
                    return exe
    return os.environ.get("ROCKFORM_PYTHON", "")


def holdgen_out_dir(pr, name):
    base = bpy.path.abspath(pr.hg_out) if bpy.data.filepath or not pr.hg_out.startswith("//") else ""
    base = base or os.path.join(tempfile.gettempdir(), "rock_wall_routes")
    return os.path.join(base, safe_key(name))


def holdgen_command(context, pr, wall, name, out):
    """The generator command line for the current wall. Holds already on it are passed as obstacles."""
    color = ROUTE_DEFAULTS[len(pr.routes) % len(ROUTE_DEFAULTS)]
    os.makedirs(out, exist_ok=True)
    avoid = os.path.join(out, "avoid.json")
    with open(avoid, "w", encoding="utf-8") as f:
        json.dump([[o.location.x, o.location.y, hold_radius(o)] for o in holds_on_wall(context.scene, wall)], f)
    # -I: ignore the current folder and PYTHON* variables, so nothing there can shadow Rockform's modules.
    cmd = [holdgen_python(pr), "-I", "-u", "-m", HOLDGEN_MODULE, "--grade", pr.hg_grade, "--seed", str(pr.hg_seed),
           "--wall-width", str(float(wall["rh_w"])), "--wall-height", str(float(wall["rh_h"])),
           "--wall-angle", str(float(wall["rh_angle"])), "--bolt-spacing", str(float(wall["rh_g"])),
           "--bolt", str(float(pr.hg_bolt)), "--name", name, "--color", *[str(c) for c in color],
           "--avoid", avoid, "--out", out]
    if pr.hg_moves > 0:
        cmd += ["--moves", str(pr.hg_moves)]
    if not pr.hg_feet:
        cmd.append("--no-feet")
    return cmd


def add_layout_route(context, data, base=""):
    """Add the routes and holds of a layout file to the current wall, keeping everything already there.
    Returns (holds made, list of problems)."""
    if not isinstance(data, dict) or data.get("format") != LAYOUT_FORMAT:
        raise ValueError("This is not a Rock Wall layout file")
    scene = context.scene
    pr = scene.rock_wall
    wall = find_wall(scene) or build_wall(context, pr, frame=True)
    problems, keys = [], {}
    first = len(pr.routes)
    for i, r in enumerate(data.get("routes", [])):
        item = pr.routes.add()
        item.name = str(r.get("name", f"Route {first + i + 1}"))
        item.color = (list(r.get("color", ROUTE_DEFAULTS[(first + i) % len(ROUTE_DEFAULTS)]))[:3] + [0.5] * 3)[:3]
        item.holdgen = json.dumps(r["holdgen"]) if r.get("holdgen") else ""
    pr.route_index = len(pr.routes) - 1

    for key, info in data.get("scans", {}).items():
        new_key, n = key, 2
        while get_scan_template(new_key) is not None:      # a regenerated route gets fresh templates
            new_key, n = f"{key}_{n}", n + 1
        try:
            load_scan_template(context, new_key, info, base)
            keys[key] = new_key
        except Exception as exc:
            problems.append(f"scan '{key}': {exc}")

    col = get_collection(context, "RockHolds")
    made = []
    for n, rec in enumerate(data.get("holds", [])):
        tmpl = get_scan_template(keys.get(rec["params"].get("scan"), ""))
        if tmpl is None:
            problems.append(f"hold {n + 1}: its scan did not load")
            continue
        obj = create_scan_hold(context, tmpl, col)
        attach_to_wall(obj, wall, float(rec["u"]), float(rec["v"]), float(rec.get("spin", 0.0)))
        if rec.get("role") in ROLE_NAMES:
            obj.rh_role = rec["role"]
        obj.rh_route = first + max(1, int(rec.get("route", 1)))
        if rec.get("holdgen"):
            obj["rh_holdgen"] = json.dumps(rec["holdgen"])
        made.append(obj)
    recolor_all(scene)
    return made, problems


def route_summary(pr, index):
    """The generator summary of route `index` (0-based), or {}."""
    try:
        return json.loads(pr.routes[index].holdgen) if pr.routes[index].holdgen else {}
    except Exception:
        return {}


# ---- setter's sheet ----------------------------------------------------------------
def collect_sheet_data(context):
    scene = context.scene
    pr = scene.rock_wall
    wall = find_wall(scene)
    if wall is None:
        raise RuntimeError("Build the wall first")
    wd = dict(w=float(wall["rh_w"]), h=float(wall["rh_h"]), t=float(wall.get("rh_t", pr.wall_thickness)),
              angle=float(wall["rh_angle"]), panel_w=float(wall.get("rh_pw", pr.panel_width)),
              panel_h=float(wall.get("rh_ph", pr.panel_height)), grid=float(wall["rh_g"]))
    holds = []
    for o in holds_on_wall(scene, wall):
        p = hold_params(o)
        holds.append(dict(name=o.name, type=p.get("type", "JUG"), w=float(p.get("w", 0)), h=float(p.get("h", 0)),
                          d=float(p.get("d", 0)), bolt=float(p.get("bolt", 0)), u=float(o.location.x),
                          v=float(o.location.y), spin=float(degrees(o.rotation_euler.z)), role=o.rh_role,
                          route=int(o.rh_route), status=str(o.get("rh_mfg", "")), seed=int(p.get("seed", 0)),
                          scan=str(p.get("scan", ""))))
    routes = [dict(name=r.name, color=tuple(r.color)) for r in pr.routes]
    return wd, holds, routes


# ---- per-hold export ------------------------------------------------------------------
def hold_key(obj):
    p = hold_params(obj)
    if p.get("type") == "SCAN":
        return "scan:" + str(p.get("scan"))
    p.pop("color", None)
    return json.dumps(p, sort_keys=True)


def hold_file_stem(obj):
    p = hold_params(obj)
    if p.get("type") == "SCAN":
        return safe_key(str(p.get("scan", obj.name)))
    return safe_key("{}_{:.0f}x{:.0f}x{:.0f}_s{}".format(
        type_label(p.get("type", "hold")), p.get("w", 0), p.get("h", 0), p.get("d", 0), p.get("seed", 0)))


# ---- manufacturing --------------------------------------------------------------------
def clear_overlays(scene, hold=None):
    """Remove the problem-highlight objects (of one hold, or all of them)."""
    n = 0
    for o in list(bpy.data.objects):
        if o.get("rh_overlay") and (hold is None or o.parent == hold):
            mesh = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            n += 1
    if hold is not None and "rh_mfg_map" in hold:
        del hold["rh_mfg_map"]
    return n


def show_problems(context, obj, mode="PROBLEMS"):
    """Colour the trouble spots of a hold with a thin overlay object. Returns {label: area mm2}."""
    scene = context.scene
    clear_overlays(scene, obj)
    p = hold_params(obj)
    method = obj.get("rh_mfg_method") or scene.rock_wall.mfg_method
    codes = []
    try:
        codes = [i.get("code", "") for i in json.loads(obj.get("rh_mfg_issues", "[]"))]
    except Exception:
        pass
    V, T = mesh_arrays(obj.data)
    if len(T) == 0 or len(T) > 250000:
        return {}
    V, T, labels = problem_faces(V, T, method, float(p.get("bolt", 0.0)), codes, mode)
    sel = labels != ""
    if not np.any(sel):
        return {}
    tri, lab = T[sel], labels[sel]
    P = V[tri]
    cr = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(cr, axis=1)
    nrm = cr / np.maximum(ln, 1e-12)[:, None]
    P = P + nrm[:, None, :] * 0.4                       # float just above the surface
    keys = [k for k in ALL_CATS if np.any(lab == k)]
    areas = {ALL_CATS[k][0]: float((ln / 2.0)[lab == k].sum()) for k in keys}
    verts = P.reshape(-1, 3)
    faces = np.arange(len(verts)).reshape(-1, 3)
    mesh = bpy.data.meshes.new(obj.name + "_map")
    _fill_mesh(mesh, verts, faces, mats=[keys.index(k) for k in lab], smooth=False)
    for k in keys:
        mesh.materials.append(get_material("RW_Map_" + k, ALL_CATS[k][1], 0.6))
    ov = bpy.data.objects.new(obj.name + "_map", mesh)
    (obj.users_collection[0] if obj.users_collection else scene.collection).objects.link(ov)
    ov.parent = obj
    ov.matrix_parent_inverse = Matrix.Identity(4)
    ov["rh_overlay"] = True
    try:
        ov.hide_select = True
    except Exception:
        pass
    obj["rh_mfg_map"] = json.dumps(areas)
    return areas


def check_hold(context, obj, cache=None):
    """Run the manufacturing check on one hold and store the verdict on it. Returns the issues."""
    pr = context.scene.rock_wall
    p = hold_params(obj)
    is_scan = p.get("type") == "SCAN"
    bed = tuple(float(x) for x in pr.bed_size)
    bolt = float(p.get("bolt", 0.0))
    ckey = (obj.data.name, pr.mfg_method, bed, bolt) if is_scan else None
    if cache is not None and ckey in cache:
        issues, metrics = cache[ckey]
    else:
        V, T = mesh_arrays(obj.data)
        issues, metrics = check_manufacturing(V, T, None if is_scan else p, pr.mfg_method, bed, bolt, is_scan)
        if cache is not None and ckey is not None:
            cache[ckey] = (issues, metrics)
    obj["rh_mfg"] = mfg_status(issues)
    obj["rh_mfg_method"] = pr.mfg_method
    obj["rh_mfg_issues"] = json.dumps([{k: i.get(k, "") for k in ("level", "code", "title", "why", "fix", "how")}
                                       for i in issues])
    obj["rh_mfg_metrics"] = json.dumps({k: (list(map(float, v)) if hasattr(v, "__len__") else float(v))
                                        for k, v in (metrics or {}).items() if v is not None})
    recolor(obj, context.scene)
    try:
        if pr.mfg_highlight and (obj["rh_mfg"] != "OK" or pr.mfg_view == "ANGLES"):
            show_problems(context, obj, pr.mfg_view)
        else:
            clear_overlays(context.scene, obj)
    except Exception as exc:
        print("Rock Wall Builder: could not highlight", obj.name, exc)
    return issues


def metrics_line(m):
    """One line of the numbers the check measured."""
    if not m:
        return ""
    bits = []
    ext = m.get("extent")
    if ext:
        bits.append(f"size {ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm")
    if "volume" in m:
        bits.append(f"volume {m['volume'] / 1000.0:.0f} cm3")
    if "flat_frac" in m:
        bits.append(f"flat back {min(m['flat_frac'], 9.99) * 100:.0f}% of footprint")
    if m.get("r_min") is not None and m.get("bolt_dia", 0) > 0:
        bits.append(f"material beside bolt {m['r_min'] - m['bolt_dia'] / 2.0:.1f} mm")
    if m.get("t_bolt") is not None:
        bits.append(f"thickness under bolt {m['t_bolt']:.1f} mm")
    if "over_frac" in m:
        bits.append(f"over-limit faces {m['over_frac'] * 100:.1f}% (steepest lean {m.get('max_lean', 0):.0f} deg)")
    if "sharp_len" in m:
        bits.append(f"sharp edges {m['sharp_len']:.0f} mm")
    return "; ".join(bits)


def build_report(rows, method):
    """Plain-text report. rows = [(hold name, label, status, issues[, metrics])]."""
    width = 92
    wrap = lambda text, ind: textwrap.fill(text, width=width, initial_indent=ind, subsequent_indent=ind + "  ")
    n_ok = sum(1 for r in rows if r[2] == "OK")
    n_warn = sum(1 for r in rows if r[2] == "WARN")
    n_fail = sum(1 for r in rows if r[2] == "FAIL")
    out = [f"MANUFACTURING REPORT - {MFG_RULES[method]['label']}",
           f"Checked {len(rows)} holds: {n_ok} OK, {n_warn} with warnings, {n_fail} failing",
           "Rules: FAIL = cannot be made / will break. WARN = makeable but risky. INFO = for your notes.", ""]
    ranked = sorted(rows, key=lambda r: ("FAIL", "WARN", "OK").index(r[2]))
    for status, head in (("FAIL", "HOLDS THAT FAIL"), ("WARN", "HOLDS THAT ARE RISKY")):
        sel = [r for r in ranked if r[2] == status]
        if not sel:
            continue
        out.append(f"{head} ({len(sel)})")
        for r in sel:
            reasons = "; ".join(f"{i['title']} ({i['code']})" for i in r[3] if i["level"] == status)
            out.append(wrap(f"- {r[0]}: {reasons}", "  "))
        out.append("")
    tally = {}
    for r in rows:
        for it in r[3]:
            if it["level"] in ("FAIL", "WARN"):
                tally.setdefault((it["level"], it["title"]), []).append(r[0])
    if tally:
        out.append("MOST COMMON CAUSES")
        for (lvl, title), names in sorted(tally.items(), key=lambda kv: (kv[0][0] != "FAIL", -len(kv[1]))):
            out.append(f"  {len(names):3d} x [{lvl}] {title}")
        out.append("")
    out.append("HOW TO READ THE VIEWPORT COLOURS (Manufacturing panel > Show problem areas)")
    names = {"OVERHANG_HI": "red", "OVERHANG_LO": "orange", "SHARP": "cyan", "OPEN": "pink", "BOLT": "purple"}
    for key, (label, rgb) in PROBLEM_CATS.items():
        out.append(f"  {names[key]:7s} = {label}")
    out.append("")
    for row in ranked:
        name, label, status, issues = row[:4]
        metrics = row[4] if len(row) > 4 else None
        out.append("=" * width)
        out.append(f"{status:5s} {name}   ({label})")
        line = metrics_line(metrics)
        if line:
            out.append(wrap("Measured: " + line, "      "))
        for it in issues:
            if it["level"] == "INFO" and status != "OK":
                continue
            out.append(f"  [{it['level']}] {it['title']}  ({it['code']})")
            out.append(wrap("Why: " + it["why"], "      "))
            out.append(wrap("Fix: " + it["fix"], "      "))
            if it.get("how"):
                out.append(wrap("How measured: " + it["how"], "      "))
        out.append("")
    return "\n".join(out)


def write_text(name, text):
    t = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    t.clear()
    t.write(text)
    return t


# ---- small UI helpers --------------------------------------------------------------------
LEVEL_ICONS = {"FAIL": "CANCEL", "WARN": "ERROR", "INFO": "INFO", "OK": "CHECKMARK"}


def wrap_width(context):
    try:
        return max(24, int(context.region.width / 7.5) - 4)
    except Exception:
        return 38


def draw_wrapped(layout, context, text, icon="NONE"):
    col = layout.column(align=True)
    col.scale_y = 0.8
    for i, line in enumerate(textwrap.wrap(text, wrap_width(context)) or [""]):
        col.label(text=line, icon=icon if i == 0 else "NONE")


# =============================================================================
# 5. Settings (what the menu edits)
# =============================================================================
_hold_items_cache = []


def hold_type_items(self, context):
    """Hold types of the chosen category (the list must be kept alive for Blender)."""
    global _hold_items_cache
    _hold_items_cache = [i for i in TYPE_ITEMS if category_of(i[0]) == self.hold_category]
    return _hold_items_cache


def _category_changed(self, context):
    first = next(i[0] for i in TYPE_ITEMS if category_of(i[0]) == self.hold_category)
    self.hold_type = first
    _type_changed(self, context)


def _type_changed(self, context):
    pre = TYPE_PRESETS[self.hold_type]
    self.width, self.height, self.depth = float(pre["w"]), float(pre["h"]), float(pre["d"])
    self.bolt_hole = float(pre.get("bolt", 12.0))


def _wall_changed(self, context):
    """Rebuild the wall live while its sliders move (only if a wall already exists)."""
    try:
        if not _SUPPRESS and find_wall(context.scene) is not None:
            build_wall(context, self)
    except Exception as exc:
        print("Rock Wall Builder: wall update failed:", exc)


def _color_changed(self, context):
    try:
        recolor_all(context.scene)
    except Exception as exc:
        print("Rock Wall Builder: recolour failed:", exc)


SPIN_STEP = 15.0      # degrees per rotate-button press, R / Q / E key or wheel notch


def _spin_all_changed(self, context):
    for o in context.selected_objects:
        if o.get("rh_hold"):
            o.rotation_euler.z = radians(wrap_deg(self.spin_all))


def _tag_changed(self, context):
    """Role or route of a hold changed (self is the hold object)."""
    recolor(self, context.scene)


class RockWallRoute(PropertyGroup):
    name: StringProperty(name="Name", default="Route")
    color: FloatVectorProperty(name="Colour", subtype="COLOR", size=3, min=0.0, max=1.0,
                               default=(0.9, 0.25, 0.25), update=_color_changed)
    holdgen: StringProperty(name="Generator summary", default="",
                            description="Grade and rating of a generated route (JSON), empty for hand-made routes")


class RockWallProps(PropertyGroup):
    # ---- wall ----
    wall_width: FloatProperty(description="Overall width of the wall, left to right, in mm", name="Width (mm)", default=3000.0, min=500.0, max=20000.0, update=_wall_changed)
    wall_height: FloatProperty(description="Height of the wall measured up its surface, in mm", name="Height (mm)", default=4000.0, min=500.0, max=20000.0, update=_wall_changed)
    wall_angle: FloatProperty(
        name="Wall angle (deg)", description="0 = vertical, positive = overhang (top leans out)",
        default=20.0, min=-20.0, max=90.0, update=_wall_changed)
    wall_thickness: FloatProperty(description="Thickness of the plywood panels, in mm", name="Thickness (mm)", default=18.0, min=5.0, max=200.0, update=_wall_changed)
    panel_width: FloatProperty(description="Width of one plywood panel; the wall is split into panels this wide", name="Panel width (mm)", default=1200.0, min=200.0, max=5000.0, update=_wall_changed)
    panel_height: FloatProperty(description="Height of one plywood panel; the wall is split into panels this tall", name="Panel height (mm)", default=2400.0, min=200.0, max=5000.0, update=_wall_changed)
    bolt_spacing: FloatProperty(
        name="Bolt grid (mm)", description="Spacing of the bolt holes, 0 for none",
        default=200.0, min=0.0, max=1000.0, update=_wall_changed)

    # ---- hold ----
    hold_category: EnumProperty(
        name="Category", items=CATEGORY_ITEMS, default="NATURAL", update=_category_changed,
        description="Natural = lumpy rock-like holds. Gym = smooth man-made holds like a climbing gym's")
    hold_type: EnumProperty(description="Kind of hold to make. Changing it loads that type's usual size",
                            name="Type", items=hold_type_items, update=_type_changed)
    width: FloatProperty(description="Hold size across the wall (left to right), in mm", name="Width (mm)", default=160.0, min=15.0, max=600.0)
    height: FloatProperty(description="Hold size up the wall, in mm", name="Height (mm)", default=110.0, min=15.0, max=600.0)
    depth: FloatProperty(name="Thickness (mm)", description="How far the hold sticks out of the wall",
                         default=70.0, min=5.0, max=300.0)
    roughness: FloatProperty(name="Roughness", description="How lumpy and natural the surface is",
                             default=0.3, min=0.0, max=1.0)
    crimp_edge: FloatProperty(name="Edge depth (mm)", description="Depth of the flat crimp edge",
                              default=6.0, min=1.0, max=30.0)
    pocket_diameter: FloatProperty(description="Width of the finger pocket in mm (a two-finger pocket is cut wider)", name="Pocket diameter (mm)", default=30.0, min=10.0, max=80.0)
    pocket_depth: FloatProperty(description="How deep the pocket is pressed into the hold, in mm", name="Pocket depth (mm)", default=25.0, min=5.0, max=60.0)
    bolt_hole: FloatProperty(name="Bolt hole (mm)", description="Mounting hole diameter, 0 for none",
                             default=12.0, min=0.0, max=30.0)
    color_mode: EnumProperty(
        name="Colour", default="TYPE", update=_color_changed,
        items=[("TYPE", "By type", "Each hold type gets its own colour"),
               ("ROCK", "Natural", "Grey rock colour"),
               ("ROUTE", "By route", "Colour of the route each hold belongs to"),
               ("ROLE", "By role", "Start / hand / foot / finish"),
               ("CHECK", "By manufacturing check", "Green = OK, yellow = warning, red = fails the check")])

    # ---- scans ----
    scan_pick: EnumProperty(description="Which imported scan to place", name="Scan", items=scan_enum_items)
    scan_count: IntProperty(description="How many copies of the scan to add to the wall", name="Copies", default=1, min=1, max=50)
    scan_share: FloatProperty(
        name="Scans in a set", description="Share of Generate Set holds that are copies of your scans (0 = none)",
        default=0.0, min=0.0, max=1.0, subtype="FACTOR")

    # ---- routes ----
    routes: CollectionProperty(description="The routes (problems) set on this wall", type=RockWallRoute)
    route_index: IntProperty(description="The route chosen in the list", default=0)

    # ---- manufacturing ----
    mfg_method: EnumProperty(
        name="Method", default="PRINT",
        items=[("PRINT", "3D print", "FDM printing with the flat back on the bed"),
               ("CAST", "Cast", "Resin / polyurethane cast in a one-piece mould")])
    bed_size: FloatVectorProperty(description="Build volume of your 3D printer: X, Y and Z in mm", name="Print bed (mm)", size=3, default=(220.0, 220.0, 250.0),
                                  min=10.0, max=3000.0)
    mfg_scope: EnumProperty(description="Check every hold, or only the selected ones", name="Check", default="ALL",
                            items=[("ALL", "All holds", ""), ("SELECTED", "Selected", "")])
    show_why: BoolProperty(description="Show the reason and suggested fix for each problem, not just its title", name="Show why and fix", default=True)
    mfg_highlight: BoolProperty(
        name="Mark problems on the holds", default=True,
        description="After a check, colour the trouble spots of holds that are not OK directly on the hold")
    mfg_view: EnumProperty(
        name="View", default="PROBLEMS",
        items=[("PROBLEMS", "Problem spots", "Mark only the faces and edges that cause trouble"),
               ("ANGLES", "Face angles", "Colour every face by how far it leans back (the measurement behind "
                                         "overhangs and undercuts)")])

    # ---- seeds and sets ----
    seed: IntProperty(name="Seed", description="Same seed + same settings = same rock",
                      default=1, min=0, max=99999)
    set_mode: EnumProperty(
        name="Set", default="PICK",
        items=[("PICK", "Choose holds", "Tick the hold types you want and set how many of each"),
               ("RANDOM", "Random mix", "A number of random holds")])
    set_count: IntProperty(description="How many holds Generate Set creates", name="Count", default=12, min=1, max=100)
    set_types: EnumProperty(
        name="Types", default="MIXED",
        items=[("MIXED", "Mixed (this category)", "Random type of the chosen category for each hold"),
               ("BOTH", "Mixed (both categories)", "Random type from natural and gym holds"),
               ("SAME", "Menu type", "Every hold uses the type chosen in the Hold panel")])
    seed_mode: EnumProperty(
        name="Seeds", default="SEQ",
        items=[("SEQ", "Sequential", "Seed, seed+1, seed+2 ..."),
               ("RANDOM", "Random", "A random seed per hold")])
    size_var: FloatProperty(name="Size variation", description="Random +/- change to width, height and thickness",
                            default=0.2, min=0.0, max=0.6, subtype="FACTOR")
    place_on_wall: BoolProperty(description="Place the new holds on the wall without overlaps (off = line them up at the 3D cursor)", name="Place on wall", default=True)

    # ---- arrange ----
    layout_mode: EnumProperty(
        name="Layout", default="RANDOM",
        items=[("RANDOM", "Random scatter", "Random spots that do not overlap"),
               ("GRID", "Tidy grid", "Evenly spaced rows, order shuffled by the layout seed")])
    layout_seed: IntProperty(description="Same layout seed gives the same arrangement; Shuffle picks a new one", name="Layout seed", default=1, min=0, max=99999)
    min_gap: FloatProperty(description="Smallest empty space kept between neighbouring holds, in mm", name="Min gap (mm)", default=60.0, min=0.0, max=500.0)
    edge_margin: FloatProperty(description="Space kept clear between holds and the wall edges, in mm", name="Edge margin (mm)", default=100.0, min=0.0, max=1000.0)
    spin_mode: EnumProperty(
        name="Orientation", default="RANDOM",
        description="How holds are turned when they are placed or auto-arranged",
        items=[("UPRIGHT", "Upright (0 deg)", "Every hold the way it was generated"),
               ("FIXED", "Fixed angle", "Every hold turned by the angle below"),
               ("RANDOM", "Random, near upright", "Random turn within 90 deg of upright"),
               ("ANY", "Random, any direction", "Random turn all the way round"),
               ("KEEP", "Keep current", "Holds that are already on the wall keep their turn")])
    spin_angle: FloatProperty(
        name="Angle (deg)", default=0.0, min=-180.0, max=180.0,
        description="Turn used for 'Fixed angle'. Positive = counter-clockwise as you look at the wall")
    spin_all: FloatProperty(
        name="Turn all selected to", default=0.0, min=-180.0, max=180.0, precision=0, update=_spin_all_changed,
        description="Drag to set the orientation of every selected hold at once (0 = as generated, "
                    "positive = counter-clockwise)")
    snap_bolt: BoolProperty(name="Snap to bolt holes",
                            description="Hold origins (bolt positions) land on the wall's bolt grid",
                            default=True)
    arrange_scope: EnumProperty(
        name="Holds", default="ALL",
        items=[("ALL", "All holds", "Every hold in the scene"),
               ("SELECTED", "Selected", "Only the selected holds; the rest stay put")])
    # ---- generated routes (Rockform hold generator) ----
    hg_grade: EnumProperty(
        name="Grade", default="V3", description="Boulder grade the route is planned for",
        items=[(g, g, f"Plan a {g} route") for g in ("V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8")])
    hg_seed: IntProperty(name="Seed", default=1, min=0, max=99999,
                         description="Same seed + same grade + same wall = same route")
    hg_moves: IntProperty(name="Hand moves", default=0, min=0, max=30,
                          description="Stop after this many hand moves, 0 = climb to the top of the wall")
    hg_feet: BoolProperty(name="Foot holds", default=True, description="Add small foot holds below the hands")
    hg_bolt: FloatProperty(name="Bolt hole (mm)", default=10.5, min=0.0, max=30.0,
                           description="Hole cut through each hand hold, 0 for none (foot holds are screw-on)")
    hg_python: StringProperty(
        name="Rockform Python", subtype="FILE_PATH", default="",
        description="python.exe of the Rockform checkout (its .venv). Empty = look next to this script, "
                    "then the ROCKFORM_PYTHON environment variable")
    hg_out: StringProperty(name="Output folder", subtype="DIR_PATH", default="//generated_routes",
                           description="Where the generated STLs, reports and layout files go")
    hg_status: StringProperty(name="Status", default="")


# One tick box and one quantity per hold type, for "Generate Set > Choose holds".
for _key, _label, _desc in TYPE_ITEMS:
    RockWallProps.__annotations__[f"set_on_{_key}"] = BoolProperty(
        name=_label, description=f"Include {_label.lower()} holds in the set. {_desc}",
        default=_key in ("JUG", "CRIMP", "SLOPER", "PINCH", "POCKET"))
    RockWallProps.__annotations__[f"set_n_{_key}"] = IntProperty(
        name="Quantity", description=f"How many {_label.lower()} holds to make", default=3, min=0, max=50)


def set_total(pr):
    return sum(int(getattr(pr, f"set_n_{k}")) for k, _n, _d in TYPE_ITEMS if getattr(pr, f"set_on_{k}"))


# Per-object properties (shown in the "Selected Hold" panel): position on the wall
def _get_u(self):
    return float(self.location.x)


def _set_u(self, value):
    self.location.x = value


def _get_v(self):
    return float(self.location.y)


def _set_v(self, value):
    self.location.y = value


def _get_spin(self):
    return float(degrees(self.rotation_euler.z))


def _set_spin(self, value):
    self.rotation_euler.z = radians(wrap_deg(value))


def wrap_deg(a):
    """Angle in degrees wrapped to -180 .. 180."""
    a = (float(a) + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


# ---- editing one generated hold (or every selected hold) -----------------------
def edit_targets(obj):
    """Holds an edit applies to: all selected generated holds if `obj` is one of them, else just `obj`."""
    try:
        sel = [o for o in bpy.context.selected_objects if o.get("rh_hold")]
    except Exception:
        sel = []
    group = sel if obj in sel else [obj]
    return [o for o in group if hold_params(o).get("type") not in (None, "SCAN")]


def edit_holds(obj, **changes):
    """Change generator settings of the hold(s) and rebuild them in place (position, spin, route, role stay)."""
    context = bpy.context
    for o in edit_targets(obj):
        p = hold_params(o)
        if "type" in changes and changes["type"] != p.get("type"):
            pre = TYPE_PRESETS[changes["type"]]          # a new type brings its usual size and bolt hole
            p.update(w=float(pre["w"]), h=float(pre["h"]), d=float(pre["d"]),
                     bolt=float(pre.get("bolt", p.get("bolt", 12.0))))
        p.update(changes)
        try:
            rebuild_hold(context, o, p)
        except Exception as exc:
            print("Rock Wall Builder: edit failed on", o.name, ":", exc)


def _edit_prop(key, default, **kw):
    """A Float/Int property that reads one value out of a hold's stored settings and rebuilds on change."""
    cast = int if kw.pop("is_int", False) else float
    Prop = IntProperty if cast is int else FloatProperty

    def getter(self):
        return cast(hold_params(self).get(key, default))

    def setter(self, value):
        edit_holds(self, **{key: cast(value)})
    return Prop(get=getter, set=setter, **kw)


def _get_edit_type(self):
    t = hold_params(self).get("type", "JUG")
    for i, it in enumerate(TYPE_ITEMS):
        if it[0] == t:
            return i
    return 0


def _set_edit_type(self, index):
    edit_holds(self, type=TYPE_ITEMS[int(index)][0])


# =============================================================================
# 6. Operators
# =============================================================================
class ROCKWALL_OT_build_wall(Operator):
    bl_idname = "rockwall.build_wall"
    bl_label = "Build / Update Wall"
    bl_description = "Create the climbing wall, or rebuild it with the current settings"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pr = context.scene.rock_wall
        existed = find_wall(context.scene) is not None
        wall = build_wall(context, pr, frame=not existed)
        self.report({"INFO"}, f"Wall {pr.wall_width:.0f} x {pr.wall_height:.0f} mm at {pr.wall_angle:.0f} deg")
        return {"FINISHED"}


class ROCKWALL_OT_shuffle_seed(Operator):
    bl_idname = "rockwall.shuffle_seed"
    bl_label = "Shuffle Seed"
    bl_description = "Pick a new random seed"

    def execute(self, context):
        context.scene.rock_wall.seed = random.randint(0, 99999)
        return {"FINISHED"}


class ROCKWALL_OT_generate_hold(Operator):
    bl_idname = "rockwall.generate_hold"
    bl_label = "Generate Hold"
    bl_description = "Create one hold from the Hold settings and the current seed"
    bl_options = {"REGISTER", "UNDO"}

    shuffle: BoolProperty(name="Shuffle seed first", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        pr = context.scene.rock_wall
        if self.shuffle:
            pr.seed = random.randint(0, 99999)
        p = params_from_props(pr)
        obj = create_hold(context, p, get_collection(context, "RockHolds"))

        wall = find_wall(context.scene)
        if wall is not None:
            fails = arrange_holds(context, [obj], wall, pr, "RANDOM", random.randint(0, 99999))
            msg = f"{obj.name} placed on the wall"
            if fails:
                msg += " (overlaps another hold: no free spot)"
        else:
            obj.location = context.scene.cursor.location
            msg = f"{obj.name} created at the 3D cursor (build a wall to place it)"

        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        prepare_viewports(context)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class ROCKWALL_OT_generate_set(Operator):
    bl_idname = "rockwall.generate_set"
    bl_label = "Generate Set"
    bl_description = "Create several different holds from a run of seeds and place them on the wall"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pr = context.scene.rock_wall
        rng = np.random.default_rng(int(pr.seed))
        col = get_collection(context, "RockHolds")
        type_keys = [t[0] for t in TYPE_ITEMS if t[0] not in VOLUME_TYPES      # add volumes by hand
                     and (pr.set_types == "BOTH" or category_of(t[0]) == pr.hold_category)]
        templates = scan_templates()

        if pr.set_mode == "PICK":             # the ticked types, each as many times as asked
            plan = [k for k, _n, _d in TYPE_ITEMS if getattr(pr, f"set_on_{k}") for _ in range(int(getattr(pr, f"set_n_{k}")))]
            if not plan:
                self.report({"WARNING"}, "Tick at least one hold type, with a quantity above 0")
                return {"CANCELLED"}
        else:
            plan = [None] * int(pr.set_count)

        made = []
        for i, kind in enumerate(plan):
            seed = pr.seed + i if pr.seed_mode == "SEQ" else int(rng.integers(0, 100000))
            if kind is None and templates and pr.scan_share > 0 and float(rng.random()) < pr.scan_share:
                made.append(create_scan_hold(context, templates[int(rng.integers(0, len(templates)))], col))
                continue
            p = params_from_props(pr)
            if kind is None and pr.set_types in ("MIXED", "BOTH"):
                kind = type_keys[int(rng.integers(0, len(type_keys)))]
            if kind is not None:
                pre = TYPE_PRESETS[kind]
                p.update(type=kind, w=pre["w"], h=pre["h"], d=pre["d"], bolt=pre.get("bolt", p["bolt"]))
            vary = pr.size_var
            p["w"] *= 1.0 + float(rng.uniform(-vary, vary))
            p["h"] *= 1.0 + float(rng.uniform(-vary, vary))
            p["d"] *= 1.0 + float(rng.uniform(-vary, vary))
            p["seed"] = seed
            made.append(create_hold(context, p, col))

        wall = find_wall(context.scene)
        if pr.place_on_wall and wall is None:
            wall = build_wall(context, pr, frame=True)

        fails = 0
        if pr.place_on_wall and wall is not None:
            fails = arrange_holds(context, made, wall, pr, pr.layout_mode, pr.layout_seed)
        else:
            x = 0.0
            origin = context.scene.cursor.location.copy()
            for o in made:
                x += hold_radius(o)
                o.location = origin + Vector((x, 0.0, 0.0))
                x += hold_radius(o) + 40.0

        for o in context.view_layer.objects:
            o.select_set(False)
        for o in made:
            o.select_set(True)
        if made:
            context.view_layer.objects.active = made[0]
        prepare_viewports(context)
        msg = f"Created {len(made)} holds"
        if fails:
            msg += f" ({fails} could not avoid overlap: wall is too full)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class ROCKWALL_OT_set_pick(Operator):
    bl_idname = "rockwall.set_pick"
    bl_label = "Tick Hold Types"
    bl_description = "Tick or clear all the hold types of a category in the set list"
    bl_options = {"REGISTER", "UNDO"}

    category: EnumProperty(name="Category", items=CATEGORY_ITEMS + [("ALL", "All", "")], default="ALL",
                           options={"SKIP_SAVE"})
    value: BoolProperty(name="Tick", default=True, options={"SKIP_SAVE"})

    def execute(self, context):
        pr = context.scene.rock_wall
        for k, _n, _d in TYPE_ITEMS:
            if self.category == "ALL" or category_of(k) == self.category:
                setattr(pr, f"set_on_{k}", self.value)
        return {"FINISHED"}


class ROCKWALL_OT_update_selected(Operator):
    bl_idname = "rockwall.update_selected"
    bl_label = "Update Selected"
    bl_description = "Rebuild the selected holds in place"
    bl_options = {"REGISTER", "UNDO"}

    reroll: BoolProperty(
        name="Re-roll seed", default=False, options={"SKIP_SAVE"},
        description="Keep each hold's own settings but give it a new random seed")

    @classmethod
    def poll(cls, context):
        return any(o.get("rh_hold") for o in context.selected_objects)

    def execute(self, context):
        pr = context.scene.rock_wall
        holds = [o for o in context.selected_objects if o.get("rh_hold")]
        for i, o in enumerate(holds):
            if hold_params(o).get("type") == "SCAN":
                continue                        # scans are fixed meshes: nothing to rebuild
            if self.reroll:
                p = json.loads(o["rh_params"])
                p["seed"] = random.randint(0, 99999)
            else:
                p = params_from_props(pr)
                p["seed"] = int(pr.seed) + i
            rebuild_hold(context, o, p)
        self.report({"INFO"}, f"Rebuilt {len(holds)} hold(s)")
        return {"FINISHED"}


class ROCKWALL_OT_hold_to_panel(Operator):
    bl_idname = "rockwall.hold_to_panel"
    bl_label = "Copy Hold to Panel 2"
    bl_description = ("Load the active hold's type, size and settings into the Hold panel, so you can "
                      "generate more like it or apply it to other holds")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        o = context.active_object
        return o is not None and o.get("rh_hold") and hold_params(o).get("type") not in (None, "SCAN")

    def execute(self, context):
        p = hold_params(context.active_object)
        pr = context.scene.rock_wall
        pr.hold_category = category_of(p["type"])
        pr.hold_type = p["type"]
        pr.width, pr.height, pr.depth = p["w"], p["h"], p["d"]
        pr.roughness, pr.seed = p["rough"], int(p["seed"])
        pr.crimp_edge, pr.bolt_hole = max(1.0, p["crimp"]), p["bolt"]
        pr.pocket_diameter = max(10.0, p["pocket_dia"])
        pr.pocket_depth = max(5.0, p["pocket_depth"])
        self.report({"INFO"}, "Hold settings copied to the Hold panel")
        return {"FINISHED"}


class ROCKWALL_OT_auto_arrange(Operator):
    bl_idname = "rockwall.auto_arrange"
    bl_label = "Auto-Arrange"
    bl_description = "Place the holds on the wall without overlaps"
    bl_options = {"REGISTER", "UNDO"}

    shuffle: BoolProperty(name="New layout seed", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return find_wall(context.scene) is not None

    def execute(self, context):
        pr = context.scene.rock_wall
        wall = find_wall(context.scene)
        if self.shuffle:
            pr.layout_seed = random.randint(0, 99999)
        holds = all_holds(context.scene)
        if pr.arrange_scope == "SELECTED":
            holds = [o for o in holds if o.select_get()]
        if not holds:
            self.report({"WARNING"}, "No holds to arrange: generate some first")
            return {"CANCELLED"}
        fails = arrange_holds(context, holds, wall, pr, pr.layout_mode, pr.layout_seed)
        msg = f"Arranged {len(holds)} holds"
        if fails:
            msg += f" ({fails} could not avoid overlap: lower Min gap or enlarge the wall)"
        self.report({"INFO" if not fails else "WARNING"}, msg)
        return {"FINISHED"}


class ROCKWALL_OT_clear_holds(Operator):
    bl_idname = "rockwall.clear_holds"
    bl_label = "Clear Wall"
    bl_description = "Delete every hold from the wall and the scene (the wall itself stays). Undo brings them back"
    bl_options = {"REGISTER", "UNDO"}

    keep_scans: BoolProperty(name="Keep my scan holds", default=False,
                             description="Only delete generated holds and leave copies of your imported scans")
    only_selected: BoolProperty(name="Only selected holds", default=False,
                                description="Delete just the selected holds")

    @classmethod
    def poll(cls, context):
        return len(all_holds(context.scene)) > 0

    def _targets(self, context):
        holds = all_holds(context.scene)
        if self.only_selected:
            holds = [o for o in holds if o.select_get()]
        if self.keep_scans:
            holds = [o for o in holds if hold_params(o).get("type") != "SCAN"]
        return holds

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        lay = self.layout
        lay.prop(self, "keep_scans")
        lay.prop(self, "only_selected")
        n = len(self._targets(context))
        lay.label(text=f"This will delete {n} hold(s).", icon="TRASH" if n else "INFO")

    def execute(self, context):
        holds = self._targets(context)
        for o in holds:
            clear_overlays(context.scene, o)
            mesh = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        self.report({"INFO"}, f"Removed {len(holds)} holds" if holds else "No holds to remove")
        return {"FINISHED"}


class ROCKWALL_OT_arrange(Operator):
    """Drag holds around on the wall surface with the mouse."""
    bl_idname = "rockwall.arrange"
    bl_label = "Arrange Holds (Drag)"
    bl_description = ("Click and drag holds along the wall. While dragging: R / Shift+R or the wheel "
                      "turn it by 15 degrees (E / R / Q / wheel; 0 = upright). Over a hold: D duplicates, X deletes. G toggles bolt snapping. "
                      "Esc, Enter or right-click finishes")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == "VIEW_3D"
                and find_wall(context.scene) is not None)

    # ---- setup ----
    def invoke(self, context, event):
        wall = find_wall(context.scene)
        if not holds_on_wall(context.scene, wall):
            self.report({"WARNING"}, "No holds on the wall yet: use Generate Set or Auto-Arrange first")
            return {"CANCELLED"}
        self._area = context.area
        self._region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
        if self._region is None:
            return {"CANCELLED"}
        self._rv3d = context.area.spaces.active.region_3d
        self._wall = wall
        self._drag = None
        self._hover = None
        self._off = (0.0, 0.0)
        self._start = None
        self._workspace = context.workspace
        self._status(context, "")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    # ---- helpers ----
    def _inside(self, event):
        mx, my = event.mouse_x, event.mouse_y
        r = self._region
        if not (r.x <= mx < r.x + r.width and r.y <= my < r.y + r.height):
            return False
        for reg in self._area.regions:      # sidebar, toolbar and headers overlap the viewport
            if reg.type in {"UI", "TOOLS", "HEADER", "TOOL_HEADER", "FOOTER", "NAVIGATION_BAR"}:
                if reg.width > 1 and reg.height > 1 and reg.x <= mx < reg.x + reg.width \
                        and reg.y <= my < reg.y + reg.height:
                    return False
        return True

    def _hit(self, event):
        """Mouse position on the wall surface as (u, v), or None."""
        r = self._region
        coord = (event.mouse_x - r.x, event.mouse_y - r.y)
        origin = view3d_utils.region_2d_to_origin_3d(r, self._rv3d, coord)
        vec = view3d_utils.region_2d_to_vector_3d(r, self._rv3d, coord)
        mw = self._wall.matrix_world
        normal = (mw.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        point = geometry.intersect_line_plane(origin, origin + vec * 1.0e6, mw.translation, normal)
        if point is None:
            return None
        local = mw.inverted() @ point
        return float(local.x), float(local.y)

    def _on_wall(self, context):
        return holds_on_wall(context.scene, self._wall)

    def _pick(self, context, u, v):
        best, best_d = None, 1e18
        for o in self._on_wall(context):
            d = ((o.location.x - u) ** 2 + (o.location.y - v) ** 2) ** 0.5
            if d <= hold_radius(o) and d < best_d:
                best, best_d = o, d
        return best

    def _move(self, u, v):
        W, H, g = float(self._wall["rh_w"]), float(self._wall["rh_h"]), float(self._wall["rh_g"])
        nu, nv = u + self._off[0], v + self._off[1]
        if bpy.context.scene.rock_wall.snap_bolt:
            nu, nv = snap_bolts(nu, nv, W, H, g)
        nu = min(max(nu, -W / 2.0), W / 2.0)
        nv = min(max(nv, 0.0), H)
        self._drag.location = (nu, nv, 0.0)

    def _overlaps(self, context, obj):
        gap = context.scene.rock_wall.min_gap
        for o in self._on_wall(context):
            if o == obj:
                continue
            d = ((o.location.x - obj.location.x) ** 2 + (o.location.y - obj.location.y) ** 2) ** 0.5
            if d < hold_radius(o) + hold_radius(obj) + gap:
                return True
        return False

    def _begin_drag(self, context, obj, u, v):
        self._drag = obj
        self._off = (obj.location.x - u, obj.location.y - v)
        self._start = (obj.location.x, obj.location.y, obj.rotation_euler.z)
        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

    def _spin(self, obj, step):
        obj.rotation_euler.z += radians(step)

    def _status(self, context, extra):
        snap = "ON" if context.scene.rock_wall.snap_bolt else "OFF"
        text = ("Arrange: drag a hold | R,E / Q / wheel turn | 0 upright | D duplicate | X delete | "
                f"G bolt snap {snap} | Esc / Enter / right-click done")
        target = self._drag or self._hover
        if target is not None:
            warn = "   OVERLAP" if self._overlaps(context, target) else ""
            text += (f"   [{target.name}  across {target.location.x:.0f}  up {target.location.y:.0f}  "
                     f"spin {degrees(target.rotation_euler.z):.0f}{warn}]")
        if extra:
            text += "   " + extra
        try:
            self._workspace.status_text_set(text)
        except Exception:
            pass

    def _finish(self, context):
        try:
            self._workspace.status_text_set(None)
        except Exception:
            pass
        self._area.tag_redraw()
        return {"FINISHED"}

    # ---- events ----
    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except ReferenceError:      # the wall or a hold was deleted under us
            return self._finish(context)

    def _modal(self, context, event):
        et, ev = event.type, event.value
        inside = self._inside(event)

        if et == "ESC" and ev == "PRESS":
            if self._drag is not None:
                self._cancel_drag()
                self._status(context, "move cancelled")
                return {"RUNNING_MODAL"}
            return self._finish(context)
        if et in {"RET", "NUMPAD_ENTER"} and ev == "PRESS":
            return self._finish(context)
        if et == "RIGHTMOUSE" and ev == "PRESS" and inside:
            if self._drag is not None:
                self._cancel_drag()
                self._status(context, "move cancelled")
                return {"RUNNING_MODAL"}
            return self._finish(context)

        if et == "LEFTMOUSE" and ev == "RELEASE" and self._drag is not None:
            self._drag = None
            self._status(context, "")
            return {"RUNNING_MODAL"}

        if not inside and self._drag is None:
            return {"PASS_THROUGH"}

        if et == "MOUSEMOVE":
            if not inside:
                return {"RUNNING_MODAL"}
            hit = self._hit(event)
            if hit is None:
                return {"PASS_THROUGH"}
            if self._drag is not None:
                self._move(*hit)
                self._area.tag_redraw()
                self._status(context, "")
                return {"RUNNING_MODAL"}
            self._hover = self._pick(context, *hit)
            self._status(context, "")
            return {"PASS_THROUGH"}

        if et == "LEFTMOUSE" and ev == "PRESS" and inside:
            hit = self._hit(event)
            if hit is not None:
                obj = self._pick(context, *hit)
                if obj is not None:
                    self._begin_drag(context, obj, *hit)
                    self._status(context, "")
            return {"RUNNING_MODAL"}

        if et in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and self._drag is not None:
            step = SPIN_STEP
            self._spin(self._drag, step if et == "WHEELUPMOUSE" else -step)
            self._status(context, "")
            return {"RUNNING_MODAL"}

        if ev == "PRESS" and inside:
            target = self._drag or self._hover
            if et in {"R", "Q", "E", "ZERO"} and target is not None:
                step = SPIN_STEP
                if et == "ZERO":
                    target.rotation_euler.z = 0.0
                elif et == "Q" or (et == "R" and event.shift):
                    self._spin(target, -step)
                else:
                    self._spin(target, step)
                self._status(context, "")
                return {"RUNNING_MODAL"}
            if et == "G":
                pr = context.scene.rock_wall
                pr.snap_bolt = not pr.snap_bolt
                self._status(context, "")
                return {"RUNNING_MODAL"}
            if et == "D" and self._drag is None and self._hover is not None:
                src = self._hover
                new = src.copy()
                new.data = src.data.copy()
                for c in src.users_collection:
                    c.objects.link(new)
                hit = self._hit(event)
                if hit is not None:
                    self._begin_drag(context, new, *hit)
                self._hover = None
                self._status(context, "duplicated")
                return {"RUNNING_MODAL"}
            if et in {"X", "DEL"} and self._drag is None and self._hover is not None:
                victim = self._hover
                mesh = victim.data
                self._hover = None
                bpy.data.objects.remove(victim, do_unlink=True)
                if mesh is not None and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
                self._status(context, "deleted")
                return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def _cancel_drag(self):
        if self._drag is not None and self._start is not None:
            self._drag.location = (self._start[0], self._start[1], 0.0)
            self._drag.rotation_euler.z = self._start[2]
        self._drag = None


# ---- scans ------------------------------------------------------------------------
def _select_only(context, objs):
    for o in context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        try:
            o.select_set(True)
        except RuntimeError:
            pass
    if objs:
        context.view_layer.objects.active = objs[0]


def _place_new(context, pr, objs):
    """Put new holds on the wall (random free spots) or at the 3D cursor. Returns how many did not fit."""
    wall = find_wall(context.scene)
    if wall is not None and pr.place_on_wall:
        return arrange_holds(context, objs, wall, pr, "RANDOM", random.randint(0, 99999))
    x, origin = 0.0, context.scene.cursor.location.copy()
    for o in objs:
        x += hold_radius(o)
        o.location = origin + Vector((x, 0.0, 0.0))
        x += hold_radius(o) + 40.0
    return 0


AXIS_ITEMS = [
    ("+Z", "+Z up", "The face to climb on points up the Z axis (most scans: scanned lying on a table)"),
    ("-Z", "-Z (down)", "The climbing face points down the Z axis"),
    ("+Y", "+Y", "The climbing face points along +Y"),
    ("-Y", "-Y", "The climbing face points along -Y"),
    ("+X", "+X", "The climbing face points along +X"),
    ("-X", "-X", "The climbing face points along -X"),
]


class ROCKWALL_OT_import_scan(Operator, ImportHelper):
    bl_idname = "rockwall.import_scan"
    bl_label = "Import Scan"
    bl_description = ("Bring in your own 3D scan (STL, OBJ or PLY) as a hold. It is turned so the chosen face "
                      "points out of the wall, scaled to millimetres, centred on the bolt position and set flat on the wall")
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.stl;*.obj;*.ply", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    axis: EnumProperty(name="Climbing face", items=AXIS_ITEMS, default="+Z",
                       description="Which direction in the scan points away from the wall, towards the climber")
    unit: EnumProperty(
        name="Scan units", default="AUTO",
        items=[("AUTO", "Auto-detect", "Guess from the size: up to 1.5 = metres, up to 40 = cm, otherwise mm"),
               ("MM", "Millimetres", ""), ("CM", "Centimetres", ""), ("M", "Metres", "")])
    max_tris: IntProperty(name="Max triangles", default=80000, min=0,
                          description="Heavy scans are simplified to about this many triangles (0 = keep all)")
    add_bolt: BoolProperty(name="Cut bolt hole", default=True,
                           description="Cut the bolt hole (size from the Hold panel) at the centre")

    def execute(self, context):
        pr = context.scene.rock_wall
        paths = []
        if self.files and self.directory:
            paths = [os.path.join(self.directory, f.name) for f in self.files if f.name]
        if not paths:
            paths = [self.filepath]
        col = get_collection(context, "RockHolds")
        made, errors, notes = [], [], []
        for path in paths:
            base = safe_key(os.path.splitext(os.path.basename(path))[0])
            key, n = base, 2
            while get_scan_template(key) is not None:
                key, n = f"{base}_{n}", n + 1
            try:
                mesh = make_scan_template(context, path, key, self.axis, self.unit, self.max_tris,
                                          float(pr.bolt_hole) if self.add_bolt else 0.0)
            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
                continue
            d = mesh["rh_dims"]
            notes.append(f"{key} {d[0]:.0f} x {d[1]:.0f} x {d[2]:.0f} mm")
            if self.add_bolt and pr.bolt_hole > 0 and float(mesh["rh_bolt"]) == 0.0:
                errors.append(f"{key}: the bolt hole could not be cut (try 'Add Bolt Hole to Scan' or lower Max triangles)")
            made.append(create_scan_hold(context, mesh, col))
        for e in errors:
            self.report({"WARNING"}, e)
        if not made:
            self.report({"ERROR"}, "No scan was imported")
            return {"CANCELLED"}
        pr.scan_pick = hold_params(made[-1])["scan"]
        _place_new(context, pr, made)
        _select_only(context, made)
        prepare_viewports(context)
        self.report({"INFO"}, "Imported: " + "; ".join(notes) +
                    ". If it looks upside-down or sideways, undo and change 'Climbing face'.")
        return {"FINISHED"}


class ROCKWALL_OT_add_scan(Operator):
    bl_idname = "rockwall.add_scan"
    bl_label = "Place Scan Copies"
    bl_description = "Add copies of the chosen scan to the wall (copies share one mesh, so they cost almost no memory)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(scan_templates()) > 0

    def execute(self, context):
        pr = context.scene.rock_wall
        mesh = get_scan_template(pr.scan_pick)
        if mesh is None:
            self.report({"WARNING"}, "Pick an imported scan first")
            return {"CANCELLED"}
        col = get_collection(context, "RockHolds")
        made = [create_scan_hold(context, mesh, col) for _ in range(pr.scan_count)]
        fails = _place_new(context, pr, made)
        _select_only(context, made)
        msg = f"Added {len(made)} x {mesh['rh_scan']}"
        if fails:
            msg += " (no free spot for some: wall is full)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class ROCKWALL_OT_scan_bolt(Operator):
    bl_idname = "rockwall.scan_bolt"
    bl_label = "Add Bolt Hole to Scan"
    bl_description = "Cut a bolt hole at the centre of the selected scans that have none (all copies get it)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.get("rh_hold") and hold_params(o).get("type") == "SCAN" for o in context.selected_objects)

    def execute(self, context):
        pr = context.scene.rock_wall
        dia = float(pr.bolt_hole)
        if dia <= 0:
            self.report({"WARNING"}, "Set a Bolt hole size above 0 in the Hold panel first")
            return {"CANCELLED"}
        seen, done, failed = set(), 0, 0
        for o in list(context.selected_objects):
            if not o.get("rh_hold") or hold_params(o).get("type") != "SCAN":
                continue
            m = o.data
            if m.name in seen or not m.get("rh_scan_template") or float(m.get("rh_bolt", 0.0)) > 0:
                continue
            seen.add(m.name)
            work = m.copy()
            tmp = _scratch_object(context, "rw_scan_tmp", work)
            try:
                ok = cut_bolt_hole(context, tmp, dia, float(m["rh_dims"][2]))
                newm = tmp.data
            finally:
                _drop_object(tmp)
            if not ok:
                bpy.data.meshes.remove(newm)
                failed += 1
                continue
            replace_template(m, newm)
            newm["rh_bolt"] = dia
            for ob in all_holds(context.scene):
                if ob.data == newm:
                    p = hold_params(ob)
                    p["bolt"] = dia
                    _tag_hold(ob, p, {"radius": float(newm["rh_radius"])})
            done += 1
        if failed:
            self.report({"WARNING"}, f"{failed} scan(s): the boolean cut failed (try a lower Max triangles when importing)")
        self.report({"INFO"}, f"Cut a {dia:.0f} mm bolt hole in {done} scan(s)")
        return {"FINISHED"}


class ROCKWALL_OT_remove_scan(Operator):
    bl_idname = "rockwall.remove_scan"
    bl_label = "Forget Scan"
    bl_description = "Remove the chosen scan from the library (only if no hold uses it)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(scan_templates()) > 0

    def execute(self, context):
        m = get_scan_template(context.scene.rock_wall.scan_pick)
        if m is None:
            return {"CANCELLED"}
        if any(o.data == m for o in bpy.data.objects):
            self.report({"WARNING"}, "Holds in the scene still use this scan: delete them first")
            return {"CANCELLED"}
        m.use_fake_user = False
        bpy.data.meshes.remove(m)
        return {"FINISHED"}


# ---- routes and tags -------------------------------------------------------------
class ROCKWALL_OT_route_add(Operator):
    bl_idname = "rockwall.route_add"
    bl_label = "Add Route"
    bl_description = "Add a new route to the list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pr = context.scene.rock_wall
        r = pr.routes.add()
        r.name = f"Route {len(pr.routes)}"
        r.color = ROUTE_DEFAULTS[(len(pr.routes) - 1) % len(ROUTE_DEFAULTS)]
        pr.route_index = len(pr.routes) - 1
        return {"FINISHED"}


class ROCKWALL_OT_route_remove(Operator):
    bl_idname = "rockwall.route_remove"
    bl_label = "Remove Route"
    bl_description = "Remove the selected route (its holds become unassigned)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        pr = context.scene.rock_wall
        return 0 <= pr.route_index < len(pr.routes)

    def execute(self, context):
        pr = context.scene.rock_wall
        i = pr.route_index
        for o in all_holds(context.scene):
            v = int(o.rh_route)
            if v == i + 1:
                o.rh_route = 0
            elif v > i + 1:
                o.rh_route = v - 1
        pr.routes.remove(i)
        pr.route_index = max(0, min(i, len(pr.routes) - 1))
        recolor_all(context.scene)
        return {"FINISHED"}


class ROCKWALL_OT_assign_route(Operator):
    bl_idname = "rockwall.assign_route"
    bl_label = "Assign Selected"
    bl_description = "Put the selected holds in the chosen route (use the 'None' button to take them out)"
    bl_options = {"REGISTER", "UNDO"}

    clear: BoolProperty(name="Unassign", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return any(o.get("rh_hold") for o in context.selected_objects)

    def execute(self, context):
        pr = context.scene.rock_wall
        if not self.clear and not (0 <= pr.route_index < len(pr.routes)):
            self.report({"WARNING"}, "Add a route first (the + button)")
            return {"CANCELLED"}
        n = 0
        for o in context.selected_objects:
            if o.get("rh_hold"):
                o.rh_route = 0 if self.clear else pr.route_index + 1
                n += 1
        self.report({"INFO"}, f"{n} hold(s) {'unassigned' if self.clear else 'added to ' + pr.routes[pr.route_index].name}")
        return {"FINISHED"}


class ROCKWALL_OT_set_role(Operator):
    bl_idname = "rockwall.set_role"
    bl_label = "Set Role"
    bl_description = "Mark the selected holds as start, hand, foot or finish"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(name="Role", items=ROLE_ITEMS, default="HAND", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return any(o.get("rh_hold") for o in context.selected_objects)

    def execute(self, context):
        for o in context.selected_objects:
            if o.get("rh_hold"):
                o.rh_role = self.role
        return {"FINISHED"}


class ROCKWALL_OT_route_view(Operator):
    bl_idname = "rockwall.route_view"
    bl_label = "Show Route"
    bl_description = "Hide every hold that is not in the chosen route (or show all holds again)"
    bl_options = {"REGISTER", "UNDO"}

    show_all: BoolProperty(name="Show all", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        pr = context.scene.rock_wall
        idx = pr.route_index + 1
        if not self.show_all and not (0 <= pr.route_index < len(pr.routes)):
            self.report({"WARNING"}, "Pick a route in the list first")
            return {"CANCELLED"}
        for o in all_holds(context.scene):
            try:
                o.hide_set(False if self.show_all else int(o.rh_route) != idx)
            except RuntimeError:
                pass
        return {"FINISHED"}


class ROCKWALL_OT_generate_route(Operator):
    bl_idname = "rockwall.generate_route"
    bl_label = "Generate Route"
    bl_description = ("Plan a route for the chosen grade with Rockform's hold generator: it makes start, hand, "
                      "foot and finish holds, turns each so its best grip faces the pull, rates them as placed "
                      "and puts them on the wall as a new route. Holds already on the wall stay")
    bl_options = {"REGISTER", "UNDO"}

    _proc = None
    _timer = None
    _lines = None

    def _start(self, context):
        pr = context.scene.rock_wall
        python = holdgen_python(pr)
        if not python or not os.path.isfile(python):
            raise RuntimeError("Set 'Rockform Python' to the python.exe in the Rockform checkout's .venv folder")
        wall = find_wall(context.scene) or build_wall(context, pr, frame=True)
        name = f"{pr.hg_grade} seed {pr.hg_seed}"
        if any(r.name == name for r in pr.routes):
            name += f" ({len(pr.routes) + 1})"
        cmd = holdgen_command(context, pr, wall, name, holdgen_out_dir(pr, name))
        self._proc = subprocess.Popen(cmd, cwd=cmd[-1], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                      encoding="utf-8", errors="replace",
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._lines = []
        pr.hg_status = f"Generating {name}..."

    def _read(self):
        for line in self._proc.stdout:
            self._lines.append(line.rstrip())

    def _finish(self, context):
        pr = context.scene.rock_wall
        layout = next((l[7:] for l in reversed(self._lines) if l.startswith("LAYOUT ")), None)
        if self._proc.returncode != 0 or layout is None:
            pr.hg_status = "Generation failed: " + (self._lines[-1] if self._lines else "no output")
            print("Rock Wall Builder: route generator output:\n" + "\n".join(self._lines))
            self.report({"ERROR"}, pr.hg_status + " (full output in the system console)")
            return {"CANCELLED"}
        with open(layout, "r", encoding="utf-8") as f:
            made, problems = add_layout_route(context, json.load(f), os.path.dirname(layout))
        for msg in problems[:8]:
            self.report({"WARNING"}, msg)
        summary = route_summary(pr, pr.route_index)
        pr.hg_status = (f"{pr.routes[pr.route_index].name}: {len(made)} holds, estimated "
                        f"{summary.get('estimated_grade', '?')} (score {summary.get('route_score', '?')})")
        pr.color_mode = "ROUTE"
        prepare_viewports(context)
        self.report({"INFO"}, pr.hg_status)
        return {"FINISHED"}

    def execute(self, context):
        try:
            self._start(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._read()
        self._proc.wait()
        return self._finish(context)

    def invoke(self, context, event):
        # Runs in the background so Blender stays responsive; the panel shows the generator's progress.
        try:
            self._start(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        threading.Thread(target=self._read, daemon=True).start()
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        pr = context.scene.rock_wall
        if self._lines:
            pr.hg_status = self._lines[-1][:90]
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
        if self._proc.poll() is None:
            return {"RUNNING_MODAL"}
        context.window_manager.event_timer_remove(self._timer)
        return self._finish(context)


# ---- layouts ----------------------------------------------------------------------
class ROCKWALL_OT_save_layout(Operator, ExportHelper):
    bl_idname = "rockwall.save_layout"
    bl_label = "Save Layout"
    bl_description = "Save the wall, routes and every hold's settings and position to a JSON file"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        data = layout_to_dict(context)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        self.report({"INFO"}, f"Saved {len(data['holds'])} holds to {os.path.basename(self.filepath)}")
        return {"FINISHED"}


class ROCKWALL_OT_load_layout(Operator, ImportHelper):
    bl_idname = "rockwall.load_layout"
    bl_label = "Load Layout"
    bl_description = "Rebuild the wall, routes and holds from a saved layout file"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    clear_existing: BoolProperty(name="Remove current holds first", default=True)

    def execute(self, context):
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            made, problems = apply_layout(context, data, self.clear_existing, os.path.dirname(self.filepath))
        except Exception as exc:
            self.report({"ERROR"}, f"Could not load layout: {exc}")
            return {"CANCELLED"}
        for msg in problems[:8]:
            self.report({"WARNING"}, msg)
        if len(problems) > 8:
            self.report({"WARNING"}, f"... and {len(problems) - 8} more problems")
        prepare_viewports(context)
        self.report({"INFO"}, f"Loaded {len(made)} holds" + (f" ({len(problems)} skipped)" if problems else ""))
        return {"FINISHED"}


# ---- setter's sheet and hold export ------------------------------------------------
class ROCKWALL_OT_export_sheet(Operator, ExportHelper):
    bl_idname = "rockwall.export_sheet"
    bl_label = "Setter's Sheet"
    bl_description = ("Write a printable HTML setter's sheet (wall map, parts list, every hold with its bolt grid "
                      "reference) and a matching CSV file")

    filename_ext = ".html"
    filter_glob: StringProperty(default="*.html", options={"HIDDEN"})
    title: StringProperty(name="Title", default="Setter's sheet")
    open_after: BoolProperty(name="Open in browser", default=True)

    def execute(self, context):
        try:
            wd, holds, routes = collect_sheet_data(context)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not holds:
            self.report({"WARNING"}, "No holds are on the wall yet")
            return {"CANCELLED"}
        page, csv_text = build_sheet(wd, holds, routes, self.title)
        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write(page)
        csv_path = os.path.splitext(self.filepath)[0] + ".csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write(csv_text)
        if self.open_after:
            try:
                import webbrowser
                webbrowser.open("file://" + self.filepath.replace("\\", "/"))
            except Exception:
                pass
        self.report({"INFO"}, f"Wrote {os.path.basename(self.filepath)} and {os.path.basename(csv_path)}")
        return {"FINISHED"}


class ROCKWALL_OT_export_holds(Operator):
    bl_idname = "rockwall.export_holds"
    bl_label = "Export Holds"
    bl_description = ("Write each hold to its own STL or OBJ file in millimetres, flat back down at the origin, "
                      "ready to slice or send to a mould maker, plus manifest.csv with quantities")

    directory: StringProperty(subtype="DIR_PATH")
    fmt: EnumProperty(name="Format", default="STL",
                      items=[("STL", "STL (binary)", "Best for slicers"), ("OBJ", "OBJ", "Text mesh format")])
    scope: EnumProperty(name="Holds", default="ALL",
                        items=[("ALL", "All holds", ""), ("SELECTED", "Selected", "")])
    unique: BoolProperty(name="One file per design", default=True,
                         description="Identical holds are written once; manifest.csv says how many to make")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        holds = all_holds(context.scene)
        if self.scope == "SELECTED":
            holds = [o for o in holds if o.select_get()]
        if not holds:
            self.report({"WARNING"}, "No holds to export")
            return {"CANCELLED"}
        os.makedirs(self.directory, exist_ok=True)
        groups = {}
        for i, o in enumerate(holds):
            groups.setdefault(hold_key(o) if self.unique else f"{i}:{o.name}", []).append(o)
        used, rows = set(), []
        for objs in groups.values():
            o = objs[0]
            stem, n = hold_file_stem(o), 2
            base = stem
            while stem in used:
                stem, n = f"{base}_{n}", n + 1
            used.add(stem)
            V, T = mesh_arrays(o.data)
            fname = stem + (".stl" if self.fmt == "STL" else ".obj")
            path = os.path.join(self.directory, fname)
            if self.fmt == "STL":
                with open(path, "wb") as f:
                    f.write(stl_bytes(V, T, stem))
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(obj_text(V, T, stem))
            P = V[T]
            vol = abs(float(np.einsum("ij,ij->i", P[:, 0], np.cross(P[:, 1], P[:, 2])).sum())) / 6.0 / 1000.0
            p = hold_params(o)
            rows.append([fname, len(objs), type_label(p.get("type", "")), round(p.get("w", 0)), round(p.get("h", 0)),
                         round(p.get("d", 0)), round(p.get("bolt", 0), 1), p.get("seed", ""), p.get("scan", ""),
                         o.get("rh_mfg", ""), round(vol, 1)])
        with open(os.path.join(self.directory, "manifest.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["file", "quantity", "type", "width_mm", "height_mm", "thickness_mm", "bolt_hole_mm",
                        "seed", "scan", "manufacturing_check", "volume_cm3"])
            w.writerows(rows)
        self.report({"INFO"}, f"Wrote {len(rows)} file(s) for {len(holds)} hold(s) to {self.directory}")
        return {"FINISHED"}


# ---- manufacturing ------------------------------------------------------------------
class ROCKWALL_OT_check_mfg(Operator):
    bl_idname = "rockwall.check_mfg"
    bl_label = "Check Manufacturing"
    bl_description = ("Check whether the holds can be 3D printed or cast. Every problem comes with the reason "
                      "and a suggested change")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pr = context.scene.rock_wall
        holds = all_holds(context.scene)
        if pr.mfg_scope == "SELECTED":
            holds = [o for o in holds if o.select_get()]
        if not holds:
            self.report({"WARNING"}, "No holds to check")
            return {"CANCELLED"}
        cache, rows = {}, []
        for o in holds:
            try:
                issues = check_hold(context, o, cache)
            except Exception as exc:
                self.report({"WARNING"}, f"{o.name}: check failed ({exc})")
                continue
            p = hold_params(o)
            label = "{} {:.0f}x{:.0f}x{:.0f} mm".format(type_label(p.get("type", "")), p.get("w", 0),
                                                         p.get("h", 0), p.get("d", 0))
            try:
                metrics = json.loads(o.get("rh_mfg_metrics", "{}"))
            except Exception:
                metrics = {}
            rows.append((o.name, label, o["rh_mfg"], issues, metrics))
        write_text(REPORT_NAME, build_report(rows, pr.mfg_method))
        n = {k: sum(1 for r in rows if r[2] == k) for k in ("OK", "WARN", "FAIL")}
        failing = [r[0] for r in rows if r[2] == "FAIL"]
        tail = (" Failing: " + ", ".join(failing[:5]) + (" ..." if len(failing) > 5 else "")) if failing else ""
        self.report({"INFO" if not n["FAIL"] else "WARNING"},
                    f"{len(rows)} checked: {n['OK']} OK, {n['WARN']} warnings, {n['FAIL']} fail.{tail} "
                    f"Details: Manufacturing panel, or the Text Editor > '{REPORT_NAME}'")
        return {"FINISHED"}


class ROCKWALL_OT_select_hold(Operator):
    bl_idname = "rockwall.select_hold"
    bl_label = "Select Hold"
    bl_description = "Select this hold and show it in the viewport"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None:
            self.report({"WARNING"}, "That hold no longer exists")
            return {"CANCELLED"}
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        try:
            bpy.ops.view3d.view_selected()
        except Exception:
            pass
        return {"FINISHED"}


class ROCKWALL_OT_select_failing(Operator):
    bl_idname = "rockwall.select_failing"
    bl_label = "Select Failing Holds"
    bl_description = "Select every hold that failed the manufacturing check (then you can fix or edit them together)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bad = [o for o in all_holds(context.scene) if o.get("rh_mfg") == "FAIL"]
        for o in context.selected_objects:
            o.select_set(False)
        for o in bad:
            o.select_set(True)
        if bad:
            context.view_layer.objects.active = bad[0]
        self.report({"INFO"}, f"Selected {len(bad)} failing hold(s)")
        return {"FINISHED"}


class ROCKWALL_OT_show_problems(Operator):
    bl_idname = "rockwall.show_problems"
    bl_label = "Show Problem Areas"
    bl_description = ("Colour the parts of the selected holds that make them hard to make: overhangs and undercuts "
                      "(from the direction each face points), sharp edges, holes in the surface and thin bolt areas")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pr = context.scene.rock_wall
        holds = [o for o in context.selected_objects if o.get("rh_hold")] or all_holds(context.scene)
        n = 0
        for o in holds:
            if hold_params(o).get("type") == "SCAN" and len(holds) > 20:
                continue
            if not o.get("rh_mfg") and pr.mfg_view == "PROBLEMS":
                check_hold(context, o)
            if show_problems(context, o, pr.mfg_view):
                n += 1
        self.report({"INFO"}, f"Marked problem areas on {n} of {len(holds)} hold(s)")
        return {"FINISHED"}


class ROCKWALL_OT_clear_problems(Operator):
    bl_idname = "rockwall.clear_problems"
    bl_label = "Remove Highlights"
    bl_description = "Remove the coloured problem markers from the holds"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        n = clear_overlays(context.scene)
        for o in all_holds(context.scene):
            if "rh_mfg_map" in o:
                del o["rh_mfg_map"]
        self.report({"INFO"}, f"Removed {n} highlight(s)")
        return {"FINISHED"}


class ROCKWALL_OT_apply_fixes(Operator):
    bl_idname = "rockwall.apply_fixes"
    bl_label = "Apply Suggested Fixes"
    bl_description = ("Change the settings of the selected generated holds the way the check suggests, rebuild "
                      "them and check again (scans cannot be changed this way)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.get("rh_hold") for o in context.selected_objects)

    def execute(self, context):
        fixed = still = skipped = 0
        for o in [o for o in context.selected_objects if o.get("rh_hold")]:
            p = hold_params(o)
            if p.get("type") == "SCAN":
                skipped += 1
                continue
            changed = False
            for _ in range(3):
                issues = check_hold(context, o)
                if not any(i["level"] in ("FAIL", "WARN") and i["params"] for i in issues):
                    break
                new_p, _prop = merge_fixes(p, [i for i in issues if i["level"] in ("FAIL", "WARN")])
                if new_p == p:
                    break
                rebuild_hold(context, o, new_p)
                p, changed = new_p, True
            status = o.get("rh_mfg") if not changed else mfg_status(check_hold(context, o))
            fixed += 1 if (changed and status != "FAIL") else 0
            still += 1 if status == "FAIL" else 0
        msg = f"Fixed {fixed} hold(s)"
        if still:
            msg += f"; {still} still fail (read the reasons in the panel: some need a bigger printer or a manual edit)"
        if skipped:
            msg += f"; {skipped} scan(s) skipped"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


# =============================================================================
# 7. Menu (sidebar panels)
# =============================================================================
TYPE_HELP = {k: d for k, _n, d in TYPE_ITEMS}


def hint(layout, context, text, icon="NONE"):
    """Greyed explanatory text that wraps to the sidebar width."""
    col = layout.column(align=True)
    col.active = False
    col.scale_y = 0.85
    for i, line in enumerate(textwrap.wrap(text, wrap_width(context)) or [""]):
        col.label(text=line, icon=icon if i == 0 else "NONE")


def next_step(context):
    """What the user should probably do next: (icon, text)."""
    scene = context.scene
    pr = scene.rock_wall
    wall = find_wall(scene)
    holds = all_holds(scene)
    if wall is None:
        return "LIGHT", "Step 1: set the wall size in panel 1, then press Build Wall (or press Quick Start)."
    if not holds:
        return "LIGHT", "Step 2: pick a hold type in panel 2 and press Generate Hold, or Generate Set in panel 3."
    if any(o.parent != wall for o in holds):
        return "LIGHT", "Some holds are not on the wall yet: press Auto-Arrange in panel 5, or drag them."
    if not pr.routes:
        return "LIGHT", "Optional: make a route in panel 6 and tag its holds, so the setter's sheet is organised."
    if not any(o.get("rh_mfg") for o in holds):
        return "LIGHT", "Check the holds can be made: panel 7, Check Manufacturing."
    return "CHECKMARK", "Looking good. Save the layout or print the setter's sheet in panel 8."


class ROCKWALL_OT_rotate(Operator):
    bl_idname = "rockwall.rotate"
    bl_label = "Rotate Holds"
    bl_description = ("Turn the selected holds about their bolt, either by an amount or to an exact angle. "
                      "Positive = counter-clockwise as you look at the wall")
    bl_options = {"REGISTER", "UNDO"}

    angle: FloatProperty(name="Angle (deg)", default=15.0, min=-360.0, max=360.0)
    absolute: BoolProperty(name="Set exact angle", default=False,
                           description="Set the orientation to the angle instead of adding to it")

    @classmethod
    def poll(cls, context):
        return any(o.get("rh_hold") for o in context.selected_objects)

    def execute(self, context):
        n = 0
        for o in context.selected_objects:
            if not o.get("rh_hold"):
                continue
            cur = degrees(o.rotation_euler.z)
            o.rotation_euler.z = radians(wrap_deg(self.angle if self.absolute else cur + self.angle))
            n += 1
        self.report({"INFO"}, f"Turned {n} hold(s)")
        return {"FINISHED"}


def draw_rotate_buttons(layout, context):
    """Rotate-selected controls (used by the Arrange and Selected Hold panels)."""
    p = context.scene.rock_wall
    step = SPIN_STEP
    box = layout.box()
    box.label(text="Rotate selected holds", icon="DRIVER_ROTATIONAL_DIFFERENCE")
    box.prop(p, "spin_all", slider=True)
    row = box.row(align=True)
    op = row.operator("rockwall.rotate", text=f"-{step:.0f}", icon="LOOP_BACK")
    op.angle, op.absolute = -step, False
    op = row.operator("rockwall.rotate", text=f"+{step:.0f}", icon="LOOP_FORWARDS")
    op.angle, op.absolute = step, False
    row = box.row(align=True)
    for a in (0, 90, 180, -90):
        op = row.operator("rockwall.rotate", text=f"{a}")
        op.angle, op.absolute = float(a), True


class ROCKWALL_OT_quick_start(Operator):
    bl_idname = "rockwall.quick_start"
    bl_label = "Quick Start"
    bl_description = ("Build the wall if there is none, then fill it with a set of different holds "
                      "(uses the current settings; each step can be redone from its own panel)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pr = context.scene.rock_wall
        if find_wall(context.scene) is None:
            build_wall(context, pr, frame=True)
        if all_holds(context.scene):
            bpy.ops.rockwall.auto_arrange()
        else:
            bpy.ops.rockwall.generate_set()
        return {"FINISHED"}


class ROCKWALL_OT_help(Operator):
    bl_idname = "rockwall.help"
    bl_label = "How It Works"
    bl_description = "Short guide to the workflow and the drag-arrange keys"

    def invoke(self, context, event):
        def draw(self_menu, ctx):
            lay = self_menu.layout
            for line in ("1  Wall: set size and angle, Build Wall",
                         "2  Hold: choose a type and sizes, Generate Hold",
                         "3  Seeds: same seed + settings = same rock; Shuffle for a new one",
                         "4  Scans: import your own STL / OBJ / PLY holds",
                         "5  Arrange: drag holds, or Auto-Arrange without overlaps",
                         "6  Tags: routes and start / hand / foot / finish roles",
                         "7  Manufacturing: check printing or casting, with reasons",
                         "8  Save layouts, setter's sheet, STL per hold",
                         "",
                         "Drag mode: click+drag moves a hold; R / wheel spins it 15 deg;",
                         "R / E / Q / wheel turn it 15 degrees, 0 = upright;",
                         "D duplicates, X deletes (cursor over a hold); G toggles bolt snap;",
                         "Esc / Enter / right-click finishes.",
                         "Hover any button or field for a tooltip."):
                lay.label(text=line)
        context.window_manager.popup_menu(draw, title="Rock Wall Builder", icon="QUESTION")
        return {"FINISHED"}


class ROCKWALL_UL_routes(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "color", text="")
        row.prop(item, "name", text="", emboss=False)
        n = sum(1 for o in all_holds(context.scene) if int(o.rh_route) == index + 1)
        row.label(text=f"{n} holds")


class _Sub:
    """Shared settings of the sub-panels."""
    bl_parent_id = "ROCKWALL_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Rock Wall"


class ROCKWALL_PT_main(Panel):
    bl_label = "Rock Wall Builder"
    bl_idname = "ROCKWALL_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Rock Wall"

    def draw(self, context):
        scene = context.scene
        lay = self.layout
        wall = find_wall(scene)
        holds = all_holds(scene)
        on = len(holds_on_wall(scene, wall)) if wall else 0

        row = lay.row(align=True)
        row.label(text="Wall built" if wall else "No wall yet", icon="CHECKMARK" if wall else "X")
        row.label(text=f"{len(holds)} holds ({on} on wall)", icon="MESH_ICOSPHERE")

        icon, text = next_step(context)
        box = lay.box()
        draw_wrapped(box, context, text, icon=icon)

        row = lay.row(align=True)
        row.scale_y = 1.3
        row.operator("rockwall.quick_start", icon="PLAY")
        row.operator("rockwall.clear_holds", text="Clear", icon="TRASH")
        row.operator("rockwall.help", text="", icon="QUESTION")


class ROCKWALL_PT_wall(_Sub, Panel):
    bl_label = "1. Wall"
    bl_idname = "ROCKWALL_PT_wall"

    def draw_header(self, context):
        self.layout.label(text="", icon="GRID")

    def draw(self, context):
        p = context.scene.rock_wall
        lay = self.layout
        hint(lay, context, "The wall updates live once it is built. Holds on it stay attached.")
        col = lay.column(align=True)
        col.label(text="Size")
        col.prop(p, "wall_width")
        col.prop(p, "wall_height")
        col.prop(p, "wall_angle")
        col = lay.column(align=True)
        col.label(text="Panels and bolt holes")
        col.prop(p, "wall_thickness")
        col.prop(p, "panel_width")
        col.prop(p, "panel_height")
        col.prop(p, "bolt_spacing")
        W, H = float(p.wall_width), float(p.wall_height)
        cols, rows = int(-(-W // p.panel_width)), int(-(-H // p.panel_height))
        us, vs = bolt_axes(W, H, float(p.bolt_spacing))
        hint(lay, context, f"{W * H / 1e6:.1f} m2, {cols * rows} panels ({cols} x {rows}), "
                           f"{len(us) * len(vs)} bolt holes. Angle {p.wall_angle:.0f} deg "
                           f"({'overhang' if p.wall_angle > 0 else 'vertical' if p.wall_angle == 0 else 'slab'}).",
             icon="INFO")
        lay.operator("rockwall.build_wall", text="Update Wall" if find_wall(context.scene) else "Build Wall",
                     icon="GRID")


class ROCKWALL_PT_hold(_Sub, Panel):
    bl_label = "2. Hold"
    bl_idname = "ROCKWALL_PT_hold"

    def draw_header(self, context):
        self.layout.label(text="", icon="MESH_ICOSPHERE")

    def draw(self, context):
        p = context.scene.rock_wall
        lay = self.layout
        lay.prop(p, "hold_category", expand=True)
        lay.prop(p, "hold_type", text="Type")
        hint(lay, context, TYPE_HELP.get(p.hold_type, ""))
        gym = category_of(p.hold_type) == "GYM"
        col = lay.column(align=True)
        col.label(text="Size (changing the type loads its usual size)")
        col.prop(p, "width")
        col.prop(p, "height")
        col.prop(p, "depth")
        if p.hold_type == "CRIMP":
            col = lay.column(align=True)
            col.label(text="Shape")
            col.prop(p, "crimp_edge")
            hint(lay, context, "Printing needs 1.5 mm or more, casting 2.5 mm or more.")
        elif p.hold_type in ("POCKET", "POCKET2"):
            col = lay.column(align=True)
            col.label(text="Shape")
            col.prop(p, "pocket_diameter")
            col.prop(p, "pocket_depth")
        elif p.hold_type == "UNDERCLING":
            hint(lay, context, "The shelf is below the lip: fingers go under it and pull up.")
        col = lay.column(align=True)
        col.label(text="Surface and mounting")
        if gym:
            hint(lay, context, "Gym holds are smooth and moulded. The seed only nudges their proportions a little.")
        else:
            col.prop(p, "roughness")
        col.prop(p, "bolt_hole")
        lay.prop(p, "color_mode", text="Colour")
        row = lay.row()
        row.scale_y = 1.3
        row.operator("rockwall.generate_hold", text="Generate Hold", icon="ADD")


class ROCKWALL_PT_seeds(_Sub, Panel):
    bl_label = "3. Seeds and Sets"
    bl_idname = "ROCKWALL_PT_seeds"

    def draw_header(self, context):
        self.layout.label(text="", icon="FILE_REFRESH")

    def draw(self, context):
        p = context.scene.rock_wall
        lay = self.layout
        hint(lay, context, "The seed is the rock's identity: the same seed and settings always give the same "
                           "hold. Shuffle for a new one.")
        row = lay.row(align=True)
        row.prop(p, "seed")
        row.operator("rockwall.shuffle_seed", text="", icon="FILE_REFRESH")
        row = lay.row(align=True)
        row.operator("rockwall.generate_hold", text="Generate", icon="ADD")
        op = row.operator("rockwall.generate_hold", text="Shuffle + Generate", icon="FILE_REFRESH")
        op.shuffle = True

        box = lay.box()
        box.label(text="Generate a set of holds", icon="GROUP")
        box.prop(p, "set_mode", expand=True)
        total = int(p.set_count)
        if p.set_mode == "PICK":
            hint(box, context, "Tick the holds you want and set how many of each.")
            for cat, title in (("NATURAL", "Natural rock"), ("GYM", "Gym (man-made)")):
                head = box.row(align=True)
                head.label(text=title)
                op = head.operator("rockwall.set_pick", text="All")
                op.category, op.value = cat, True
                op = head.operator("rockwall.set_pick", text="None")
                op.category, op.value = cat, False
                col = box.column(align=True)
                for key, label, _desc in TYPE_ITEMS:
                    if category_of(key) != cat:
                        continue
                    on = bool(getattr(p, f"set_on_{key}"))
                    row = col.row(align=True)
                    row.prop(p, f"set_on_{key}", text=label)
                    sub = row.row(align=True)
                    sub.active = on
                    sub.scale_x = 0.55
                    sub.prop(p, f"set_n_{key}", text="")
            total = set_total(p)
            box.label(text=f"Total: {total} holds", icon="INFO")
        else:
            box.prop(p, "set_count")
            box.prop(p, "set_types")
        box.prop(p, "seed_mode")
        box.prop(p, "size_var")
        box.prop(p, "place_on_wall")
        row = box.row()
        row.scale_y = 1.3
        row.enabled = total > 0
        row.operator("rockwall.generate_set", text=f"Generate {total} Holds", icon="ADD")
        lay.operator("rockwall.clear_holds", text="Clear Wall (delete all holds)", icon="TRASH")


class ROCKWALL_PT_scans(_Sub, Panel):
    bl_label = "4. Your Scans"
    bl_idname = "ROCKWALL_PT_scans"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="IMPORT")

    def draw(self, context):
        p = context.scene.rock_wall
        lay = self.layout
        hint(lay, context, "Import an STL, OBJ or PLY scan. If it lands sideways or upside-down, undo and change "
                           "'Climbing face' in the import options. The bolt hole size comes from panel 2.")
        lay.operator("rockwall.import_scan", icon="IMPORT")
        mats = scan_templates()
        if not mats:
            return
        box = lay.box()
        box.label(text=f"Scan library ({len(mats)})", icon="OUTLINER_COLLECTION")
        box.prop(p, "scan_pick", text="")
        m = get_scan_template(p.scan_pick)
        if m is not None:
            d = m["rh_dims"]
            hole = f"{float(m['rh_bolt']):.0f} mm" if float(m["rh_bolt"]) > 0 else "none"
            hint(box, context, f"{d[0]:.0f} x {d[1]:.0f} x {d[2]:.0f} mm, bolt hole: {hole}")
        row = box.row(align=True)
        row.prop(p, "scan_count")
        row.operator("rockwall.add_scan", icon="ADD")
        row = box.row(align=True)
        row.operator("rockwall.scan_bolt", icon="MESH_CIRCLE")
        row.operator("rockwall.remove_scan", text="Forget", icon="TRASH")
        box.prop(p, "scan_share")


class ROCKWALL_PT_arrange(_Sub, Panel):
    bl_label = "5. Arrange"
    bl_idname = "ROCKWALL_PT_arrange"

    def draw_header(self, context):
        self.layout.label(text="", icon="HAND")

    def draw(self, context):
        p = context.scene.rock_wall
        lay = self.layout
        row = lay.row()
        row.scale_y = 1.3
        row.operator("rockwall.arrange", icon="HAND")
        hint(lay, context, "Click and drag holds on the wall. E / R / Q or wheel: turn. 0: upright. "
                           "D: duplicate. X: delete. G: bolt snap. Esc: finish.")
        lay.prop(p, "snap_bolt")
        draw_rotate_buttons(lay, context)

        box = lay.box()
        box.label(text="Auto-arrange (no overlaps)", icon="GRID")
        box.prop(p, "layout_mode")
        box.prop(p, "arrange_scope")
        box.prop(p, "layout_seed")
        col = box.column(align=True)
        col.prop(p, "min_gap")
        col.prop(p, "edge_margin")
        box.prop(p, "spin_mode")
        if p.spin_mode == "FIXED":
            box.prop(p, "spin_angle", slider=True)
        row = box.row(align=True)
        row.operator("rockwall.auto_arrange", icon="GRID")
        op = row.operator("rockwall.auto_arrange", text="Shuffle", icon="FILE_REFRESH")
        op.shuffle = True
        lay.operator("rockwall.clear_holds", text="Clear Wall (delete all holds)", icon="TRASH")


class ROCKWALL_PT_tags(_Sub, Panel):
    bl_label = "6. Tags and Routes"
    bl_idname = "ROCKWALL_PT_tags"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="BOOKMARKS")

    def draw(self, context):
        p = context.scene.rock_wall
        lay = self.layout
        hint(lay, context, "1) Add a route with +. 2) Select holds in the viewport and press Add Selected. "
                           "3) Mark start, hand, foot and finish holds.")
        row = lay.row()
        row.template_list("ROCKWALL_UL_routes", "", p, "routes", p, "route_index", rows=3)
        col = row.column(align=True)
        col.operator("rockwall.route_add", text="", icon="ADD")
        col.operator("rockwall.route_remove", text="", icon="REMOVE")
        row = lay.row(align=True)
        row.operator("rockwall.assign_route", text="Add Selected to Route", icon="CHECKMARK")
        op = row.operator("rockwall.assign_route", text="", icon="X")
        op.clear = True
        row = lay.row(align=True)
        row.operator("rockwall.route_view", text="Only This Route", icon="HIDE_OFF")
        op = row.operator("rockwall.route_view", text="Show All")
        op.show_all = True

        box = lay.box()
        box.label(text="Role of the selected holds", icon="PINNED")
        row = box.row(align=True)
        for key, name, _desc in ROLE_ITEMS:
            op = row.operator("rockwall.set_role", text=name)
            op.role = key
        lay.prop(p, "color_mode", text="Colour holds by")


class ROCKWALL_PT_holdgen(_Sub, Panel):
    bl_label = "6b. Generate a Route"
    bl_idname = "ROCKWALL_PT_holdgen"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="SHADERFX")

    def draw(self, context):
        p = context.scene.rock_wall
        lay = self.layout
        wall = find_wall(context.scene)
        hint(lay, context, "Rockform's hold generator plans a whole route for the wall: it makes each hold, turns "
                           "it so its best grip faces the pull and rates it as placed. The route is added to the "
                           "list in panel 6; holds already on the wall are kept clear of.")
        col = lay.column(align=True)
        col.prop(p, "hg_grade")
        col.prop(p, "hg_seed")
        col.prop(p, "hg_moves")
        row = lay.row(align=True)
        row.prop(p, "hg_feet")
        row.prop(p, "hg_bolt")
        if wall is not None:
            lay.label(text=f"Rated for this wall: {float(wall['rh_angle']):.0f} deg", icon="ORIENTATION_GIMBAL")
        row = lay.row()
        row.scale_y = 1.3
        row.operator("rockwall.generate_route", icon="PLAY")
        if p.hg_status:
            draw_wrapped(lay.box(), context, p.hg_status, icon="INFO")

        summary = route_summary(p, p.route_index) if 0 <= p.route_index < len(p.routes) else {}
        if summary:
            box = lay.box()
            box.label(text=f"{p.routes[p.route_index].name}: asked {summary.get('requested_grade')}, "
                           f"estimate {summary.get('estimated_grade')}", icon="BOOKMARKS")
            box.label(text=f"Score {summary.get('route_score')} (target {summary.get('requested_score')}), "
                           f"{summary.get('hand_moves')} hand moves")
            if wall is not None and abs(float(summary.get("wall_angle_deg", 0.0)) - float(wall["rh_angle"])) > 0.5:
                box.label(text=f"Rated at {summary.get('wall_angle_deg'):.0f} deg: generate again", icon="ERROR")
            holds = sorted((o for o in all_holds(context.scene)
                            if int(o.rh_route) == p.route_index + 1 and o.get("rh_holdgen")),
                           key=lambda o: (o.rh_role == "FOOT", o.location.y))
            col = box.column(align=True)
            for o in holds:
                g = json.loads(o["rh_holdgen"])
                row = col.row(align=True)
                op = row.operator("rockwall.select_hold", text=f"{ROLE_NAMES[o.rh_role]}: {g['type']}", emboss=False)
                op.name = o.name
                row.label(text=f"{g['difficulty']:.1f}  {g['style'].replace('_', ' ')}")

        sub = lay.box().column(align=True)
        sub.label(text="Rockform", icon="FILE_SCRIPT")
        sub.prop(p, "hg_python", text="Python")
        sub.prop(p, "hg_out", text="Output")
        if not holdgen_python(p):
            sub.label(text="Python not found: set it above", icon="ERROR")


class ROCKWALL_PT_mfg(_Sub, Panel):
    bl_label = "7. Manufacturing"
    bl_idname = "ROCKWALL_PT_mfg"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="MODIFIER")

    def draw(self, context):
        p = context.scene.rock_wall
        lay = self.layout
        hint(lay, context, "Checks that a hold can really be made, and explains every problem with a suggested "
                           "fix. Green = OK, yellow = risky, red = will not work (use Colour holds by > "
                           "Manufacturing check).")
        lay.prop(p, "mfg_method", expand=True)
        if p.mfg_method == "PRINT":
            lay.prop(p, "bed_size")
        lay.prop(p, "mfg_scope", expand=True)
        lay.operator("rockwall.check_mfg", icon="VIEWZOOM")
        lay.operator("rockwall.apply_fixes", icon="MODIFIER")

        holds = all_holds(context.scene)
        n = {k: sum(1 for o in holds if o.get("rh_mfg") == k) for k in ("OK", "WARN", "FAIL")}
        if sum(n.values()):
            row = lay.row(align=True)
            row.label(text=f"OK {n['OK']}", icon="CHECKMARK")
            row.label(text=f"Risky {n['WARN']}", icon="ERROR")
            row.label(text=f"Fail {n['FAIL']}", icon="CANCEL")

            bad = [o for o in holds if o.get("rh_mfg") in ("FAIL", "WARN")]
            bad.sort(key=lambda o: (o.get("rh_mfg") != "FAIL", o.name))
            if bad:
                box = lay.box()
                box.label(text="Needs attention (click a name to select it)", icon="VIEWZOOM")
                for o in bad[:12]:
                    try:
                        titles = [i["title"] for i in json.loads(o.get("rh_mfg_issues", "[]"))
                                  if i["level"] == o["rh_mfg"]]
                    except Exception:
                        titles = []
                    col = box.column(align=True)
                    op = col.operator("rockwall.select_hold", text=o.name, icon=LEVEL_ICONS.get(o["rh_mfg"], "INFO"),
                                      emboss=o != context.active_object)
                    op.name = o.name
                    if titles:
                        draw_wrapped(col, context, "   " + "; ".join(titles))
                if len(bad) > 12:
                    box.label(text=f"... and {len(bad) - 12} more (see the report text)")
                if n["FAIL"]:
                    box.operator("rockwall.select_failing", icon="RESTRICT_SELECT_OFF")

        box = lay.box()
        box.label(text="Show problem areas on the holds", icon="SHADING_RENDERED")
        box.prop(p, "mfg_view", expand=True)
        box.prop(p, "mfg_highlight")
        row = box.row(align=True)
        row.operator("rockwall.show_problems", text="Show", icon="HIDE_OFF")
        row.operator("rockwall.clear_problems", text="Remove", icon="X")
        if p.mfg_view == "PROBLEMS":
            legend = [(k, v) for k, v in PROBLEM_CATS.items()]
            hint(box, context, "Colours come from each face's normal (the way it points) and from the angle "
                               "between neighbouring faces.")
        else:
            legend = [(k, v) for k, v in ANGLE_CATS.items()]
            lim = MFG_RULES[p.mfg_method]["overhang_deg"]
            hint(box, context, f"Every face coloured by how far it tips back toward the wall. The limit for "
                               f"this method is {lim:.0f} degrees.")
        for k, (label, rgb) in legend:
            box.label(text=("   " + label), icon="SEQUENCE_COLOR_0" + str({"OVERHANG_HI": 1, "OVERHANG_LO": 2, "SHARP": 5,
                      "OPEN": 7, "BOLT": 6, "A_UP": 4, "A_FINE": 3, "A_LO": 2, "A_HI": 1}[k]))

        o = context.active_object
        if o is None or not o.get("rh_hold") or not o.get("rh_mfg"):
            hint(lay, context, "Select a hold after checking to see its details here.")
            return
        method = o.get("rh_mfg_method", p.mfg_method)
        if method != p.mfg_method:
            lay.label(text=f"Last checked for {method.lower()}: check again", icon="INFO")
        box = lay.box()
        box.label(text=f"{o.name}: {o['rh_mfg']}", icon=LEVEL_ICONS.get(o["rh_mfg"], "INFO"))
        try:
            line = metrics_line(json.loads(o.get("rh_mfg_metrics", "{}")))
        except Exception:
            line = ""
        if line:
            draw_wrapped(box, context, "Measured: " + line)
        try:
            areas = json.loads(o.get("rh_mfg_map", "{}"))
        except Exception:
            areas = {}
        for label, a in areas.items():
            box.label(text=f"   marked: {label}, {a:.0f} mm2")
        lay.prop(p, "show_why")
        try:
            issues = json.loads(o["rh_mfg_issues"])
        except Exception:
            issues = []
        for it in issues:
            if it["level"] == "INFO" and not p.show_why:
                continue
            sub = box.box()
            sub.label(text=it["title"], icon=LEVEL_ICONS.get(it["level"], "INFO"))
            if p.show_why:
                draw_wrapped(sub, context, "Why: " + it["why"])
                draw_wrapped(sub, context, "Fix: " + it["fix"])
                if it.get("how"):
                    draw_wrapped(sub, context, "How measured: " + it["how"])
        if not issues:
            box.label(text="Nothing to report", icon="CHECKMARK")


class ROCKWALL_PT_files(_Sub, Panel):
    bl_label = "8. Layouts and Export"
    bl_idname = "ROCKWALL_PT_files"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="EXPORT")

    def draw(self, context):
        lay = self.layout
        box = lay.box()
        box.label(text="Save your work (.json)", icon="FILE_TICK")
        hint(box, context, "Keeps the wall, routes, roles and every hold, so you can come back to it.")
        row = box.row(align=True)
        row.operator("rockwall.save_layout", text="Save", icon="FILE_TICK")
        row.operator("rockwall.load_layout", text="Load", icon="FILE_FOLDER")
        box = lay.box()
        box.label(text="For the setters", icon="TEXT")
        hint(box, context, "A printable page with the wall map, parts list and where each hold bolts on.")
        box.operator("rockwall.export_sheet", text="Setter's Sheet (HTML + CSV)")
        box = lay.box()
        box.label(text="For the printer or mould maker", icon="EXPORT")
        hint(box, context, "One STL or OBJ per hold in millimetres, plus a manifest with quantities.")
        box.operator("rockwall.export_holds", text="Export Each Hold")


class ROCKWALL_PT_selected(_Sub, Panel):
    bl_label = "Selected Hold"
    bl_idname = "ROCKWALL_PT_selected"

    @classmethod
    def poll(cls, context):
        o = context.active_object
        return o is not None and bool(o.get("rh_hold"))

    def draw_header(self, context):
        self.layout.label(text="", icon="RESTRICT_SELECT_OFF")

    def draw(self, context):
        o = context.active_object
        lay = self.layout
        try:
            p = json.loads(o["rh_params"])
            if p["type"] == "SCAN":
                lay.label(text=f"Scan: {p.get('scan', o.name)}", icon="IMPORT")
            else:
                lay.label(text=f"{type_label(p['type'])}   seed {p['seed']}", icon="MESH_ICOSPHERE")
            lay.label(text=f"{p['w']:.0f} x {p['h']:.0f} x {p['d']:.0f} mm")
            if o.get("rh_holdgen"):
                g = json.loads(o["rh_holdgen"])
                lay.label(text=f"Generated {g['type']}: difficulty {g['difficulty']:.1f} as placed, "
                               f"{g['style'].replace('_', ' ')}", icon="SHADERFX")
        except Exception:
            lay.label(text=o.name)
        if o.parent is not None and o.parent.get("rh_wall"):
            col = lay.column(align=True)
            col.label(text="Position on the wall")
            col.prop(o, "rh_u")
            col.prop(o, "rh_v")
            col.prop(o, "rh_spin", slider=True)
        else:
            hint(lay, context, "Not on the wall yet: use Auto-Arrange or drag it.", icon="INFO")
        draw_rotate_buttons(lay, context)
        col = lay.column(align=True)
        col.label(text="Route and role")
        col.prop(o, "rh_role")
        row = col.row(align=True)
        row.prop(o, "rh_route")
        scene_p = context.scene.rock_wall
        if 1 <= int(o.rh_route) <= len(scene_p.routes):
            row.label(text=scene_p.routes[int(o.rh_route) - 1].name)
        if o.get("rh_mfg"):
            lay.label(text=f"Manufacturing: {o['rh_mfg']}  (see panel 7)", icon=LEVEL_ICONS.get(o["rh_mfg"], "INFO"))
        hp = hold_params(o)
        if hp.get("type") not in (None, "SCAN"):
            box = lay.box()
            box.label(text="Edit this hold", icon="MODIFIER")
            nsel = len(edit_targets(o))
            if nsel > 1:
                box.label(text=f"Changes apply to all {nsel} selected holds", icon="INFO")
            else:
                hint(box, context, "Change anything below and this hold rebuilds at once. "
                                   "Its place, turn, route and role stay.")
            box.prop(o, "rh_e_type", text="Type")
            col = box.column(align=True)
            col.prop(o, "rh_e_w")
            col.prop(o, "rh_e_h")
            col.prop(o, "rh_e_d")
            if hp["type"] == "CRIMP":
                box.prop(o, "rh_e_crimp")
            elif hp["type"] in ("POCKET", "POCKET2"):
                col = box.column(align=True)
                col.prop(o, "rh_e_pdia")
                col.prop(o, "rh_e_pdep")
            if not TYPE_PRESETS[hp["type"]].get("gym"):
                box.prop(o, "rh_e_rough")
            row = box.row(align=True)
            row.prop(o, "rh_e_seed")
            op = row.operator("rockwall.update_selected", text="", icon="FILE_REFRESH")
            op.reroll = True
            box.prop(o, "rh_e_bolt")
            row = lay.row(align=True)
            row.operator("rockwall.hold_to_panel", text="Copy to Panel 2", icon="COPYDOWN")
            op = row.operator("rockwall.update_selected", text="Use Panel 2 Settings")
            op.reroll = False
        elif hp.get("type") == "SCAN":
            hint(lay, context, "Scanned holds are fixed meshes, so only their place, turn, route and role can change. "
                               "Use the Scans panel to replace the scan itself.", icon="INFO")


# =============================================================================
# 8. Registration
# =============================================================================
CLASSES = (
    RockWallRoute,
    RockWallProps,
    ROCKWALL_OT_build_wall,
    ROCKWALL_OT_shuffle_seed,
    ROCKWALL_OT_generate_hold,
    ROCKWALL_OT_generate_set,
    ROCKWALL_OT_update_selected,
    ROCKWALL_OT_hold_to_panel,
    ROCKWALL_OT_auto_arrange,
    ROCKWALL_OT_clear_holds,
    ROCKWALL_OT_arrange,
    ROCKWALL_OT_import_scan,
    ROCKWALL_OT_add_scan,
    ROCKWALL_OT_scan_bolt,
    ROCKWALL_OT_remove_scan,
    ROCKWALL_OT_route_add,
    ROCKWALL_OT_route_remove,
    ROCKWALL_OT_assign_route,
    ROCKWALL_OT_set_role,
    ROCKWALL_OT_route_view,
    ROCKWALL_OT_generate_route,
    ROCKWALL_OT_save_layout,
    ROCKWALL_OT_load_layout,
    ROCKWALL_OT_export_sheet,
    ROCKWALL_OT_export_holds,
    ROCKWALL_OT_check_mfg,
    ROCKWALL_OT_apply_fixes,
    ROCKWALL_OT_select_hold,
    ROCKWALL_OT_select_failing,
    ROCKWALL_OT_show_problems,
    ROCKWALL_OT_clear_problems,
    ROCKWALL_OT_rotate,
    ROCKWALL_OT_set_pick,
    ROCKWALL_OT_quick_start,
    ROCKWALL_OT_help,
    ROCKWALL_UL_routes,
    ROCKWALL_PT_main,
    ROCKWALL_PT_wall,
    ROCKWALL_PT_hold,
    ROCKWALL_PT_seeds,
    ROCKWALL_PT_scans,
    ROCKWALL_PT_arrange,
    ROCKWALL_PT_tags,
    ROCKWALL_PT_holdgen,
    ROCKWALL_PT_mfg,
    ROCKWALL_PT_files,
    ROCKWALL_PT_selected,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.rock_wall = PointerProperty(type=RockWallProps)
    bpy.types.Object.rh_u = FloatProperty(name="Across wall (mm)", get=_get_u, set=_set_u)
    bpy.types.Object.rh_v = FloatProperty(name="Up wall (mm)", get=_get_v, set=_set_v)
    bpy.types.Object.rh_spin = FloatProperty(
        name="Orientation (deg)", get=_get_spin, set=_set_spin, min=-180.0, max=180.0,
        description="Turn of the hold about its bolt. 0 = as generated, positive = counter-clockwise "
                    "as you look at the wall")
    bpy.types.Object.rh_e_type = EnumProperty(
        name="Type", items=TYPE_ITEMS, get=_get_edit_type, set=_set_edit_type,
        description="Swap this hold for another type. It keeps its place, turn, route and role")
    bpy.types.Object.rh_e_w = _edit_prop("w", 160.0, name="Width (mm)", min=15.0, max=600.0,
                                         description="Hold size across the wall, in mm")
    bpy.types.Object.rh_e_h = _edit_prop("h", 110.0, name="Height (mm)", min=15.0, max=600.0,
                                         description="Hold size up the wall, in mm")
    bpy.types.Object.rh_e_d = _edit_prop("d", 70.0, name="Thickness (mm)", min=5.0, max=300.0,
                                         description="How far the hold sticks out of the wall")
    bpy.types.Object.rh_e_rough = _edit_prop("rough", 0.3, name="Roughness", min=0.0, max=1.0,
                                             description="How lumpy and natural the surface is")
    bpy.types.Object.rh_e_seed = _edit_prop("seed", 1, is_int=True, name="Seed", min=0, max=99999,
                                            description="Same seed + same settings = same shape")
    bpy.types.Object.rh_e_crimp = _edit_prop("crimp", 6.0, name="Edge depth (mm)", min=1.0, max=30.0,
                                             description="Depth of the flat crimp edge")
    bpy.types.Object.rh_e_pdia = _edit_prop("pocket_dia", 30.0, name="Pocket diameter (mm)", min=10.0, max=80.0,
                                            description="Width of the finger pocket in mm")
    bpy.types.Object.rh_e_pdep = _edit_prop("pocket_depth", 25.0, name="Pocket depth (mm)", min=5.0, max=60.0,
                                            description="How deep the pocket is pressed in, in mm")
    bpy.types.Object.rh_e_bolt = _edit_prop("bolt", 12.0, name="Bolt hole (mm)", min=0.0, max=30.0,
                                            description="Mounting hole diameter, 0 for none")
    bpy.types.Object.rh_role = EnumProperty(name="Role", items=ROLE_ITEMS, default="NONE", update=_tag_changed)
    bpy.types.Object.rh_route = IntProperty(name="Route", description="Number of the route this hold is in (0 = none)",
                                            default=0, min=0, update=_tag_changed)


def unregister():
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for attr in ("rock_wall",):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for attr in ("rh_u", "rh_v", "rh_spin", "rh_role", "rh_route", "rh_e_type", "rh_e_w", "rh_e_h", "rh_e_d",
                 "rh_e_rough", "rh_e_seed", "rh_e_crimp", "rh_e_pdia", "rh_e_pdep", "rh_e_bolt"):
        if hasattr(bpy.types.Object, attr):
            delattr(bpy.types.Object, attr)


if __name__ == "__main__":
    unregister()          # safe to re-run the script
    register()
    print("Rock Wall Builder registered")
"""The hold generator and route setter as plain functions for the browser app (web/), which runs them in
Pyodide. Results are dicts of plain values and NumPy arrays (meshes as float32 vertices and uint32 faces).
Nothing here imports browser modules, so the desktop tests exercise the same code.

State lives in this module: holds shown in the Hold view, the routes on the wall, and the hold library. In the
browser the library folder is /library, which the worker keeps in the browser's storage (IndexedDB).
"""
from __future__ import annotations

from dataclasses import asdict
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile
import numpy as np
from . import shapes
from .generate import (HoldRequest, generate, from_profile, parse_description, measure, best_mode, pull_range,
                       GRAB_STYLES, GRAB_NAMES)
from . import route as R
from .library import AXES, UNITS, USES, HoldLibrary, default_folder

HOLDS = {}          # hold id -> GeneratedHold shown in the Hold view
ROUTES = {}         # route id -> dict(name, color, holds, summary, wall)
LIBRARY = {}        # "library" -> the HoldLibrary, opened on first use
_next = [0]


def _id(prefix):
    _next[0] += 1
    return f"{prefix}{_next[0]}"


def _mesh(mesh, origin=(0., 0.)):
    return dict(vertices=np.ascontiguousarray(mesh.vertices - [origin[0], origin[1], 0.], np.float32).ravel(),
                faces=np.ascontiguousarray(mesh.faces, np.uint32).ravel())


def info():
    """Choices the page offers."""
    return dict(types=list(shapes.HOLD_TYPES), info=dict(shapes.HOLD_INFO), sizes=dict(shapes.SIZES),
                grades={g: dict(difficulty=d, move_mm=m) for g, (d, m, _f) in R.GRADES.items()},
                styles={k: dict(angle=a, name=GRAB_NAMES[k]) for k, a in GRAB_STYLES.items()},
                route_colors=[list(c) for c in R.ROUTE_COLORS], role_colors=dict(R.ROLE_COLORS),
                library_uses=list(USES), units=list(UNITS), axes=list(AXES))


# ---- Hold view ----------------------------------------------------------------------------
def _hold(hold, hold_id, rotation=0., pull_dir=(0., 0.)):
    pull = hold.pull if rotation == 0 else pull_range(hold.mesh, hold.request, rotation_deg=rotation)
    r = hold.request
    return dict(id=hold_id, type=r.hold_type, seed=str(r.seed), wall_angle=r.wall_angle, size=r.size,
                target=hold.target, difficulty=hold.difficulty, achievable=list(hold.achievable), notes=hold.notes,
                holding_area=hold.holding_area_mm2, extents=hold.mesh.extents.tolist(),
                volume_cm3=hold.mesh.volume / 1000., fingers=hold.params.get("fingers"),
                pocket_depth=hold.params.get("pocket_depth"), rotation=rotation, pull=pull,
                pull_dir=list(pull_dir), mesh=_mesh(hold.mesh), drilled=bool(getattr(hold, "hole_mm", 0.)),
                library=hold.params.get("library", ""), name=hold.params.get("name", ""),
                profile=hold.params.get("profile"))


def generate_hold(spec):
    """spec: type, difficulty (0-10), seed, wall_angle, size (body scale); or description (+ seed)."""
    if spec.get("description"):
        request = parse_description(spec["description"], seed=spec.get("seed") or 0)
    else:
        request = HoldRequest(spec["type"], float(spec["difficulty"]), None, spec.get("seed") or 0,
                              float(spec.get("wall_angle", 0.)), size=float(spec.get("size", 1.)))
    hold = generate(request)
    hold_id = _id("h")
    HOLDS[hold_id] = hold
    return _hold(hold, hold_id, float(spec.get("rotation", 0.)))


def hold_profile(hold_id):
    """The side profile the editor starts from: a custom hold's own, or a generated hold's middle section."""
    hold = HOLDS[hold_id]
    p = hold.params
    if "profile" in p:
        return dict(p["profile"], type=hold.request.hold_type)
    if "hardness" not in p:
        raise ValueError("This hold was not generated, so it has no profile to edit. Start from a shape instead.")
    return shapes.section(p["type"], p["hardness"], p["seed"], p["size"], p.get("fingers"))


def shape_profile(hold_type, seed=0):
    """A medium hold of this type's middle section, to start the profile editor from."""
    return shapes.section(hold_type, .5, seed or 0)


def custom_hold(spec):
    """spec: points ([y, z, corner radius] from the bottom wall corner to the top one), width, outline,
    texture, type (how it is gripped), seed, wall_angle, rotation. Lofts and rates the edited profile."""
    request = HoldRequest(spec["type"], None, None, spec.get("seed") or 0, float(spec.get("wall_angle", 0.)))
    hold = from_profile(request, [[float(v) for v in p] for p in spec["points"]], float(spec["width"]),
                        float(spec.get("outline", 2.5)), float(spec.get("texture", .9)))
    hold_id = _id("h")
    HOLDS[hold_id] = hold
    return _hold(hold, hold_id, float(spec.get("rotation", 0.)))


def rotate_hold(hold_id, rotation):
    """Re-rate the same shape turned on the wall (no regeneration)."""
    return _hold(HOLDS[hold_id], hold_id, float(rotation))


def grip(hold_id, around, outward, rotation, overlay="grip"):
    """Per-face weights for one pull ('grip': every surface, 'scored': one hand's counted band) and its rating."""
    hold = HOLDS[hold_id]
    area, difficulty, _w = measure(hold.mesh, hold.request, around, outward, rotation)
    weights = (hold.grip_map(around, outward, rotation) if overlay == "grip"
               else hold.counted_map(around, outward, rotation))
    mode = (best_mode(hold.mesh, hold.request, around, outward, rotation)
            if hold.request.hold_type == "pinch" else None)
    return dict(weights=np.ascontiguousarray(weights, np.float32), difficulty=float(difficulty), area=float(area),
                mode=mode)


def _zip_folder(folder):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(folder).rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder).as_posix())
    return np.frombuffer(buffer.getvalue(), np.uint8)


def export_hold(hold_id):
    """ZIP with the hold's STL and JSON report. Returns (file name, bytes)."""
    hold = HOLDS[hold_id]
    stem = f"hold_{hold.request.hold_type}_d{hold.difficulty:.1f}_{hold.request.seed}"
    folder = Path(tempfile.mkdtemp())
    try:
        hold.mesh.export(folder / f"{stem}.stl")
        report = hold.report(with_pole_figure=False) | dict(solid_watertight=bool(hold.mesh.is_watertight))
        (folder / f"{stem}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return f"{stem}.zip", _zip_folder(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ---- Route view ---------------------------------------------------------------------------
def _wall(spec):
    return R.Wall(float(spec["width"]), float(spec["height"]), float(spec["angle"]), float(spec["bolt_spacing"]))


def _placed(h, index, with_mesh):
    out = dict(index=index, role=h.role, u=h.u, v=h.v, spin=h.spin, pull_deg=h.pull_deg, type=h.hold_type,
               seed=h.seed, difficulty=h.difficulty, radius=h.radius, size=list(h.size_mm), style=h.style,
               hole_mm=h.hole_mm, moved=h.moved, library=h.library, name=h.name)
    if with_mesh:
        out["mesh"] = _mesh(h.hold.mesh, h.hold.hole)       # origin on the bolt, as placed on the wall
    return out


def _route(route_id, with_mesh=False):
    r = ROUTES[route_id]
    return dict(id=route_id, name=r["name"], color=list(r["color"]), summary=r["summary"],
                holds=[_placed(h, i, with_mesh) for i, h in enumerate(r["holds"])])


def problems():
    """Placement warnings over every route: [route id, hold index, message]."""
    wall = next(iter(ROUTES.values()))["wall"] if ROUTES else None
    if wall is None:
        return []
    found = R.placement_problems([r["holds"] for r in ROUTES.values()], wall)
    return [[rid, i, found[id(h)]] for rid, r in ROUTES.items() for i, h in enumerate(r["holds"]) if id(h) in found]


def plan(spec, progress=print):
    """spec: wall (width, height, angle, bolt_spacing), grade, seed, moves (0 = to the top), feet, bolt.
    Plans a route around the routes already on the wall and returns it with meshes."""
    wall = _wall(spec["wall"])
    if any(abs(r["wall"].angle - wall.angle) > .5 or r["wall"].width != wall.width or r["wall"].height != wall.height
           for r in ROUTES.values()):
        raise ValueError("Clear the wall first: its routes were planned for another wall.")
    avoid = [(h.u, h.v, h.radius) for r in ROUTES.values() for h in r["holds"]]
    seed = str(spec.get("seed") or "0")
    use = spec.get("library") or "off"
    holds, summary = R.plan_route(spec["grade"], wall, seed, int(spec.get("moves") or 0) or None, avoid,
                                  bool(spec.get("feet", True)), float(spec.get("bolt", 10.5)), progress=progress,
                                  library=_library() if use != "off" else None, use_library=use)
    name = f"{spec['grade']} seed {seed}"
    if any(r["name"] == name for r in ROUTES.values()):
        name += f" ({len(ROUTES) + 1})"
    used = {tuple(r["color"]) for r in ROUTES.values()}
    color = next((c for c in R.ROUTE_COLORS if tuple(c) not in used), R.ROUTE_COLORS[len(ROUTES) % len(R.ROUTE_COLORS)])
    route_id = _id("r")
    ROUTES[route_id] = dict(name=name, color=color, holds=holds, summary=summary, wall=wall)
    return dict(route=_route(route_id, with_mesh=True), problems=problems())


def adjust(route_id, index, u, v, spin, moved=True, face=False):
    """Move and/or turn one hold (u, v snapped to the bolt grid), then re-rate its route. With face=True the
    hold is turned so its best grip faces its pull. Returns the route (no meshes) and every placement warning."""
    r = ROUTES[route_id]
    h = r["holds"][int(index)]
    h.u, h.v = r["wall"].snap(float(u), float(v))
    h.spin = (float(spin) + 180.) % 360. - 180.
    h.moved = bool(moved)
    R.rerate(r["holds"], r["summary"], r["wall"])
    if face:
        h.spin = R.face_the_pull(h)
        R.rerate(r["holds"], r["summary"], r["wall"])
    return dict(route=_route(route_id), problems=problems())


def remove_route(route_id):
    ROUTES.pop(route_id, None)
    return problems()


def clear_routes():
    ROUTES.clear()


def inspect(route_id, index):
    """Open a placed hold in the Hold view: drilled, turned and pulled as the route uses it."""
    h = ROUTES[route_id]["holds"][int(index)]
    hold_id = _id("h")
    HOLDS[hold_id] = h.hold
    return _hold(h.hold, hold_id, h.spin, (h.pull_deg, 0.))


def export_wall():
    """ZIP of every route (STLs, reports, route.json, route.svg) and the wall's layout.json, with file paths
    relative to it. Returns (file name, bytes)."""
    if not ROUTES:
        raise ValueError("Add a route first.")
    wall = next(iter(ROUTES.values()))["wall"]
    folder = Path(tempfile.mkdtemp())
    try:
        R.write_wall([(r["name"], r["color"], r["holds"], r["summary"]) for r in ROUTES.values()], wall, folder)
        return f"wall_{len(ROUTES)}_routes.zip", _zip_folder(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def bolt_grid(spec):
    us, vs = _wall(spec).bolt_axes()
    return dict(us=np.asarray(us, np.float32), vs=np.asarray(vs, np.float32))


def hold_icon(hold_type, size=(76, 54)):
    """SVG side section of a typical hold of this type (pinch: from above), as the desktop app's icons."""
    mesh, _ = shapes.build(hold_type, .35, seed=0)
    top = hold_type == "pinch"
    # Slice with plain NumPy (no path/graph libraries): the triangles crossing the plane, as segments.
    axis = 1 if top else 0
    tri = mesh.triangles
    d = tri[:, :, axis]
    segments = []
    for t, dist in zip(tri, d):
        signs = np.sign(dist)
        if signs.max() <= 0 or signs.min() >= 0:
            continue
        pts = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            if (dist[i] > 0) != (dist[j] > 0):
                f = dist[i] / (dist[i] - dist[j])
                pts.append(t[i] + f * (t[j] - t[i]))
        if len(pts) == 2:
            segments.append(pts)
    seg = np.array(segments)
    pts = seg[:, :, [0, 2]] if top else seg[:, :, [2, 1]]
    lo, hi = pts.reshape(-1, 2).min(axis=0), pts.reshape(-1, 2).max(axis=0)
    w, h = size
    scale = (min(w, h) - 12) / float((hi - lo).max())
    ox = (w - (hi[0] - lo[0]) * scale) / 2 if top else 10
    oy = (h + (hi[1] - lo[1]) * scale) / 2

    def xy(p):
        return ox + (p[0] - lo[0]) * scale, oy - (p[1] - lo[1]) * scale

    lines = "".join(f'<line x1="{xy(a)[0]:.1f}" y1="{xy(a)[1]:.1f}" x2="{xy(b)[0]:.1f}" y2="{xy(b)[1]:.1f}"/>'
                    for a, b in pts)
    wall = (f'<line x1="4" y1="{oy + 1:.1f}" x2="{w - 4}" y2="{oy + 1:.1f}"/>' if top
            else f'<line x1="{ox - 1:.1f}" y1="3" x2="{ox - 1:.1f}" y2="{h - 3}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<g stroke="#7f9796" stroke-width="2">{wall}</g>'
            f'<g stroke="#9edbc1" stroke-width="2.2" stroke-linecap="round">{lines}</g></svg>')


# ---- Hold library -------------------------------------------------------------------------
def _bytes(data):
    """File contents from the page (a JavaScript Uint8Array under Pyodide) or bytes."""
    return data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)


def _library():
    if "library" not in LIBRARY:
        LIBRARY["library"] = HoldLibrary("/library" if sys.platform == "emscripten" else default_folder())
    return LIBRARY["library"]


def library_list():
    return [asdict(h) for h in _library().holds]


def library_import(name, data, options):
    """Add a 3D model file. options: type, units ('' = from the file, else mm), axis, turn, keep_hole.
    Returns the new record."""
    entry = _library().import_model(str(name), _bytes(data), options["type"], units=options.get("units") or None,
                                    axis=options.get("axis") or "auto", turn_deg=float(options.get("turn", 0.)),
                                    hole="detect" if options.get("keep_hole", True) else "drill")
    return asdict(entry)


def library_save(hold_id, name=""):
    """Save a hold from the Hold view."""
    return asdict(_library().save_generated(HOLDS[hold_id], name or None))


def library_rename(hold_id, name):
    _library().rename(hold_id, name)
    return library_list()


def library_retype(hold_id, hold_type):
    _library().set_type(hold_id, hold_type)
    return library_list()


def library_remove(hold_id):
    _library().remove(hold_id)
    return library_list()


def library_show(hold_id, wall_angle):
    """Open a library hold in the Hold view, undrilled, rated on `wall_angle`."""
    hold = _library().rated(hold_id, float(wall_angle))
    view_id = _id("h")
    HOLDS[view_id] = hold
    return _hold(hold, view_id)


def library_export():
    """The library as a ZIP. Returns (file name, bytes)."""
    return "hold-library.zip", np.frombuffer(_library().export_zip(), np.uint8)


def library_merge(data):
    """Add every hold in a library ZIP. Returns how many were added."""
    return len(_library().merge_zip(_bytes(data)))

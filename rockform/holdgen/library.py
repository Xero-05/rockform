"""A library of saved holds: 3D models imported from STL, OBJ, PLY, OFF or GLB files, and holds saved from
the generator. Routes can use them instead of generating every hold.

A library is a folder:

    library.json        index: one record per hold (name, type, size, hole, rating on a vertical wall)
    holds/<id>.stl      the hold in holdgen's frame: millimetres, X across the wall, Y up, Z out, flat back on Z = 0

Imported models are scaled to millimetres, turned so their flat back lies on the wall and centred on their
footprint. A model that already has a bolt hole keeps it (the hole is found by casting rays through the
footprint); one without is drilled for each route like a generated hold. Ratings depend on the wall, so a
library hold is rated again for the wall it is placed on. Nothing here imports Qt or browser modules: the
desktop app, the command line and the browser app share it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from datetime import date
import io
import json
import math
import os
from pathlib import Path
import re
import zipfile
import numpy as np
import trimesh
from ..model import GeometryError
from . import shapes
from .drill import drill, top_heights
from .generate import GeneratedHold, HoldRequest, measure, pull_range

LIBRARY_FORMAT, LIBRARY_VERSION = "rockform_hold_library", 1
MODEL_TYPES = (".stl", ".obj", ".ply", ".off", ".glb", ".gltf")
UNITS = {"mm": 1., "cm": 10., "m": 1000., "in": 25.4}
AXES = ("auto", "+Z", "-Z", "+Y", "-Y", "+X", "-X")
# The model's direction that ends up pointing up the wall when its out-of-wall axis is known. A Y-up model
# lying on its back (glTF, many OBJ exports) has its top toward -Z, as seen from above in Blender.
_UP_FOR = {"+Z": "+Y", "-Z": "+Y", "+X": "+Y", "-X": "+Y", "+Y": "-Z", "-Y": "+Z"}
USES = ("off", "prefer", "only")     # how a route uses the library: not at all, when close to the target, always
MATCH = 1.0         # "prefer": a library hold is used when it lands within this of the difficulty asked for
BIG_MESH = 150_000  # more triangles than this rate slowly; say so on import


def default_folder():
    """Where the desktop app keeps its library: ROCKFORM_HOLD_LIBRARY, else the user's application data."""
    if os.environ.get("ROCKFORM_HOLD_LIBRARY"):
        return Path(os.environ["ROCKFORM_HOLD_LIBRARY"])
    base = os.environ.get("APPDATA") or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "Rockform" / "hold-library"


@dataclass
class LibraryHold:
    id: str
    name: str
    type: str                     # rated as this hold type: a pinch is squeezed, every other type hung from
    source: str = "imported"      # imported or generated
    file: str = ""                # relative to library.json
    original: str = ""            # file name it was imported from, or the generator settings it came from
    size_mm: list = field(default_factory=list)
    volume_cm3: float = 0.
    triangles: int = 0
    hole: list | None = None      # (x, y) of a bolt hole already through it, mm; None = drilled when placed
    hole_mm: float = 0.           # that hole's diameter
    difficulty: float = 0.        # easiest down-pull on a vertical wall, turned to face it (as a route would)
    difficulty_range: list = field(default_factory=list)   # within 30 degrees of down, on a vertical wall
    best_angle_deg: float = 0.
    watertight: bool = True
    added: str = ""
    notes: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, record):
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in record.items() if k in known})


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "hold"


def declared_units(name, data):
    """Units an OBJ declares in a '# units: ...' comment (Scaniverse writes one), else ''."""
    if not str(name).lower().endswith(".obj"):
        return ""
    head = data[:20000].decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else ""
    m = re.search(r"^\s*#\s*units?\s*:\s*(millimet(?:er|re)s?|mm|centimet(?:er|re)s?|cm|met(?:er|re)s?|m|inch(?:es)?|in)\b",
                  head, re.I | re.M)
    if not m:
        return ""
    word = m[1].lower()
    return "mm" if word.startswith(("mm", "milli")) else "cm" if word.startswith(("cm", "centi")) else \
        "in" if word.startswith("in") else "m"


def load_model(name, data):
    """A triangle mesh from a model file's bytes; scenes (GLB, OBJ with several parts) are merged."""
    suffix = Path(name).suffix.lower()
    if suffix not in MODEL_TYPES:
        raise GeometryError(f"Choose a 3D model file: {', '.join(MODEL_TYPES)}.")
    try:
        loaded = trimesh.load(io.BytesIO(bytes(data)), file_type=suffix[1:], force="mesh", process=False)
    except Exception as error:
        raise GeometryError(f"Could not read {name}: {error}") from error
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise GeometryError(f"{name} contains no triangle mesh (point clouds and splats cannot be used).")
    mesh = trimesh.Trimesh(np.asarray(loaded.vertices, float), np.asarray(loaded.faces), process=False)
    if not np.isfinite(mesh.vertices).all():
        raise GeometryError(f"{name} contains invalid coordinates.")
    # STL repeats each vertex per triangle, and texture seams repeat positions; weld them for a closed surface.
    mesh.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=6)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    return mesh


def _axis_vector(axis):
    v = np.zeros(3)
    v["XYZ".index(axis[1])] = 1. if axis[0] == "+" else -1.
    return v


def find_back(mesh):
    """The axis direction pointing out of the wall: the one whose most negative end is the largest flat face.
    Returns (axis such as '+Z', that face's area in mm^2)."""
    normals, areas, centres = mesh.face_normals, mesh.area_faces, mesh.triangles_center
    best = ("+Z", 0.)
    tolerance = max(float(mesh.extents.max()) * .01, .2)
    for axis in AXES[1:]:
        d = _axis_vector(axis)
        along = centres @ d
        back = (normals @ d < -.98) & (along <= (mesh.vertices @ d).min() + tolerance)
        area = float(areas[back].sum())
        if area > best[1] * 1.05:
            best = (axis, area)
    return best


def orient(mesh, axis="+Z", turn_deg=0.):
    """Turn a mesh so `axis` points out of the wall, then by `turn_deg` about it (counter-clockwise seen by the
    climber); put its back on Z = 0 and its footprint centre on the origin."""
    out = _axis_vector(axis)
    up = _axis_vector(_UP_FOR[axis])
    across = np.cross(up, out)
    matrix = np.eye(4)
    matrix[:3, :3] = np.vstack([across, up, out])
    a = math.radians(turn_deg)
    spin = np.eye(4)
    spin[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    mesh = mesh.copy()
    mesh.apply_transform(spin @ matrix)
    lo, hi = mesh.bounds
    mesh.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return mesh


def find_hole(mesh, step=None):
    """A bolt hole already through the hold: vertical rays near the footprint that miss the hold but are
    surrounded by it. Returns ((x, y), diameter mm) for the one nearest the centre, or (None, 0.)."""
    lo, hi = mesh.bounds
    step = step or float(np.clip(max(hi[0] - lo[0], hi[1] - lo[1]) / 90., .75, 2.))
    xs = np.arange(lo[0] + step / 2, hi[0], step)
    ys = np.arange(lo[1] + step / 2, hi[1], step)
    if len(xs) < 3 or len(ys) < 3:
        return None, 0.
    gx, gy = np.meshgrid(xs, ys)
    miss = np.isnan(top_heights(mesh, np.column_stack([gx.ravel(), gy.ravel()]))[0]).reshape(gx.shape)
    # Flood the misses from the border: what is left are gaps the hold surrounds.
    outside = np.zeros_like(miss)
    stack = [(i, j) for i in range(miss.shape[0]) for j in (0, miss.shape[1] - 1) if miss[i, j]]
    stack += [(i, j) for j in range(miss.shape[1]) for i in (0, miss.shape[0] - 1) if miss[i, j]]
    while stack:
        i, j = stack.pop()
        if outside[i, j] or not miss[i, j]:
            continue
        outside[i, j] = True
        stack += [(a, b) for a, b in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1))
                  if 0 <= a < miss.shape[0] and 0 <= b < miss.shape[1]]
    inside = miss & ~outside
    seen, best = np.zeros_like(inside), (None, 0., np.inf)
    centre = (lo[:2] + hi[:2]) / 2
    for start in zip(*np.nonzero(inside)):
        if seen[start]:
            continue
        cells, stack = [], [start]
        while stack:
            i, j = stack.pop()
            if seen[i, j] or not inside[i, j]:
                continue
            seen[i, j] = True
            cells.append((i, j))
            stack += [(a, b) for a, b in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1))
                      if 0 <= a < inside.shape[0] and 0 <= b < inside.shape[1]]
        diameter = 2 * math.sqrt(len(cells) * step * step / math.pi)
        if not 3. <= diameter <= 30.:
            continue                    # a slot between two lobes, not a bolt hole
        ii, jj = np.array(cells).T
        at = np.array([gx[ii, jj].mean(), gy[ii, jj].mean()])
        gap = float(np.hypot(*(at - centre)))
        if gap < best[2]:
            best = (at, diameter, gap)
    if best[0] is None:
        return None, 0.
    return [round(float(v), 2) for v in best[0]], round(best[1] * 2) / 2


def _rate_vertical(mesh, hold_type):
    return pull_range(mesh, HoldRequest(hold_type, wall_angle=0.))


class HoldLibrary:
    """Holds saved in a folder. Records are LibraryHold; meshes are read when needed and kept in memory."""

    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else default_folder()
        self.holds: list[LibraryHold] = []
        self._meshes = {}
        self._rated = {}            # (id, wall angle, bore) -> (GeneratedHold, error)
        self.load()

    # ---- the folder ----------------------------------------------------------------------
    @property
    def index(self):
        return self.folder / "library.json"

    def load(self):
        self._meshes.clear()
        self._rated.clear()
        if not self.index.exists():
            self.holds = []
            return self
        data = json.loads(self.index.read_text(encoding="utf-8"))
        if data.get("format") != LIBRARY_FORMAT:
            raise GeometryError(f"{self.index} is not a Rockform hold library.")
        self.holds = [LibraryHold.from_dict(r) for r in data.get("holds", [])]
        return self

    def save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        data = dict(format=LIBRARY_FORMAT, version=LIBRARY_VERSION, holds=[asdict(h) for h in self.holds])
        temp = self.index.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp.replace(self.index)

    def get(self, hold_id):
        for h in self.holds:
            if h.id == hold_id:
                return h
        raise KeyError(f"No hold {hold_id!r} in the library.")

    def mesh(self, hold_id):
        if hold_id not in self._meshes:
            entry = self.get(hold_id)
            mesh = trimesh.load_mesh(self.folder / entry.file, process=False)
            mesh.merge_vertices(digits_vertex=6)        # STL stores each triangle's corners separately
            self._meshes[hold_id] = mesh
        return self._meshes[hold_id]

    def _new_id(self, name):
        base, taken = _slug(name), {h.id for h in self.holds}
        hold_id, n = base, 2
        while hold_id in taken:
            hold_id, n = f"{base}-{n}", n + 1
        return hold_id

    def _add(self, mesh, name, hold_type, source, original, hole=None, hole_mm=0., notes=()):
        if hold_type not in shapes.HOLD_TYPES:
            raise ValueError(f"Choose a hold type to rate it as: {', '.join(shapes.HOLD_TYPES)}.")
        notes = list(notes)
        if len(mesh.faces) > BIG_MESH:
            notes.append(f"{len(mesh.faces):,} triangles: rating it is slow. Reduce it in your modelling program.")
        pull = _rate_vertical(mesh, hold_type)
        entry = LibraryHold(self._new_id(name), str(name).strip() or "Hold", hold_type, source, "", original,
                            np.round(mesh.extents, 1).tolist(), round(float(mesh.volume) / 1000., 1), len(mesh.faces),
                            hole, float(hole_mm), round(pull["best_difficulty"], 2),
                            [round(pull["min"], 2), round(pull["max"], 2)], pull["best_angle_deg"],
                            bool(mesh.is_watertight), date.today().isoformat(), notes)
        entry.file = f"holds/{entry.id}.stl"
        (self.folder / "holds").mkdir(parents=True, exist_ok=True)
        mesh.export(self.folder / entry.file)
        self.holds.append(entry)
        self._meshes[entry.id] = mesh
        self.save()
        return entry

    # ---- adding holds ----------------------------------------------------------------------
    def import_model(self, name, data, hold_type, units=None, axis="auto", turn_deg=0., hole="detect", label=None):
        """Add a 3D model from a file's bytes. `units` is mm, cm, m or in (default: an OBJ's declared units, else
        mm); `axis` is the model's direction out of the wall ('auto' finds the flat back); `hole` is 'detect'
        (keep a bolt hole the model already has) or 'drill' (drill each time it is placed)."""
        notes = []
        mesh = load_model(name, data)
        units = units or declared_units(name, data) or "mm"
        if units not in UNITS:
            raise ValueError(f"Choose units from {', '.join(UNITS)}.")
        mesh.apply_scale(UNITS[units])
        if axis == "auto":
            axis, back = find_back(mesh)
            footprint = float(np.prod(np.sort(mesh.extents)[1:]))
            if back < .15 * footprint:
                notes.append("No large flat back was found: check that it sits flat on the wall, or choose its axis.")
        elif axis not in AXES:
            raise ValueError(f"Choose an axis from {', '.join(AXES)}.")
        mesh = orient(mesh, axis, turn_deg)
        if mesh.volume < 0:
            mesh.invert()
        if not mesh.is_watertight:
            notes.append("The model is not closed (watertight): it can be rated and placed, but not drilled. "
                         "Give it its own bolt hole, or repair it first.")
        largest = float(mesh.extents.max())
        if not 15. <= largest <= 1000.:
            notes.append(f"Its largest dimension is {largest:.1f} mm: check the units ({units}).")
        found, bore = find_hole(mesh) if hole == "detect" else (None, 0.)
        if hole == "detect" and found is None:
            notes.append("No bolt hole found: it is drilled for its bolt when a route places it.")
        original = f"{Path(name).name} ({units}, {axis} out of the wall"
        original += f", turned {turn_deg:g}°)" if turn_deg else ")"
        return self._add(mesh, label or Path(name).stem, hold_type, "imported", original, found, bore, notes)

    def import_file(self, path, hold_type, **options):
        path = Path(path)
        return self.import_model(path.name, path.read_bytes(), hold_type, **options)

    def save_generated(self, hold, name=None):
        """Save a generated hold (from the Hold view, or a drilled one from a route) to the library."""
        r = hold.request
        name = name or f"{r.hold_type.capitalize()} {hold.difficulty:.1f} (variation {r.seed})"
        bore = float(getattr(hold, "hole_mm", 0.))
        hole = [round(float(v), 2) for v in hold.hole] if bore and hasattr(hold, "hole") else None
        settings = f"generated {r.hold_type}, variation {r.seed}, size × {r.size:.2f}, target {hold.target:.1f} " \
                   f"on a {r.wall_angle:g}° wall"
        return self._add(hold.mesh.copy(), name, r.hold_type, "generated", settings, hole, bore if hole else 0.)

    # ---- editing ---------------------------------------------------------------------------
    def rename(self, hold_id, name):
        self.get(hold_id).name = str(name).strip() or self.get(hold_id).name
        self.save()

    def set_type(self, hold_id, hold_type):
        if hold_type not in shapes.HOLD_TYPES:
            raise ValueError(f"Choose a hold type: {', '.join(shapes.HOLD_TYPES)}.")
        entry = self.get(hold_id)
        entry.type = hold_type
        pull = _rate_vertical(self.mesh(hold_id), hold_type)
        entry.difficulty, entry.best_angle_deg = round(pull["best_difficulty"], 2), pull["best_angle_deg"]
        entry.difficulty_range = [round(pull["min"], 2), round(pull["max"], 2)]
        self._rated = {k: v for k, v in self._rated.items() if k[0] != hold_id}
        self.save()

    def remove(self, hold_id):
        entry = self.get(hold_id)
        self.holds.remove(entry)
        (self.folder / entry.file).unlink(missing_ok=True)
        self._meshes.pop(hold_id, None)
        self._rated = {k: v for k, v in self._rated.items() if k[0] != hold_id}
        self.save()

    # ---- sharing ---------------------------------------------------------------------------
    def export_zip(self):
        """The whole library as ZIP bytes (library.json and holds/), to share or move to another computer."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            data = dict(format=LIBRARY_FORMAT, version=LIBRARY_VERSION, holds=[asdict(h) for h in self.holds])
            archive.writestr("library.json", json.dumps(data, indent=2))
            for h in self.holds:
                archive.write(self.folder / h.file, h.file)
        return buffer.getvalue()

    def merge_zip(self, data):
        """Add every hold in a library ZIP. Holds whose id is taken get a new one. Returns the added records."""
        added = []
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            index = json.loads(archive.read("library.json"))
            if index.get("format") != LIBRARY_FORMAT:
                raise GeometryError("That ZIP is not a Rockform hold library.")
            (self.folder / "holds").mkdir(parents=True, exist_ok=True)
            for record in index.get("holds", []):
                entry = LibraryHold.from_dict(record)
                mesh_bytes = archive.read(entry.file)
                entry.id = self._new_id(entry.id)
                entry.file = f"holds/{entry.id}.stl"
                (self.folder / entry.file).write_bytes(mesh_bytes)
                self.holds.append(entry)
                added.append(entry)
        self.save()
        return added

    # ---- holds for routes ------------------------------------------------------------------
    def rated(self, hold_id, wall_angle=0., profile=None):
        """The library hold as a GeneratedHold rated on `wall_angle`, with `.hole` (where its bolt goes) and
        `.hole_mm`. A hold without its own hole is drilled for `profile` (None = left undrilled).
        Raises GeometryError when it needs drilling and cannot be drilled."""
        entry = self.get(hold_id)
        bore = 0. if entry.hole or profile is None else round(profile.bore, 2)
        key = (hold_id, round(float(wall_angle), 1), bore, getattr(profile, "head_shape", ""))
        if key not in self._rated:
            try:
                self._rated[key] = (self._rate(entry, wall_angle, profile), None)
            except GeometryError as error:
                self._rated[key] = (None, str(error))
        hold, error = self._rated[key]
        if error:
            raise GeometryError(error)
        return hold

    def _rate(self, entry, wall_angle, profile):
        mesh = self.mesh(entry.id)
        if entry.hole:
            hole, bore = np.array(entry.hole, float), entry.hole_mm
        elif profile is not None:
            if not entry.watertight:
                raise GeometryError(f"{entry.name} is not closed, so it cannot be drilled")
            mesh, hole = drill(mesh, profile)
            bore = profile.bore
        else:
            hole, bore = (mesh.bounds[0, :2] + mesh.bounds[1, :2]) / 2, 0.
        request = HoldRequest(entry.type, seed=entry.id, wall_angle=float(wall_angle))
        area, difficulty, mask = measure(mesh, request)
        pull = pull_range(mesh, request)
        hold = GeneratedHold(mesh, dict(library=entry.id, name=entry.name, source=entry.source), request, difficulty,
                             area, difficulty, mask, (pull["min"], pull["max"]),
                             [f"Library hold “{entry.name}” ({entry.source})."], pull)
        hold.hole, hold.hole_mm = hole, bore
        return hold

    def quick_rating(self, hold_id, wall_angle, pull_deg):
        """(turn, difficulty) for the undrilled hold turned so its easiest pull lies along `pull_deg`, in 15 degree
        steps. Cheap enough to compare every library hold for every move."""
        entry = self.get(hold_id)
        key = ("quick", hold_id, round(float(wall_angle), 1))
        if key not in self._rated:
            request = HoldRequest(entry.type, seed=entry.id, wall_angle=float(wall_angle))
            mesh = self.mesh(hold_id)
            turns = np.arange(-180., 180., 15.)
            # Turning by s and pulling at a scores the same as turning by s - a and pulling down.
            self._rated[key] = (request, {t: float(measure(mesh, request, 0., 0., t)[1]) for t in turns})
        _request, by_turn = self._rated[key]
        best = min(by_turn, key=by_turn.get)            # turn that is easiest to pull straight down
        spin = 15. * round(((pull_deg + best + 180.) % 360. - 180.) / 15.)
        return spin, by_turn[best]


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="python -m rockform.holdgen.library",
                                     description="List, add and remove the holds in a hold library.")
    parser.add_argument("--folder", type=Path, default=None, help=f"library folder (default: {default_folder()})")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list the holds")
    add = commands.add_parser("add", help="import 3D model files")
    add.add_argument("files", type=Path, nargs="+")
    add.add_argument("--type", required=True, choices=shapes.HOLD_TYPES, help="hold type to rate it as")
    add.add_argument("--units", choices=list(UNITS), help="model units (default: an OBJ's declared units, else mm)")
    add.add_argument("--axis", choices=AXES, default="auto", help="the model's direction out of the wall")
    add.add_argument("--turn", type=float, default=0., help="turn it on the wall, degrees anticlockwise")
    add.add_argument("--name", help="name (default: the file name)")
    add.add_argument("--drill", action="store_true", help="ignore a hole in the model: drill it for each route")
    remove = commands.add_parser("remove", help="remove holds by id")
    remove.add_argument("ids", nargs="+")
    args = parser.parse_args(argv)
    library = HoldLibrary(args.folder)
    if args.command == "add":
        for path in args.files:
            entry = library.import_file(path, args.type, units=args.units, axis=args.axis, turn_deg=args.turn,
                                        hole="drill" if args.drill else "detect",
                                        label=args.name if len(args.files) == 1 else None)
            print(f"added {entry.id}: {entry.original}")
            for note in entry.notes:
                print("  note: " + note)
    elif args.command == "remove":
        for hold_id in args.ids:
            library.remove(hold_id)
            print(f"removed {hold_id}")
    print(f"{len(library.holds)} hold(s) in {library.folder}")
    for h in library.holds:
        hole = f"Ø{h.hole_mm:g} mm hole" if h.hole else "no hole"
        print(f"  {h.id:<24} {h.type:<7} {' × '.join(f'{v:.0f}' for v in h.size_mm)} mm  {hole:<15} "
              f"vertical {h.difficulty:.1f}  {h.name}")


if __name__ == "__main__":
    main()

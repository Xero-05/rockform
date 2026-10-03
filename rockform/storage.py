"""Local, versioned project archives; only JSON and non-pickled numeric arrays."""
from dataclasses import asdict
from pathlib import Path
import io
import json
import os
import tempfile
import zipfile
import numpy as np
import trimesh
from .model import Project, Revision, Hold, Mount, HardwareProfile, GeometryError, TaskContext, default_profiles
from . import __version__
from .geometry import validate_solid, support_width
from .grip import assess_grip
from .mounts import apply_mounts


def declared_units(path):
    """Read explicit OBJ unit comments; never guess scale from mesh size."""
    if Path(path).suffix.lower() != ".obj":
        return "", 1.
    import re
    units = {"meter": ("Meters", 1000.), "meters": ("Meters", 1000.), "m": ("Meters", 1000.),
             "millimeter": ("Millimeters", 1.), "millimeters": ("Millimeters", 1.), "mm": ("Millimeters", 1.),
             "centimeter": ("Centimeters", 10.), "centimeters": ("Centimeters", 10.), "cm": ("Centimeters", 10.)}
    with Path(path).open("r", encoding="utf-8-sig", errors="replace") as stream:
        for _ in range(100):
            line = stream.readline()
            if not line:
                break
            match = re.match(r"\s*#\s*units?\s*:\s*([a-z]+)\b", line, re.I)
            if match and match[1].lower() in units:
                return units[match[1].lower()]
    return "", 1.


def import_scan(path, scale_factor=None, context=None):
    ctx = context or TaskContext()
    path = Path(path)
    if path.suffix.lower() not in (".obj", ".stl"):
        raise GeometryError("Choose an OBJ or STL triangle mesh, rather than a point cloud or Gaussian splat.")
    source_units, declared_factor = declared_units(path)
    scale_factor = declared_factor if scale_factor is None else scale_factor
    if not np.isfinite(scale_factor) or scale_factor <= 0:
        raise GeometryError("Enter a positive scale conversion to millimeters.")
    ctx.step(10, "Reading the scan…")
    mesh = trimesh.load_mesh(path, process=False, skip_materials=True)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise GeometryError("The file does not contain a triangle mesh.")
    if not np.isfinite(mesh.vertices).all():
        raise GeometryError("The scan contains invalid coordinates. Re-export it from the scanner.")
    mesh.vertices *= scale_factor
    # STL contains duplicate vertex records by design, which must be welded for connectivity.
    # OBJ texture seams repeat the same position with different UVs. Geometry
    # operations must weld those positions regardless of UVs or normal records.
    mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh)
    mesh.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=8)
    ctx.step(100, "Scan imported")
    return Project(mesh, path.name, scale_factor=scale_factor, source_units=source_units)


def _mesh_write(archive, name, mesh, **arrays):
    buffer = io.BytesIO()
    np.savez_compressed(buffer, vertices=mesh.vertices, faces=mesh.faces, **arrays)
    archive.writestr(name, buffer.getvalue())


def _mesh_read(archive, name):
    with np.load(io.BytesIO(archive.read(name)), allow_pickle=False) as arrays:
        values = {key: arrays[key] for key in arrays.files}
    mesh = trimesh.Trimesh(values.pop("vertices"), values.pop("faces"), process=False)
    if not np.isfinite(mesh.vertices).all():
        raise GeometryError("Project contains invalid mesh coordinates.")
    return mesh, values


def _hold_write(archive, hold, prefix):
    key = f"{prefix}.npz"
    _mesh_write(archive, key, hold.patch, protected_points=hold.protected_points,
                protected_triangles=hold.protected_triangles, frame=hold.frame, face_indices=hold.face_indices)
    record = {k: getattr(hold, k) for k in ("id", "name", "revision_id", "backing", "scale", "tilt_x", "tilt_y", "mode", "preparation", "edge_flare", "edge_style", "grip_review", "feature_kind", "extraction")}
    record.update(mesh=key, mounts=[asdict(m) for m in hold.mounts])
    if hold.repair_source is not None:
        record["repair_source"] = _hold_write(archive, hold.repair_source, f"{prefix}-repair-source")
    for attribute in ("solid", "result"):
        mesh = getattr(hold, attribute)
        if mesh is not None:
            _mesh_write(archive, f"{prefix}-{attribute}.npz", mesh)
    return record


def _hold_read(archive, record):
    record = record.copy()
    repair_source = record.pop("repair_source", None)
    record.setdefault("edge_flare", 0.)
    record.setdefault("edge_style", "legacy")
    key = record.pop("mesh")
    patch, arrays = _mesh_read(archive, key)
    mounts = []
    for mount in record.pop("mounts"):
        mount = mount.copy()
        mount["profile"] = HardwareProfile(**mount["profile"])
        mounts.append(Mount(**mount))
    hold = Hold(patch=patch, mounts=mounts, **arrays, **record)
    if repair_source is not None:
        hold.repair_source = _hold_read(archive, repair_source)
    for attribute in ("solid", "result"):
        mesh_key = f"{key[:-4]}-{attribute}.npz"
        if mesh_key in archive.namelist():
            setattr(hold, attribute, _mesh_read(archive, mesh_key)[0])
    return hold


def save_project(project, path, context=None):
    ctx = context or TaskContext()
    destination = Path(path)
    data = dict(version=8, source_name=project.source_name, scale_factor=project.scale_factor, source_units=project.source_units,
                up=project.up.tolist(), cursor=project.cursor,
                profiles={k: asdict(v) for k, v in project.profiles.items()}, revisions=[], archived=[], holds=[],
                workspace=project.workspace)
    descriptor, temp_path = tempfile.mkstemp(suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _mesh_write(archive, "original.npz", project.original)
            all_revisions = [("revisions", r) for r in project.revisions] + [("archived", r) for r in project.archived_revisions]
            for kind, revision in all_revisions:
                ctx.step(20, "Saving scan revisions…")
                key = f"revisions/{revision.id}.npz"
                _mesh_write(archive, key, revision.mesh, protected=revision.protected, crop_boundary=revision.crop_boundary)
                data[kind].append(dict(id=revision.id, label=revision.label, mesh=key, processing=revision.processing))
            for hold in project.holds:
                ctx.step(65, "Saving editable holds…")
                data["holds"].append(_hold_write(archive, hold, f"holds/{hold.id}"))
            if project.pending_hold is not None:
                data["pending_hold"] = _hold_write(archive, project.pending_hold, "workspace/hold")
            if project.pending_cleanup is not None:
                preview = project.pending_cleanup
                _mesh_write(archive, "workspace/cleanup.npz", preview.revision.mesh, protected=preview.revision.protected,
                            crop_boundary=preview.revision.crop_boundary)
                data["pending_cleanup"] = dict(id=preview.revision.id, label=preview.revision.label,
                                               summary=preview.summary, warnings=preview.warnings,
                                               processing=preview.revision.processing)
                for attribute in ("removed", "added"):
                    mesh = getattr(preview, attribute)
                    if mesh is not None:
                        _mesh_write(archive, f"workspace/{attribute}.npz", mesh)
            archive.writestr("project.json", json.dumps(data, indent=2, allow_nan=False))
        ctx.step(95, "Finishing project save…")
        os.replace(temp_path, destination)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    ctx.step(100, "Project saved")
    return str(destination)


def load_project(path, context=None):
    ctx = context or TaskContext()
    ctx.step(10, "Opening project…")
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 2_000_000_000:
            raise GeometryError("This project expands beyond the 2 GB project limit.")
        data = json.loads(archive.read("project.json"))
        if data.get("version") not in (1, 2, 3, 4, 5, 6, 7, 8):
            raise GeometryError("Unsupported project version.")
        original, _ = _mesh_read(archive, "original.npz")
        def revisions(kind):
            output = []
            for record in data.get(kind, []):
                mesh, arrays = _mesh_read(archive, record["mesh"])
                output.append(Revision(mesh, record["label"], record["id"], arrays["protected"],
                                       arrays.get("crop_boundary", np.zeros(0, dtype=bool)), record.get("processing", {})))
            return output
        project = Project(original, data["source_name"], revisions("revisions"), data["cursor"],
                          profiles={k: HardwareProfile(**v) for k, v in data["profiles"].items()},
                          up=np.asarray(data["up"]), scale_factor=data["scale_factor"],
                          archived_revisions=revisions("archived"), source_units=data.get("source_units", ""))
        for kind, preset in default_profiles().items():
            profile = project.profiles.get(kind)
            if profile is None or (profile.shaft_diameter == 0 and profile.head_diameter == 0 and profile.recess_depth == 0):
                project.profiles[kind] = preset
        if not 0 <= project.cursor < len(project.revisions):
            raise GeometryError("The project history index is invalid.")
        for record in data["holds"]:
            ctx.step(60, "Restoring editable holds…")
            project.holds.append(_hold_read(archive, record))
        project.workspace = data.get("workspace", {})
        if data.get("pending_hold"):
            project.pending_hold = _hold_read(archive, data["pending_hold"])
        if data.get("pending_cleanup"):
            from .cleanup import CleanupPreview
            record = data["pending_cleanup"]
            mesh, arrays = _mesh_read(archive, "workspace/cleanup.npz")
            extras = {name: _mesh_read(archive, f"workspace/{name}.npz")[0]
                      if f"workspace/{name}.npz" in archive.namelist() else None for name in ("removed", "added")}
            project.pending_cleanup = CleanupPreview(
                Revision(mesh, record["label"], record["id"], arrays["protected"],
                         arrays.get("crop_boundary", np.zeros(0, dtype=bool)), record.get("processing", {})),
                extras["removed"], record["summary"], extras["added"], record["warnings"])
    ctx.step(100, "Project restored")
    return project


def export_hold(hold, path, context=None):
    ctx = context or TaskContext()
    # Always recompute from current parameters; never export a stale preview.
    result = apply_mounts(hold, ctx)
    report = dict(application=f"Rockform {__version__}", purpose="Shape and mounting prototype; not qualified for climbing",
                  units="millimeters", stl_import_units="mm", hold=hold.name, source_revision=hold.revision_id,
                  dimensions_mm=result.extents.tolist(), volume_mm3=float(result.volume),
                  backing_mm=hold.backing, scale=hold.scale, tilt_degrees=[hold.tilt_x, hold.tilt_y],
                  edge_taper_width_mm=hold.edge_flare, edge_style=hold.edge_style,
                  feature_kind=hold.feature_kind, extraction=hold.extraction,
                  actual_wall_return_mm=support_width(hold), grip_screen=assess_grip(hold, hold.grip_review.get("scan_up"), ctx),
                  mode=hold.mode, mounts=[asdict(m) for m in hold.mounts],
                  surface_preparation=hold.preparation,
                  checks=dict(connected=True, watertight=True, consistent_orientation=True, detected_self_intersections=0))
    destination = Path(path)
    report_path = destination.with_suffix(".json")
    ctx.step(98, "Writing STL and dimension report…")
    destination.write_bytes(result.export(file_type="stl"))
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return result, str(destination), str(report_path)

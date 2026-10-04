"""A portable, explicit synthetic acceptance check bundled with the application."""
from pathlib import Path
import json
import sys
import traceback
import numpy as np
import trimesh
from .model import Project
from .geometry import demo_scan, extract_hold
from .features import prepare_feature
from .grip import assess_grip
from .cleanup import clean_revision, CleanupOptions
from .mounts import generate_mounts, apply_mounts
from .storage import save_project, load_project, export_hold


def run_preprocessing(source, destination):
    """Check import decimation, a central area crop, repair, persistence and UI."""
    from hashlib import sha256
    from .storage import import_scan
    from .preprocessing import adaptive_decimate, crop_interest
    from .geometry import frame_for_patch, validate_solid
    from .detection import suggest_holds
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    report = dict(frozen=bool(getattr(sys, 'frozen', False)), source=Path(source).name, passed=False)
    try:
        before = sha256(Path(source).read_bytes()).hexdigest()
        project = import_scan(source)
        density = adaptive_decimate(project.current)
        report['decimation'] = density.revision.processing
        if density.revision.processing.get('reduction_applied'):
            project.commit(density.revision)
        mesh = project.current.mesh
        local = trimesh.transform_points(mesh.triangles_center, frame_for_patch(mesh))
        center = (local.min(axis=0) + local.max(axis=0)) / 2
        selected = (np.abs(local[:, :2] - center[:2]) < np.ptp(local[:, :2], axis=0) * .3).all(axis=1)
        crop = crop_interest(project.current, selected, 20.)
        project.pending_cleanup = crop
        save_project(project, output / 'Pending area.rockform')
        restored = load_project(output / 'Pending area.rockform')
        assert np.array_equal(restored.pending_cleanup.revision.crop_boundary, crop.revision.crop_boundary)
        project.pending_cleanup = None
        project.commit(crop.revision)
        report['crop'] = dict(triangles=len(crop.revision.mesh.faces), summary=crop.summary)
        cleanup = clean_revision(project.current, CleanupOptions(auto_fill_small_gaps=True))
        project.commit(cleanup.revision)
        report['cleanup'] = cleanup.summary
        candidates, _ = suggest_holds(project.current, project.up)
        report['candidates'] = []
        for i, candidate in enumerate(candidates):
            hold = candidate.prepared_hold
            project.holds.append(hold)
            report['candidates'].append(candidate.explanation)
            if hold.solid is not None:
                _, path, _ = export_hold(hold, output / f'Area hold {i+1}.stl')
                validate_solid(trimesh.load_mesh(path))
        path = output / 'Preprocessed area.rockform'
        save_project(project, path)
        reopened = load_project(path)
        assert np.array_equal(reopened.original.vertices, project.original.vertices)
        assert np.array_equal(reopened.current.crop_boundary, project.current.crop_boundary)
        check_native_view(output, reopened, tab=0)
        report['source_unchanged'] = sha256(Path(source).read_bytes()).hexdigest() == before
        assert report['source_unchanged']
        report['passed'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    (output / 'preprocessing-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1


def run_scan(source, destination):
    """Check the same default repair/suggestion path on a supplied local scan.

    Writes review copies and unmounted prototypes. The input is read only;
    no hardware dimensions are assumed and no repair is applied to the input.
    """
    from hashlib import sha256
    from .storage import import_scan
    from .detection import suggest_holds
    from .geometry import validate_solid
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    report = dict(frozen=bool(getattr(sys, "frozen", False)), source=Path(source).name,
                  purpose="Review shapes only; no hardware dimensions assumed", holds=[], incomplete_features=[], passed=False)
    try:
        before = sha256(Path(source).read_bytes()).hexdigest()
        project = import_scan(source)
        report.update(source_units=project.source_units, dimensions_mm=project.original.extents.tolist(),
                      input_triangles=len(project.original.faces))
        preview = clean_revision(project.current, CleanupOptions(auto_fill_small_gaps=True))
        project.commit(preview.revision)
        report.update(repair=preview.summary, repair_warnings=preview.warnings)
        candidates, skipped = suggest_holds(project.current, project.up)
        from dataclasses import asdict
        report["screening"] = asdict(skipped)
        report["no_suitable_features"] = not candidates
        for i, candidate in enumerate(candidates):
            hold = candidate.prepared_hold
            hold.name = f"Scan hold {i + 1:02}"
            project.holds.append(hold)
            if hold.solid is None:
                report["incomplete_features"].append(dict(name=hold.name, dimensions_mm=hold.patch.extents.tolist(),
                    status="needs-repair", reason=hold.extraction.get("geometry_issue"), grip_screen=hold.grip_review))
                continue
            result, stl, settings = export_hold(hold, output / f"Scan hold {i + 1:02}.stl")
            reimported = trimesh.load_mesh(stl)
            validate_solid(reimported)
            assert np.allclose(result.extents, reimported.extents, atol=1e-4)
            report["holds"].append(dict(name=hold.name, dimensions_mm=result.extents.tolist(),
                preparation=hold.preparation, watertight=True, report=Path(settings).name))
        save_project(project, output / "Review scan.rockform")
        restored = load_project(output / "Review scan.rockform")
        assert len(restored.holds) == len(project.holds)
        assert restored.current.id == project.current.id
        assert all(np.array_equal(a.patch.vertices, b.patch.vertices) for a, b in zip(project.holds, restored.holds))
        assert sha256(Path(source).read_bytes()).hexdigest() == before
        report["source_unchanged"] = True
        check_native_view(output, restored, tab=1)
        report["native_view"] = "Opened the repaired project and captured its first hold or the scan when no feature qualified"
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    (output / "scan-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


def run_quick_repair(source, destination):
    """Exercise all explicit repairs on a saved incomplete feature."""
    from hashlib import sha256
    from .quick_repair import quick_repair, original_feature
    from .geometry import validate_solid
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    report = dict(frozen=bool(getattr(sys, "frozen", False)), repairs=[], passed=False)
    try:
        before = sha256(Path(source).read_bytes()).hexdigest()
        project = load_project(source)
        feature = next(h for h in project.holds if h.solid is None and h.feature_kind == 'protrusion')
        repairs = []
        from .model import TaskContext
        last_message = [None]
        def progress(percent, message):
            if message != last_message[0]:
                print(message, flush=True)
                last_message[0] = message
        context = TaskContext(progress)
        for method in ('fill', 'trim', 'fill-trim'):
            print(f'Checking quick repair: {method}', flush=True)
            # Zero is the UI default and reproduced the reported 50 mm failure.
            height = 0. if method == 'fill-trim' else None
            repaired = quick_repair(feature, method, trim_height=height, up=project.up, context=context)
            repaired.name = f'Quick repair - {method}'
            print(f'Repaired solid: {len(repaired.solid.faces)} triangles', flush=True)
            result, stl, _ = export_hold(repaired, output/f'{method}.stl', context)
            reimported = trimesh.load_mesh(stl)
            validate_solid(reimported, context)
            assert np.allclose(result.extents, reimported.extents, atol=1e-4)
            assert np.array_equal(original_feature(repaired).patch.vertices, feature.patch.vertices)
            repairs.append(repaired)
            report['repairs'].append(dict(method=method, dimensions_mm=result.extents.tolist(),
                                         watertight=True, metadata=repaired.extraction['quick_repair']))
        project.holds = [repairs[-1], *repairs[:-1], feature]
        project.workspace = {}
        project.pending_hold = None
        save_project(project, output/'Quick repair examples.rockform')
        restored = load_project(output/'Quick repair examples.rockform')
        for hold in restored.holds[:len(repairs)]:
            assert hold.repair_source.solid is None and hold.solid.is_volume
            assert np.array_equal(hold.repair_source.patch.vertices, feature.patch.vertices)
        check_native_view(output, restored, tab=1)
        assert sha256(Path(source).read_bytes()).hexdigest() == before
        report.update(passed=True, source_unchanged=True, native_view='Opened repaired hold; saved/reopened repair draft with its original source')
    except Exception:
        report['error'] = traceback.format_exc()
    (output/'quick-repair-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1


def check_native_view(output, project, tab=2):
    """Verify Qt, the OpenGL viewer, and bundled fonts/plugins in the executable."""
    from PySide6 import QtCore, QtWidgets
    from .app import MainWindow
    from .widgets import STYLE
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    errors = []
    window.show_error = errors.append
    window.set_project(project)
    if project.holds:
        window.select_hold(0)
    window.tabs.setCurrentIndex(tab)
    # Exercise the same workspace snapshot used by Save project in the frozen
    # executable, including an unkept draft rather than only kept library holds.
    from copy import deepcopy
    if project.holds:
        window.hold_preview = deepcopy(project.holds[0])
        window.hold_preview.id += '-draft-check'
        window.selected[window.hold_preview.face_indices] = True
        window.view_mode.setCurrentText('Hold preview')
    window.capture_workspace()
    saved_tab = window.tabs.currentIndex()
    workspace_path = output / 'Workspace save check.rockform'
    save_project(project, workspace_path)
    restored = load_project(workspace_path)
    window.set_project(restored, str(workspace_path))
    assert len(restored.holds) == len(project.holds)
    if project.holds:
        assert window.hold_preview is not None and window.view_mode.currentText() == 'Hold preview'
        assert np.array_equal(window.hold_preview.patch.vertices, project.holds[0].patch.vertices)
        assert window.selected.any()
        assert window.tabs.currentIndex() == saved_tab
        repair = project.holds[0].extraction.get('quick_repair', {})
        if repair.get('method') == 'fill-trim':
            assert window.quick_method.currentText() == 'Fill then trim'
            assert window.trim_height.value() == repair['trim_height_above_wall_mm']
            assert window.trim_height.isEnabled() and window.auto_trim.isHidden()
    project.workspace = {}
    project.pending_hold = None
    window.set_project(project)
    if project.holds:
        window.select_hold(0)
    window.tabs.setCurrentIndex(tab)
    window.show()
    def capture():
        try:
            assert window.grab().save(str(output / "packaged-window.png"))
            assert not errors, errors
        except Exception:
            errors.append(traceback.format_exc())
        finally:
            window.dirty = False
            window.close()
            app.quit()
    QtCore.QTimer.singleShot(500, capture)
    app.exec()
    if errors:
        raise RuntimeError("\n".join(errors))


def run(destination):
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    report = dict(frozen=bool(getattr(sys, "frozen", False)), executable=sys.executable,
                  data="Synthetic fixtures; no real-world accuracy claim", cases=[], passed=False)
    try:
        for index, kind in enumerate(["ledge", "rounded", "pocket"]):
            project = Project(demo_scan(kind, 23), f"Synthetic {kind}")
            preview = clean_revision(project.current, CleanupOptions(fragment_percent=0, smooth_iterations=1))
            project.commit(preview.revision)
            mesh = project.current.mesh
            ids = np.flatnonzero((np.abs(mesh.triangles[:, :, :2]) < 95).all(axis=(1, 2)))
            hold = prepare_feature(project.current, ids, kind)
            hold.grip_review = assess_grip(hold, project.up)
            bolt, screw = project.profiles["bolt"], project.profiles["screw"]
            hold.mode = "bolt-on" if index != 1 else "screw-on"
            hold.mounts = generate_mounts(hold, bolt, screw, mode=hold.mode, count=3)
            hold.result = apply_mounts(hold)
            project.holds.append(hold)
            save_project(project, output / f"{kind}.rockform")
            restored = load_project(output / f"{kind}.rockform")
            result, _, _ = export_hold(restored.holds[0], output / f"{kind}.stl")
            reimported = trimesh.load_mesh(output / f"{kind}.stl")
            assert reimported.is_volume
            assert np.allclose(result.extents, reimported.extents, atol=1e-4)
            report["cases"].append(dict(name=kind, mode=hold.mode, mounts=len(hold.mounts),
                                         feature_kind=hold.feature_kind, backing_mm=hold.backing,
                                         watertight=True, dimensions_mm=result.extents.tolist()))
        check_native_view(output, restored)
        report["native_view"] = "Opened and captured successfully"
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1

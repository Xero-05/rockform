from copy import deepcopy
import numpy as np
import pytest
import trimesh
from PySide6 import QtWidgets
from rockform.model import Project, Revision, Hold, GeometryError, TaskContext, Cancelled, default_profiles
from rockform.features import feature_regions, protrusion_hold
from rockform.geometry import build_solid, generated_faces
from rockform.quick_repair import quick_repair, original_feature, suggested_trim_height, comparison_surface, _connected_fill, _trim_solid
from rockform.storage import save_project, load_project, export_hold
from rockform.mounts import generate_mounts, apply_mounts, footprint
from rockform.app import MainWindow
from test_natural_features import wall_with_feature
from test_ui import wait_for_worker


def broken_feature():
    mesh = wall_with_feature(resolution=25)
    c = mesh.triangles_center
    mesh.update_faces(~((c[:, 2] > 1.5) & (c[:, 2] < 6. + c[:, 0] * .03)))
    mesh.remove_unreferenced_vertices()
    revision = Revision(mesh)
    reference, regions = feature_regions(revision)
    kind, patch = max(regions, key=lambda value: value[1].area)
    hold = protrusion_hold(revision, patch, reference, kind=kind, allow_incomplete=True, contact_margin_mm=0.)
    assert hold.solid is None
    return Project(mesh, 'Broken base', revisions=[revision], holds=[hold]), hold


def test_fill_handles_overlapping_rim_without_a_surrounding_plate():
    angles = np.linspace(0, 2 * np.pi, 20, endpoint=False)
    angles[[4, 5]] = angles[[5, 4]]  # A folded rim overlaps when projected.
    rim = np.column_stack([25 * np.cos(angles), 22 * np.sin(angles), 6 + 2 * np.cos(angles * 3)])
    rim[4, 2] += 3
    vertices = np.vstack([[0, 0, 28], rim])
    patch = trimesh.Trimesh(vertices, [[0, i + 1, (i + 1) % len(rim) + 1] for i in range(len(rim))], process=False)
    hold = Hold('Folded rim', 'source', np.arange(len(patch.faces)), patch, np.empty((0,3)), np.eye(4),
                feature_kind='protrusion', backing=0, edge_style='natural-base',
                extraction=dict(geometry_status='needs-repair', wall_offset_mm=1.16))
    repaired = quick_repair(hold, 'fill')
    assert repaired.solid.is_volume and generated_faces(repaired).any()
    assert footprint(repaired)[0].is_valid
    assert np.allclose(repaired.solid.extents[:2], patch.extents[:2])
    assert np.array_equal(hold.patch.vertices, repaired.repair_source.patch.vertices)


def uneven_rim_feature():
    angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    heights = np.tile([2., 10., 3., 12.], 6)
    rim = np.column_stack([40 * np.cos(angles), 36 * np.sin(angles), heights])
    vertices = np.vstack([[0, 0, 30], rim])
    patch = trimesh.Trimesh(vertices, [[0, i + 1, (i + 1) % len(rim) + 1] for i in range(len(rim))], process=False)
    revision = Revision(patch)
    hold = Hold('Uneven rim', revision.id, np.arange(len(patch.faces)), patch.copy(), np.empty((0,3)), np.eye(4),
                feature_kind='protrusion', backing=0., edge_style='natural-base',
                extraction=dict(geometry_status='needs-repair', wall_offset_mm=1.))
    return Project(patch.copy(), 'Uneven rim', revisions=[revision], holds=[hold]), hold


def test_combined_repair_closes_damage_above_cut_and_round_trips(tmp_path):
    project, source = uneven_rim_feature()
    original = source.patch.vertices.copy()
    # This cut removes the lowest extensions but leaves higher damaged sides.
    with pytest.raises(GeometryError, match='cut has not cleared'):
        quick_repair(source, 'trim', trim_height=5.)
    repaired = quick_repair(source, 'fill-trim', trim_height=5.)
    assert repaired.solid.is_volume and generated_faces(repaired).any()
    assert repaired.patch.area < source.patch.area
    assert repaired.patch.vertices[:, 2].min() == pytest.approx(0.)
    assert repaired.solid.extents[2] == pytest.approx(26.)  # 30 - (5 - wall offset 1)
    assert repaired.extraction['quick_repair']['sequence'] == 'fill-then-trim'
    assert repaired.extraction['quick_repair']['flat_back_above_wall_mm'] == 5.
    filled = quick_repair(source, 'fill')
    cut = _trim_solid(filled.solid, 5.)
    cut.vertices[:, 2] -= 5.
    assert cut.volume == pytest.approx(repaired.solid.volume)
    assert np.allclose(cut.bounds, repaired.solid.bounds)
    world = trimesh.transform_points(repaired.patch.vertices, np.linalg.inv(repaired.frame))
    assert world[:, 2].min() == pytest.approx(4.)
    assert trimesh.proximity.closest_point(source.patch, world)[1].max() < 1e-6
    assert np.array_equal(original, source.patch.vertices) and source.solid is None
    assert comparison_surface(repaired).bounds[0, 2] < repaired.patch.bounds[0, 2]
    # Lowering the cut starts from the original, restoring previously removed rock.
    lower = quick_repair(repaired, 'fill-trim', trim_height=3.)
    direct = quick_repair(source, 'fill-trim', trim_height=3.)
    assert lower.patch.area > repaired.patch.area
    assert np.allclose(lower.patch.vertices, direct.patch.vertices)
    project.holds.append(repaired)
    save_project(project, tmp_path / 'combined.rockform')
    restored = load_project(tmp_path / 'combined.rockform').holds[1]
    assert restored.extraction == repaired.extraction
    assert np.array_equal(restored.repair_body.vertices, repaired.repair_body.vertices)
    assert np.array_equal(original_feature(restored).patch.vertices, original)
    assert build_solid(restored).volume == pytest.approx(repaired.solid.volume, rel=1e-6)
    restored.mounts = generate_mounts(restored, project.profiles['bolt'], project.profiles['screw'], anti_rotation=False)
    result, path, _ = export_hold(restored, tmp_path / 'combined.stl')
    imported = trimesh.load_mesh(path)
    assert imported.is_volume and np.allclose(imported.extents, result.extents, atol=1e-4)


def test_combined_repair_refuses_protected_and_invalid_cuts():
    _, source = uneven_rim_feature()
    before = source.patch.vertices.copy()
    for height in (-1., float('nan'), 40.):
        with pytest.raises(GeometryError):
            quick_repair(source, 'fill-trim', trim_height=height)
    source.protected_triangles = source.patch.triangles[:1].copy()
    with pytest.raises(GeometryError, match='protected'):
        quick_repair(source, 'fill-trim', trim_height=5.)
    assert np.array_equal(source.patch.vertices, before) and source.solid is None


def test_fill_removes_enclosed_fragments_and_rejects_exterior_pieces():
    body = trimesh.creation.box(extents=[10, 10, 10])
    fragment = trimesh.creation.box(extents=[1, 1, 1])
    repaired = _connected_fill(trimesh.util.concatenate([body, fragment]))
    assert repaired.is_volume and repaired.volume == pytest.approx(body.volume)
    assert np.allclose(repaired.bounds, body.bounds)
    fragment.apply_translation([10, 0, 0])
    with pytest.raises(GeometryError, match='outside the main body'):
        _connected_fill(trimesh.util.concatenate([body, fragment]))


def test_filled_cut_body_survives_shape_edits_and_uses_new_project_format(tmp_path):
    import json
    import zipfile
    project, source = uneven_rim_feature()
    repaired = quick_repair(source, 'fill-trim', trim_height=5.)
    base = repaired.repair_body.vertices.copy()
    repaired.scale = 1.2
    resized = build_solid(repaired)
    assert resized.is_volume and np.allclose(resized.extents, repaired.solid.extents * 1.2)
    repaired.tilt_x, repaired.tilt_y, repaired.backing = 8., -4., 2.
    tilted = build_solid(repaired)
    assert tilted.is_volume and tilted.bounds[0, 2] == pytest.approx(0., abs=1e-6)
    assert np.array_equal(repaired.repair_body.vertices, base)
    project.holds.append(repaired)
    path = tmp_path / 'filled-cut.rockform'
    save_project(project, path)
    with zipfile.ZipFile(path) as archive:
        assert json.loads(archive.read('project.json'))['version'] == 9
    restored = load_project(path).holds[1]
    assert build_solid(restored).volume == pytest.approx(tilted.volume)


def test_trim_keeps_main_body_reports_offcuts_and_respects_protection():
    xs = np.array([-60, -30, -5, 5, 20, 30, 40, 50.])
    heights = np.array([1, 30, 1, 1, 10, 12, 10, 1.])
    vertices = np.array([[x, y, z] for x, z in zip(xs, heights) for y in (-15., 15.)])
    faces = [[2*i, 2*i+2, 2*i+1] for i in range(len(xs)-1)] + [[2*i+1, 2*i+2, 2*i+3] for i in range(len(xs)-1)]
    patch = trimesh.Trimesh(vertices, faces, process=False)
    source = Hold('Connected peaks', 'source', np.arange(len(faces)), patch, np.empty((0,3)), np.eye(4),
                  feature_kind='protrusion', backing=0., edge_style='natural-base',
                  extraction=dict(geometry_status='needs-repair', wall_offset_mm=0.))
    repaired = quick_repair(source, 'fill-trim', trim_height=5.)
    assert repaired.solid.is_volume
    assert repaired.extraction['quick_repair']['separated_offcuts'] == 1
    assert repaired.extraction['quick_repair']['offcut_volume_mm3'] > 1.
    assert repaired.solid.bounds[1, 0] < 0 and repaired.patch.bounds[1, 0] < 0
    lower = quick_repair(repaired, 'fill-trim', trim_height=0.)
    assert lower.extraction['quick_repair']['separated_offcuts'] == 0
    assert lower.patch.bounds[1, 0] == 50.
    source.protected_triangles = patch.triangles[(patch.triangles[:, :, 0] >= 20).all(axis=1) &
                                               (patch.triangles[:, :, 0] <= 40).all(axis=1)]
    with pytest.raises(GeometryError, match='protected'):
        quick_repair(source, 'fill-trim', trim_height=5.)
    assert source.solid is None and source.patch.bounds[1, 0] == 50.


def test_combined_ui_cut_height_save_reopen_and_restore(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    errors = []
    window.show_error = errors.append
    try:
        project, source = uneven_rim_feature()
        window.set_project(project)
        window.select_hold(0)
        window.quick_method.setCurrentIndex(2)
        assert window.trim_height.value() == 0.
        assert window.trim_height.isEnabled() and window.auto_trim.isHidden()
        window.trim_height.setValue(5.)
        window.preview_quick_repair()
        wait_for_worker(window)
        assert not errors and window.hold_preview.solid.is_volume
        assert window.quick_method.currentText() == 'Fill then trim' and window.trim_height.value() == 5.
        window.capture_workspace()
        save_project(project, tmp_path / 'combined-ui.rockform')
        window.set_project(load_project(tmp_path / 'combined-ui.rockform'), str(tmp_path / 'combined-ui.rockform'))
        assert window.quick_method.currentText() == 'Fill then trim' and window.trim_height.value() == 5.
        assert window.hold_preview.extraction['quick_repair']['method'] == 'fill-trim'
        window.restore_feature()
        assert window.hold_preview.solid is None
        assert np.array_equal(window.hold_preview.patch.vertices, source.patch.vertices)
        window.trim_height.setValue(3.)
        window.preview_quick_repair()
        wait_for_worker(window)
        assert not errors and window.hold_preview.solid.is_volume
        window.accept_hold_preview()
        assert window.export_button.isEnabled()
    finally:
        if window.worker:
            window.cancel_job()
            wait_for_worker(window)
        window.dirty = False
        window.close()


@pytest.mark.parametrize('method', ['fill', 'trim'])
def test_repair_is_closed_reversible_and_round_trips(method, tmp_path):
    project, source = broken_feature()
    before = source.patch.vertices.copy()
    repaired = quick_repair(source, method, up=project.up)
    assert source.solid is None and np.array_equal(before, source.patch.vertices)
    assert repaired.solid.is_volume
    assert repaired.solid.bounds[0, 2] == pytest.approx(0., abs=1e-6)
    assert np.all(repaired.solid.extents[:2] <= source.patch.extents[:2] + 1e-6)
    assert not repaired.backing and not repaired.edge_flare
    assert repaired.id != source.id and repaired.revision_id == source.revision_id
    world = trimesh.transform_points(repaired.patch.vertices, np.linalg.inv(repaired.frame))
    assert trimesh.proximity.closest_point(project.current.mesh, world)[1].max() < 1e-6
    if method == 'fill':
        assert generated_faces(repaired).any()
        assert repaired.solid.extents[2] == pytest.approx(source.patch.bounds[1, 2] + source.extraction['wall_offset_mm'])
    else:
        assert repaired.solid.extents[2] < source.patch.extents[2]
        assert not generated_faces(repaired).any()
        assert comparison_surface(repaired).bounds[0, 2] < 0
    project.holds.append(repaired)
    save_project(project, tmp_path/'repairs.rockform')
    restored = load_project(tmp_path/'repairs.rockform').holds[1]
    assert restored.extraction == repaired.extraction
    assert np.array_equal(original_feature(restored).patch.vertices, before)
    rebuilt = build_solid(restored)
    assert rebuilt.volume == pytest.approx(repaired.solid.volume, rel=1e-6)
    export_hold(restored, tmp_path/'repair.stl')
    exported = trimesh.load_mesh(tmp_path/'repair.stl')
    assert exported.is_volume
    assert np.allclose(exported.extents, restored.solid.extents, atol=1e-4)


def test_fill_and_trim_keep_hardware_dimensions_after_resize():
    p, source = broken_feature()
    profiles = default_profiles()
    for method, mode in [('fill', 'bolt-on'), ('trim', 'screw-on')]:
        h = quick_repair(source, method)
        h.mode = mode
        h.mounts = generate_mounts(h, profiles['bolt'], profiles['screw'])
        assert h.mounts
        before = [deepcopy(m.profile) for m in h.mounts]
        h.scale = 1.1
        h.solid = build_solid(h)
        assert [m.profile for m in h.mounts] == before
        assert apply_mounts(h).is_volume


def test_invalid_trim_protection_and_cancellation_leave_source_intact():
    _, h = broken_feature()
    before = h.patch.vertices.copy()
    with pytest.raises(GeometryError, match='3 mm'):
        quick_repair(h, 'trim', trim_height=1000)
    with pytest.raises(GeometryError, match='Raise the trim height'):
        quick_repair(h, 'trim', trim_height=0)
    h.protected_triangles = h.patch.triangles[:1].copy()
    with pytest.raises(GeometryError, match='protected'):
        quick_repair(h, 'trim', trim_height=suggested_trim_height(h))
    ctx = TaskContext(); ctx.cancelled = True
    with pytest.raises(Cancelled):
        quick_repair(h, 'fill', context=ctx)
    assert h.solid is None and np.array_equal(before, h.patch.vertices)


def test_quick_repair_preview_switch_restore_and_save(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow(); errors=[]; w.show_error=errors.append
    try:
        p, source = broken_feature()
        w.set_project(p); w.select_hold(0)
        assert w.quick_repair_button.isEnabled()
        w.preview_quick_repair(); wait_for_worker(w)
        assert not errors, errors
        assert w.hold_preview.solid.is_volume and w.hold.solid is None
        w.quick_method.setCurrentIndex(1)
        w.preview_quick_repair(); wait_for_worker(w)
        assert not errors, errors
        assert w.hold_preview.extraction['quick_repair']['method']=='trim'
        w.compare_repair.setChecked(True)
        w.project_path=str(tmp_path/'draft.rockform')
        w.save_dialog();wait_for_worker(w)
        w.set_project(load_project(w.project_path), w.project_path)
        assert w.hold_preview.solid.is_volume and w.compare_repair.isChecked()
        assert w.hold_preview.repair_source.solid is None
        w.restore_feature()
        assert w.hold_preview.solid is None and w.hold_preview.repair_source is None
        assert np.array_equal(w.hold_preview.patch.vertices,source.patch.vertices)
        w.preview_quick_repair();wait_for_worker(w)
        assert not errors, errors
        w.accept_hold_preview()
        assert len(w.project.holds)==2 and w.export_button.isEnabled()
        assert w.project.holds[0].solid is None
    finally:
        if w.worker:
            w.cancel_job();wait_for_worker(w)
        w.dirty=False;w.close()

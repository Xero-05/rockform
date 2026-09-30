import numpy as np
import pytest
import trimesh
from PySide6 import QtWidgets
from rockform.model import Project, Revision, GeometryError
from rockform.detection import suggest_holds
from rockform.geometry import build_solid
from rockform.storage import save_project, load_project, export_hold
from rockform.mounts import generate_mounts
from rockform.app import MainWindow
from test_natural_features import wall_with_feature
from test_ui import wait_for_worker


def incomplete_scan():
    mesh = wall_with_feature(resolution=45)
    centers = mesh.triangles_center
    # A missing strip disconnects the feature's underside from the surrounding
    # wall; an interior gap also interrupts the front surface.
    missing = ((centers[:, 2] > 2.) & (centers[:, 2] < 6.)) | ((np.abs(centers[:, :2]) < 5.).all(axis=1))
    mesh.update_faces(~missing)
    mesh.remove_unreferenced_vertices()
    return mesh


def test_damage_elsewhere_does_not_block_complete_feature():
    mesh = wall_with_feature(resolution=41)
    c = mesh.triangles_center
    mesh.update_faces(~((c[:, 0] > 80) & (c[:, 0] < 100) & (c[:, 1] > 80) & (c[:, 1] < 110)))
    mesh.remove_unreferenced_vertices()
    candidates, summary = suggest_holds(Revision(mesh), [0,1,0])
    assert len(candidates) == 1 and summary.needs_repair == 0
    assert candidates[0].prepared_hold.solid.is_volume


def test_branched_recess_boundary_does_not_abort_other_feature_recognition():
    mesh = wall_with_feature('recess', resolution=41)
    center = np.argmin(np.linalg.norm(mesh.vertices[:, :2], axis=1))
    incident = np.flatnonzero((mesh.faces == center).any(axis=1))
    first = incident[0]
    opposite = next(i for i in incident if len(set(mesh.faces[first]) & set(mesh.faces[i])) == 1)
    keep = np.ones(len(mesh.faces), dtype=bool)
    keep[[first, opposite]] = False
    mesh.update_faces(keep)
    candidates, summary = suggest_holds(Revision(mesh), [0,1,0])
    assert candidates and summary.needs_repair == 1
    hold = candidates[0].prepared_hold
    assert hold.feature_kind == 'recess' and hold.solid is None
    assert 'branches' in hold.extraction['geometry_issue']


def test_incomplete_feature_is_recognized_without_inventing_missing_surface(tmp_path):
    mesh = incomplete_scan()
    before = mesh.vertices.copy()
    revision = Revision(mesh)
    candidates, summary = suggest_holds(revision, [0,1,0])
    assert len(candidates) == 1 and summary.needs_repair == 1
    c = candidates[0]
    assert 'Needs repair' in c.explanation
    h = c.prepared_hold
    assert h.solid is None and h.result is None
    assert not h.grip_review['geometry_ready']
    assert h.extraction['geometry_status'] == 'needs-repair'
    world = trimesh.transform_points(h.patch.vertices, np.linalg.inv(h.frame))
    assert trimesh.proximity.closest_point(mesh, world)[1].max() < 1e-6
    assert np.array_equal(mesh.vertices, before)
    p = Project(mesh, 'Broken scan', revisions=[revision], holds=[h])
    save_project(p, tmp_path/'feature.rockform')
    restored = load_project(tmp_path/'feature.rockform').holds[0]
    assert restored.solid is None
    assert np.array_equal(restored.patch.vertices, h.patch.vertices)
    with pytest.raises(GeometryError, match='repair'):
        build_solid(restored)
    with pytest.raises(GeometryError, match='repair'):
        generate_mounts(restored, p.profiles['bolt'], p.profiles['screw'])
    with pytest.raises(GeometryError):
        export_hold(restored, tmp_path/'should-not-exist.stl')
    assert not (tmp_path/'should-not-exist.stl').exists()


def test_incomplete_feature_can_be_kept_reopened_and_reextracted_after_repair(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    errors=[]; window.show_error=errors.append
    try:
        p=Project(incomplete_scan(), 'Incomplete workflow')
        window.set_project(p)
        window.suggest();wait_for_worker(window)
        assert not errors and len(window.candidates)==1
        window.choose_candidate(0);window.create_hold();wait_for_worker(window)
        assert not errors and window.hold_preview.solid is None
        window.cutaway.setChecked(True)  # A prior solid's cutaway must not hide the feature.
        assert not errors
        assert window.keep_hold_button.text()=='Keep feature for repair'
        window.accept_hold_preview()
        original=window.hold
        assert original.solid is None
        assert 'Needs repair' in window.hold_list.item(0).text()
        assert not window.export_button.isEnabled() and not window.tabs.isTabEnabled(2)
        window.project_path=str(tmp_path/'incomplete.rockform')
        window.save_dialog();wait_for_worker(window)
        window.set_project(load_project(window.project_path),window.project_path)
        assert window.view_mode.currentText()=='Hold' and window.hold.solid is None
        assert not errors and not window.export_button.isEnabled()
        # A replacement scan revision supplies the missing geometry. Explicit
        # re-extraction creates a new preview; the original remains revision-bound.
        window.project.commit(Revision(wall_with_feature(resolution=45),'Rescan'))
        window.reset_selection()
        window.use_tapered_defaults();wait_for_worker(window)
        assert not errors
        assert window.hold_preview.solid.is_volume
        assert window.hold_preview.revision_id == window.project.current.id
        assert window.hold.revision_id == original.revision_id and window.hold.solid is None
        window.accept_hold_preview()
        assert window.export_button.isEnabled() and window.tabs.isTabEnabled(2)
        # An incomplete preview must never export the previously selected solid.
        window.hold_preview = window.project.holds[0]
        window.view_mode.setCurrentText('Hold preview')
        window.refresh()
        assert not window.export_button.isEnabled() and not window.tabs.isTabEnabled(2)
    finally:
        if window.worker:
            window.cancel_job();wait_for_worker(window)
        window.dirty=False;window.close()

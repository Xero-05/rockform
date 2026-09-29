from copy import deepcopy
import json
import zipfile
import numpy as np
import pytest
from scipy.spatial import cKDTree
from PySide6 import QtWidgets
from rockform.geometry import (demo_scan, extract_hold, build_solid, oriented_patch,
                               generated_faces, support_ledge_area, validate_solid)
from rockform.model import Hold, Project, Revision, Mount, GeometryError, TaskContext, Cancelled, default_profiles
from rockform.grip import assess_grip
from rockform.detection import suggest_holds, Candidate
from rockform.storage import save_project, load_project, export_hold


def texture_hold(resolution=25, amplitude=1.2, backing=6., slope=0.):
    mesh = demo_scan('rounded', resolution)
    mesh.vertices[:, :2] *= .4
    x, y = mesh.vertices[:, :2].T
    mesh.vertices[:, 2] = amplitude * np.sin(x / 4.) * np.cos(y / 5.) + slope * y
    hold = Hold('Texture', 'revision', np.arange(len(mesh.faces)), mesh, np.empty((0, 3)), np.eye(4), backing=backing)
    hold.solid = build_solid(hold)
    return hold


@pytest.mark.parametrize('resolution', [17, 41])
def test_texture_cannot_qualify_with_more_triangles_or_backing(resolution):
    hold = texture_hold(resolution, backing=30.)
    review = assess_grip(hold, [0, 1, 0])
    assert not review['eligible']
    assert review['relief_mm'] < 3.
    assert review['contact_area_mm2'] == 0
    assert any('Backing does not count' in reason for reason in review['reasons'])


def test_tilting_a_flat_scan_cannot_invent_natural_relief():
    hold = texture_hold(amplitude=0., slope=.2)
    review = assess_grip(hold, [0, 1, 0])
    assert review['relief_mm'] < .001
    assert not review['eligible']


@pytest.mark.parametrize('kind', ['rounded', 'ledge'])
def test_detector_keeps_broad_natural_features_with_low_surroundings(kind):
    revision = Revision(demo_scan(kind, 23))
    candidates, _ = suggest_holds(revision, [0, 1, 0], maximum_size=300.)
    assert candidates
    for candidate in candidates:
        hold = candidate.prepared_hold
        assert hold.grip_review['eligible']
        assert hold.grip_review['relief_mm'] >= 8.
        assert hold.grip_review['contact_width_mm'] >= 20.
        assert support_ledge_area(hold) < 25.
        assert 'solid checked' not in candidate.explanation
        validate_solid(hold.solid)


def test_detection_checks_retained_surface_after_preparation(monkeypatch):
    import rockform.detection as detection
    import rockform.geometry as geometry
    source = Revision(demo_scan('ledge', 17))
    flat = texture_hold(amplitude=0.)
    # A border repair can discard the original seed feature. Its coarse score
    # must never be reused as evidence that the remaining patch is a handhold.
    monkeypatch.setattr(detection, 'detect_candidates', lambda *a: [Candidate(np.arange(10), 100., 'Strong seed')])
    monkeypatch.setattr(geometry, 'prepare_hold', lambda *a, **kw: flat)
    candidates, summary = detection.suggest_holds(source, [0, 1, 0])
    assert candidates == []
    assert summary.rejected_grip == 1
    assert summary.rejected_geometry == 0


def test_wall_return_removes_added_shelf_and_preserves_the_scan():
    hold = texture_hold(amplitude=0., backing=24.)
    hold.edge_style = 'legacy'
    hold.solid = build_solid(hold)
    assert support_ledge_area(hold) > 25.
    hold.edge_style = 'wall-ramp'
    hold.solid = build_solid(hold)
    assert support_ledge_area(hold) == 0.
    # For a flat cut, every added surface stays within 30 degrees of the wall.
    assert hold.solid.face_normals[generated_faces(hold), 2].min() >= np.cos(np.radians(30))
    patch, _ = oriented_patch(hold)
    assert cKDTree(hold.solid.vertices).query(patch.vertices)[0].max() < 1e-7
    validate_solid(hold.solid)


def test_high_abrupt_cut_is_rejected_instead_of_making_a_side_grip():
    mesh = demo_scan('rounded', 13)
    xy = mesh.triangles_center[:, :2]
    selected = np.flatnonzero(~((xy[:, 0] > 10) & (xy[:, 1] > 10)))
    old = extract_hold(Revision(mesh), selected, edge_style='legacy')
    assert support_ledge_area(old) >= 25.
    old.edge_style = 'wall-ramp'
    with pytest.raises(GeometryError, match='artificial ledge'):
        build_solid(old)


def test_legacy_shape_and_mounts_survive_opening_and_explicit_upgrade(tmp_path):
    hold = texture_hold(amplitude=0., backing=12.)
    hold.edge_style = 'legacy'
    hold.solid = build_solid(hold)
    hold.mounts = [Mount(0., 0., default_profiles()['bolt'], manual=True)]
    project = Project(hold.patch.copy(), 'Migration fixture', holds=[hold])
    saved, legacy = tmp_path / 'new.rockform', tmp_path / 'old.rockform'
    save_project(project, saved)
    with zipfile.ZipFile(saved) as source, zipfile.ZipFile(legacy, 'w') as target:
        for name in source.namelist():
            content = source.read(name)
            if name == 'project.json':
                data = json.loads(content)
                data['version'] = 2
                del data['holds'][0]['edge_style']
                del data['holds'][0]['grip_review']
                content = json.dumps(data).encode()
            target.writestr(name, content)
    restored = load_project(legacy)
    updated = restored.holds[0]
    assert updated.edge_style == 'legacy'
    assert np.array_equal(updated.solid.vertices, hold.solid.vertices)
    mounts = deepcopy(updated.mounts)
    updated.edge_style = 'wall-ramp'
    updated.solid = build_solid(updated)
    updated.grip_review = assess_grip(updated, [0, 1, 0])
    save_project(restored, saved)
    reopened = load_project(saved).holds[0]
    assert reopened.mounts == mounts
    assert reopened.edge_style == 'wall-ramp'
    assert reopened.grip_review == updated.grip_review
    _, _, report = export_hold(reopened, tmp_path / 'model.stl')
    data = json.loads(open(report).read())
    assert data['checks']['watertight']
    assert not data['grip_screen']['eligible']
    assert data['actual_wall_return_mm'] >= 24.


def test_no_features_is_a_normal_ui_result():
    from rockform.app import MainWindow
    from test_ui import wait_for_worker
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    errors = []
    window.show_error = errors.append
    try:
        mesh = demo_scan('rounded', 13)
        mesh.vertices[:, 2] *= .01
        window.set_project(Project(mesh, 'Texture only'))
        window.suggest()
        wait_for_worker(window)
        assert not errors
        assert window.candidate_list.count() == 0
        assert 'No suitable broad handhold features' in window.hold_preview_text.text()
    finally:
        if window.worker:
            window.cancel_job()
            wait_for_worker(window)
        window.dirty = False
        window.close()


def test_grip_screen_can_be_cancelled():
    hold = texture_hold(amplitude=0.)
    ctx = TaskContext()
    ctx.cancelled = True
    with pytest.raises(Cancelled):
        assess_grip(hold, context=ctx)

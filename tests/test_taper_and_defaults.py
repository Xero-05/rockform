from copy import deepcopy
import json
import zipfile
import numpy as np
import pytest
from PySide6 import QtWidgets
from rockform.model import Project, Revision, HardwareProfile, Mount, GeometryError, default_profiles
from rockform.geometry import demo_scan, extract_hold, prepare_hold, build_solid, validate_solid
from rockform.mounts import generate_mounts, mount_fit, apply_mounts
from rockform.storage import save_project, load_project
from rockform.widgets import ProfileDialog


def plate(width=80., height=60., backing=12., flare=10.):
    mesh = demo_scan('rounded', 9)
    mesh.vertices[:, :2] *= [width / 280., height / 280.]
    mesh.vertices[:, 2] = 0.
    hold = extract_hold(Revision(mesh), np.arange(len(mesh.faces)))
    hold.backing, hold.edge_flare = backing, flare
    hold.solid = build_solid(hold)
    return hold


def test_defaults_are_valid_independent_and_generate_both_mount_modes():
    first, second = Project(demo_scan(), 'a'), Project(demo_scan(), 'b')
    for profile in first.profiles.values():
        profile.validate()
        assert profile.bore > 0
    first.profiles['bolt'].shaft_diameter = 7
    assert second.profiles['bolt'].shaft_diameter == 10
    hold = plate()
    for mode in ('bolt-on', 'screw-on'):
        hold.mounts = generate_mounts(hold, **dict(bolt_profile=second.profiles['bolt'],
            screw_profile=second.profiles['screw']), mode=mode, anti_rotation=False)
        result = apply_mounts(hold)
        validate_solid(result)
        assert len(hold.mounts) == (1 if mode == 'bolt-on' else 2)
        for mount in hold.mounts:
            assert not result.ray.intersects_any([[mount.x, mount.y, 30]], [[0, 0, -1]])[0]


def test_trim_follows_elongated_outline_instead_of_circle():
    mesh = demo_scan('ledge', 31)
    centers = mesh.triangles_center[:, :2]
    mesh.update_faces(~(np.abs(centers - [101., 101.]) < 7).all(axis=1))
    mesh.vertices[:, 0] *= 1.8
    mesh.remove_unreferenced_vertices()
    hold = prepare_hold(Revision(mesh), np.arange(len(mesh.faces)))
    assert 'Trimmed' in hold.preparation
    assert max(hold.patch.extents[:2]) / min(hold.patch.extents[:2]) > 1.5
    validate_solid(hold.solid)


def test_concave_outline_has_closed_taper_and_unchanged_rock_surface():
    from scipy.spatial import cKDTree
    from rockform.geometry import oriented_patch
    mesh = demo_scan('rounded', 13)
    xy = mesh.triangles_center[:, :2]
    selected = np.flatnonzero(~((xy[:, 0] > 10) & (xy[:, 1] > 10)))
    hold = extract_hold(Revision(mesh), selected)
    validate_solid(hold.solid)
    surface, _ = oriented_patch(hold)
    assert cKDTree(hold.solid.vertices).query(surface.vertices)[0].max() < 1e-7
    ring_heights = np.unique(hold.solid.vertices[:, 2])
    assert len(ring_heights[(ring_heights > 0) & (ring_heights < hold.backing)]) > 2


def test_straight_sides_remain_available_and_taper_keeps_manual_hardware():
    hold = plate(flare=0.)
    assert np.allclose(hold.solid.extents[:2], hold.patch.extents[:2])
    profiles = default_profiles()
    hold.mounts = generate_mounts(hold, profiles['bolt'], profiles['screw'], anti_rotation=False)
    hold.mounts[0].manual = True
    before = deepcopy(hold.mounts)
    hold.edge_flare, hold.scale = 12., 1.2
    hold.solid = build_solid(hold)
    assert hold.mounts == before
    validate_solid(apply_mounts(hold))


def test_thin_hold_explains_real_dimensions_and_fix():
    hold = plate(backing=6.)
    profile = default_profiles()['bolt']
    with pytest.raises(GeometryError, match=r'needs 9.0 mm.*only 6.0 mm.*at least 3.0 mm'):
        mount_fit(hold, Mount(0, 0, profile))
    hold.backing = 9.1
    hold.solid = build_solid(hold)
    mount_fit(hold, Mount(0, 0, profile))


def test_joint_placement_moves_bolt_to_leave_room_for_second_hole():
    hold = plate(width=41., height=27., flare=0.)
    profiles = default_profiles()
    mounts = generate_mounts(hold, profiles['bolt'], profiles['screw'], anti_rotation=True)
    assert len(mounts) == 2
    assert abs(mounts[0].x) > 3.
    hold.mounts = mounts
    validate_solid(apply_mounts(hold))


def test_impossible_optional_screw_explains_action_without_partial_layout():
    hold = plate(width=27., height=27., flare=0.)
    profiles = default_profiles()
    with pytest.raises(GeometryError, match='Turn off Add anti-rotation screw'):
        generate_mounts(hold, profiles['bolt'], profiles['screw'], anti_rotation=True)
    assert not hold.mounts


def test_zero_depth_uses_shaft_clearance_without_a_head_seat():
    profile = HardwareProfile('Through screw', 5, .5, 15, 0)
    hold = plate(width=18., height=18., flare=0.)
    hold.mounts = [Mount(0, 0, profile)]
    validate_solid(apply_mounts(hold))
    assert profile.seat_diameter == 5.5
    # A through-hole has no flat seat floor; a curved front is valid too.
    mesh = demo_scan('rounded', 17)
    curved = extract_hold(Revision(mesh), np.arange(len(mesh.faces)))
    curved.mounts = [Mount(0, 0, profile)]
    validate_solid(apply_mounts(curved))


def test_legacy_project_fills_empty_profiles_preserves_design_and_manual_edits(tmp_path):
    project = Project(demo_scan(), 'legacy')
    hold = plate(flare=0.)
    hold.mounts = [Mount(0, 0, HardwareProfile('My hardware', 7, .3, 13, 4), manual=True)]
    project.holds = [hold]
    project.profiles['screw'] = HardwareProfile('Custom screw', 4., .2, 8., 2., 'countersink')
    saved = tmp_path / 'new.rockform'
    legacy = tmp_path / 'legacy.rockform'
    save_project(project, saved)
    with zipfile.ZipFile(saved) as source, zipfile.ZipFile(legacy, 'w') as target:
        for name in source.namelist():
            content = source.read(name)
            if name == 'project.json':
                data = json.loads(content)
                data['version'] = 1
                del data['holds'][0]['edge_flare']
                for key in ('shaft_diameter', 'clearance', 'head_diameter', 'recess_depth'):
                    data['profiles']['bolt'][key] = 0
                content = json.dumps(data).encode()
            target.writestr(name, content)
    restored = load_project(legacy)
    assert restored.profiles['bolt'].shaft_diameter == 10
    assert restored.profiles['screw'] == project.profiles['screw']
    assert restored.holds[0].edge_flare == 0
    assert restored.holds[0].mounts == hold.mounts
    assert np.array_equal(restored.holds[0].solid.vertices, hold.solid.vertices)
    restored.holds[0].edge_flare = 15
    restored.holds[0].solid = build_solid(restored.holds[0])
    save_project(restored, saved)
    assert load_project(saved).holds[0].edge_flare == 15
    assert load_project(saved).holds[0].mounts == hold.mounts


def test_profile_dialog_live_limits_and_presets():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = ProfileDialog(default_profiles()['bolt'])
    try:
        assert '24.5 mm' in dialog.guidance.text()
        assert '9.0 mm' in dialog.guidance.text()
        dialog.fields['shaft_diameter'].setValue(0)
        assert not dialog.save_button.isEnabled()
        assert 'shaft' in dialog.error.text()
        dialog.presets.setCurrentIndex(2)
        assert dialog.save_button.isEnabled()
        assert dialog.profile() == default_profiles()['screw']
        assert '5.5 mm' in dialog.guidance.text()
    finally:
        dialog.close()

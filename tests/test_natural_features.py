from copy import deepcopy
import numpy as np
import pytest
import trimesh
from scipy.spatial import cKDTree
from rockform.model import Revision, Project, GeometryError, TaskContext, Cancelled, default_profiles
from rockform.geometry import demo_scan, build_solid, validate_solid, generated_faces, oriented_patch
from rockform.features import wall_reference, feature_regions, protrusion_hold, prepare_feature
from rockform.detection import suggest_holds
from rockform.mounts import generate_mounts, apply_mounts
from rockform.storage import save_project, load_project, export_hold


def wall_with_feature(kind='protrusion', resolution=31):
    mesh = demo_scan('rounded', resolution)
    x, y = mesh.vertices[:, :2].T
    depth = 28. if kind == 'protrusion' else -16.
    mesh.vertices[:, 2] = depth * np.exp(-((x / 45.) ** 4 + (y / 38.) ** 4))
    return mesh


def isolated(mesh):
    candidates, summary = suggest_holds(Revision(mesh), [0, 1, 0])
    assert len(candidates) == 1, summary
    return candidates[0].prepared_hold


def test_protrusion_has_natural_outline_zero_backing_and_no_generated_side():
    mesh = wall_with_feature()
    hold = isolated(mesh)
    assert hold.feature_kind == 'protrusion'
    assert hold.backing == 0. and hold.edge_flare == 0.
    assert hold.edge_style == 'natural-base'
    assert hold.solid.extents[0] < mesh.extents[0] / 2
    assert hold.solid.extents[1] < mesh.extents[1] / 2
    assert np.allclose(hold.solid.extents, hold.patch.extents, atol=1e-6)
    assert not generated_faces(hold).any()  # The only added faces are the flat underside.
    assert hold.solid.bounds[0, 2] == pytest.approx(0., abs=1e-7)
    world = trimesh.transform_points(hold.patch.vertices, np.linalg.inv(hold.frame))
    assert trimesh.proximity.closest_point(mesh, world)[1].max() < 1e-6
    validate_solid(hold.solid)


def test_wall_plane_ignores_protrusion_and_is_rotation_equivariant():
    source = wall_with_feature()
    reference = wall_reference(source)
    assert reference.noise < .4
    first = isolated(source)
    rotation = trimesh.transformations.euler_matrix(.4, -.6, .8)
    rotation[:3, 3] = [150., -80., 200.]
    moved = source.copy()
    moved.apply_transform(rotation)
    up = rotation[:3, :3] @ [0., 1., 0.]
    candidates, summary = suggest_holds(Revision(moved), up)
    assert len(candidates) == 1, summary
    second = candidates[0].prepared_hold
    assert second.backing == 0.
    assert second.solid.volume == pytest.approx(first.solid.volume, rel=.015)
    # PCA axes in a square wall can rotate in-plane; physical geometry must not.
    restored_normal = second.frame[2, :3] @ rotation[:3, :3]
    assert restored_normal @ first.frame[2, :3] > .9999
    world = trimesh.transform_points(second.patch.vertices, np.linalg.inv(second.frame))
    world = trimesh.transform_points(world, np.linalg.inv(rotation))
    assert trimesh.proximity.closest_point(source, world)[1].max() < 1e-6


def test_recess_keeps_its_inward_surface_and_gets_only_conditional_support():
    source = wall_with_feature('recess')
    hold = isolated(source)
    assert hold.feature_kind == 'recess'
    assert hold.backing == 3.
    assert hold.edge_style == 'wall-ramp'
    assert generated_faces(hold).any()
    assert hold.patch.vertices[:, 2].min() < -10.
    assert hold.patch.vertices[:, 2].max() == pytest.approx(0., abs=1e-6)
    # A pocket is not inverted into a bump or filled across its mouth.
    surface, _ = oriented_patch(hold)
    hits, _, _ = surface.ray.intersects_location([[0, 0, 50]], [[0, 0, -1]])
    assert len(hits) == 1 and hits[0, 2] < 5.
    validate_solid(hold.solid)


def test_small_painted_seed_expands_to_feature_not_a_rectangular_plate():
    mesh = wall_with_feature()
    centers = mesh.triangles_center
    seed = np.flatnonzero((np.abs(centers[:, :2]) < 22.).all(axis=1))
    hold = prepare_feature(Revision(mesh), seed)
    assert hold.backing == 0.
    assert len(hold.face_indices) > len(seed)
    assert hold.solid.extents[:2].min() > 70.
    assert hold.solid.extents[:2].max() < 140.


def test_unfinished_feature_at_scan_edge_is_not_capped_into_a_fake_hold():
    mesh = wall_with_feature()
    mesh = trimesh.intersections.slice_mesh_plane(mesh, [1, 0, 0], [-10, 0, 0], cap=False)
    ref, regions = feature_regions(Revision(mesh))
    assert regions
    with pytest.raises(GeometryError, match='open scan edge'):
        protrusion_hold(Revision(mesh), regions[0][1], ref)


def test_feature_with_missing_center_needs_repair_not_a_cap_over_the_grip():
    mesh = wall_with_feature()
    mesh.update_faces(~(np.abs(mesh.triangles_center[:, :2]) < 6.).all(axis=1))
    mesh.remove_unreferenced_vertices()
    ref, regions = feature_regions(Revision(mesh))
    with pytest.raises(GeometryError, match='missing scan data'):
        protrusion_hold(Revision(mesh), regions[0][1], ref)


def test_two_separate_protrusions_produce_two_natural_outlines():
    mesh = demo_scan('rounded', 45)
    x, y = mesh.vertices[:, :2].T
    mesh.vertices[:, 2] = (22 * np.exp(-(((x - 65) / 26) ** 4 + (y / 32) ** 4))
                          + 30 * np.exp(-(((x + 65) / 28) ** 4 + (y / 30) ** 4)))
    candidates, summary = suggest_holds(Revision(mesh), [0, 1, 0])
    assert len(candidates) == 2, summary
    for candidate in candidates:
        assert candidate.prepared_hold.backing == 0.
        assert candidate.prepared_hold.solid.extents[:2].max() < 100.
    assert not set(candidates[0].faces).intersection(candidates[1].faces)


def test_natural_base_mounts_save_resize_export_without_padding(tmp_path):
    mesh = wall_with_feature()
    hold = isolated(mesh)
    profiles = default_profiles()
    for mode in ('bolt-on', 'screw-on'):
        hold.mounts = generate_mounts(hold, profiles['bolt'], profiles['screw'], mode=mode, anti_rotation=False)
        hold.mounts[0].manual = True
        saved_mounts = deepcopy(hold.mounts)
        hold.scale = 1.1
        hold.solid = build_solid(hold)
        assert hold.mounts == saved_mounts
        validate_solid(apply_mounts(hold))
    project = Project(mesh, 'Natural feature', holds=[hold])
    path = tmp_path / 'feature.rockform'
    save_project(project, path)
    restored = load_project(path).holds[0]
    assert restored.feature_kind == 'protrusion'
    assert restored.extraction == hold.extraction
    assert restored.backing == 0.
    assert restored.mounts == hold.mounts
    result, stl, _ = export_hold(restored, tmp_path / 'feature.stl')
    reimported = trimesh.load_mesh(stl)
    validate_solid(reimported)
    assert np.allclose(result.extents, reimported.extents, atol=1e-4)
    assert np.allclose(result.extents[:2], hold.solid.extents[:2], atol=1e-5)
    assert result.volume < hold.solid.volume


def test_wall_fitting_is_cancellable_and_does_not_change_source():
    mesh = wall_with_feature()
    before = mesh.vertices.copy()
    ctx = TaskContext()
    ctx.cancelled = True
    with pytest.raises(Cancelled):
        wall_reference(mesh, ctx)
    assert np.array_equal(before, mesh.vertices)


def test_preview_edits_and_reextraction_keep_saved_design_intact():
    from PySide6 import QtWidgets
    from rockform.app import MainWindow
    from test_ui import wait_for_worker
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    errors = []
    window.show_error = errors.append
    try:
        window.set_project(Project(wall_with_feature(resolution=25), 'Preview fixture'))
        window.selected[:] = True
        window.create_hold()
        wait_for_worker(window)
        assert not errors
        original = window.hold_preview.solid.extents.copy()
        assert window.backing.value() == 0.
        assert not window.edge_flare.isEnabled()
        window.hold_scale.setValue(110.)
        window.rebuild_hold()
        wait_for_worker(window)
        assert not errors
        assert not window.project.holds
        assert np.allclose(window.hold_preview.solid.extents, original * 1.1)
        window.accept_hold_preview()
        saved = window.hold
        saved.mounts = generate_mounts(saved, **dict(bolt_profile=window.project.profiles['bolt'],
                screw_profile=window.project.profiles['screw']), anti_rotation=False)
        saved.mounts[0].manual = True
        before = deepcopy(saved.mounts)
        window.use_tapered_defaults()
        wait_for_worker(window)
        assert not errors
        assert window.hold_preview is not None
        assert saved.mounts == before
        assert window.project.holds == [saved]
        assert not window.hold_preview.mounts
    finally:
        if window.worker:
            window.cancel_job()
            wait_for_worker(window)
        window.dirty = False
        window.close()

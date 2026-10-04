from copy import deepcopy
import numpy as np
import pytest
import trimesh
from PySide6 import QtWidgets
from rockform.model import Revision, Project, GeometryError, TaskContext, Cancelled
from rockform.features import prepare_feature, feature_regions, protrusion_hold, raised_patches, WallReference
from rockform.detection import suggest_holds
from rockform.geometry import oriented_patch, generated_faces, validate_solid, build_solid, boundary_loops
from rockform.storage import save_project, load_project, export_hold
from rockform.mounts import generate_mounts
from rockform.app import MainWindow
from test_natural_features import wall_with_feature
from test_ui import wait_for_worker


def textured_feature():
    mesh = wall_with_feature(resolution=41)
    x, y = mesh.vertices[:, :2].T
    mesh.vertices[:, 2] += .45 * np.sin(x / 3.5) * np.cos(y / 4.)
    return mesh


def world_surface(hold):
    result = hold.patch.copy()
    result.apply_transform(np.linalg.inv(hold.frame))
    return result


def test_default_margin_preserves_texture_root_and_protection_without_changing_scan():
    mesh = textured_feature()
    revision = Revision(mesh)
    revision.protected[(mesh.vertices[:, 0] > 55) & (np.abs(mesh.vertices[:, 1]) < 20)] = True
    before = mesh.vertices.copy(), mesh.faces.copy()
    selection = np.arange(len(mesh.faces))
    root = prepare_feature(revision, selection, contact_margin_mm=0.)
    hold = prepare_feature(revision, selection)
    assert hold.extraction['contact_margin_mm'] == 10.
    assert hold.backing == 3. and hold.edge_style == 'wall-ramp'
    assert hold.patch.area > root.patch.area
    assert (hold.patch.extents[:2] > root.patch.extents[:2] + 15.).all()
    world = world_surface(hold)
    assert trimesh.proximity.closest_point(mesh, world.vertices)[1].max() < 1e-6
    assert trimesh.proximity.closest_point(world, world_surface(root).vertices)[1].max() < 1e-6
    assert trimesh.proximity.closest_point(world, world_surface(root).triangles_center)[1].max() < 1e-6
    # The band includes rough wall below the previous noise-height cut.
    assert hold.patch.vertices[:, 2].min() < -.9
    assert set(root.face_indices) <= set(hold.face_indices)
    assert len(hold.protected_triangles) > len(root.protected_triangles)
    surface, _ = oriented_patch(hold)
    assert trimesh.proximity.closest_point(hold.solid, surface.triangles_center)[1].max() < 1e-6
    assert generated_faces(hold).any()
    assert hold.grip_review == {}  # Extraction itself does not invent grip evidence.
    assert np.array_equal(before[0], mesh.vertices) and np.array_equal(before[1], mesh.faces)
    validate_solid(hold.solid)


def test_contact_margin_is_measured_in_wall_plane_and_survives_rotation():
    from shapely.geometry import Polygon
    import shapely
    mesh = wall_with_feature()
    revision = Revision(mesh)
    reference, regions = feature_regions(revision)
    root = regions[0][1]
    root_polygon = Polygon(root.vertices[boundary_loops(root)[0], :2])
    hold = protrusion_hold(revision, root, reference, contact_margin_mm=15.)
    world = world_surface(hold)
    world.apply_transform(reference.frame)
    distances = shapely.distance(root_polygon, shapely.points(world.vertices[boundary_loops(world)[0], :2]))
    # Buffered curves are polygonal, within 0.3 mm of the requested offset.
    assert distances.min() > 14.7 and distances.max() <= 15. + 1e-6
    rotation = trimesh.transformations.euler_matrix(.4, -.6, .8)
    rotation[:3, 3] = [150., -80., 200.]
    moved = mesh.copy()
    moved.apply_transform(rotation)
    second = prepare_feature(Revision(moved), np.arange(len(moved.faces)), contact_margin_mm=15.)
    assert second.solid.volume == pytest.approx(hold.solid.volume, rel=.015)
    restored = world_surface(second)
    restored.apply_transform(np.linalg.inv(rotation))
    assert trimesh.proximity.closest_point(mesh, restored.vertices)[1].max() < 1e-6


def test_suggestions_keep_separate_features_and_recess_support_is_unchanged():
    mesh = wall_with_feature(resolution=41)
    x, y = mesh.vertices[:, :2].T
    mesh.vertices[:, 2] = (22 * np.exp(-(((x - 65) / 26) ** 4 + (y / 32) ** 4))
                          + 30 * np.exp(-(((x + 65) / 28) ** 4 + (y / 30) ** 4)))
    candidates, summary = suggest_holds(Revision(mesh), [0, 1, 0])
    assert len(candidates) == 2, summary
    for candidate in candidates:
        assert candidate.prepared_hold.extraction['contact_margin_mm'] == 10.
        assert candidate.prepared_hold.grip_review['eligible']
        assert candidate.prepared_hold.solid.extents[:2].max() < 130.
    recess = wall_with_feature('recess')
    a = prepare_feature(Revision(recess), np.arange(len(recess.faces)), contact_margin_mm=0.)
    b = prepare_feature(Revision(recess), np.arange(len(recess.faces)), contact_margin_mm=20.)
    assert b.extraction['contact_margin_mm'] == 0.
    assert b.backing == 3.
    assert np.allclose(a.solid.extents, b.solid.extents)
    assert np.allclose(a.patch.vertices, b.patch.vertices)


def test_contact_margin_preserves_overhang_beyond_root_and_vertical_grip_faces():
    angles = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    rings = [(90., 0.), (30., 0.), (22., 5.), (42., 25.), (42., 28.)]
    vertices = np.vstack([np.column_stack([r * np.cos(angles), r * np.sin(angles), np.full(48, z)])
                          for r, z in rings] + [np.array([[0., 0., 30.]])])
    faces = []
    for ring in range(len(rings) - 1):
        for i in range(48):
            j = (i + 1) % 48
            a, b, c, d = ring * 48 + i, ring * 48 + j, (ring + 1) * 48 + j, (ring + 1) * 48 + i
            faces.extend([[a, b, c], [a, c, d]])
    faces.extend([[192 + i, 192 + (i + 1) % 48, 240] for i in range(48)])
    mesh = trimesh.Trimesh(vertices, faces, process=False)
    revision = Revision(mesh)
    reference = WallReference(np.eye(4), 1., .8, 0.)
    seed = raised_patches(revision, reference)[0]
    hold = protrusion_hold(revision, seed, reference, contact_margin_mm=5.)
    retained = world_surface(hold)
    assert trimesh.proximity.closest_point(retained, seed.triangles_center)[1].max() < 1e-6
    assert trimesh.proximity.closest_point(retained, mesh.vertices[96:240])[1].max() < 1e-6
    assert trimesh.proximity.closest_point(mesh, retained.vertices)[1].max() < 1e-6
    assert (retained.face_normals[:, 2] < -.1).any()  # Undercut is still present.
    validate_solid(hold.solid)


def test_contact_band_does_not_cap_missing_scan_data_or_invent_wall_texture():
    mesh = wall_with_feature(resolution=41)
    mesh = trimesh.intersections.slice_mesh_plane(mesh, [1, 0, 0], [-66, 0, 0], cap=False)
    revision = Revision(mesh)
    selection = np.arange(len(mesh.faces))
    root = prepare_feature(revision, selection, contact_margin_mm=0.)
    assert root.solid.is_volume
    with pytest.raises(GeometryError, match='open scan edge'):
        prepare_feature(revision, selection, contact_margin_mm=20.)
    mesh = wall_with_feature(resolution=41)
    centers = mesh.triangles_center
    mesh.update_faces(~((centers[:, 0] > 59) & (centers[:, 0] < 64) & (np.abs(centers[:, 1]) < 8)))
    mesh.remove_unreferenced_vertices()
    with pytest.raises(GeometryError, match='missing scan data'):
        prepare_feature(Revision(mesh), np.arange(len(mesh.faces)), contact_margin_mm=20.)


def test_margin_validation_and_cancellation():
    mesh = wall_with_feature()
    revision = Revision(mesh)
    for margin in (-1., 51., float('nan')):
        with pytest.raises(GeometryError, match='Surrounding rock'):
            prepare_feature(revision, np.arange(len(mesh.faces)), contact_margin_mm=margin)
    ctx = TaskContext()
    ctx.cancelled = True
    with pytest.raises(Cancelled):
        prepare_feature(revision, np.arange(len(mesh.faces)), context=ctx)


def test_contact_geometry_mounts_and_metadata_survive_save_resize_and_export(tmp_path):
    project = Project(textured_feature(), 'Contact fixture')
    hold = prepare_feature(project.current, np.arange(len(project.current.mesh.faces)))
    hold.mounts = generate_mounts(hold, project.profiles['bolt'], project.profiles['screw'], anti_rotation=False)
    hold.mounts[0].manual = True
    mounts = deepcopy(hold.mounts)
    hold.scale = 1.1
    hold.solid = build_solid(hold)
    assert hold.mounts == mounts
    project.holds = [hold]
    save_project(project, tmp_path / 'contact.rockform')
    restored = load_project(tmp_path / 'contact.rockform').holds[0]
    assert restored.extraction == hold.extraction
    assert np.array_equal(restored.patch.vertices, hold.patch.vertices)
    assert restored.mounts == mounts
    result, stl, report = export_hold(restored, tmp_path / 'contact.stl')
    imported = trimesh.load_mesh(stl)
    validate_solid(imported)
    assert np.allclose(imported.extents, result.extents, atol=1e-4)
    import json
    from pathlib import Path
    assert json.loads(Path(report).read_text())['extraction']['contact_margin_mm'] == 10.


def test_margin_control_reextracts_cached_suggestion_and_restores_workspace(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    errors = []
    window.show_error = errors.append
    try:
        window.set_project(Project(wall_with_feature(resolution=25), 'Contact workflow'))
        assert window.contact_margin.value() == 10.
        window.suggest()
        wait_for_worker(window)
        assert not errors and len(window.candidates) == 1
        window.choose_candidate(0)
        window.contact_margin.setValue(15.)
        window.create_hold()
        wait_for_worker(window)
        assert not errors
        assert window.hold_preview.extraction['contact_margin_mm'] == 15.
        window.accept_hold_preview()
        saved = window.hold
        saved.mounts = generate_mounts(saved, window.project.profiles['bolt'], window.project.profiles['screw'], anti_rotation=False)
        saved.mounts[0].manual = True
        old_mounts = deepcopy(saved.mounts)
        window.contact_margin.setValue(0.)
        window.use_tapered_defaults()
        wait_for_worker(window)
        assert not errors
        assert window.hold_preview.extraction['contact_margin_mm'] == 0.
        assert window.hold_preview.backing == 0.
        assert saved.extraction['contact_margin_mm'] == 15. and saved.mounts == old_mounts
        window.capture_workspace()
        path = tmp_path / 'contact-ui.rockform'
        save_project(window.project, path)
        window.set_project(load_project(path), str(path))
        assert window.contact_margin.value() == 0.
        assert window.hold_preview.extraction['contact_margin_mm'] == 0.
        assert window.hold.extraction['contact_margin_mm'] == 15.
        window.prepare_boundary.setChecked(False)
        assert not window.contact_margin.isEnabled()
        window.capture_workspace()
        save_project(window.project, path)
        window.set_project(load_project(path), str(path))
        assert not window.prepare_boundary.isChecked() and not window.contact_margin.isEnabled()
    finally:
        if window.worker:
            window.cancel_job()
            wait_for_worker(window)
        window.dirty = False
        window.close()

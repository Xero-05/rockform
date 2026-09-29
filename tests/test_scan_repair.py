"""Regression cases for texture seams, meter metadata, gaps, and rough selection edges."""
import numpy as np
import pytest
import trimesh
from rockform.model import Revision, GeometryError, Project, TaskContext, Cancelled
from rockform.storage import import_scan, save_project, load_project
from rockform.cleanup import clean_revision, CleanupOptions, gap_info, split_touching_boundaries
from rockform.geometry import demo_scan, prepare_hold, extract_hold, boundary_loops, validate_solid


def mesh_with_gap(center=(101., 101.)):
    mesh = demo_scan("ledge", 31)
    points = mesh.triangles_center[:, :2]
    mesh.update_faces(~(np.abs(points - center) < 7).all(axis=1))
    mesh.remove_unreferenced_vertices()
    return mesh


def test_scaniverse_units_and_texture_seams(tmp_path):
    # The common edge has distinct UV records on each triangle.
    obj = """# Captured with Scaniverse
# Unit: meter
v 0 0 0
v 0.1 0 0
v 0.1 0.1 0
v 0 0.1 0
vt 0 0
vt 1 0
vt 1 1
vt 0.2 0.2
vt 0.8 0.8
vt 0 1
f 1/1 2/2 3/3
f 1/4 3/5 4/6
"""
    path = tmp_path / "scan.obj"
    path.write_text(obj)
    project = import_scan(path)
    assert project.source_units == "Meters"
    assert project.scale_factor == 1000
    assert np.allclose(project.original.extents, [100, 100, 0])
    assert len(project.original.vertices) == 4
    assert len(project.original.face_adjacency) == 1
    assert np.allclose(import_scan(path, 1).original.extents, [.1, .1, 0])
    saved = tmp_path / "units.rockform"
    save_project(project, saved)
    assert load_project(saved).source_units == "Meters"


def test_automatic_gap_repair_preview_and_undo():
    mesh = mesh_with_gap()
    # The repair must also handle inconsistent winding, which needs networkx.
    mesh.faces[::3] = mesh.faces[::3, ::-1]
    project = Project(mesh, "Noisy mesh")
    original = mesh.vertices.copy(), mesh.faces.copy()
    preview = clean_revision(project.current, CleanupOptions(auto_fill_small_gaps=True, max_gap_diameter=30))
    assert preview.added is not None
    assert preview.revision.mesh.is_winding_consistent
    assert len(boundary_loops(preview.revision.mesh)) == 1
    assert not preview.revision.mesh.is_watertight  # The scan edge must stay open.
    assert np.array_equal(mesh.vertices, original[0])
    assert np.array_equal(mesh.faces, original[1])
    project.commit(preview.revision)
    project.undo()
    assert len(gap_info(project.current.mesh)) == 2
    project.redo()
    assert len(gap_info(project.current.mesh)) == 1
    project.reset()
    assert np.array_equal(project.current.mesh.faces, original[1])


def test_protected_gap_stays_open():
    mesh = mesh_with_gap()
    revision = Revision(mesh)
    gap = next(g for g in gap_info(mesh) if not g["outer"])
    revision.protected[gap["vertices"]] = True
    preview = clean_revision(revision, CleanupOptions(auto_fill_small_gaps=True, max_gap_diameter=30))
    assert preview.added is None
    assert any("protected" in warning for warning in preview.warnings)
    assert len(boundary_loops(preview.revision.mesh)) == 2


def test_touching_boundary_repair_preserves_triangles():
    mesh = trimesh.Trimesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]],
                           [[0, 1, 2], [0, 3, 4]], process=False)
    original = mesh.triangles.copy()
    assert split_touching_boundaries(mesh) == 1
    assert np.array_equal(mesh.triangles, original)
    assert len(boundary_loops(mesh)) == 2


def test_boundary_preparation_trims_without_reconstructing_surface(tmp_path):
    mesh = mesh_with_gap()
    revision = Revision(mesh)
    indices = np.arange(len(mesh.faces))
    with pytest.raises(GeometryError):
        extract_hold(revision, indices)
    hold = prepare_hold(revision, indices)
    validate_solid(hold.solid)
    assert "Trimmed" in hold.preparation
    assert hold.solid.extents[0] < mesh.extents[0]
    world = trimesh.transform_points(hold.patch.vertices, np.linalg.inv(hold.frame))
    # Every new cut vertex still lies on an original surface triangle.
    assert trimesh.proximity.closest_point(mesh, world)[1].max() < 1e-6
    project = Project(mesh, "gap scan", revisions=[revision], holds=[hold])
    path = tmp_path / "prepared.rockform"
    save_project(project, path)
    restored = load_project(path).holds[0]
    assert restored.preparation == hold.preparation
    assert np.array_equal(restored.patch.vertices, hold.patch.vertices)


def test_preparation_does_not_cap_missing_center():
    mesh = mesh_with_gap(center=(0., 0.))
    with pytest.raises(GeometryError, match="scan repair"):
        prepare_hold(Revision(mesh), np.arange(len(mesh.faces)))


def test_preparation_cancelled():
    mesh = mesh_with_gap()
    context = TaskContext()
    context.cancelled = True
    with pytest.raises(Cancelled):
        prepare_hold(Revision(mesh), np.arange(len(mesh.faces)), context=context)

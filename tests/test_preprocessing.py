"""Physical-size, feature, topology and persistence regressions for preprocessing."""
import numpy as np
import pytest
import trimesh
from rockform.model import Revision, Project, GeometryError, TaskContext, Cancelled
from rockform.preprocessing import adaptive_decimate, crop_interest, DecimationOptions
from rockform.cleanup import clean_revision, CleanupOptions, gap_info
from rockform.geometry import demo_scan
from rockform.storage import save_project, load_project


def surface(n=101):
    x, y = np.meshgrid(np.linspace(-50, 50, n), np.linspace(-50, 50, n))
    # A narrow raised edge and a recessed feature amid a dense flat wall.
    z = np.maximum(0, 10 - np.abs(x + 20) * 4) * (np.abs(y) < 18)
    z -= 12 * np.exp(-((x - 18) ** 2 + y ** 2) / 30)
    a = (np.arange(n - 1)[:, None] * n + np.arange(n - 1)).ravel()
    faces = np.vstack([np.column_stack([a, a + 1, a + n + 1]),
                       np.column_stack([a, a + n + 1, a + n])])
    return trimesh.Trimesh(np.column_stack([x.ravel(), y.ravel(), z.ravel()]), faces, process=False)


def boundary_positions(mesh):
    edges, counts = np.unique(np.sort(mesh.edges, axis=1), axis=0, return_counts=True)
    return {tuple(sorted(map(tuple, mesh.vertices[e]))) for e in edges[counts != 2]}


def test_decimation_reduces_dense_wall_preserves_features_holes_and_original(tmp_path):
    mesh = surface()
    mesh.update_faces(~((np.abs(mesh.triangles_center[:, 0] + 35) < 2) &
                        (np.abs(mesh.triangles_center[:, 1] - 30) < 2)))
    mesh.remove_unreferenced_vertices()
    revision = Revision(mesh)
    # Protect a small grip patch, independent of automatic curvature detection.
    revision.protected = np.linalg.norm(mesh.vertices - [18, 0, -12], axis=1) < 6
    original_vertices, original_faces = mesh.vertices.copy(), mesh.faces.copy()
    result = adaptive_decimate(revision)
    reduced = result.revision.mesh
    assert len(reduced.faces) < len(mesh.faces) * .6, result.summary
    assert np.array_equal(mesh.vertices, original_vertices)
    assert np.array_equal(mesh.faces, original_faces)
    assert boundary_positions(reduced) == boundary_positions(mesh)
    assert reduced.euler_number == mesh.euler_number
    assert reduced.is_winding_consistent
    assert np.allclose(reduced.bounds, mesh.bounds)
    assert np.isclose(reduced.vertices[:, 2].max(), 10)
    assert np.isclose(reduced.vertices[:, 2].min(), -12)
    fixed = {tuple(sorted(map(tuple, t))) for t in mesh.triangles[revision.protected[mesh.faces].any(axis=1)]}
    retained = {tuple(sorted(map(tuple, t))) for t in reduced.triangles}
    assert fixed <= retained
    # Independent samples over every original triangle, not only the decimator's probes.
    assert trimesh.proximity.closest_point(reduced, mesh.triangles_center)[1].max() <= .2 + 1e-6
    project = Project(mesh, "dense wall", revisions=[revision])
    project.commit(result.revision)
    path = tmp_path / "preprocessed.rockform"
    save_project(project, path)
    restored = load_project(path)
    assert restored.current.processing == result.revision.processing
    restored.undo()
    assert np.array_equal(restored.current.mesh.faces, original_faces)
    restored.redo()
    assert len(restored.current.mesh.faces) == len(reduced.faces)
    restored.reset()
    assert np.array_equal(restored.current.mesh.vertices, original_vertices)


def test_coarse_scan_is_not_decimated_and_cancel_does_not_change_source():
    revision = Revision(demo_scan('rounded', 15))
    result = adaptive_decimate(revision)
    assert not result.revision.processing['reduction_applied']
    assert len(result.revision.mesh.faces) == len(revision.mesh.faces)
    context = TaskContext()
    context.cancelled = True
    with pytest.raises(Cancelled):
        adaptive_decimate(revision, context=context)
    with pytest.raises(GeometryError):
        adaptive_decimate(revision, DecimationOptions(tolerance_mm=-1))


def test_curved_dense_scan_retries_and_stays_inside_tolerance():
    revision = Revision(demo_scan('rounded', 151))
    result = adaptive_decimate(revision)
    assert result.revision.processing['reduction_applied']
    assert len(result.revision.mesh.faces) < len(revision.mesh.faces) * .8
    assert result.revision.processing['sampled_error_mm'] <= .2
    assert np.allclose(result.revision.mesh.bounds, revision.mesh.bounds)


def test_crop_excludes_broken_geometry_before_repair_and_preserves_surface():
    mesh = surface(51)
    # Broken fragment far away; crop must remove it before the repair pipeline.
    debris = trimesh.Trimesh([[500,0,0], [501,0,0], [500,1,1]], [[0,1,2]], process=False)
    mesh = trimesh.util.concatenate([mesh, debris])
    revision = Revision(mesh)
    selected = np.linalg.norm(mesh.triangles_center[:, :2], axis=1) < 25
    preview = crop_interest(revision, selected, 10)
    cropped = preview.revision
    assert preview.removed is not None
    assert cropped.crop_boundary.any()
    assert cropped.mesh.bounds[1, 0] < 50
    assert len(cropped.mesh.faces) > selected.sum()  # Surrounding-wall margin retained.
    original_triangles = {tuple(sorted(map(tuple, t))) for t in mesh.triangles}
    assert {tuple(sorted(map(tuple, t))) for t in cropped.mesh.triangles} <= original_triangles
    result = clean_revision(cropped, CleanupOptions(auto_fill_small_gaps=True, max_gap_diameter=100))
    assert result.added is None
    assert result.revision.mesh.bounds[1, 0] < 50
    assert result.revision.crop_boundary.any()
    assert result.revision.processing['area_of_interest'] == cropped.processing['area_of_interest']
    assert len(mesh.faces) == 5001


def test_intentionally_excluded_interior_area_is_never_filled_and_survives_save(tmp_path):
    mesh = surface(51)
    centers = mesh.triangles_center
    selected = (np.abs(centers[:, :2]) < 40).all(axis=1)
    selected &= ~((np.abs(centers[:, 0] + 28) < 4) & (np.abs(centers[:, 1] - 24) < 4))
    revision = Revision(mesh)
    preview = crop_interest(revision, selected, 0)
    project = Project(mesh, 'crop', revisions=[revision], pending_cleanup=preview)
    path = tmp_path / 'pending-area.rockform'
    save_project(project, path)
    restored = load_project(path)
    assert restored.cursor == 0
    cropped = restored.pending_cleanup.revision
    assert np.array_equal(cropped.crop_boundary, preview.revision.crop_boundary)
    assert cropped.processing['operation'] == 'area-of-interest'
    assert len(gap_info(cropped.mesh)) == 2
    result = clean_revision(cropped, CleanupOptions(auto_fill_small_gaps=True, max_gap_diameter=100))
    assert result.added is None
    assert len(gap_info(result.revision.mesh)) == 2
    assert any('cut edge' in warning for warning in result.warnings)
    restored.commit(result.revision)
    restored.undo()
    assert np.array_equal(restored.current.mesh.faces, mesh.faces)


def test_invalid_crop_and_cancel_leave_revision_unchanged():
    revision = Revision(surface(21))
    with pytest.raises(GeometryError, match='Paint'):
        crop_interest(revision, np.zeros(len(revision.mesh.faces), bool))
    with pytest.raises(GeometryError, match='entire'):
        crop_interest(revision, np.ones(len(revision.mesh.faces), bool))
    context = TaskContext(); context.cancelled = True
    with pytest.raises(Cancelled):
        crop_interest(revision, np.ones(len(revision.mesh.faces), bool), context=context)


def test_format_seven_revision_defaults_remain_readable(tmp_path):
    import io, json, zipfile
    project = Project(surface(11), 'old archive')
    modern, old = tmp_path / 'new.rockform', tmp_path / 'old.rockform'
    save_project(project, modern)
    with zipfile.ZipFile(modern) as source, zipfile.ZipFile(old, 'w') as target:
        for name in source.namelist():
            data = source.read(name)
            if name == 'project.json':
                meta = json.loads(data); meta['version'] = 7
                for record in meta['revisions']:
                    record.pop('processing')
                data = json.dumps(meta).encode()
            elif name.startswith('revisions/'):
                with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
                    stream = io.BytesIO()
                    np.savez_compressed(stream, **{key: arrays[key] for key in arrays.files if key != 'crop_boundary'})
                    data = stream.getvalue()
            target.writestr(name, data)
    loaded = load_project(old)
    assert not loaded.current.crop_boundary.any()
    assert loaded.current.processing == {}

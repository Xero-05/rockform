from copy import deepcopy
import json
import numpy as np
import pytest
import trimesh
from rockform.model import Project, Revision, HardwareProfile, Mount, GeometryError, TaskContext, Cancelled
from rockform.geometry import demo_scan, extract_hold, build_solid, validate_solid, self_intersections, boundary_loops
from rockform.cleanup import clean_revision, CleanupOptions, gap_info
from rockform.mounts import generate_mounts, apply_mounts, validate_mounts
from rockform.storage import save_project, load_project, export_hold, import_scan
from rockform.detection import detect_candidates


@pytest.fixture
def revision():
    return Revision(demo_scan("rounded", resolution=17))


@pytest.fixture
def hold(revision):
    mesh = revision.mesh
    selected = np.flatnonzero((np.abs(mesh.triangles[:, :, :2]) < 95).all(axis=(1, 2)))
    return extract_hold(revision, selected)


@pytest.fixture
def profiles():
    # Synthetic test hardware, not a certified mounting specification.
    return HardwareProfile("Test bolt", 10, .4, 18, 6), HardwareProfile("Test screw", 5, .3, 10, 3, "countersink")


def test_extract_preserves_contact_surface(hold):
    validate_solid(hold.solid)
    assert hold.solid.bounds[0, 2] == pytest.approx(0)
    assert hold.solid.bounds[1, 2] == pytest.approx(np.ptp(hold.patch.vertices[:, 2]) + 10)
    assert np.allclose(hold.patch.vertices[:, :2].min(axis=0), hold.solid.vertices[:, :2].min(axis=0))


@pytest.mark.parametrize("mode,count,anti", [("bolt-on", 2, True), ("bolt-on", 2, False), ("screw-on", 2, False), ("screw-on", 4, False)])
def test_mount_modes_produce_printable_solid(hold, profiles, mode, count, anti):
    hold.mounts = generate_mounts(hold, *profiles, mode, count, anti)
    solid = apply_mounts(hold)
    assert solid.is_volume
    assert solid.volume < hold.solid.volume
    assert len(hold.mounts) == (1 + anti if mode == "bolt-on" else count)
    assert solid.bounds[0, 2] == pytest.approx(0)
    # Centerline should pass all the way through each hole.
    for mount in hold.mounts:
        hits = solid.ray.intersects_any([[mount.x, mount.y, solid.bounds[1, 2] + 5]], [[0, 0, -1]])
        assert not hits[0]


def test_hardware_dimensions_unchanged_after_resizing(hold, profiles):
    hold.mounts = generate_mounts(hold, *profiles)
    hold.mounts[0].manual = True
    old = deepcopy(hold.mounts)
    hold.scale = 1.3
    hold.solid = build_solid(hold)
    assert hold.mounts == old
    validate_mounts(hold)


def test_invalid_edge_and_overlapping_holes(hold, profiles):
    hold.mounts = [Mount(1000, 0, profiles[0])]
    with pytest.raises(GeometryError, match="boundary"):
        validate_mounts(hold)
    hold.mounts = [Mount(0, 0, profiles[0]), Mount(0, 0, profiles[1])]
    with pytest.raises(GeometryError, match="overlap"):
        validate_mounts(hold)


def test_excessive_recess_rejected(hold, profiles):
    profiles[0].recess_depth = 100
    hold.mounts = [Mount(0, 0, profiles[0])]
    with pytest.raises(GeometryError, match="material"):
        validate_mounts(hold)


def test_protected_grip_excludes_mounts(hold, profiles):
    hold.protected_points = hold.patch.vertices.copy()
    with pytest.raises(GeometryError, match="Could not place"):
        generate_mounts(hold, *profiles)


def test_protection_covers_face_interiors_between_vertices(profiles):
    mesh = demo_scan("rounded", 5)
    revision = Revision(mesh, protected=np.ones(len(mesh.vertices), dtype=bool))
    hold = extract_hold(revision, np.arange(len(mesh.faces)))
    hold.mounts = [Mount(30, 30, profiles[1])]
    with pytest.raises(GeometryError, match="protected"):
        validate_mounts(hold)


def test_missing_patch_rejected(revision):
    mesh = revision.mesh.copy()
    centers = mesh.triangles_center
    mesh.update_faces(np.linalg.norm(centers[:, :2], axis=1) > 24)
    with pytest.raises(GeometryError, match="missing interior"):
        extract_hold(Revision(mesh), np.arange(len(mesh.faces)))


def test_cleanup_protects_regions_and_original(revision):
    revision.protected[np.linalg.norm(revision.mesh.vertices[:, :2], axis=1) < 45] = True
    original = revision.mesh.vertices.copy()
    preview = clean_revision(revision, CleanupOptions(fragment_percent=0, smooth_iterations=4, reduction=.35))
    assert np.array_equal(revision.mesh.vertices, original)
    from scipy.spatial import cKDTree
    protected = original[revision.protected]
    assert cKDTree(preview.revision.mesh.vertices).query(protected)[0].max() < 1e-7
    assert len(preview.revision.mesh.faces) < len(revision.mesh.faces)


def test_fragments_preview_and_undo(revision):
    small = trimesh.creation.icosphere(subdivisions=1, radius=.4)
    small.apply_translation([200, 0, 30])
    mesh = trimesh.util.concatenate([revision.mesh, small])
    project = Project(mesh, "test")
    preview = clean_revision(project.current, CleanupOptions(fragment_percent=1))
    assert preview.removed is not None
    assert len(preview.revision.mesh.faces) < len(mesh.faces)
    project.commit(preview.revision)
    project.undo()
    assert len(project.current.mesh.faces) == len(mesh.faces)
    project.redo()
    assert project.current.id == preview.revision.id
    project.reset()
    assert np.array_equal(project.current.mesh.vertices, project.original.vertices)


def test_selected_gap_repair_keeps_outer_boundary():
    mesh = demo_scan("ledge", 31)
    center = mesh.triangles_center
    mesh.update_faces(~((center[:, 0] > 95) & (center[:, 0] < 109) & (center[:, 1] > 95) & (center[:, 1] < 109)))
    revision = Revision(mesh)
    gaps = gap_info(mesh)
    interior = [g for g in gaps if not g["outer"]]
    assert len(interior) == 1
    preview = clean_revision(revision, CleanupOptions(fragment_percent=0, gap_ids=[interior[0]["id"]], max_gap_diameter=30))
    assert len(boundary_loops(preview.revision.mesh)) == 1
    assert not preview.revision.mesh.is_watertight
    preview = clean_revision(revision, CleanupOptions(gap_ids=[g["id"] for g in gaps if g["outer"]]))
    assert any("outer" in warning for warning in preview.warnings)
    assert not preview.revision.mesh.is_watertight


def test_roundtrip_and_revision_isolation(revision, hold, profiles, tmp_path):
    project = Project(revision.mesh.copy(), "source.obj", revisions=[revision])
    hold.mounts = generate_mounts(hold, *profiles)
    hold.mounts[0].x += 1
    hold.mounts[0].manual = True
    hold.result = apply_mounts(hold)
    project.holds.append(hold)
    project.profiles = dict(zip(["bolt", "screw"], profiles))
    preview = clean_revision(revision, CleanupOptions(smooth_iterations=2))
    project.commit(preview.revision)
    path = tmp_path / "test.rockform"
    save_project(project, path)
    restored = load_project(path)
    assert restored.holds[0].revision_id == revision.id
    assert restored.current.id != revision.id
    assert restored.holds[0].mounts == hold.mounts
    assert np.array_equal(restored.holds[0].patch.vertices, hold.patch.vertices)
    assert restored.holds[0].result.is_volume


def test_export_dimensions_units_and_stale_result(hold, profiles, tmp_path):
    hold.mounts = generate_mounts(hold, *profiles)
    hold.result = trimesh.creation.box()  # stale preview must never leak into export
    out, stl, report = export_hold(hold, tmp_path / "hold.stl")
    reread = trimesh.load_mesh(stl)
    assert np.allclose(reread.extents, out.extents, atol=1e-4)
    assert np.allclose(reread.extents[:2], hold.solid.extents[:2], atol=1e-4)
    data = json.loads(open(report).read())
    assert data["units"] == "millimeters"
    assert np.allclose(data["dimensions_mm"], reread.extents, atol=1e-4)
    assert out.volume > 1


def test_import_meters(tmp_path):
    mesh = demo_scan(resolution=9)
    mesh.vertices /= 1000
    path = tmp_path / "scan.obj"
    mesh.export(path)
    project = import_scan(path, 1000)
    assert project.original.extents[0] == pytest.approx(280)


def test_cancel_does_not_mutate(revision):
    ctx = TaskContext()
    ctx.cancelled = True
    before = revision.mesh.vertices.copy()
    with pytest.raises(Cancelled):
        clean_revision(revision, CleanupOptions(), context=ctx)
    assert np.array_equal(before, revision.mesh.vertices)


def test_detect_candidates_and_flat_plane(revision):
    candidates = detect_candidates(revision, np.array([0., 1., 0.]))
    assert 0 < len(candidates) <= 10
    assert all(len(c.faces) > 0 for c in candidates)
    flat = revision.mesh.copy()
    flat.vertices[:, 2] = 0
    assert detect_candidates(Revision(flat), np.array([0., 1., 0.])) == []


def test_intersection_checker_detects_crossing_triangles():
    vertices = np.array([[-2,-2,0],[2,-2,0],[0,2,0],[0,-1,-1],[0,-1,1],[0,1,0]])
    mesh = trimesh.Trimesh(vertices, [[0,1,2],[3,4,5]], process=False)
    assert self_intersections(mesh)
    assert not self_intersections(trimesh.creation.box())

"""Read the Swift-produced ZIP using Windows Python, then exercise Rockform."""
from pathlib import Path
import json
import sys
import zipfile

import numpy as np

workspace = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(workspace))
from rockform.storage import import_scan, save_project, load_project, export_hold
from rockform.cleanup import clean_revision, CleanupOptions
from rockform.geometry import extract_hold, validate_solid
from rockform.model import HardwareProfile
from rockform.mounts import generate_mounts


def main():
    folder = workspace / "iphone" / "test-output"
    with zipfile.ZipFile(folder / "capture-demo.zip") as archive:
        assert archive.testzip() is None, "ZIP CRC validation failed"
        assert archive.read("reference/échelle.txt").decode() == "UTF-8 filename test"
        report = json.loads(archive.read("capture.json"))
        assert report["kind"] == "synthetic-demo"
        assert report["units"] == "millimeters"
        assert report["boundsMM"]["x"] == 300 and report["boundsMM"]["y"] == 300
        (folder / "surface-mm.obj").write_bytes(archive.read("surface-mm.obj"))
    project = import_scan(folder / "surface-mm.obj")
    np.testing.assert_allclose(project.original.extents[:2], [300, 300])
    assert len(project.original.faces) == 5000
    assert not project.original.is_watertight
    cleanup = clean_revision(project.current, CleanupOptions(fragment_percent=0))
    project.commit(cleanup.revision)
    mesh = project.current.mesh
    selected = np.flatnonzero((np.abs(mesh.triangles[:, :, :2]) < 95).all(axis=(1, 2)))
    hold = extract_hold(project.current, selected)
    hold.name = "SYNTHETIC iPhone exporter test"
    bolt = HardwareProfile("Synthetic bolt", 10, .4, 18, 6)
    screw = HardwareProfile("Synthetic screw", 5, .3, 10, 3, "countersink")
    hold.mounts = generate_mounts(hold, bolt, screw)
    project.holds.append(hold)
    save_project(project, folder / "swift-to-rockform.rockform")
    reopened = load_project(folder / "swift-to-rockform.rockform")
    export_hold(reopened.holds[0], folder / "synthetic-hold.stl")
    import trimesh
    result = trimesh.load_mesh(folder / "synthetic-hold.stl")
    validate_solid(result)
    summary = dict(
        swift_zip_crc=True, unicode_filename=True, obj_units="millimeters",
        original_bounds_mm=project.original.extents.tolist(), original_triangles=5000,
        desktop_import=True, cleanup=True, extraction=True, mounts=len(hold.mounts),
        project_roundtrip=True, stl_closed=result.is_watertight, stl_connected=len(result.split()) == 1,
        source="synthetic; not an iPhone hardware test",
    )
    (folder / "desktop-verification.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

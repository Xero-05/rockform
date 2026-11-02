import io
import json
import numpy as np
import pytest
import trimesh
from rockform.model import GeometryError
from rockform.holdgen import HoldRequest, generate
from rockform.holdgen.drill import drill, top_heights
from rockform.holdgen.library import HoldLibrary, declared_units, find_back, main
from rockform.holdgen.route import Wall, hardware, plan_route, write_route


def quiet(*_):
    pass


def model_bytes(mesh, file_type="stl"):
    buffer = io.BytesIO()
    mesh.export(buffer, file_type=file_type)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def drilled_jug():
    hold = generate(HoldRequest("jug", level=.3, seed=5))
    mesh, hole = drill(hold.mesh, hardware("bolt"))
    return hold, mesh, hole


def test_import_keeps_scale_orientation_and_the_bolt_hole(tmp_path, drilled_jug):
    hold, mesh, hole = drilled_jug
    library = HoldLibrary(tmp_path)
    # Exported Y-up in centimetres, the way many modelling programs write it: back toward -Y.
    turned = mesh.copy()
    turned.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    turned.apply_scale(.1)
    assert find_back(turned)[0] == "+Y"
    entry = library.import_model("Blue jug.stl", model_bytes(turned), "jug", units="cm")
    assert entry.id == "blue-jug" and entry.name == "Blue jug" and entry.watertight
    assert np.allclose(sorted(entry.size_mm), sorted(np.round(mesh.extents, 1)), atol=.2)
    stored = library.mesh(entry.id)
    assert abs(stored.bounds[0, 2]) < 1e-6 and np.allclose(stored.extents, mesh.extents[[0, 1, 2]], atol=.2)
    # Same hole, from the footprint centre, and the same rating as the hold it came from.
    centre = (mesh.bounds[0, :2] + mesh.bounds[1, :2]) / 2
    assert np.allclose(entry.hole, hole - centre, atol=1.5) and entry.hole_mm == pytest.approx(10.5, abs=1.)
    assert np.isnan(top_heights(stored, [entry.hole])[0][0])
    assert entry.difficulty == pytest.approx(hold.pull["best_difficulty"], abs=.4)


def test_saved_library_reloads_renames_and_shares(tmp_path):
    library = HoldLibrary(tmp_path / "a")
    edge = generate(HoldRequest("edge", level=.5, seed=2))
    obj = model_bytes(edge.mesh, "obj").decode()
    entry = library.import_model("edge.obj", ("# units: mm\n" + obj).encode(), "edge")
    assert entry.hole is None and any("drilled" in n for n in entry.notes)
    saved = library.save_generated(generate(HoldRequest("crimp", difficulty=7, seed=9)), "Small crimp")
    assert saved.source == "generated" and saved.type == "crimp"
    library.rename(entry.id, "Grey edge")
    library.set_type(entry.id, "sloper")

    again = HoldLibrary(tmp_path / "a")
    assert [h.name for h in again.holds] == ["Grey edge", "Small crimp"] and again.get(entry.id).type == "sloper"
    other = HoldLibrary(tmp_path / "b")
    other.save_generated(generate(HoldRequest("jug", level=.2, seed=1)), "Small crimp")
    added = other.merge_zip(again.export_zip())
    assert len(other.holds) == 3 and added[1].id == "small-crimp-2"
    assert other.mesh(added[0].id).is_watertight
    again.remove(saved.id)
    assert not (tmp_path / "a" / saved.file).exists() and len(HoldLibrary(tmp_path / "a").holds) == 1
    with pytest.raises(GeometryError):
        library.import_model("notes.txt", b"hello", "jug")
    assert declared_units("scan.obj", b"# Unit: meter\nv 0 0 0") == "m"


def test_routes_take_library_holds(tmp_path, drilled_jug):
    _hold, mesh, _hole = drilled_jug
    library = HoldLibrary(tmp_path)
    library.import_model("jug.stl", model_bytes(mesh), "jug")
    for kind, d in (("edge", 3.), ("sloper", 3.5), ("pinch", 4.), ("crimp", 6.)):
        library.save_generated(generate(HoldRequest(kind, difficulty=d, seed=4)))
    library = HoldLibrary(tmp_path)             # read back from disk, as after restarting the app
    wall = Wall(2000., 3000., 10.)

    holds, summary = plan_route("V2", wall, seed=3, library=library, use_library="only", progress=quiet)
    hands = [h for h in holds if h.role != "FOOT"]
    assert all(h.library for h in holds) and summary["library_holds"] == len(holds)
    assert len({h.library for h in holds}) == len(holds)                  # each library hold once
    jug = next(h for h in holds if h.library == "jug")
    assert jug.hole_mm == pytest.approx(10.5, abs=1.)                     # its own hole, not drilled again
    assert all(h.spin % 15 == 0 for h in hands) and all(h.hole_mm > 0 for h in holds)

    layout = json.loads(write_route(holds, summary, wall, tmp_path / "out", "Library route").read_text())
    assert {rec["holdgen"]["library"] for rec in layout["holds"]} == {h.library for h in holds}

    mixed, summary = plan_route("V2", wall, seed=3, library=library, use_library="prefer", progress=quiet)
    assert summary["library_holds"] == sum(bool(h.library) for h in mixed) > 0
    assert not any(h.library for h in mixed if h.role == "FOOT")         # feet stay generated
    for h in mixed:
        if h.library:      # only taken where it lands near the difficulty asked for
            assert abs(h.difficulty - h.report["difficulty"]) < 3
    plain, summary = plan_route("V2", wall, seed=3, library=library, use_library="off", foot_holds=False, progress=quiet)
    assert summary["library_holds"] == 0
    with pytest.raises(ValueError):
        plan_route("V2", wall, library=HoldLibrary(tmp_path / "empty"), use_library="only", progress=quiet)


def test_command_line_adds_and_lists(tmp_path, capsys):
    path = tmp_path / "hold.stl"
    generate(HoldRequest("edge", level=.4, seed=3)).mesh.export(path)
    main(["--folder", str(tmp_path / "lib"), "add", str(path), "--type", "edge"])
    main(["--folder", str(tmp_path / "lib"), "list"])
    out = capsys.readouterr().out
    assert "added hold" in out and "1 hold(s)" in out

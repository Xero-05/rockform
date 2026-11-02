import io
import json
import zipfile
import numpy as np
import pytest
from rockform.holdgen import HoldRequest, drill, generate, webapi as W
from rockform.holdgen.route import hardware
from rockform.mounts import _top_heights

WALL = dict(width=2000, height=3000, angle=20, bolt_spacing=200)


@pytest.fixture(autouse=True)
def fresh_state():
    W.HOLDS.clear()
    W.ROUTES.clear()
    yield
    W.ROUTES.clear()


def test_numpy_rays_match_the_scan_apps_rays():
    hold = generate(HoldRequest("jug", level=.3, seed=3))
    xy = np.random.default_rng(0).uniform(hold.mesh.bounds[0, :2], hold.mesh.bounds[1, :2], (200, 2))
    ours, theirs = drill.top_heights(hold.mesh, xy), _top_heights(hold.mesh, xy)
    assert np.array_equal(np.isnan(ours[0]), np.isnan(theirs[0]))
    both = ~np.isnan(ours[0])
    assert np.allclose(ours[0][both], theirs[0][both], atol=1e-5) and np.array_equal(ours[1][both], theirs[1][both])
    for kind in ("bolt", "screw"):
        assert drill.seat_cutter(0., 0., hardware(kind), 10., 30.).is_watertight


def test_hold_view_calls():
    hold = W.generate_hold(dict(type="crimp", difficulty=6.5, seed="4", wall_angle=10, size=1))
    assert hold["mesh"]["vertices"].dtype == np.float32 and hold["mesh"]["faces"].dtype == np.uint32
    assert len(hold["mesh"]["vertices"]) % 3 == 0 and abs(hold["difficulty"] - 6.5) < .5
    g = W.grip(hold["id"], 0., 0., 0., "scored")
    assert len(g["weights"]) == len(hold["mesh"]["faces"]) // 3 and g["difficulty"] == pytest.approx(hold["difficulty"], abs=.01)
    turned = W.rotate_hold(hold["id"], 90.)
    assert turned["rotation"] == 90. and turned["pull"]["rotation_deg"] == 90.
    described = W.generate_hold(dict(description="hard 2-finger pocket on a 30 degree overhang", seed="1"))
    assert described["type"] == "pocket" and described["wall_angle"] == 30. and described["fingers"] == 2
    name, data = W.export_hold(hold["id"])
    files = zipfile.ZipFile(io.BytesIO(data.tobytes())).namelist()
    assert name.endswith(".zip") and any(f.endswith(".stl") for f in files) and any(f.endswith(".json") for f in files)
    assert all(W.hold_icon(t).startswith("<svg") for t in W.info()["types"])


def test_route_calls_plan_adjust_inspect_and_export():
    first = W.plan(dict(wall=WALL, grade="V2", seed="3"), progress=lambda *_: None)["route"]
    second = W.plan(dict(wall=WALL, grade="V4", seed="5"), progress=lambda *_: None)["route"]
    assert first["color"] != second["color"] and all("mesh" in h for h in first["holds"])
    with pytest.raises(ValueError):
        W.plan(dict(wall=dict(WALL, angle=30), grade="V2"), progress=lambda *_: None)
    hand = next(h for h in first["holds"] if h["role"] == "HAND")
    out = W.adjust(first["id"], hand["index"], hand["u"] + 210., hand["v"] - 5., hand["spin"] + 15.)
    moved = out["route"]["holds"][hand["index"]]
    assert (moved["u"], moved["v"], moved["moved"]) == (hand["u"] + 200., hand["v"], True)   # snapped to the grid
    other = next(h for h in second["holds"])
    clash = W.adjust(first["id"], hand["index"], other["u"], other["v"], 0.)
    assert [first["id"], hand["index"], "may touch another hold"] in clash["problems"]
    faced = W.adjust(first["id"], hand["index"], hand["u"], hand["v"], 90., face=True)
    assert faced["route"]["holds"][hand["index"]]["spin"] % 15 == 0
    opened = W.inspect(first["id"], hand["index"])
    assert opened["drilled"] and opened["rotation"] == faced["route"]["holds"][hand["index"]]["spin"]
    name, data = W.export_wall()
    archive = zipfile.ZipFile(io.BytesIO(data.tobytes()))
    layout = json.loads(archive.read("layout.json"))
    assert len(layout["routes"]) == 2 and all(s["file"] in archive.namelist() for s in layout["scans"].values())
    W.remove_route(second["id"])
    assert list(W.ROUTES) == [first["id"]]


def test_library_calls_import_save_show_and_plan():
    W.LIBRARY.clear()
    jug = generate(HoldRequest("jug", level=.3, seed=5))
    buffer = io.BytesIO()
    drill.drill(jug.mesh, hardware("bolt"))[0].export(buffer, file_type="stl")
    entry = W.library_import("My jug.stl", np.frombuffer(buffer.getvalue(), np.uint8),
                             dict(type="jug", units="", axis="auto", turn=0, keep_hole=True))
    assert entry["name"] == "My jug" and entry["hole"] and W.info()["library_uses"] == ["off", "prefer", "only"]
    for kind, d in (("edge", 3.5), ("sloper", 3.), ("crimp", 6.)):
        shown = W.generate_hold(dict(type=kind, difficulty=d, seed="2", wall_angle=0, size=1))
        W.library_save(shown["id"], "")
    assert [h["type"] for h in W.library_list()] == ["jug", "edge", "sloper", "crimp"]
    opened = W.library_show(entry["id"], 20.)
    assert opened["library"] == entry["id"] and opened["wall_angle"] == 20. and opened["drilled"]
    W.library_rename(entry["id"], "Blue jug")
    sloper = W.library_list()[2]["id"]
    assert sloper.startswith("sloper-3-0") and W.library_retype(sloper, "pinch")[2]["type"] == "pinch"

    route = W.plan(dict(wall=WALL, grade="V2", seed="3", library="only"), progress=lambda *_: None)["route"]
    assert all(h["library"] for h in route["holds"]) and route["summary"]["library_holds"] == len(route["holds"])
    assert any(h["name"] == "Blue jug" for h in route["holds"])
    name, data = W.library_export()
    W.library_remove(entry["id"])
    assert len(W.library_list()) == 3
    assert W.library_merge(data) == 4 and len(W.library_list()) == 7
    W.LIBRARY.clear()


def test_profile_editor_calls():
    hold = W.generate_hold(dict(type="edge", difficulty=4, seed="2", wall_angle=0, size=1))
    profile = W.hold_profile(hold["id"])
    assert profile["type"] == "edge" and len(profile["points"]) >= 3
    edited = W.custom_hold(dict(profile, seed="2", wall_angle=20, rotation=0))
    assert edited["profile"]["width"] == profile["width"] and edited["wall_angle"] == 20.
    assert W.hold_profile(edited["id"])["points"] == edited["profile"]["points"]
    assert W.grip(edited["id"], 0., 0., 0., "grip")["difficulty"] == pytest.approx(edited["difficulty"], abs=.01)
    assert len(W.shape_profile("sloper", "1")["points"]) <= 12
    with pytest.raises(ValueError):
        W.hold_profile(W.library_show(W.library_save(hold["id"])["id"], 0.)["id"])

import json
import math
import numpy as np
import pytest
from rockform.geometry import validate_solid
from rockform.holdgen import HoldRequest, generate
from rockform.holdgen.route import GRADES, Wall, drill, estimate_grade, grade_score, hardware, plan_route, write_route
from rockform.mounts import _top_heights


def quiet(*_):
    pass


@pytest.fixture(scope="module")
def v3_route():
    wall = Wall(2000., 3000., 20.)
    avoid = [(-100., 1300., 120.), (300., 1900., 100.)]
    holds, summary = plan_route("V3", wall, seed=4, avoid=avoid, progress=quiet)
    return wall, avoid, holds, summary


def test_every_grade_maps_back_to_itself():
    for grade in GRADES:
        assert estimate_grade([grade_score(grade)] * 3)[1] == grade


def test_drilled_jug_is_a_solid_with_a_through_hole():
    hold = generate(HoldRequest("jug", level=.2, seed=3))
    mesh, (x, y) = drill(hold.mesh, hardware("bolt"))
    validate_solid(mesh)
    assert mesh.volume < hold.mesh.volume
    assert np.isnan(_top_heights(mesh, [[x, y]])[0][0])            # nothing left on the bolt's axis
    assert not np.isnan(_top_heights(hold.mesh, [[x, y]])[0][0])


def test_route_roles_order_and_placement(v3_route):
    wall, avoid, holds, summary = v3_route
    hands = [h for h in holds if h.role != "FOOT"]
    assert hands[0].role == "START" and hands[-1].role == "FINISH"
    assert all(h.role == "HAND" for h in hands[1:-1]) and len(hands) >= 3
    assert all(b.v > a.v for a, b in zip(hands, hands[1:]))
    assert any(h.role == "FOOT" for h in holds)
    us, vs = wall.bolt_axes()
    for h in holds:
        assert np.isclose(us, h.u).any() and np.isclose(vs, h.v).any()     # on the bolt grid
        assert abs(h.u) + h.radius <= wall.width / 2 and h.v + h.radius <= wall.height
        assert h.spin % 15 == 0 and h.hole_mm > 0
    spots = [(h.u, h.v, h.radius) for h in holds] + list(avoid)
    for i, a in enumerate(spots):
        for b in spots[:i]:
            assert math.hypot(a[0] - b[0], a[1] - b[1]) >= a[2] + b[2], (a, b)


def test_route_lands_near_the_requested_grade(v3_route):
    *_, summary = v3_route
    names = list(GRADES)
    assert abs(names.index(summary["estimated_grade"]) - names.index("V3")) <= 1
    assert summary["hand_moves"] == len(summary["move_costs"]) >= 2


def test_harder_grade_rates_harder():
    wall = Wall(2000., 3000., 0.)
    easy = plan_route("V0", wall, seed=1, foot_holds=False, progress=quiet)[1]
    hard = plan_route("V6", wall, seed=1, foot_holds=False, progress=quiet)[1]
    assert hard["route_score"] > easy["route_score"] + 2


def test_written_layout_is_a_rock_wall_layout(v3_route, tmp_path):
    wall, _, holds, summary = v3_route
    layout = json.loads(write_route(holds, summary, wall, tmp_path, "Test route").read_text())
    assert layout["format"] == "rock_wall_layout" and layout["version"] == 1
    assert layout["wall"]["angle"] == 20. and layout["routes"][0]["holdgen"]["requested_grade"] == "V3"
    assert len(layout["holds"]) == len(holds) == len(layout["scans"])
    for rec in layout["holds"]:
        scan = layout["scans"][rec["params"]["scan"]]
        assert rec["params"]["type"] == "SCAN" and rec["route"] == 1
        assert scan["bolt"] == 0 and scan["drilled"] == rec["params"]["bolt"] > 0    # already drilled
        assert len(scan["bolt_at"]) == 2 and (tmp_path / "holds").exists()
        assert scan["file"].startswith("holds/") and (tmp_path / scan["file"]).is_file()      # relative, portable
        assert json.loads((tmp_path / (scan["file"][:-4] + ".json")).read_text())["placement"]
    assert (tmp_path / "route.svg").read_text().startswith("<svg")
    assert json.loads((tmp_path / "route.json").read_text())["estimated_grade"] == summary["estimated_grade"]

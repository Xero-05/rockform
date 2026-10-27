import numpy as np
import pytest
import trimesh
from rockform.geometry import validate_solid
from rockform.holdgen import HOLD_TYPES, GripCone, build, generate, holding_area, parse_description, HoldRequest
from rockform.holdgen.generate import measure
from rockform.holdgen.holding import arc_vector, difficulty_from_area, area_for_difficulty, pole_figure


def ledge(depth=20., width=100., tilt_deg=0.):
    """A box on the wall whose top tilts outward (+) or into the wall (-) by tilt_deg."""
    box = trimesh.creation.box((width, 30., depth))
    box.apply_translation((0, 0, depth / 2))
    top = box.vertices[:, 1] > 0
    box.vertices[top, 1] -= np.tan(np.radians(tilt_deg)) * box.vertices[top, 2]
    # Small faces so the hand window can select part of the top.
    return trimesh.Trimesh(*trimesh.remesh.subdivide_to_size(box.vertices, box.faces, 5.))


def test_flat_ledge_counts_only_its_top():
    cone = GripCone(quality=None, hand_width=None)
    area, weights = holding_area(ledge(), [cone])
    assert area == pytest.approx(100 * 20)
    top = ledge().face_normals[:, 1] > .99
    assert np.array_equal(weights > 0, top)     # back, bottom, front and sides excluded


def test_arc_limits_outward_slope_and_includes_incut():
    cone = GripCone(quality=None, hand_width=None)   # arc from 45 degrees out to 80 degrees in
    assert holding_area(ledge(tilt_deg=30.), [cone])[0] > 0
    assert holding_area(ledge(tilt_deg=60.), [cone])[0] == 0
    assert holding_area(ledge(tilt_deg=-40.), [cone])[0] > 0


def test_grip_quality_and_hand_window():
    flat, sloped = holding_area(ledge(), [GripCone()])[0], holding_area(ledge(tilt_deg=30.), [GripCone()])[0]
    assert sloped < flat
    assert holding_area(ledge(width=200.), [GripCone(quality=None)])[0] == pytest.approx(90 * 20, rel=.05)


def test_overhang_rotates_the_wall_not_the_pull():
    cone = GripCone(quality=None, hand_width=None)
    assert holding_area(ledge(), [cone], theta_deg=40.)[0] > 0
    assert holding_area(ledge(), [cone], theta_deg=50.)[0] == 0   # top now slopes 50 degrees outward


def test_score_scale_round_trip():
    assert difficulty_from_area(area_for_difficulty(6.5)) == pytest.approx(6.5)
    assert difficulty_from_area(0.) == 10.


@pytest.mark.parametrize("kind", HOLD_TYPES)
def test_every_type_is_a_valid_solid_and_harder_when_hardness_rises(kind):
    easy, _ = build(kind, 0., seed=4)
    hard, params = build(kind, 1., seed=4)
    validate_solid(hard)
    assert hard.bounds[0, 2] == pytest.approx(0.)
    request = HoldRequest(kind)
    assert measure(hard, request)[1] > measure(easy, request)[1]


def test_generate_hits_requested_difficulty_and_is_reproducible():
    hold = generate(HoldRequest("crimp", difficulty=7., seed="abc"))
    assert hold.difficulty == pytest.approx(7., abs=.1)
    again = generate(HoldRequest("crimp", difficulty=7., seed="abc"))
    assert np.allclose(hold.mesh.vertices, again.mesh.vertices)
    assert not np.allclose(hold.mesh.vertices[:50], generate(HoldRequest("crimp", 7., seed="xyz")).mesh.vertices[:50])


def test_out_of_range_target_reports_the_closest_shape():
    hold = generate(HoldRequest("jug", difficulty=9.))
    assert hold.difficulty == pytest.approx(hold.achievable[1])
    assert hold.notes


def test_words_are_relative_to_the_type():
    easy, hard = generate(parse_description("easy sloper")), generate(parse_description("hard sloper"))
    assert easy.difficulty < hard.difficulty
    assert hard.achievable[0] <= hard.difficulty <= hard.achievable[1]


def test_parse_description():
    r = parse_description("Hard 2-finger pocket on a 30 degree overhang, seed 12")
    assert (r.hold_type, r.level, r.wall_angle, r.seed, r.difficulty) == ("pocket", .8, 30., "12", None)
    r = parse_description("difficulty 6.5 crimp on a roof")
    assert (r.hold_type, r.difficulty, r.wall_angle) == ("crimp", 6.5, 90.)
    with pytest.raises(ValueError):
        parse_description("something grippy")


def test_report_contains_pole_figure():
    hold = generate(HoldRequest("edge", difficulty=3.))
    report = hold.report()
    grid = np.array(report["pole_figure"]["area_mm2"])
    assert grid.shape == (10, 37) and grid[0].max() > grid[-1].max()
    assert report["cones"][0]["start"] == pytest.approx(arc_vector(45.).tolist())


def test_difficulty_range_by_pull_angle():
    hold = generate(HoldRequest("crimp", difficulty=5., seed=2))
    low, high = hold.difficulty_range
    grid = np.array(hold.pull["difficulty"])
    assert grid.shape == (len(hold.pull["outward_deg"]), len(hold.pull["around_deg"]))
    straight_down = grid[0, len(hold.pull["around_deg"]) // 2]
    assert straight_down == pytest.approx(hold.difficulty, abs=1e-3)
    assert low <= hold.difficulty <= high and high > low
    assert grid[-1].mean() > grid[0].mean()      # pulling away from the wall is harder on an edge
    assert hold.report()["difficulty_range_by_pull"] == [round(low, 3), round(high, 3)]


def test_grip_map_is_continuous_and_spares_the_back():
    from rockform.holdgen.holding import wall_faces
    hold = generate(HoldRequest("sloper", level=.5))
    grip = hold.grip_map()
    assert grip.min() == 0 and .5 < grip.max() <= .61       # no incut surface: a sloper never beats flat
    assert len(np.unique(np.round(grip, 2))) > 10
    assert not grip[wall_faces(hold.mesh)].any()


def test_grab_styles_side_pull_and_undercling():
    crimp = generate(HoldRequest("crimp", difficulty=5., seed=2)).pull["styles"]
    assert crimp["down"] < crimp["side_left"] and crimp["down"] < crimp["undercling"]
    assert crimp["undercling"] > 9.            # only the sloped belly faces down
    pinch = generate(HoldRequest("pinch", level=.5)).pull["styles"]
    assert abs(pinch["side_left"] - pinch["side_right"]) < 1.5


def test_incut_jug_outgrips_sloper_of_similar_size():
    jug, _ = build("jug", .5, seed=3)
    sloper, _ = build("sloper", .5, seed=3)
    q_jug = generate(HoldRequest("jug", level=.5, seed=3)).grip_map()
    q_sloper = generate(HoldRequest("sloper", level=.5, seed=3)).grip_map()
    assert (q_jug > .9).sum() > 3 * (q_sloper > .9).sum()
    assert measure(jug, HoldRequest("jug"))[1] < measure(sloper, HoldRequest("sloper"))[1]

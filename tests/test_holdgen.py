import numpy as np
import pytest
import trimesh
from rockform.geometry import validate_solid
from rockform.holdgen import HOLD_TYPES, GripCone, build, generate, holding_area, parse_description, HoldRequest
from rockform.holdgen.generate import from_profile, measure
from rockform.holdgen.shapes import build_profile, section
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


def _pinch(monkeypatch, **spec):
    from rockform.holdgen import shapes
    s = dict(shapes.SPECS["pinch"][1], **spec)
    monkeypatch.setitem(shapes.SPECS, "pinch", [s, s, s])
    return measure(shapes.build("pinch", .5, seed=0)[0], HoldRequest("pinch"))[1]


def test_rounder_and_wider_pinches_rate_harder(monkeypatch):
    parallel, round_ = _pinch(monkeypatch, p=8.), _pinch(monkeypatch, p=2.2)
    narrow, wide = _pinch(monkeypatch, W=62.), _pinch(monkeypatch, W=90.)
    assert round_ > parallel + .5
    assert wide > narrow + 2.


def test_squeeze_friction_penalises_sides_facing_the_climber():
    from rockform.holdgen.holding import pinch_cones, holding_weights
    left = pinch_cones()[0]
    tilt = np.radians([0., 20., 39.])
    normals = np.column_stack([-np.cos(tilt), np.zeros(3), np.sin(tilt)])    # left side turning toward climber
    w = holding_weights(normals, left)
    assert w[0] == pytest.approx(.8) and w[0] > w[1] > w[2] > 0
    assert holding_weights(np.array([[-np.cos(.35), 0., -np.sin(.35)]]), left)[0] == pytest.approx(.8)  # toward wall


def test_pinch_is_rated_by_its_easiest_grip_and_has_no_ledge_top():
    from rockform.holdgen.generate import grip_modes, best_mode
    for level in (0., .5, 1.):
        hold = generate(HoldRequest("pinch", level=level, seed=2))
        modes = grip_modes(hold.mesh, hold.request)
        assert best_mode(hold.mesh, hold.request) == "squeeze"
        assert difficulty_from_area(modes["hang"][0]) > hold.difficulty + .5   # the top is no easier ledge
        assert hold.difficulty == pytest.approx(difficulty_from_area(max(a for a, _ in modes.values())))


def test_rotating_on_the_wall_equals_pulling_the_other_way():
    from rockform.holdgen.generate import pull_range
    hold = generate(HoldRequest("edge", difficulty=4., seed=3))
    for rot in (45., 90., -60.):
        for around in (0., 90.):
            turned = measure(hold.mesh, hold.request, around, 0., rot)[1]
            assert turned == pytest.approx(measure(hold.mesh, hold.request, around - rot)[1])
    turned = pull_range(hold.mesh, hold.request, rotation_deg=90.)
    assert turned["styles"]["down"] > hold.difficulty + 2          # the ledge now faces sideways
    assert turned["styles"]["side_right"] == pytest.approx(hold.difficulty, abs=1e-3)   # ledge faces left: pull right
    assert np.allclose(hold.grip_map(0., 0., 90.), hold.grip_map(-90.))


def test_pocket_is_rated_by_its_hole_not_its_top():
    for level in (0., .5, 1.):
        hold = generate(HoldRequest("pocket", level=level, seed=3))
        centers = hold.mesh.triangles_center
        top = centers[:, 1] > hold.mesh.bounds[1, 1] - 1.2 * hold.params["D"]
        assert (hold.holding_faces * hold.mesh.area_faces)[top].sum() < 1.


def test_pocket_floor_is_incut():
    from rockform.holdgen.holding import arc_vector
    hold = generate(HoldRequest("pocket", level=0., seed=3))
    counted = hold.holding_faces > 0
    mean = (hold.mesh.face_normals[counted] * hold.mesh.area_faces[counted, None]).sum(axis=0)
    assert mean[2] < 0          # the gripped floor faces into the wall: fingers hook rather than slide out


def test_size_scales_the_body_and_smaller_is_harder():
    from rockform.holdgen.shapes import SIZES
    xs, _ = build("jug", .5, seed=1, size=SIZES["XS"])
    large, _ = build("jug", .5, seed=1, size=SIZES["L"])
    assert xs.extents.max() / large.extents.max() == pytest.approx(SIZES["XS"] / SIZES["L"], rel=.1)
    small = generate(HoldRequest("jug", difficulty=1., seed=1, size=SIZES["XS"]))
    big = generate(HoldRequest("jug", difficulty=1., seed=1, size=SIZES["L"]))
    assert small.achievable[0] > big.achievable[0] + 1       # less surface: an XS jug cannot be as easy
    crimp_xs, _ = build("crimp", 1., seed=1, size=SIZES["XS"])
    crimp_m, _ = build("crimp", 1., seed=1)
    assert crimp_xs.extents[2] == pytest.approx(crimp_m.extents[2], rel=.05)   # edge depth keeps its size


def test_parse_size_and_fingers():
    r = parse_description("hard 2 inch crimp")
    assert r.max_mm == pytest.approx(50.8)
    hold = generate(r)
    assert 40 < hold.mesh.extents.max() < 65
    assert parse_description("small jug").size == .7
    assert parse_description("two-finger pocket").fingers == 2
    assert generate(parse_description("mono pocket")).params["fingers"] == 1


@pytest.mark.parametrize("kind", HOLD_TYPES)
def test_edited_profile_starts_as_the_generated_hold(kind):
    sec = section(kind, .4, seed=3)
    assert sec["type"] == kind and 3 <= len(sec["points"]) <= 12
    assert sec["points"][0][1] == 0 and sec["points"][-1][1] == 0
    generated = build(kind, .4, seed=3)[0]
    mesh, params = build_profile(sec["points"], sec["width"], sec["outline"], sec["texture"], seed=3)
    validate_solid(mesh)
    assert np.allclose(mesh.extents, generated.extents, rtol=.05, atol=2.)
    assert params["profile"]["width"] == sec["width"]


def test_edited_profile_is_rated_by_its_shape():
    sec = section("jug", .5, seed=1)
    request = HoldRequest("jug", seed=1)
    shallow = from_profile(request, sec["points"], sec["width"], sec["outline"], sec["texture"])
    deeper = [p[:] for p in sec["points"]]
    deeper[2][1] += 25.                         # the lip crest, pulled out from the wall
    deep = from_profile(request, deeper, sec["width"], sec["outline"], sec["texture"])
    assert deep.difficulty < shallow.difficulty and deep.target == deep.difficulty
    assert deep.params["type"] == "jug" and deep.pull["styles"]


def test_edited_profile_rejects_bad_outlines():
    with pytest.raises(ValueError, match="crosses itself"):
        build_profile([[0, 0, 0], [40, 30, 0], [0, 30, 0], [40, 0, 0]], 80)
    with pytest.raises(ValueError, match="at least three"):
        build_profile([[0, 0, 0], [40, 0, 0]], 80)
    with pytest.raises(ValueError, match="above the bottom"):
        build_profile([[40, 0, 0], [20, 30, 0], [0, 0, 0]], 80)

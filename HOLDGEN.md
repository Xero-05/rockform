# Rockform hold generator (experimental branch)

Generates printable climbing holds from a short description, such as *"hard crimp on a 30 degree overhang"*,
and rates them by how a climber's pull interacts with the surface. It is separate from the scan workflow:
nothing in the scan, repair or mount pipeline changes.

```powershell
.venv\Scripts\python.exe -m rockform.holdgen.app                       # window with heat map and pull-angle chart
.venv\Scripts\python.exe -m rockform.holdgen "hard sloper on a 30 degree overhang" --out holds
.venv\Scripts\python.exe -m rockform.holdgen --type crimp --difficulty 7 --seed 1 --count 10 --out holds
```

Each export writes an STL (millimeters, flat back on the bed) and a JSON report with the measured
difficulty, its range across pull angles, each grab style's difficulty, holding area, shape parameters,
grip-cone vectors and a pole figure. Exports pass Rockform's
`validate_solid` check: closed, oriented, one body, no self-intersections.

## The window

1. **Hold type:** click one of the six icons. Each icon is the real cross-section of that type, viewed from
   the side with the wall on the left (pinch: from above, with the wall below).
2. **Difficulty** and **wall angle** sliders regenerate the hold as you move them. Presets cover slab,
   vertical, 30°, 45° and roof. **New variation** picks a new seed.
3. **Or describe it:** type a description (see below) instead of using the controls.

The 3D view shows a **grip heat map** for the selected pull: rock grey where the surface gives no grip,
then blue to red as it holds better. Incut surfaces (a jug's lip) show hottest, flat tops in the middle,
and outward slopes (a sloper) cool. **Counted area** switches the overlay to the surface one hand uses,
which is what sets the difficulty. **Pull arrows** show the grip arc (v1 blue, v2 violet) and the pull
(red), turned to the selected grab. The title above the view names the pull and its difficulty.

Below the view:

- **Difficulty:** the score for a straight-down pull, a bar showing its **range across pull angles**, and
  one button per **grab style**: down-pull, side-pull in each direction, and undercling. Each button shows
  that style's difficulty. Click one to show its heat map on the hold.
- **Difficulty by angle of pull:** each cell is a pull direction. Across is the direction around the hold:
  down in the middle, side-pulls at ±90°, undercling at the edges. Up is how far the pull leans away from
  the wall. The dashed line marks pulls within 30° of straight down, which the reported range covers.
  Hover a cell for its value; click it to show that pull's heat map.
- **Hold:** size, volume, holding area, and **Export STL + report**.

## Difficulty by angle of pull and grab style

Every generated hold is rated for pulls all the way around it, in 15° steps, each from flat on the wall to
45° away from it. Pulling at angle *a* around the hold is equivalent to turning the hold by -*a* on the
wall. Pulling away from the wall by *o* is equivalent to steepening the wall by *o*. The JSON report stores:

- `difficulty_range_by_pull`: the range over pulls within 30° of straight down;
- `pull_angles.styles`: down-pull, side-pull right, side-pull left and undercling;
- `pull_angles.difficulty`: the full grid.

On a vertical wall, with seed 1 at mid-range shapes:

| Type | Down-pull | Within 30° of down | Side-pull left / right | Undercling |
| --- | --- | --- | --- | --- |
| jug | 1.1 | 1.1 - 1.9 | 4.7 / 4.5 | 3.6 |
| sloper | 2.9 | 2.9 - 6.9 | 4.0 / 3.6 | 2.9 |
| pinch | 3.9 | 3.6 - 3.9 | 4.3 / 4.1 | 3.9 |
| pocket | 4.0 | 4.0 - 7.9 | 4.9 / 4.6 | 4.8 |
| edge | 4.1 | 4.1 - 7.0 | 8.1 / 7.7 | 10 |
| crimp | 6.8 | 6.8 - 10 | 10 / 10 | 10 |

Edges and crimps only work as down-pulls. A pinch is about the same from any side. A rounded sloper is
nearly symmetric, so it side-pulls and underclings about as badly as it down-pulls. A jug is best as a
down-pull and still usable from the side.

## Describing a hold

- **Type:** jug, crimp, edge, sloper, pinch or pocket. Synonyms also work: bucket, ledge, rail, dome, mono, 2-finger.
- **Difficulty words** are relative to the type: *very easy, easy, medium, hard, very hard* map to 0, 0.2, 0.5,
  0.8 and 1 of that type's achievable range. A hard jug is still easier than a hard crimp.
- **Numbers** are absolute: *difficulty 6.5* or *7/10* targets that measured score directly. A target the type
  cannot reach produces the closest shape and a note.
- **Wall:** *30 degree overhang*, *10 degree slab*, *roof*. The default is vertical.
- **Seed:** *seed 12*. The same description and seed always reproduce the same hold.

## Difficulty: holding area

The metric ports the `HoldingArea` geometry-nodes group from `normals_V3.blend`. Frame: X across the wall,
Y up, Z out of the wall.

1. The pulling arc runs from **v1**, 45° from straight up toward the climber, to **v2**, 80° from up into the
   wall. Each face normal is projected into the arc's plane.
2. A face counts if its projected normal lies between v1 and v2 and its angle out of that plane is at most
   the 30° cutoff. The flat back against the wall never counts.
3. **Holding area** is the weighted area of the counted faces. **Difficulty** is a log scale: 0 at about
   6000 mm² (a large incut jug lip) and 10 at about 80 mm² (a few-millimeter crimp).
4. The hold is evaluated on the requested wall. The wall tilts while the pull stays pointing down, as in the
   Blender pole-figure script. The **pole figure** sweeps wall steepness from 0 to 90° and rotation on the
   wall from -90 to 90°.

Two refinements are on by default. Set `quality=None` and `hand_width=None` on `GripCone` to get the
node group exactly:

- **Grip quality:** a face's weight depends on its angle against the pull. An outward slope holds only by
  friction, so it counts 0 at v1. A flat top counts 0.6. An incut surface the fingers curl into counts fully
  from 30° incut. That is why a jug lip out-grips a sloper of the same area, and why slopers rate harder
  as the wall steepens.
- **One hand:** only the best 90 mm band across the wall counts. One hand cannot use the whole width of a big hold.

**Pinches** use two side cones, one for the thumb and one for the fingers. Each covers side normals from 30°
below to 60° above horizontal, within a 35 mm thumb-pad band. The weaker side counts twice, and the result is
scaled down as the gap widens past a 50 mm comfortable span, reaching zero at 100 mm.

How the generator works: the jug has a single incut lip whose bowl ramps back into the wall below the
crest; the reference generator's raised rear rim was removed. The pocket block has a sloped belly, so its
underside is not an undercling ledge. Each type's shape comes from the Easy/Medium/Hard dimension tables in
[ekamveer241/Climbing-Holds](https://github.com/ekamveer241/Climbing-Holds), interpolated along one
continuous hardness value. The seed fixes the organic variation. Generation searches that hardness value
until the measured difficulty matches the target, usually to within 0.05.

Calibration (seed 3, achievable difficulty from easiest to hardest shape):

| Type | Vertical | 30° overhang |
| --- | --- | --- |
| jug | 0.0 - 2.2 | 0.8 - 3.1 |
| sloper | 1.4 - 3.6 | 4.7 - 8.8 |
| pocket | 1.8 - 5.1 | 5.3 - 9.2 |
| edge | 1.9 - 6.2 | 3.7 - 10 |
| pinch | 1.6 - 4.2 | 1.3 - 4.0 |
| crimp | 4.0 - 9.7 | 6.3 - 10 |

## Limits

- This is a geometric proxy, not a grade. The anchors (6000 and 80 mm², arc angles, grip-quality
  curve, hand width) are
  starting values to tune against holds people have actually climbed on.
- Pocket ratings include the rounded top of the block, so small pockets rate easier than they climb.
- Grab styles turn the same grip arc around the hold. They do not model thumb catches, gastons pushing
  outward, or body position.
- Face visibility and occlusion are not checked. A surface counts if its normal points the right way,
  even where fingers cannot reach it.
- Generated holds have no mounting holes. Mount and structural qualification remain outside this tool,
  as with the scan workflow.

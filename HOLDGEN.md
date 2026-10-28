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
   the side with the wall on the left (pinch: from above, with the wall below). **Size** (XS, S, M, L, XL)
   scales the hold's body by 0.5, 0.7, 1, 1.25 or 1.5. Finger-sized features keep their size: a crimp's edge
   depth, a pocket's hole and a pinch's gap. The actual size shows in millimeters and inches.
2. **Difficulty** and **wall angle** sliders regenerate the hold as you move them. Presets cover slab,
   vertical, 30°, 45° and roof.
3. **Rotation on the wall** turns the same hold, as if re-bolted at another angle, while gravity's grip arc
   and pull stay fixed. It does not regenerate the shape: the heat map, difficulty, grab styles and chart
   are re-rated for the new placement. **New variation** picks a new seed.
4. **Or describe it:** type a description (see below) instead of using the controls.

The 3D view shows a **grip heat map** for the selected pull: rock grey where the surface gives no grip,
then blue to red as it holds better. Incut surfaces (a jug's lip) show hottest, flat tops in the middle,
and outward slopes (a sloper) cool. **Scored area** keeps only the best 90 mm hand-width band (a 35 mm
thumb band on each side of a pinch). Its weighted area is what the difficulty is computed from. **Pull arrows** show the grip arc (v1 blue, v2 violet) and the pull
(red), turned to the selected grab. The title above the view names the pull and its difficulty.

Below the view:

- **Difficulty:** the score for the selected pull (straight down until you pick another), a bar showing its **range across pull angles**, and
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
| pinch | 5.5 | 5.4 - 8.4 | 2.7 / 2.4 | 5.5 |
| pocket | 5.6 | 5.6 - 7.3 | 5.6 / 5.4 | 4.9 |
| edge | 4.1 | 4.1 - 7.0 | 8.1 / 7.7 | 10 |
| crimp | 6.8 | 6.8 - 10 | 10 / 10 | 10 |

Edges and crimps only work as down-pulls. A pinch is the same pulled down or up, and easy as a side-pull
because its long side works like a rail. A rounded sloper is
nearly symmetric, so it side-pulls and underclings about as badly as it down-pulls. A jug is best as a
down-pull and still usable from the side.

## Describing a hold

- **Type:** jug, crimp, edge, sloper, pinch or pocket. Synonyms also work: bucket, ledge, rail, dome, mono, 2-finger.
- **Difficulty words** are relative to the type: *very easy, easy, medium, hard, very hard* map to 0, 0.2, 0.5,
  0.8 and 1 of that type's achievable range. A hard jug is still easier than a hard crimp.
- **Numbers** are absolute: *difficulty 6.5* or *7/10* targets that measured score directly. A target the type
  cannot reach produces the closest shape and a note.
- **Wall:** *30 degree overhang*, *10 degree slab*, *roof*. The default is vertical.
- **Size:** *tiny / small / large / huge*, or a largest dimension such as *2 inch*, *60 mm* or *8 cm*.
  A requested dimension lands within about 15%, since finger-sized features do not shrink. Smaller holds
  have less surface and rate harder; an XS jug cannot be made as easy as a medium one.
- **Pocket fingers:** *mono*, *2-finger*, *three finger*. Without it, the count follows difficulty.
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

**Pinches** are squeezed, not hung from, so they use two side cones instead of the down-pull arc: one for
the thumb side and one for the finger side. A face counts as part of a side when it faces within 30° of
horizontal; steeper faces are the hold's top or bottom. Each side face is weighted by how well a squeeze
holds on it:

- **Up/down tilt:** a side facing downward lets the hold slip through the hand (0). A vertical, parallel
  side holds well (0.8). A side facing 30° upward, so the hold widens toward the bottom, holds best (1).
- **Tilt toward the climber:** squeezing a face turned toward you by *t* pushes your hand off the hold.
  Friction resists that only while tan *t* < μ, with μ = 0.84 for skin on resin. Such a face therefore
  counts 1 - tan *t* / μ, reaching zero at 40°. Faces turned toward the wall are not penalised.

Each side counts only a 35 mm band up the wall, the length of a thumb pad. Because the thumb side limits a
pinch, the weaker side's area counts twice. The result is then scaled by span: full at a 50 mm gap, falling
to zero at 100 mm.

A pinch is also scored as an ordinary ledge with the down-pull arc, and **the easier of the two counts**:
a climber who can hang the top will not squeeze. The generated pinch has long ramps at both ends, so its top
and bottom are steep slopes rather than ledges. Used as a ledge it rates 6.4 - 7.4, harder than squeezing,
so pinches are rated as squeezes. The Hold card says which grip a pull is scored as.

Both rounder and wider pinches rate harder, and width matters most. At a fixed 78 mm width, going from
parallel sides to a very round loaf raises difficulty from 4.2 to 5.5. At fixed roundness, a 63 mm gap scores
3.0 and a 92 mm gap 6.6. Pulled sideways, a pinch's long flat side works like a rail, so pinches side-pull easily.

How the generator works: the jug has a single incut lip whose bowl ramps back into the wall below the
crest; the reference generator's raised rear rim was removed. The pocket floor runs down into the hold, so a finger
hooks instead of sliding out (the reference sloped it the other way). The pocket block's top ramps to the wall
more steeply than 45°, so the top is no ledge and the hole is what is rated; its belly is sloped too. Each type's shape comes from the Easy/Medium/Hard dimension tables in
[ekamveer241/Climbing-Holds](https://github.com/ekamveer241/Climbing-Holds), interpolated along one
continuous hardness value. The seed fixes the organic variation. Generation searches that hardness value
until the measured difficulty matches the target, usually to within 0.05.

Calibration (seed 3, achievable difficulty from easiest to hardest shape):

| Type | Vertical | 30° overhang |
| --- | --- | --- |
| jug | 0.0 - 2.2 | 0.8 - 3.1 |
| sloper | 1.4 - 3.6 | 4.7 - 8.8 |
| pocket | 3.1 - 10 | 4.7 - 10 |
| edge | 1.9 - 6.2 | 3.7 - 10 |
| pinch | 2.6 - 6.2 | 2.3 - 6.3 |
| crimp | 4.0 - 9.7 | 6.3 - 10 |

## Limits

- This is a geometric proxy, not a grade. The anchors (6000 and 80 mm², arc angles, grip-quality
  curve, hand width) are
  starting values to tune against holds people have actually climbed on.
- A pocket's ratings by finger count: 3-finger 3.0 - 7.6, 2-finger 3.9 - 8.4, mono 5.6 - 9.5 (seed 1). The
  roof of the hole and the sloped belly face down, so a pocket can sometimes be underclung more easily than
  it can be pulled down.
- Pinch span limits (50 mm comfortable, 100 mm impossible), the 35 mm thumb band and μ = 0.84 are estimates
  to tune against real pinches.
- Grab styles turn the same grip arc around the hold. They do not model thumb catches, gastons pushing
  outward, or body position.
- Face visibility and occlusion are not checked. A surface counts if its normal points the right way,
  even where fingers cannot reach it.
- Generated holds have no mounting holes. Mount and structural qualification remain outside this tool,
  as with the scan workflow.

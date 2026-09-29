# Rockform

A local Windows app for turning iPhone rock-surface meshes into printable **shape and mounting prototypes**. Imports phone scans; it does not directly operate the iPhone LiDAR sensor. No Mac or cloud account is required.

**Joining the project? Start with [TEAM-SETUP.md](TEAM-SETUP.md)** for GitHub access,
a Windows app download, source setup, and the team's pull-request workflow.
The source repository excludes personal scans, local tools, and packaged binaries.

## Open the application

For the portable Windows download, open [GitHub Releases](https://github.com/Xero-05/rockform/releases)
and select the **Windows-x64.zip** asset. Extract the whole archive and run
**Rockform.exe**; no Python installation is needed. The repository is private,
so teammates need access before downloading. See **START-HERE.txt** inside the ZIP.

Double-click **`dist\Rockform\Rockform.exe`**, or **`Start Rockform.cmd`** in this folder. Keep the complete `Rockform` folder together when copying it to another computer. The packaged application includes its Python runtime and libraries.

For development, run `.venv\Scripts\python.exe -m rockform.app`.

A lightweight custom iPhone capture companion is now available as source in [`iphone/README.md`](iphone/README.md). It includes LiDAR scanning, a synthetic demo for phones without LiDAR, and reference photos. Read its setup and validation notes: Apple SDK compilation, signing, and phone testing are separate from the verified Windows application.

## First workflow

1. In your iPhone scanner, capture a small rock area as a **triangle mesh**, not a Gaussian splat. Export OBJ or STL and transfer it to Windows. Include a physically measured reference in the scan. Scaniverse mesh exports are suitable inputs.
2. Choose **Import scan**. The app reads declared OBJ units, including Scaniverse's `# Unit: meter`. Confirm the displayed dimensions against a physical measurement. Files without unit metadata still require you to choose units or enter a measured reference; dimensions alone are not used to guess scale.
3. In **1 Repair**, choose **Preview repair & cleanup**. The defaults join geometry seams, fix face orientation and touching boundary connections, remove tiny fragments, and repair small interior gaps. **Yellow** shows added patches; **coral** shows removed fragments. **Compare original** overlays the original wireframe. Choose **Apply preview** when satisfied. Smoothing and triangle reduction remain optional.
4. Choose a brush below the viewer. **Right-drag** paints a radius in millimeters. Amber selects a hold, teal protects grip detail, blue limits cleanup. Left-drag orbits; the wheel zooms. Brushes operate on nearby 3D points, so inspect the back and edges of thin surfaces after painting.
5. **Inspect boundaries** highlights open edges. Automatic small-gap repair is enabled by default (10 mm limit), or turn it off and check individual gaps. The outer scan edge remains open; this is normal. Protected, large, strongly uneven, or tangled gaps remain open with an explanation.
6. In **2 Holds**, set the upward direction and feature sizes (initially 50–180 mm), then **Suggest holds**. Suggestions are checked for solid creation before appearing. Click one and choose **Preview hold from selection**. You can also paint a connected region directly. **Prepare a simpler edge when needed** may discard disconnected pieces and trim a troublesome perimeter to an outline following the selection's proportions, rather than a circular crop. It subdivides existing triangles without rebuilding the remaining surface. Inspect the preview, then click **Keep hold**.
7. New holds add a tapered edge outside the scanned grip, blending down to the flat wall contact. Defaults are **6 mm added backing** and **10 mm edge taper width**, both editable in **2 Holds**. Use **Rebuild selected hold** after changing thickness, taper width, scale, or tilt. Width 0 retains straight sides. **Use tapered shape defaults** updates a selected older hold without moving its mounting holes. These are prototype modeling settings, not strength specifications.
8. In **3 Mounts**, choose **bolt-on** or **screw-on**. Editable **10 mm bolt** and **5 mm screw** prototype profiles are included. Match their dimensions to your real hardware before printing. The panel and profile dialogs explain the required hole/seat sizes, clear width, and local thickness. Bolt-on starts with one bolt; the anti-rotation screw is optional and initially off. Screw-on starts with two screws.
9. **Generate / regenerate mounts** creates a layout. Drag the orange handles to move holes, or select a hole and enter X/Y coordinates. **Hole dimensions** edits that hole independently; add/delete screws as needed. Invalid layouts remain visible with an explanation and cannot be drilled/exported.
10. **Preview drilled hold** performs the cuts. Enable **Cutaway preview** and move its slider to inspect the seats. **Export STL** creates the mesh plus a JSON dimensions/settings report. Import the STL into your slicer in **millimeters**.
11. **Save project** retains the source, scan revisions, protection, extracted holds, hardware profiles, and individual mount edits. Double-click a hold name to rename it.

Three **synthetic** sample surfaces are included in the sidebar. They exercise the workflow without phone data; they are not measurements of real rock.

The **Guide** button opens these basic steps inside the app. **Face scan** returns to the working scan and looks straight at its main surface. Recoverable problems appear in a message above the viewer with a suggested next step instead of opening repeated error dialogs. Apply or discard pending repair before selecting holds.

## Cleanup and editing behavior

- Original geometry is retained. Undo/redo applies to committed scan cleanup and protection edits. Reset creates an undoable original-scan revision.
- Smoothing pins explicit protection, open boundaries, and high-angle feature edges. Simplification preserves protected faces and patch boundaries.
- A cleanup region restricts geometric cleanup; redundant-face removal and orientation repair may normalize connectivity across the mesh.
- Protected areas are also excluded from automatic mounting. Protection must be painted before extracting a hold; existing holds retain the protection snapshot from their source revision.
- Hold edits are nondestructive: the selected surface and numeric parameters are retained. Existing holds stay bound to their original scan revision after later cleanup.
- Surface scaling does not scale hardware. Existing hole coordinates stay in the hold's millimeter coordinate system and are rechecked after shape edits.
- Editing a saved profile affects future holes. Existing holes keep their own settings. Regeneration replaces a layout and asks before replacing manually edited holes.
- Version 0.1.0 projects retain their original shapes, custom profiles, and individual hole edits. Empty legacy profiles receive the new presets. Newly saved projects use format 2, requiring Rockform 0.1.1 or later; save a new copy if retaining compatibility with the older app matters.
- Cleanup and generation operate in workers. Cancel takes effect at checkpoints; individual library calls such as simplification and solid subtraction must return before cancellation completes.

## Geometry limits

The first version is intended for broad ledges and palm-sized features. A final patch needs one connected boundary without missing interior data. Its boundary must project onto the flat back without crossing itself. Boundary preparation tries progressively smaller outlines and never fills missing interior data during extraction. Small scan gaps are filled only in the separate repair preview; those new triangles are estimated geometry. Complex undercuts and cracks can still be rejected.

Candidate suggestions are geometric heuristics, not grip-quality predictions or climbing grades. Mount screening checks head-seat edge clearance, sampled material thickness, protection, and hole separation. It does not perform stress analysis. Thickness is sampled over the recess disk, so the preview still requires inspection.

Prototype starting profiles (millimeters):

| Profile | Shaft + clearance | Head + clearance | Recess depth | Minimum clear width | Minimum local thickness |
| --- | --- | --- | --- | --- | --- |
| Bolt, cylindrical seat | 10 + 0.5 | 18 + 0.5 | 6 | 24.5 | 9 |
| Screw, conical seat | 5 + 0.5 | 10 + 0.5 | 2.5 | 16.5 | 5.5 |

Both start with 3 mm geometric allowances outside and below the seat. Clear width is seat diameter plus twice the edge allowance; local thickness is recess depth plus remaining-material allowance. These are lower bounds: tapered sides, slopes, protection, and other holes can require more room. Failed placements report the limiting measurement and a suggested adjustment. Hardware length/thread engagement and structural adequacy are outside these geometry checks.

Export validates connectedness, closure, face orientation, positive volume, and triangle intersections within numerical tolerance (one millionth of the model's largest dimension, minimum 0.000001 mm). STL uses float32 coordinates. Hardware seats use double precision internally.

Prints are for shape and mounting experiments. Qualification of material, print orientation, repeated loads, mounting hardware, and the complete wall assembly is outside this release. Do not treat an export check as qualification for climbing.

## Developer setup and checks

Requires Windows x64 and Python 3.12 for the tested development configuration. Create a virtual environment and install the versions in `requirements-lock.txt`, then install this project with `pip install --no-deps -e .`.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m pip install --no-deps -e .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\smoke_ui.py
.venv\Scripts\python.exe -m PyInstaller --noconfirm Rockform.spec
```

The test suite covers extraction, both mount modes, actual through-holes, profile preservation, invalid placements, scan cleanup, protected vertices, gaps, history, saving/reopening, units, stale previews, and export validation. The UI smoke script captures the app's own window for visual inspection.

`Rockform.exe --self-test <output-folder>` runs the included synthetic pipeline verification and writes its results. It does not upload files or modify other applications.

`Rockform.exe --verify-scan <input.obj> <output-folder>` runs default repair and checked suggestions on a local scan, exports unmounted review shapes, reloads and validates the STLs, saves an editable review project, and captures the application window. It leaves the input unchanged and does not assume hardware sizes. Its JSON report distinguishes a frozen executable run from a source run.

## Field validation still required

Use three real rock scans, record their reference measurements, export selected holds, and confirm each in a slicer without repair. Print at least one shape prototype and compare its dimensions and contact surface with the source. Record scanner error separately from printer error. This software project cannot substitute synthetic fixtures for those physical checks.

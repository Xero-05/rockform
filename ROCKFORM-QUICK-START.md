# Using your Scaniverse scan in Rockform

Open **Start Rockform.cmd** in this folder. You can use the prepared examples or start again from your OBJ.

## Open the prepared examples

1. Click **Open project** and open **Your Scan - Repaired\Review scan.rockform**.
2. Click any of the six **Scan hold** entries in the left-hand library to inspect it. These are unmounted shape examples, with 10 mm of added backing. Their rough selection edges were trimmed to rounded outlines; the remaining surface comes from your repaired scan.
3. These are older 0.1.0 examples. In 0.1.1, **2 Holds → Use tapered shape defaults** replaces their straight sides with a tapered edge and 6 mm backing, keeping the scanned grip. To replace a previous circular crop, make a new hold from the scan using the steps below.
4. Open **3 Mounts** and generate a layout using the included editable prototype profiles. Match these to your actual bolt/screw dimensions. The default bolt needs at least 24.5 mm clear width and 9 mm local thickness; the default screw needs 16.5 mm width and 5.5 mm thickness. Slopes can need more space. Inspect **Preview drilled hold** and the cutaway before exporting.

The matching STL files in that folder are ready for shape inspection in a slicer. Choose **millimeters** when importing them. They contain no mounting holes; add your measured hardware in Rockform first if needed.

## Start again from your scan

1. **Import scan** → choose your Scaniverse OBJ. Its header declares meters, which Rockform now detects. Your file should show approximately **363 × 50 × 341 mm**. Confirm this against a real measurement.
2. **1 Repair → Preview repair & cleanup**. Keep the default settings initially. Your scan should show five small gaps filled, tiny fragments removed, and touching surface boundaries separated. Yellow marks new patches. The original scan is retained.
3. Click **Apply preview**. The outside edge of the scan stays open; it does not need to become a sealed object.
4. **2 Holds → Suggest holds**. Wait for the solid checks, choose a suggestion, then **Preview hold from selection**.
5. Inspect the shape. New boundary preparation keeps the feature's proportions and adds a tapered edge down to the wall. It may trim the selection to avoid tangled borders. Click **Keep hold** to put the preview in your library.
6. **Save project** to keep the scan, repair history, shapes, and future mount edits.

To choose your own area, switch the brush below the viewer to **Select hold** and **right-drag** over a small feature. Left-drag rotates the view; the wheel zooms. **Face scan** makes selection easier. Avoid missing patches and deep cracks.

If an area still fails, the message above the viewer explains the next step. Try a smaller selection away from missing geometry. **Undo**, **Redo**, **Compare original**, and **Reset scan** help review repairs. Existing library holds retain the scan revision they were made from.

These are shape and mounting prototypes. Geometry checks do not establish strength for climbing; physical testing remains a separate project step.

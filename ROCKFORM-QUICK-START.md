# Extracting the natural feature in Rockform 0.1.11

1. Import the Scaniverse OBJ and confirm scale. **Automatically reduce dense, low-detail areas** is enabled initially; uncheck it to retain every triangle. The full-resolution original remains available with Undo or Reset scan.
2. In **1 Repair**, click **Paint area of interest** and RIGHT-drag over useful rock. Violet marks the selection. Include surrounding wall; the default **20 mm margin** adds context. Click **Preview selected area**, inspect coral excluded geometry, then **Apply selected area**. You can also use the entire current scan. Next, preview repair and apply it after inspection; excluded regions are not repaired.
2. Open **2 Holds**, keep **Isolate natural feature** checked, and click **Suggest holds**.
3. Select the raised feature and click **Preview hold from selection**. **Surrounding rock** defaults to **10 mm**, retaining texture around the base for fingers wrapping around it. Adjust it before previewing or re-extracting; set **0 mm** for the previous root-only shape.
4. Inspect the green scanned contact surface and amber support. A 3 mm backing under the lowest retained point and a shallow outer return close the default shape. Check the displayed dimensions, then **Keep hold**.
5. In **3 Mounts**, choose bolt-on or screw-on and generate mounts. Match the profiles to your hardware, inspect the drilled preview and cutaway, then export STL in millimeters.
6. **Save project** includes unfinished previews, area selections and margins, crop-edge exclusions, settings and the current view. Use **Open project** to continue where you left off. Saving does not accept a preview. Projects with **Fill then trim** use format 9 and require Rockform 0.1.11 or later; other saves use format 8 and require 0.1.7 or later.

The app fits the surrounding wall and identifies the protrusion's root with a small allowance for scan noise. It then retains the original scan within the chosen margin around that root, including low rock below the allowance and overhanging parts of the feature. Retained triangles remain on the source surface. Cropped or incomplete features still retain available contact texture. Partial coverage is shown on the preview and stays marked **Needs repair**; missing texture is not added. The context excludes deep rock beyond the requested depth below the root. Repair/rescan, reduce the margin, or explicitly preview **Fill down to wall**. Filling puts the back beneath the retained low contact texture; trimming can remove that band.

A **recess into the wall** keeps its inward shape. Supporting material is added beneath and around that cavity; its mouth is not filled and its shape is not inverted. Added support appears in amber. Deep pockets can need a broad base to return gradually to the mounting wall; check the full preview dimensions before printing. In 0.1.6, that necessary support is no longer rejected by the width rule intended for raised features.

For manual selection, right-drag over the feature with **Select hold**. With isolation enabled, the selection identifies the feature and extraction expands to its natural outline. Turning off isolation explicitly requests an older-style backed surface patch.

For an existing large-plate design, select it and choose **Re-extract natural feature**. Review and keep the new shape, then generate its own mounts. The old design and its manual holes remain intact. If it belongs to another scan revision, select the feature again on the current working scan.

**Needs repair** means a feature was recognized but cannot yet form a reliable solid. Choose it and preview the scanned surface; coral lines mark missing or damaged boundaries. **Keep feature for repair** saves it in the library. Mounting and export remain disabled. Preview and apply scan repair, then select the saved feature and use **Re-extract natural feature** to make a new preview from the repaired revision. Larger gaps, missing undersides or deeply uneven gaps may still need a rescan. Missing surfaces are not silently capped. Set **Scan upward direction** along the wall; if it points out of the surface, the review explains that mounting orientation remains unresolved.

## Quick repair for a broken raised feature

1. Select or preview the **Needs repair** feature, then find **Quick repair** in **2 Holds**.
2. Choose **Fill then trim**. The original feature is filled first, then the completed solid is cut at **Trim height above wall** in millimeters. Start at 0 mm and raise it until the unwanted lower rock is removed. This plane becomes the flat back.
3. Click **Preview quick repair**. The main body is kept if the cut separates offcuts; the preview reports them. Compare with the original to inspect removed rock. **Fill down to wall** and **Trim damaged base** are also available. Trim-only height is automatic initially; disable automatic height to adjust it manually.
4. Inspect amber added material. **Show original feature in coral** overlays the unrepaired surface, including material removed by trimming.
5. Click **Keep hold** when satisfied. Switch methods or lower the cut to revise the repair from its original surface, or choose **Restore original feature**. Save/reopen retains the original and unfinished repair preview.

Fill can cover undercuts and does not bridge a missing front patch. Trim reduces the remaining feature height. These are explicit shape edits; quick repair currently applies to raised features. Protected details and invalid cuts produce an explanation instead of an exportable solid.

Outputs remain shape and mounting prototypes requiring separate physical validation.

Older project files open their first kept hold when no saved view is available. Previews or selections omitted by an older app cannot be recovered from that archive. Kept holds remain in the library.

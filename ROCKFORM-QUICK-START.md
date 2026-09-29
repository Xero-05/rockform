# Extracting the natural feature in Rockform 0.1.4

1. Import the Scaniverse OBJ and confirm scale. The supplied scan is approximately **363 × 50 × 341 mm**. Preview repair and apply it after inspection.
2. Open **2 Holds**, keep **Isolate natural feature** checked, and click **Suggest holds**.
3. Select the raised feature and click **Preview hold from selection**. This scan produces a roughly **135 × 94 × 38 mm** feature, with **0 mm added backing and no surrounding plate**.
4. Inspect the green scanned surface. Only its underside is closed; the oversized amber skirt is absent. Shape settings can edit the preview before **Keep hold**.
5. In **3 Mounts**, choose bolt-on or screw-on and generate mounts. Both default profiles geometrically fit this example without added backing. Match them to your hardware, inspect the drilled preview and cutaway, then export STL in millimeters.
6. **Save project** includes unfinished previews, selections, settings and the current view. Use **Open project** to continue where you left off. Saving does not accept a preview. New files use format 5 and require Rockform 0.1.4 or later.

The app fits the surrounding wall and follows the protrusion's intersection with a plane just above it. A small allowance excludes scan noise; this example uses 1 mm. Retained triangles remain on the source surface.

A **recess into the wall** keeps its inward shape. Supporting material is added beneath and around that cavity; its mouth is not filled and its shape is not inverted. Added support appears in amber. Deep pockets can need a broad base to return gradually to the mounting wall; check the full preview dimensions before printing. In 0.1.4, that necessary support is no longer rejected by the width rule intended for raised features.

For manual selection, right-drag over the feature with **Select hold**. With isolation enabled, the selection identifies the feature and extraction expands to its natural outline. Turning off isolation explicitly requests an older-style backed surface patch.

For an existing large-plate design, select it and choose **Re-extract natural feature**. Review and keep the new shape, then generate its own mounts. The old design and its manual holes remain intact. If it belongs to another scan revision, select the feature again on the current working scan.

Missing interior data and features continuing beyond a scan edge are rejected rather than silently capped. Repair or scan more surrounding wall. Set **Scan upward direction** along the wall; if it points out of the surface, the review explains that mounting orientation remains unresolved.

Outputs remain shape and mounting prototypes requiring separate physical validation.

Older project files open their first kept hold when no saved view is available. Previews or selections omitted by an older app cannot be recovered from that archive. Kept holds remain in the library.

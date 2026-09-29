# Using a rock scan in Rockform 0.1.2

1. **Import scan** and confirm its size against a measured reference. Scaniverse OBJ files declaring meters are converted automatically. The supplied example is approximately **363 × 50 × 341 mm**.
2. In **1 Repair**, preview cleanup and inspect the changes before applying them. Repair fixes mesh problems; it cannot add a missing natural grip. An open outer scan edge is normal.
3. In **2 Holds**, set scan up along the rock surface, then **Suggest holds**. The app checks retained natural relief and a continuous contact slope, separately from whether the patch can form a solid. **No suitable broad handhold features found** is a normal outcome.
4. Select a suggestion and choose **Preview hold from selection**. **Green is retained rock; amber is generated support.** Inspect both the natural contact and the wall return. Click **Keep hold** only after reviewing the shape.
5. Manual selection remains available: choose **Select hold** below the viewer and right-drag over the feature and surrounding low rock. A manual shape that fails the natural-feature screen is labeled **Manual shape only**. Keeping it does not promote it to a screened handhold.
6. In **3 Mounts**, editable bolt and screw prototype profiles are included. Match them to your hardware, generate a layout, inspect the drilled preview and cutaway, then export STL in **millimeters**. Save the project to retain editable settings.

## Why an old suggestion may disappear

Versions 0.1.0 and 0.1.1 could suggest a textured plate simply because it made a solid. Added backing and crop edges could then become easier grips than the scanned rock. Version 0.1.2 excludes backing and generated edges from natural-feature evidence and checks the retained patch after border trimming.

On the supplied Scaniverse example, the default search currently finds no qualifying automatic handholds. Two surviving patches have about 1.0 and 2.6 mm of broad natural relief, below the 8 mm screening threshold. Other regions fail shape construction. This is a limit of the current selection/screening workflow, not proof that the entire real rock has no usable features. A scan of a more distinct feature, including low surrounding rock, is a better input. The broad-feature screen deliberately does not target small crimps or friction-only slopers.

The older local `Your Scan - Repaired` examples are **shape experiments, not validated handholds**. Opening them preserves their geometry. Choose **2 Holds → Use gradual wall return** to explicitly upgrade an edge. Tall or abrupt cuts may be rejected; include low surrounding rock or adjust the backing plane instead. Failed rebuilds leave the original shape intact. Manual hole positions and hardware dimensions stay fixed and are rechecked.

New shapes use a gradual return to the wall whose width grows with cut-edge height. The preview reports its actual width. The app rejects substantial artificial ledges and excludes support that would dominate a suggested feature. These geometric checks cannot guarantee how someone will grip the object.

To try a positive example, open the **Ledge** synthetic sample. These fixtures demonstrate the workflow; they are not real scans. Left-drag rotates, right-drag paints, the wheel zooms, and **Face scan** returns to the source surface.

Projects saved in 0.1.2 use format 3. Save a new copy if you also need an older app. Physical strength, slicer behavior, printing accuracy and climbing qualification remain separate validation steps.

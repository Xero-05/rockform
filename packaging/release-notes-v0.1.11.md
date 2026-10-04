# Rockform 0.1.11 — fill first, then trim

Download **Rockform-0.1.11-Windows-x64.zip**, extract the entire archive, and run **Rockform.exe**. Keep `_internal` beside it. Python is not required.

The combined repair now fills the original feature first, then cuts the completed solid. **Fill down to wall** also handles enclosed union fragments that previously caused a false “separate pieces” failure. Truly separate exterior pieces are still rejected by fill.

1. Select the feature and open **2 Holds → Quick repair → Fill then trim**.
2. Start with **Trim height above wall = 0 mm**, then raise it to remove unwanted lower rock.
3. Click **Preview quick repair**. The cut becomes the flat back. If trimming separates offcuts, the main body is kept and the preview reports the removed pieces.
4. Inspect amber support and enable **Show original feature in coral**, then **Keep hold**.

Protected grip detail cannot be cut away, discarded as an offcut, or covered by fill. Filling can cover other undercuts, and missing front scan patches can remain through-openings. Inspect the preview before keeping.

Each preview starts from the original. Lowering the height restores rock. Save/reopen preserves the filled-and-cut body for later shape edits and export; these projects use format 9 and require 0.1.11 or later. Other projects continue using format 8. Older projects and saved repairs remain readable; a new preview applies the new sequence.

Validation: **102 full-suite tests passed**, followed by **12 focused repair checks** after the final metadata adjustment. Checks cover enclosed fragments versus real exterior pieces, solid cuts, reported offcuts, protection, restoring rock, shape edits, save/reopen, mounting, and STL reloads. A representative 50 mm crop from the reported OBJ reproduced the previous failure and now passes fill-only and fill-first repairs at 0 mm in the standalone executable. Private scans and review projects are excluded from the release.

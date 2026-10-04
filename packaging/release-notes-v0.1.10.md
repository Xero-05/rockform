# Rockform 0.1.10 — trim then fill

Download **Rockform-0.1.10-Windows-x64.zip**, extract the entire archive, and run **Rockform.exe**. Keep `_internal` beside it. Python is not required.

**Trim then fill** repairs an incomplete raised feature whose damaged edges extend above a useful base cut. It removes rock below one chosen height, then fills the remaining open edges down to a flat back 3 mm below the cut. Unlike trim alone, the cut does not have to clear every damaged boundary.

1. Select the feature and open **2 Holds → Quick repair → Trim then fill**.
2. Set **Trim height above wall**. Start low, then raise it to remove unwanted lower extensions.
3. Click **Preview quick repair**. Inspect amber added support and enable **Show original feature in coral** to see removed rock.
4. Click **Keep hold** when the shape is right.

Each preview starts from the original feature, so lowering the height restores previously trimmed rock. The original, repair mode, and cut height survive save/reopen. Existing fill-only and trim-only modes remain available; there are no separate side-cut controls.

Filling can cover undercuts, and front scan openings may remain through-openings. Protected grip detail cannot be cut away or covered by fill. Solid and intersection checks still gate mounting and export. Existing projects retain their saved geometry until a new repair is previewed.

Validation: **99 full-suite tests passed**, covering a cut that leaves damaged edges above it, source-surface preservation, restoring rock by lowering the cut, protection, saved drafts, mounting, and STL reloads. Representative crops from the reported OBJ also produced closed combined repairs at 10 mm and 20 mm. The standalone Windows app passed native viewer, saved-workspace, and all three repair-mode export checks. Private scans and review projects are excluded from the release.

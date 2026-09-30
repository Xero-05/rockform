Quick repair turns an incomplete raised feature into a reviewable solid by filling down to the wall or trimming off its damaged base.

### Download

Download **Rockform-0.1.6-Windows-x64.zip**, extract the entire archive, and run **Rockform.exe**. Keep `_internal` beside it. Python and Docker are not required.

### Use quick repair

1. Select or preview a raised feature marked **Needs repair**.
2. In **2 Holds → Quick repair**, choose **Fill down to wall** or **Trim damaged base**.
3. Click **Preview quick repair**. Trim height is automatic initially; turn it off to enter an exact height above the fitted wall.
4. Inspect amber added material and enable **Show original feature in coral** to compare. Click **Keep hold** when satisfied.

Fill adds material below the observed surface without a surrounding plate and resolves overlapping rim projections. It can cover undercuts; missing front patches may remain through-openings. Trim removes the lower damaged portion and closes the new underside, preserving the remaining rock surface. Invalid cuts and removal of protected details are rejected.

The original feature is saved with the repair. **Restore original feature** works after save/reopen, including unfinished previews. Switching repair methods starts from the original, preventing accumulated edits. Mounting uses the repaired solid's actual boundary. Repair operates at original scan scale and wall alignment; the usual shape and mount controls remain available afterward.

Quick repair currently applies to incomplete **raised features**. Recesses continue to use scan gap repair to preserve their cavities. Geometry validation remains required before export, and inferred material is not measured scan data.

### Compatibility

**Validation:** 76 tests passed, followed by six focused passing checks after the mounting-boundary fix. Both repaired examples exported and reimported successfully: approximately **65 × 69 × 34 mm** for fill and **62 × 64 × 15 mm** for trim. Release acceptance checks the final extracted executable on synthetic workflows, three supplied scans, and both quick repairs, including save/reopen of the original feature and unfinished previews.

Older projects remain readable. New saves use **format 7** and require **Rockform 0.1.6 or later**. Save a separate copy when retaining compatibility with an older app. Personal scans and derived examples are excluded from the download.

The app remains an unsigned shape and mounting prototype; geometry checks do not establish climbing qualification.

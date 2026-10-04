# Rockform 0.1.9 — contact texture on cropped features

Download **Rockform-0.1.9-Windows-x64.zip**, extract the entire archive, and run **Rockform.exe**. Keep `_internal` beside it. Python is not required.

Version 0.1.8 skipped the surrounding-rock margin when a feature's root touched an open crop edge. The new preview retains available original scan texture within the chosen margin and marks incomplete coverage for repair. The margin is also shown beside the control and on the model preview.

- Contact clipping preserves scanned triangles and undercuts; missing texture remains absent.
- The margin excludes arbitrarily deep adjacent rock faces below the observed root.
- **Fill down to wall** puts the back beneath the lowest retained contact surface, so the repair does not discard rock below the fitted wall. Generated support remains amber. Fill can cover undercuts; inspect before keeping.
- Open edges and missing areas still require scan repair or explicit quick repair before mounting or STL export.
- Existing projects retain their saved geometry. Preview or re-extract the natural feature to apply this fix. No project-format change.

Validation: **96 full-suite tests passed**, plus **17 contact/repair regressions** after the final metadata changes. Checks cover geometry and native UI behavior, representative crops from the reported OBJ, closed repaired STL reloads, and the standalone Windows bundle. The test scan and generated review projects are excluded from the release.

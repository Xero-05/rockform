# Rockform 0.1.6 validation

Quick repair offers explicit downward fill and base trimming for incomplete raised features. It preserves the original feature inside the new design and in saved previews. Both methods require a connected, watertight, consistently oriented solid without detected intersections. Filling resolves overlapping projected rim segments; only numerical union seams and sub-0.01 mm³ slivers are removed. The fill's reported numerical surface tolerance is 0.001 mm. Trimming refuses protected regions, cuts still crossing missing data, and less than 3 mm remaining feature height.

**76 tests passed** in 306.53 seconds. After the final mounting-footprint correction, all **six focused quick-repair tests passed** in 12.78 seconds. They cover folded rims, no surrounding plate, source preservation, STL round trips, save/reopen of the original and unkept repair previews, switching methods, restoration, protected areas, invalid cuts, cancellation, both mounting modes, and hardware sizes after resizing. Existing VTK/NumPy deprecation warnings remain non-fatal.

Both repairs passed source-runtime acceptance on the supplied `Rock_3DScanner.obj` feature (millimeters):

- Fill: **65.3 × 68.9 × 34.0 mm**, no surrounding plate and no through-openings.
- Trim: **62.3 × 64.3 × 15.2 mm**, automatic cut **18.86 mm above the fitted wall**, no added sides.

Both exported STLs were reimported and checked for connectivity, closure, consistent orientation, and intersections. Their originals survived save/reopen and a native-window draft save check. The input project hash remained unchanged. Private artifacts are under ignored `test-output/quick-016/source-acceptance-final/`.

Release acceptance uses the final extracted Windows ZIP for the synthetic workflows, all three supplied scans, and both quick repairs via `--verify-quick-repair <project.rockform> <output-folder>`. It clears Python environment variables, limits PATH to Windows system directories, and checks the executable against its manifest before running. Publication requires all checks to pass. New saves use format 7; formats 1–6 remain readable.

## Previous 0.1.5 validation

Version 0.1.5 adds incomplete-feature recognition without presenting an open scan surface as a printable solid. Tests cover complete features surrounded by scan damage, preservation of missing surface data, save/reopen of incomplete library items, mounting/export rejection, and explicit re-extraction from a repaired revision without changing the old feature.

The supplied `Rock_3DScanner.obj` contains 1,183,669 triangles and lacks unit metadata; the user confirmed millimeters. Default cleanup produces 1,185,011 triangles after fragment removal and small-gap repair. Detection identifies the central raised feature at approximately 65.3 × 68.9 × 27.8 mm, with about 15.6 mm measured broad relief. Its underside is incomplete. It is saved for repair with no solid or STL; the failed natural contact screen remains visible. Private review data lives under ignored `test-output/rock-015/`.

**69 automated tests passed** in 278.69 seconds on the development Windows computer. VTK's existing NumPy deprecation warnings remain non-fatal.

All four focused incomplete-feature workflow tests passed after the final adjustments, including a new branching-boundary regression found during packaged acceptance. Incomplete surfaces have no generated support to measure; their damaged boundaries must not interrupt recognition of other features. A preview cannot export a previously selected library solid.

Earlier regressions cover deep irregular recesses requiring wide support, preservation of the open cavity and source surface, save-dialog/reopen of unkept hold previews, unaccepted repair previews, painted regions, pending numeric edits, camera projection, active hold/tab, manual mount persistence and projects without saved view information. Saving does not accept a preview. Format 5 introduced workspace state; format 6 also supports incomplete library features. Readers retain support for formats 1–5.

The second supplied Scaniverse OBJ (approximately 792 × 508 × 200 mm) yields **six pocket holds**, with 3 mm beneath each cavity and gradual outer returns. Two additional incomplete regions remain available for repair without blocking the six valid solids. All six completed STL export/reimport and connected, watertight, orientation and intersection checks; the source hash is unchanged. The latest source acceptance report is under ignored `test-output/015-pocket-fixed/`. These are geometry checks, not climbing qualification.

New regressions cover natural-root extraction with no extra plate, zero added backing, preservation of the scanned surface, wall-plane fitting independent of scan rotation, separate protrusions, conditional support for recesses without filling their mouth, manual seed expansion, incomplete scan boundaries, missing interior data, default mount modes, unchanged hardware after resizing, persistence, STL round trips, cancellation and editing a preview before keeping it. Re-extraction creates a new preview without changing the old design or manual holes. Earlier repair, grip screening, geometry and native-interface regressions remain covered.

## Supplied real scan

The Scaniverse OBJ completes unit conversion, repair, natural-feature detection, extraction, save/reopen, STL export/reimport and native rendering. Its input hash is unchanged. The fitted wall is supported by about 72% of the spatial samples, with roughly 0.29 mm estimated residual noise.

The distinct protrusion produces a closed solid approximately **135.1 × 94.4 × 37.9 mm**, with **0 mm added backing and no surrounding plate**. The root is cut 1 mm above the fitted wall to exclude noise; retained triangles remain on the scan. It passes the broad-feature screen with about 32 mm measured natural relief. This replaces the older oversized-plate result and the 0.1.2 default search that missed the feature.

Both default mounting modes were successfully generated, cut, exported and checked: one bolt or two screws, without extra backing. A hole can remove the highest point, slightly reducing the exported maximum height. These are geometric fit checks, not strength tests.

Private project files, STLs, reports and screenshots are in ignored `test-output/feature-013-*` folders. The native preview was visually inspected with the feature upright against its wall plane. No scan files are included in the release.

## Packaged acceptance

`Rockform.exe --self-test <output-folder>` exercises three synthetic scan-to-export workflows and native rendering. `Rockform.exe --verify-scan <scan.obj> <output-folder>` checks repair, screening, extraction, exports, persistence and rendering on a supplied local scan. Rejection reasons remain recorded when no complete feature is available.

Release acceptance runs both commands using the executable extracted from the final ZIP, including all three supplied scans. Native-window checks also save and reopen an unkept preview through the workspace snapshot used by Save project. Python environment variables are cleared, PATH contains only Windows system directories, and the executable is checked against its build manifest. Publication requires passing these frozen-runtime checks. The tested dependencies remain in `requirements-lock.txt`.

## Still outstanding

- Three measured real iPhone captures, repeatability observations and small-feature accuracy checks.
- Slicer/toolpath review without repair, a prototype print and dimensional comparison with the source.
- A separate Windows computer without development tools, for independent graphics-driver and installation testing.
- Physical assessment of intended and unintended grips and qualification of print material, hardware, assembly and repeated loads before climbing use.

Natural-feature and support checks are conservative geometry heuristics, not guarantees of climbing usability or safety.

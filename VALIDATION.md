# Rockform 0.1.4 validation

**66 automated tests passed** in 249.26 seconds on the development Windows computer. VTK's existing NumPy deprecation warnings remain non-fatal.

New regressions cover deep irregular recesses requiring wide support, preservation of the open cavity and source surface, save-dialog/reopen of unkept hold previews, unaccepted repair previews, painted regions, pending numeric edits, camera projection, active hold/tab, manual mount persistence and projects without saved view information. Saving does not accept a preview. Project format 5 adds workspace state while retaining readers for formats 1–4.

The second supplied Scaniverse OBJ (approximately 792 × 508 × 200 mm) now yields **six pocket holds**, with 3 mm beneath each cavity and gradual outer returns. Two regions are rejected for incomplete/tangled data. All six completed STL export/reimport and connected, watertight, orientation and intersection checks; the source hash is unchanged. The prepared editable project remains local under ignored `test-output/recess-014-*` directories. These are geometry checks, not climbing qualification.

New regressions cover natural-root extraction with no extra plate, zero added backing, preservation of the scanned surface, wall-plane fitting independent of scan rotation, separate protrusions, conditional support for recesses without filling their mouth, manual seed expansion, incomplete scan boundaries, missing interior data, default mount modes, unchanged hardware after resizing, persistence, STL round trips, cancellation and editing a preview before keeping it. Re-extraction creates a new preview without changing the old design or manual holes. Earlier repair, grip screening, geometry and native-interface regressions remain covered.

## Supplied real scan

The Scaniverse OBJ completes unit conversion, repair, natural-feature detection, extraction, save/reopen, STL export/reimport and native rendering. Its input hash is unchanged. The fitted wall is supported by about 72% of the spatial samples, with roughly 0.29 mm estimated residual noise.

The distinct protrusion produces a closed solid approximately **135.1 × 94.4 × 37.9 mm**, with **0 mm added backing and no surrounding plate**. The root is cut 1 mm above the fitted wall to exclude noise; retained triangles remain on the scan. It passes the broad-feature screen with about 32 mm measured natural relief. This replaces the older oversized-plate result and the 0.1.2 default search that missed the feature.

Both default mounting modes were successfully generated, cut, exported and checked: one bolt or two screws, without extra backing. A hole can remove the highest point, slightly reducing the exported maximum height. These are geometric fit checks, not strength tests.

Private project files, STLs, reports and screenshots are in ignored `test-output/feature-013-*` folders. The native preview was visually inspected with the feature upright against its wall plane. No scan files are included in the release.

## Packaged acceptance

`Rockform.exe --self-test <output-folder>` exercises three synthetic scan-to-export workflows and native rendering. `Rockform.exe --verify-scan <scan.obj> <output-folder>` checks repair, screening, extraction, exports, persistence and rendering on a supplied local scan. Rejection reasons remain recorded when no complete feature is available.

Release acceptance runs both commands using the executable extracted from the final ZIP, including both supplied scans. Native-window checks also save and reopen an unkept preview through the workspace snapshot used by Save project. Python environment variables are cleared, PATH contains only Windows system directories, and the executable is checked against its build manifest. Publication requires passing these frozen-runtime checks. The tested dependencies remain in `requirements-lock.txt`.

## Still outstanding

- Three measured real iPhone captures, repeatability observations and small-feature accuracy checks.
- Slicer/toolpath review without repair, a prototype print and dimensional comparison with the source.
- A separate Windows computer without development tools, for independent graphics-driver and installation testing.
- Physical assessment of intended and unintended grips and qualification of print material, hardware, assembly and repeated loads before climbing use.

Natural-feature and support checks are conservative geometry heuristics, not guarantees of climbing usability or safety.

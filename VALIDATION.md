# Rockform 0.1.1 validation

## Completed on the development Windows machine

- **41 automated tests passed** across the geometry engine and native Qt interface.
- New regressions cover tapered concave outlines, full-resolution grip-vertex preservation, elongated boundary preparation, valid default profiles in both modes, measured failure explanations, alternate primary-hole placement, optional screw failures, zero-depth holes, live profile help, and migration of existing projects without changing manual hole edits or old shapes.
- Core checks cover contact-surface extraction, bolt-on and screw-on patterns, real through-holes, resizing without scaling hardware, invalid placements, excessive recess depth, protected grip regions (including interiors of coarse triangles), missing patches, smoothing/simplification, fragment previews, gap repair, undo/redo/reset, project round trips, source-revision isolation, scale conversion, cancellation, candidate detection, and actual triangle crossings.
- Native interface tests exercise cleanup preview/apply, history, candidate suggestions, hold creation, automatic mounting, dragging/editing mount positions, drilling, cutaway viewing, and a right-mouse protection brush followed by undo.
- The native window was visually inspected in scan, drilled-hold, and cutaway modes. Screenshots are in `test-output`.
- The self-contained executable completed all three synthetic workflows with `PYTHONHOME`/`PYTHONPATH` cleared and only Windows system directories on `PATH`.
- The packaged **native 3D viewer also opened and rendered successfully**. Release acceptance reports and screenshots are under `test-output`. A conflicting ICU DLL inherited from the build host was removed from the package, and `Rockform.spec` excludes it so Qt uses Windows' system ICU library.
- Three synthetic surfaces (ledge, rounded protrusion, and shallow pocket) complete cleanup, extraction, mounting, project save/reopen, STL export, and mesh reimport without losing closed-solid geometry. These are software fixtures, not real rock scans.
- One supplied Scaniverse OBJ completed automatic unit detection, repair preview/application, larger tapered hold suggestions, project save/reopen, and closed-solid export/reimport. Five small gaps were filled; regions unable to form valid solids were skipped. The input scan remained unchanged. New prepared edges follow the selected feature's proportions rather than a fixed circle.
- Both included hardware profiles were exercised on real-scan holds. A thin region was rejected for the larger bolt with a numerical thickness shortfall, while two screws fitted the same region. Successful drilled examples were exported and geometry-checked. These checks are not substitutes for hardware measurement, slicer validation, or physical testing.

The reproducible acceptance runner is `Rockform.exe --self-test <output-folder>`. Its `verification.json` records whether the packaged runtime was used, the three cases, and the native viewer check. It also produces project files, STL files, dimension reports, and a screenshot of its own application window.

For a local real scan, `Rockform.exe --verify-scan <scan.obj> <output-folder>` runs repair, suggestions, export/reimport, project round trip, and the native viewer. Its report is `scan-verification.json`. Private scans and test outputs are excluded from release downloads. Release acceptance runs both checks using the executable extracted from the final ZIP with Python environment variables cleared and only Windows system directories on `PATH`.

The test environment uses Windows x64, Python 3.12.14, and the dependency versions in `requirements-lock.txt`. VTK currently emits NumPy deprecation warnings in the tests; these did not cause failures.

## Still requires physical equipment or a separate environment

- Three measured **real** iPhone rock scans, including repeatability and small-feature accuracy observations.
- Opening each real output in the intended printer's slicer and checking its toolpaths without mesh repair.
- A physical prototype print, comparison with the source, and measurement separating scanner and printer error.
- A separate clean Windows computer or VM for independent installation and graphics-driver compatibility testing. Running the bundled runtime on this machine does not substitute for that test.
- Material, hardware, and assembly qualification for any load-bearing climbing use. No strength or climbing-safety claim is made by these software tests.

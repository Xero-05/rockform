# Rockform 0.1.0 validation

## Completed on the development Windows machine

- **22 automated tests passed** across the geometry engine and native Qt interface.
- Core checks cover contact-surface extraction, bolt-on and screw-on patterns, real through-holes, resizing without scaling hardware, invalid placements, excessive recess depth, protected grip regions (including interiors of coarse triangles), missing patches, smoothing/simplification, fragment previews, gap repair, undo/redo/reset, project round trips, source-revision isolation, scale conversion, cancellation, candidate detection, and actual triangle crossings.
- Native interface tests exercise cleanup preview/apply, history, candidate suggestions, hold creation, automatic mounting, dragging/editing mount positions, drilling, cutaway viewing, and a right-mouse protection brush followed by undo.
- The native window was visually inspected in scan, drilled-hold, and cutaway modes. Screenshots are in `test-output`.
- The self-contained executable completed all three synthetic workflows with `PYTHONHOME`/`PYTHONPATH` cleared and only Windows system directories on `PATH`.
- The packaged **native 3D viewer also opened and rendered successfully**. The final passing report and screenshot are in `test-output/dll-fixed-release`. A conflicting ICU DLL inherited from the build host was removed from the package, and `Rockform.spec` now excludes it so Qt uses Windows' system ICU library.
- Three synthetic surfaces (ledge, rounded protrusion, and shallow pocket) complete cleanup, extraction, mounting, project save/reopen, STL export, and mesh reimport without losing closed-solid geometry. These are software fixtures, not real rock scans.

The reproducible acceptance runner is `Rockform.exe --self-test <output-folder>`. Its `verification.json` records whether the packaged runtime was used, the three cases, and the native viewer check. It also produces project files, STL files, dimension reports, and a screenshot of its own application window.

The test environment uses Windows x64, Python 3.12.14, and the dependency versions in `requirements-lock.txt`. VTK currently emits NumPy deprecation warnings in the tests; these did not cause failures.

## Still requires physical equipment or a separate environment

- Three measured **real** iPhone rock scans, including repeatability and small-feature accuracy observations.
- Opening each real output in the intended printer's slicer and checking its toolpaths without mesh repair.
- A physical prototype print, comparison with the source, and measurement separating scanner and printer error.
- A separate clean Windows computer or VM for independent installation and graphics-driver compatibility testing. Running the bundled runtime on this machine does not substitute for that test.
- Material, hardware, and assembly qualification for any load-bearing climbing use. No strength or climbing-safety claim is made by these software tests.

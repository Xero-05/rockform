# Rockform 0.1.2 validation

## Software checks on the development Windows machine

**52 regression tests passed** (295.42 seconds). It covers natural-feature screening separately from closed-solid validity, as well as the existing repair, extraction, mounting, persistence and native-interface workflows.

New cases check fine texture at different mesh densities, extra backing that must not create grip evidence, a tilted flat scan, broad positive ledge/rounded fixtures, loss of a feature during boundary preparation, rejection of abrupt artificial ledges, gradual-return surface angles, preservation of scanned vertices, project migration, manual hole preservation, export reports, cancellation and a normal no-suitable-features UI result. Earlier tests still cover both mounting modes, protected regions, through-holes, resize behavior, invalid layouts, scan repairs, history and actual triangle intersections.

The source acceptance runner completed three **synthetic** scan-to-export workflows (ledge, rounded feature and pocket), including both mounting modes, save/reopen, STL reimport and native 3D rendering. These manual-extraction fixtures demonstrate solid construction; they are not all expected to qualify as automatic handhold suggestions. The built-in Ledge sample separately produced a positive default suggestion. Its green/amber preview was visually inspected.

The supplied real Scaniverse OBJ completed unit conversion, repair, screening, project save/reopen and native rendering. The input hash was unchanged. At default settings it produced **zero qualifying automatic suggestions**: two retained patches had about 1.0 and 2.6 mm broad natural relief; four other candidate regions failed geometry preparation. Reports distinguish missing natural-feature evidence from shape failures. This is not an exhaustive assessment of every possible boundary or mounting plane in the scan.

The previously suggested 8.4 mm plate contained only 2.4 mm total scanned height variation and roughly 1 mm of broad relief after detrending/filtering. It is now rejected regardless of added backing. The taller old example is also excluded because its generated edge contains a substantial artificial ledge. A valid solid or a fitting mount is not evidence of a useful grip.

Source acceptance reports and screenshots are in ignored `test-output/grip-012-*` directories. Personal scans and derived geometry are excluded from releases.

## Reproducing packaged acceptance

`Rockform.exe --self-test <output-folder>` runs the three synthetic workflows and captures its own native window. `Rockform.exe --verify-scan <scan.obj> <output-folder>` runs repair and screening on a local file. A legitimate no-suitable-features result is recorded without inventing an output hold or treating it as a software crash.

Release verification runs both commands using the executable **extracted from the final ZIP**, with Python environment variables cleared and only Windows system directories on PATH. It checks the executable against the manifest, checks exit codes and frozen-runtime reports, and captures the native viewer. The versioned ZIP is published only after these checks pass.

The development environment is Windows x64, Python 3.12.14 and the versions in `requirements-lock.txt`. VTK emits NumPy deprecation warnings; those are distinct from geometry failures.

## Still requires equipment or an independent environment

- Three measured real iPhone rock scans, repeatability observations and small-feature accuracy checks.
- Slicer/toolpath inspection without mesh repair, a physical prototype print and dimensional comparison with the source.
- A separate Windows computer or VM without development tools to test installation and graphics-driver compatibility. Restricting the bundled runtime on this computer is useful but does not replace that test.
- Physical assessment of intended and unintended grips. The support-angle and natural-relief screens are conservative heuristics, not a guarantee about how someone can hold the shape.
- Material, print-orientation, hardware, assembly and repeated-load qualification before climbing use.

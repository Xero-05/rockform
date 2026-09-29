Extracts the natural rock feature itself instead of surrounding it with an oversized backing plate.

### Download

Download **Rockform-0.1.3-Windows-x64.zip**, choose **Extract All**, and run **Rockform.exe**. Keep `_internal` beside it. No Python or Docker installation is needed.

### Changed

- A robust surrounding-wall fit identifies protrusions and recesses. Extraction follows the natural root instead of a fixed crop or a backing plane fitted through the grip.
- **Protrusions use 0 mm added backing and no surrounding plate.** A flat underside closes the retained rock, with a small reported allowance to exclude scan noise.
- **Recesses get conditional support** beneath and around the cavity. The inward shape is preserved; the mouth is not filled and the shape is not inverted.
- **Isolate natural feature** is enabled by default for manual selection and suggestions. Painting chooses the feature; extraction follows its complete outline. The default size range is 50–300 mm.
- Shape controls can rebuild a preview before keeping it. **Re-extract natural feature** makes a new preview from a saved design while preserving the old hold and its manual holes.
- Missing interior data and features cut off by the scan edge are rejected. Extraction does not silently repair gaps.

### Validation

**62 tests passed.** The supplied Scaniverse scan produces a **135 × 94 × 38 mm** natural-feature suggestion with **zero added backing**. Both default bolt-on and screw-on layouts were cut, exported and geometry-checked successfully. The original scan is unchanged. Private scans and derived examples are excluded from release downloads.

### Compatibility and limits

Older projects keep their shapes and hardware edits. New saves use format 4 and require Rockform 0.1.3 or later; save a separate copy for older-app compatibility. Re-extracted shapes are new designs and need their own mounting layouts.

Extraction needs enough surrounding wall for a reliable plane. Curved walls, complex undercuts, missing data and incomplete boundaries can still be rejected. The app remains an unsigned Windows prototype. Independent PC, slicer, print and physical climbing qualification remain separate validation steps.

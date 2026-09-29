Fixes circular pedestal-shaped holds and makes mounting usable with editable starting profiles.

### Download

Download **Rockform-0.1.1-Windows-x64.zip**, choose **Extract All**, then run **Rockform.exe**. Keep `_internal` beside it. No Python or Docker installation is needed.

### Changed

- Boundary preparation follows the selected feature's outline and proportions instead of falling back to a circular crop.
- New holds preserve the scanned grip and add a tapered edge down to the flat back. Defaults are 6 mm backing and 10 mm taper width. Both are editable; width 0 gives straight sides.
- Suggestions initially target larger 50–180 mm features.
- Editable 10 mm bolt and 5 mm screw prototype profiles are ready to use. Profile dialogs include preset selection and live dimension requirements.
- Mount failures explain required versus available edge distance, local thickness, or hole separation. Placement checks include tapered sides and retry primary-hole positions to make room for subsequent holes.
- Bolt-on starts with one bolt; the optional anti-rotation screw starts off.
- Existing projects retain shapes, custom profiles, and manual mounting edits. Empty profiles receive defaults. Use **2 Holds → Use tapered shape defaults** to update an older shape, or create a new hold to replace an old circular crop.

### Hardware starting points

The bolt preset cuts a 10.5 mm hole with an 18.5 mm cylindrical seat, 6 mm deep. It needs at least 24.5 mm clear width and 9 mm local thickness.

The screw preset cuts a 5.5 mm hole with a 10.5 mm conical seat, 2.5 mm deep. It needs at least 16.5 mm clear width and 5.5 mm local thickness.

These are editable prototype dimensions, not a hardware standard or strength rating. Match actual hardware before printing. Slopes, protected regions, and additional holes may need more room.

### Validation and compatibility

Geometry, mounting, project migration and native interface regressions are covered by the automated suite. Real Scaniverse testing includes non-circular tapered holds and both default mounting modes, with thin regions correctly rejected for the larger bolt. See `VALIDATION.md` for the current results.

Version 0.1.0 projects can be opened. Newly saved projects use format 2 and require version 0.1.1 or later; save a new copy if keeping the older version in use. The app remains an unsigned Windows prototype. Independent PC, slicer, print, and physical climbing-use validation remain outstanding.

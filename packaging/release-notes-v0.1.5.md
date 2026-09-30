Recognizes visible features in incomplete scans instead of hiding every feature that cannot yet form a printable solid.

### Download

Download **Rockform-0.1.5-Windows-x64.zip**, extract the entire archive, and run **Rockform.exe**. Keep `_internal` beside it. Python and Docker are not required.

### Recognition and repair

- Suggestions distinguish **Ready to preview** from **Needs repair**. A missing base, side, or interior patch no longer automatically removes a feature with broad measured relief from the list.
- Incomplete previews preserve the scanned surface and highlight damaged boundaries in coral. Missing grip geometry is not invented. The grip review still reports unconfirmed contact or orientation.
- **Keep feature for repair** adds the feature to the library. Save/reopen preserves it and its source revision. Mounting and STL export stay unavailable until a valid solid exists.
- After repairing the scan, **Re-extract natural feature** explicitly locates the saved incomplete feature on the repaired revision and creates a replacement preview. The original saved feature remains intact.
- Damage elsewhere in a scan does not prevent extraction of an intact feature. Ready shapes rank ahead of incomplete features.

The supplied `Rock_3DScanner.obj`, interpreted in millimeters as confirmed by the user, now produces a recognized feature approximately **65 × 69 × 28 mm**. Default cleanup repairs the small surface gap, while its incomplete underside remains clearly marked. This result is a reviewable scanned feature, not an exported printable hold.

### Validation and compatibility

**69 tests passed.** Regression checks cover recognition without filling missing surfaces, complete features amid damaged surrounding scans, save/reopen of incomplete library items, disabled mounting/export, and explicit re-extraction after a repaired revision. Release acceptance exercises the final extracted executable on the sample workflows and three supplied scans.

Older projects remain readable. New saves use **format 6** and require **Rockform 0.1.5 or later**. Preserve a copy for older-app compatibility. Private scans and derived review projects are excluded from release downloads.

The app remains an unsigned shape and mounting prototype. Geometry checks do not provide physical climbing qualification.

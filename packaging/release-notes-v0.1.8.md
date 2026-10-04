Raised holds now preserve surrounding scanned rock so fingers wrapping around the base retain more of the original contact geometry.

### Download

Download **Rockform-0.1.8-Windows-x64.zip**, extract the entire archive, and run **Rockform.exe**. Keep `_internal` beside it. Python and development tools are not required.

### Preserve surrounding rock

- **2 Holds → Surrounding rock** defaults to **10 mm** and accepts **0–50 mm**. Set it before suggesting or previewing a hold.
- The margin follows the natural root in the fitted wall plane. Original scanned texture, overhangs, and vertical grip surfaces remain intact; clipped triangles stay on their original surface.
- A **3 mm backing** beneath the lowest retained point and a shallow outer return close the shape. Green shows scanned rock; amber shows generated support.
- Set **0 mm** for the previous root-only shape with no added backing.
- Use **Re-extract natural feature** to make an updated preview of an existing hold. The saved design and its manual holes remain intact.

If the margin reaches an open scan edge or gap, repair or capture more surrounding rock, or reduce the margin. Missing texture is not invented. Already incomplete features remain available for the explicit repair workflow. Recess support is unchanged.

Margin settings, retained geometry, protection, mounts, and unfinished previews survive saving and reopening. Changing the margin invalidates cached suggestions before extraction. Generated return outlines are simplified by only 0.001 mm to avoid numerically thin back triangles; scanned contact geometry is unchanged.

### Validation and compatibility

The surrounding-rock update passed **93 tests**, including surface preservation, undercuts, protection, rotations, both mount modes, resizing, STL reimport, saved metadata, and native UI workflows. A reference Scaniverse scan produces a valid **171 × 130 × 42 mm** hold with the default margin, and retained vertices match its working surface within 0.000001 mm. Release acceptance checks the extracted Windows ZIP before publication.

Older projects remain readable and existing designs are preserved until re-extracted. New saves continue to use **format 8**, compatible with Rockform 0.1.7 or later. Personal scans and credentials are excluded from the download.

Rockform remains an unsigned shape and mounting prototype; geometry checks do not qualify prints for climbing loads.

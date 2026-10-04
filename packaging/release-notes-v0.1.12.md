# Rockform 0.1.12 — fill below the lowest point

Download **Rockform-0.1.12-Windows-x64.zip**, extract the entire archive, and run **Rockform.exe**. Keep `_internal` beside it. Python is not required.

Fill now places the flat back **3 mm below the lowest scanned point** for every raised feature. **Fill then trim** measures the cut upward from that filled back: **0 mm preserves the full fill and scanned patch**. Previously, zero meant the fitted wall, which could pass through low surrounding rock. Numerical seams left in the generated back by fill cleanup are now closed without capping missing front scan patches.

1. Open the project and select the feature in **2 Holds**.
2. Choose **Fill then trim**, with **Trim up from filled back = 0 mm**.
3. Click **Preview quick repair**. Raise the trim distance to remove lower extensions.
4. Inspect amber support, compare the original in coral, then **Keep hold**.

Old repairs retain their saved geometry. Their wall-relative cut controls display the equivalent distance above the filled back. Each new preview still starts from the original, so lowering the cut restores rock. Protected grip detail cannot be removed or covered by fill. Fill can cover other undercuts; front openings can remain through-openings.

The exact supplied project reproduced both fill-only and combined failures. Its full fill now forms a closed solid while retaining the scanned patch, with no through-openings. Private scans and projects are excluded from the release.

Validation: **106 full-suite tests passed**, followed by **17 focused repair tests** covering the final controls and legacy cut conversion. The exact saved project passes fill-only, zero trim, and 10/30/50 mm upward cuts, with STL reloads and saved-body rebuilds. Native viewer and save/reopen checks pass.

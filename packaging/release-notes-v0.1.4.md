Fixes deep pocket extraction and restores unfinished work when a saved project is reopened.

### Download

Download **Rockform-0.1.4-Windows-x64.zip**, extract the entire archive, and run **Rockform.exe**. Keep `_internal` beside it. No Python or Docker installation is needed.

### Pocket extraction

Deep recesses were detected but rejected by a backing-width rule intended for raised rock features. Pockets now allow the supporting body needed to preserve their depth. Natural contact, closed-solid, missing-data and artificial-ledge checks remain active.

The generated outer edge now follows the actual faceted outline, avoiding small artificial lips and nearly coincident seams. The supplied recess scan yields **six closed pocket holds**; two incomplete or tangled regions remain rejected. Raised features retain zero added backing and no outside plate. Deep pockets can have a substantially larger mounting footprint than their mouth; inspect the full preview dimensions.

### Saving and reopening

**Save project** now stores unfinished hold and repair previews, painted hold/cleanup selections, pending settings, the selected hold, active tab, and camera view alongside the existing geometry, history, protection, profiles and manual mounts. **Open project** restores this workspace. Saving does not silently accept a preview.

Older projects without saved view information open their first kept hold. Unkept previews or selections omitted by older app versions cannot be recovered from those archives.

### Validation and compatibility

**66 tests passed**, including save-dialog/reopen workflows for unkept previews, pending repair, pending shape edits, camera restoration and manually placed mounts. The supplied recess scan completed extraction, STL round trips and project reopening without changing its source file. Release acceptance also checks the final extracted executable with development Python paths removed.

Older projects remain readable. New saves use **format 5** and require **Rockform 0.1.4 or later**. Preserve a copy before saving if an older app still needs the original file. Private scans and derived projects are excluded from the download.

The application remains an unsigned shape and mounting prototype. Physical climbing qualification and independent PC testing remain separate validation steps.

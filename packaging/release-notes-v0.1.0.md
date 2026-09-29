First portable Windows release of Rockform, for scan-to-hold prototype testing.

### Download and run

1. Download **Rockform-0.1.0-Windows-x64.zip** from the assets below.
2. Right-click the ZIP and choose **Extract All**.
3. Open the extracted folder and double-click **Rockform.exe**. Keep the `_internal` folder beside it.

Python, Docker, and development tools are not required. This is a portable app, with no installer. Read `START-HERE.txt` in the download for the workflow. The repository is private, so teammates need repository access to download the release. The GitHub-generated source-code archives are for development and do not contain the Windows executable.

### Included

- OBJ/STL import, scale confirmation, and Scaniverse OBJ unit detection.
- Repair and cleanup previews, small-gap filling, protected grip areas, and undo/redo.
- Manual hold extraction and ranked suggestions, flat backs, and adjustable backing thickness.
- Bolt-on/screw-on mounting, editable hardware profiles and hole positions, and cutaway previews.
- Editable project saving, STL export with geometry checks, and a dimensions/settings report.

### Validation and limits

The automated suite passes 31 tests. Release checks exercise the extracted Windows app, native 3D viewer, three synthetic mounted exports, and repair/extraction of six hold prototypes from one real Scaniverse scan. Exported STLs are reimported and geometry-checked. Personal scans and their outputs are not included.

Tested on the development Windows 11 x64 PC. A separate clean Windows PC, other graphics drivers, slicer checks, physical prints, and measured scan accuracy still require validation. The executable is unsigned. The iPhone companion is a separate source project and is not included in this download.

Outputs remain **shape and mounting prototypes requiring separate physical validation before climbing use**. See `VALIDATION.md` in the repository for the full testing status.

The SHA-256 file verifies the ZIP download. The build-info file records the source commit and bundled library versions; library notices are included in the ZIP.

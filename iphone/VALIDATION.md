# Capture prototype validation — 2026-09-28

## Passed

- Swift 6.4 compiler running directly in Ubuntu 26.04 WSL; xtool 1.20.1 starts successfully. No Docker dependency.
- **12 Swift tests, zero failures**: meter-to-millimeter conversion in a translated/rotated frame, synthetic dimensions and winding, crop boundaries and index compaction, rejection of invalid indices and empty meshes, merging anchor index offsets, OBJ provenance/units, cancellation, CRC32, ZIP path validation, duplicate-path rejection, preservation of an existing file on cancellation, and photo-only metadata.
- Portable Swift executable generated `test-output/capture-demo.zip` using the same core exporter as the iPhone app. It contains 2,601 vertices and 5,000 faces with a 300 × 300 mm footprint.
- Windows Python read the actual Swift ZIP, verified all CRCs and a UTF-8 filename, and read its OBJ and report.
- Actual Rockform desktop code imported that OBJ in millimeters, ran cleanup, extracted a hold, generated a bolt and anti-rotation screw, saved and reopened an editable project, and exported STL. The reread STL passed closedness, connectivity, orientation, and intersection checks.
- iPhone property list parsed; camera permission is declared; LiDAR is optional; Files sharing is enabled.
- iOS Swift source parsed for syntax. **This is not Apple framework type checking.**
- Windows build helper ran successfully for portable tests. Its preflight identified the missing Darwin SDK and stopped with a clear message; it did not claim an IPA had been built.

Evidence: `test-output/swift-tests.txt`, `test-output/swift-export.txt`, `test-output/desktop-verification.json`, `test-output/swift-to-rockform.rockform`, and `test-output/synthetic-hold.stl`.

## Not yet verified

- Apple SDK compilation and linking of SwiftUI/ARKit/SceneKit source.
- Signing, IPA generation, installing, launching, and native UI rendering on iPhone.
- Camera permission flows, actual ARKit callbacks, LiDAR capability checks, crop overlay registration, buffer reading on hardware, tracking interruptions, thermal behavior, or memory/performance limits on a phone.
- Camera-free demo and photo-reference mode on the user's non-LiDAR phone.
- Any actual scan from the teammate's LiDAR phone, capture accuracy, or real scan-to-slicer/print workflow.

The user does not yet have the required Xcode `.xip`. That Apple download, followed by SDK setup, device signing, and physical phone tests, is the next prerequisite. See `SETUP-WINDOWS.md`. The delivered ZIP is a **source package, not an installable IPA**.

## Reproduce

```powershell
.\Build-iPhone.ps1 -Action test
# From the parent Rockform directory, using its existing Python environment:
.venv\Scripts\python.exe iphone\scripts\verify_desktop.py
```

The first command tests portable Swift and parses iOS syntax; the second uses the Swift-generated synthetic fixture through Rockform. Neither substitutes for on-device testing.

# Rockform Capture — iPhone prototype

A small, local Swift iPhone companion for the Windows Rockform app. No App Store submission or backend is needed. **Source prototype: the iOS app must still be compiled with Apple's SDK and tested on phones.**

## What each phone can test

| Mode | LiDAR needed? | Output |
|---|---|---|
| LiDAR | Yes, checked automatically | Cropped ARKit surface mesh, OBJ in millimeters, settings report, optional reference photos |
| Demo | No | Clearly labelled synthetic 300 × 300 mm surface and report; exercises preview, saving, sharing, and desktop import |
| Photos | No | Up to 20 reference JPEGs and a report; **no mesh or photogrammetry reconstruction** |

Requires iOS 17 or later. The app does not require LiDAR in its installation manifest, so the same build can install on both phones. Unsupported phones disable the LiDAR button. Camera permission is requested only for camera modes.

## Install and build

Read **[SETUP-WINDOWS.md](SETUP-WINDOWS.md)**. This project uses xtool and WSL, **not Docker**. Apple SDK download and device signing are still required even for a private project. Sending an unsigned IPA to someone does not make it installable on their phone.

From PowerShell in this directory:

```powershell
.\Build-iPhone.ps1 -Action check
.\Build-iPhone.ps1 -Action setup
.\Build-iPhone.ps1 -Action install
```

Use `package` to build an **unsigned** IPA, or `signed-package` after configuring signing. The tool prints the resulting path. Use `install` with the actual phone attached for the simplest first test.

## First test on your phone without LiDAR

1. Open **Demo**. Rotate the teal synthetic surface with a finger; pinch to zoom.
2. Enter an optional reference measurement and notes.
3. Choose **Save & share ZIP** and save/transfer the archive. It is also stored in Files → On My iPhone → Rockform Capture → Exports.
4. Unzip on Windows. Import `surface-mm.obj` into Rockform and select **Millimeters**. The full scan's X/Y bounds should read **300 × 300 mm**.
5. Run cleanup, selection, extraction, mounts, and export in Rockform as usual. The data remains explicitly synthetic.
6. Try **Photos**, approve the camera prompt, take several views, finish, and export. This archive deliberately contains no OBJ.

## First test on your teammate's LiDAR phone

1. Use diffuse light on a dry rock patch with a broad feature. Place a measured reference alongside it or measure two identifiable points.
2. Choose **LiDAR**. Set the square patch width (30–120 cm). Aim at its center, roughly 0.2–2 m away. Wait for **Tracking ready** and a center depth reading.
3. Tap **Start scan**. An orange box fixes the capture region to the rock; it no longer follows the phone. Box depth is 40 cm. Teal triangles show captured geometry.
4. Move slowly around the patch, including views of the top and underside. Watch for holes. Confidence shown is only the center depth sample, **not whole-mesh quality or coverage**.
5. Finish, rotate the mesh to inspect it, add the measured reference and notes, and save the ZIP.
6. Import OBJ in millimeters into Rockform and independently check scale. A stored reference is a note; it does not automatically correct geometry.

Keep the original ZIP. Restarting a scan or changing modes discards only the working capture after confirmation. Saved ZIPs can be shared again from the folder button; manage/delete them through Files.

## What this lightweight version deliberately leaves to Windows

- It exports an **open surface**. Cleanup, hole repair, hold extraction, backing, and mount cuts happen in Rockform.
- It uses ARKit's reconstructed mesh with plane detection disabled. It does not save raw depth sequences, reconstruct from photos, or claim greater fidelity than mature scanning apps.
- The camera-centered region is frozen at start; all vertices of a triangle must be inside it. Crop edges can therefore be jagged. Rescan with a margin around the desired hold.
- Reference JPEGs are reduced to 1600 px and rotated to portrait. Their sidecar camera intrinsics refer to the original ARKit frame, as explicitly recorded; this is not a ready-to-use calibrated photogrammetry dataset.
- Export limits: 500,000 vertices, 1,000,000 triangles, 20 photos, and a 300 MB archive. Smaller captures are recommended.
- App interruptions stop the current scan for review. Working captures are not recovered after force-quitting; save a ZIP before closing.
- Depth and tracking require physical device tests. Scan dimensions and printable parts require independent validation; nothing here qualifies a climbing hold for loading.

## Project contents

- `Sources/RockformCapture`: SwiftUI interface, ARKit capture, SceneKit live overlay and review, reference photos, local library.
- `Sources/CaptureCore`: portable mesh cropping, meter-to-millimeter coordinates, OBJ and ZIP export, capture report.
- `Tests/CaptureCoreTests`: coordinate, crop, indexing, report, cancellation, and archive checks.
- `Sources/CaptureCheck`: emits a real fixture using the same Swift exporter as the app.
- `Info.plist`, `xtool.yml`, `Package.swift`: iPhone packaging configuration.
- `Build-iPhone.ps1`, `scripts/build.sh`: setup checks, tests, building, signing, installation.

Run `Build-iPhone.ps1 -Action test` for portable Swift tests and iOS syntax parsing. This does not substitute for Apple SDK type checking or phone tests. See `VALIDATION.md` for the actual verification status.

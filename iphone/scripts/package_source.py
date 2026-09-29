"""Package only source, setup instructions, and small verification fixtures."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
destination = root.parent / "dist" / "RockformCapture-source.zip"
destination.parent.mkdir(parents=True, exist_ok=True)
fixed = ["Package.swift", "xtool.yml", "Info.plist", ".gitignore", "Build-iPhone.ps1",
         "README.md", "SETUP-WINDOWS.md", "VALIDATION.md"]
files = [root / name for name in fixed]
for directory in ("Sources", "Tests", "scripts"):
    files.extend(p for p in (root / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, "RockformCapture/" + path.relative_to(root).as_posix())
    for name in ("capture-demo.zip", "desktop-verification.json", "swift-tests.txt"):
        path = root / "test-output" / name
        if path.exists():
            archive.write(path, "RockformCapture/verification/" + name)
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert not any(any(s in name for s in (".tools/", ".build/", ".ipa", ".xip")) for name in archive.namelist())
print(f"Source package: {destination} ({destination.stat().st_size:,} bytes)")

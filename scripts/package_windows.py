"""Package an already-built Windows app without scans or development tools.

Run with the same Python environment used by PyInstaller. Outputs a portable
ZIP, its SHA-256 checksum, and a build manifest. No network or credentials used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def package(bundle: Path, output: Path):
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    bundle = bundle.resolve()
    if not (bundle / 'Rockform.exe').is_file() or not (bundle / '_internal').is_dir():
        raise ValueError('Build Rockform.spec with PyInstaller first; the complete app folder is required.')
    if git('status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Commit tracked source changes before packaging a versioned release.')
    source_commit = git('rev-parse', 'HEAD')
    output.mkdir(parents=True, exist_ok=True)
    stem = f'Rockform-{version}-Windows-x64'
    destination = output / f'{stem}.zip'
    if destination.exists():
        raise FileExistsError(f'Refusing to replace an existing release archive: {destination}')

    # Only the built app directory is included, never a recursive copy of the repo.
    files = sorted(p for p in bundle.rglob('*') if p.is_file())
    if any(p.is_symlink() for p in bundle.rglob('*')):
        raise ValueError('The release bundle must not contain symbolic links.')
    dependencies = []
    notices = ['# Bundled library notices', '',
        'These libraries retain their upstream licenses. Copies supplied by the installed',
        'distributions are included under licenses/. No project license is granted by this list.', '',
        'Qt/PySide DLLs are dynamically loaded and unmodified. Corresponding Qt/PySide source',
        'releases are available from https://download.qt.io/official_releases/QtForPython/.', '',
        '| Distribution | Version | License metadata |', '| --- | --- | --- |']
    license_files = {}
    # Metadata directories are copied by the PyInstaller spec for runtime libraries.
    names = {metadata.PathDistribution(p).metadata['Name'] for p in (bundle / '_internal').glob('*.dist-info')}
    for name in sorted(names, key=str.lower):
        dist = metadata.distribution(name)
        expression = dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See license files'
        expression = expression.replace('\n', ' ').replace('|', '/')
        if len(expression) > 180:
            expression = 'See included license text'
        dependencies.append(dict(name=dist.metadata['Name'], version=dist.version))
        notices.append(f'| {dist.metadata["Name"]} | {dist.version} | {expression} |')
        for entry in dist.files or []:
            relative = Path(str(entry))
            if (any(part.lower() == 'licenses' for part in relative.parts)
                or any(key in relative.name.lower() for key in ('license', 'copying', 'copyright', 'notice'))):
                if '..' in relative.parts or relative.is_absolute():
                    continue
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    license_files[f'licenses/{dist.metadata["Name"]}/{relative.as_posix()}'] = source
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        license_files['licenses/Python/LICENSE.txt'] = python_license
    for source in (ROOT / 'packaging' / 'licenses').rglob('*'):
        if source.is_file():
            relative = source.relative_to(ROOT / 'packaging' / 'licenses')
            license_files[f'licenses/{relative.as_posix()}'] = source
    with (bundle / 'Rockform.exe').open('rb') as stream:
        executable_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    info = dict(application='Rockform', version=version, source_commit=source_commit,
        repository='https://github.com/Xero-05/rockform', platform='Windows x64',
        built_with_python=platform.python_version(), packaged_utc=datetime.now(timezone.utc).isoformat(),
        executable_sha256=executable_digest,
        dependencies=dependencies, code_signed=False, purpose='Shape and mounting prototype')
    manifest = json.dumps(info, indent=2) + '\n'
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, f'{stem}/{path.relative_to(bundle).as_posix()}')
        archive.write(ROOT / 'WINDOWS-START-HERE.txt', f'{stem}/START-HERE.txt')
        archive.writestr(f'{stem}/build-info.json', manifest)
        archive.writestr(f'{stem}/THIRD-PARTY-NOTICES.md', '\n'.join(notices) + '\n')
        for name, path in sorted(license_files.items()):
            archive.write(path, f'{stem}/{name}')
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive integrity check failed.')
        forbidden = ('.venv/', '.tools/', 'test-output/', 'Your Scan - Repaired/')
        if any(any(part in name for part in forbidden) for name in archive.namelist()):
            raise ValueError('Development files were found in the release archive.')
    with destination.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    checksum = output / f'{stem}.sha256'
    checksum.write_text(f'{digest}  {destination.name}\n', encoding='ascii')
    (output / f'{stem}.build-info.json').write_text(manifest, encoding='utf-8')
    print(json.dumps(dict(zip=str(destination), bytes=destination.stat().st_size,
                          sha256=digest, source_commit=source_commit, libraries=len(dependencies)), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'dist' / 'Rockform')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'releases')
    args = parser.parse_args()
    package(args.bundle, args.output)

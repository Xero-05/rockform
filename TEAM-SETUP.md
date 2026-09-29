# Rockform for teammates

This repository contains the Windows Rockform app and the Swift iPhone capture
prototype in `iphone/`. Scans, exported holds, local tools, and Apple signing
credentials are kept outside Git. All scan processing stays on your computer.

## Get access

The repository owner can invite teammates from **Settings → Collaborators →
Add people**. Accept the invitation while signed in to your own GitHub account.
Use your own account for commits; never share a GitHub password or token.

## Use the Windows app without installing development tools

For a published version, open [Releases](https://github.com/Xero-05/rockform/releases),
download **Rockform-0.1.3-Windows-x64.zip**, extract the whole folder, and open
**Rockform.exe**. Choose the Windows ZIP asset, not GitHub's source-code ZIP.
Read **START-HERE.txt** inside the download. Teammates must be signed in and have
repository access to download a private release.

To build a newer development snapshot:

1. Open the repository's **Actions** tab and choose **Build Windows app**.
2. Someone with write access clicks **Run workflow** on `main`. The build is
   manual so ordinary pushes do not start a package build.
3. Open a successful run and download **Rockform-Windows-x64** from **Artifacts**.
4. Extract the entire ZIP to a folder on your Windows PC.
5. Open **Rockform.exe**. Keep its `_internal` folder beside it.

The workflow tests geometry and creates the package. It does not test camera
capture, graphics drivers, or physical prints. Artifact downloads expire after
14 days; run the build again when a new download is needed. Account Actions and
storage limits apply. See [GitHub's manual workflow instructions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

The app's **Guide** button explains importing, repair, hold selection, and mounts.
Start with a synthetic sample if you do not have a scan yet. Personal prepared
scan examples mentioned in the quick-start guide are not part of a source clone.

Release maintainers can build with `python -m PyInstaller --noconfirm Rockform.spec`
and then run `python scripts/package_windows.py`. Packaging requires a clean,
committed source checkout and produces a ZIP, checksum, and build manifest in
`dist/releases`. Test the extracted ZIP before publishing it with a matching tag.

## Develop on Windows

Install Git and **Python 3.12 x64**, then clone the repository using its **Code →
HTTPS** URL (GitHub Desktop also works). Open PowerShell in the cloned folder:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m pip install --no-deps -e .
.venv\Scripts\python.exe -m rockform.app
```

After setup, **Start Rockform.cmd** opens the app. If you built a packaged copy
in `dist`, the launcher prefers that copy. Use the Python command above when
testing current source edits.

Before sharing changes:

```powershell
git switch main
git pull --ff-only
git switch -c your-feature-name
# Make your changes.
.venv\Scripts\python.exe -m pytest -q
git add <files-you-changed>
git commit -m "Describe the change"
git push -u origin your-feature-name
```

Open a pull request to `main` for team review. Do not commit the `.venv`, `dist`,
tool downloads, scan files, or credentials. Store scans in `scans/` and exports
in `exports/`; both folders are ignored. Share large scan examples separately.

## iPhone companion

Read [iphone/README.md](iphone/README.md) and
[iphone/SETUP-WINDOWS.md](iphone/SETUP-WINDOWS.md). The repository includes source,
not an installable IPA. Each build environment needs the matching Apple SDK and
its own signing setup. Device testing is still required.

The printed outputs are shape and mounting prototypes; software geometry checks
do not qualify them for climbing loads.

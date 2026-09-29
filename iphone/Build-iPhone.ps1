param(
    [ValidateSet('check','setup','test','devices','package','signed-package','install')]
    [string]$Action = 'check',
    [string]$Distribution = 'Ubuntu'
)
$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$linuxDirectory = (& wsl.exe -d $Distribution -- wslpath -u $projectDirectory)
if ($LASTEXITCODE -ne 0 -or -not $linuxDirectory) {
    throw 'WSL is unavailable. Follow SETUP-WINDOWS.md. Docker is not required.'
}
$linuxDirectory = $linuxDirectory.Trim()
& wsl.exe -d $Distribution --cd $linuxDirectory -- bash scripts/build.sh $Action
exit $LASTEXITCODE

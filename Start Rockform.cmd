@echo off
cd /d "%~dp0"
if exist "dist\Rockform\Rockform.exe" (
  start "" "dist\Rockform\Rockform.exe"
) else (
  start "" ".venv\Scripts\pythonw.exe" "launch.pyw"
)

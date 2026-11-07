@echo off
rem First-time setup. Double-click once after unpacking. See INSTALLATION-GUIDE.md.
cd /d "%~dp0"
rem Files from a downloaded zip carry a "from the internet" mark that can stop PowerShell scripts; remove it from this folder only.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File | Unblock-File"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
echo.
pause

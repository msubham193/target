@echo off
rem Stops the API, the worker and the web app, then the database container. Your data is kept.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"
docker stop geopoc-db >nul 2>&1
echo.
echo Stopped. Your projects and files are kept. Double-click START.cmd to start again.
pause

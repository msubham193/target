@echo off
rem Starts the application: Docker, the database, the API, the worker and the web app, then opens the browser.
cd /d "%~dp0"
rem On a computer without a disk D:, setup maps C:\INT-TGT-storage to D:. That mapping ends at every restart; renew it.
if not exist D:\ if exist C:\INT-TGT-storage subst D: C:\INT-TGT-storage
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1" -StartDocker
if errorlevel 1 (
  echo.
  echo Something did not start. Read the messages above and the Troubleshooting section of INSTALLATION-GUIDE.md.
  pause
  exit /b 1
)
start "" http://127.0.0.1:5173
echo.
echo The application is running at http://127.0.0.1:5173
echo Keep the API, worker and web windows open while you use it. Double-click STOP.cmd to stop everything.
pause

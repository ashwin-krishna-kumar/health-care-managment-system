@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Install Node.js 22 or 24 including npm, then try again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" goto setup
if not exist "node_modules\hardhat" goto setup
if not exist ".tools\ipfs.exe" goto setup
goto start
:setup
python scripts\setup_project.py
if errorlevel 1 goto failed
:start
python scripts\dev.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Setup or startup failed. Read the message above. Python 3.12 is required.
pause
exit /b 1

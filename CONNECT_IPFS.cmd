@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto have_python
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto have_python
:use_python
python -m venv .venv
if errorlevel 1 goto failed
:have_python
".venv\Scripts\python.exe" -m pip install -r backend\requirements-ipfs.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\connect_ipfs.py %*
if errorlevel 1 goto failed
echo.
echo IPFS checks passed. Restart your backend with:
echo .venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0
:failed
echo.
echo Setup did not finish. Read the message above and run CONNECT_IPFS.cmd again.
pause
exit /b 1

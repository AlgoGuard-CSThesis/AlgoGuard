@echo off
REM ---------------------------------------------------------------------
REM AlgoGuard setup for an analyst computer (Stage 6D.1).
REM
REM Installs the Python packages into the "venv" folder and creates the
REM settings file .env. Run it once after unpacking AlgoGuard, and again
REM after an update. It never trains a model and needs no secret key:
REM only the project URL and publishable key from your administrator.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title AlgoGuard setup
set "PYTHON_VERSION=3.14.6"
set "CHECK_VERSION=import platform, sys; sys.exit(platform.python_version() != '%PYTHON_VERSION%')"

if exist "venv\Scripts\python.exe" goto have_venv
py -3.14 -c "%CHECK_VERSION%" >nul 2>&1
if errorlevel 1 (
    echo AlgoGuard needs Python %PYTHON_VERSION% ^(64-bit^), which was not found.
    echo Install it from https://www.python.org/downloads/release/python-3146/
    echo then run this file again.
    goto failed
)
echo [1/3] Creating the Python environment in the venv folder...
py -3.14 -m venv venv
if errorlevel 1 goto failed

:have_venv
venv\Scripts\python.exe -c "%CHECK_VERSION%"
if errorlevel 1 (
    echo The venv folder was made with a different Python version.
    echo Delete the venv folder, install Python %PYTHON_VERSION% if needed, and run this file again.
    goto failed
)
echo [2/3] Installing AlgoGuard's Python packages ^(a few minutes the first time^)...
venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Installing the packages failed. Check the internet connection and run this file again.
    goto failed
)

echo [3/3] Checking the settings file .env...
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo Created .env from .env.example.
)
venv\Scripts\python.exe config.py
if not errorlevel 1 goto settings_ok
echo.
echo Notepad will open .env. Enter the project URL and publishable key your administrator
echo gave you after NEXT_PUBLIC_SUPABASE_URL= and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=,
echo then save and close Notepad.
start /wait notepad ".env"
venv\Scripts\python.exe config.py
if errorlevel 1 (
    echo Fix .env as described above, then run this file again.
    goto failed
)

:settings_ok
if not exist "%SystemRoot%\System32\Npcap\wpcap.dll" (
    echo.
    echo Live capture needs Npcap: install it from https://npcap.com with the default options.
    echo Recorded files and sample replays work without it.
)
echo.
echo Setup finished. Start AlgoGuard with START_ALGOGUARD.cmd.
pause
exit /b 0

:failed
echo.
echo Setup did not finish.
pause
exit /b 1

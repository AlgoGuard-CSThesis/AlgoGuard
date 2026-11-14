@echo off
REM ---------------------------------------------------------------------
REM AlgoGuard: double-click this file. It opens the AlgoGuard window,
REM where you set up, enter your settings, start and stop AlgoGuard.
REM SETUP_ALGOGUARD.cmd and START_ALGOGUARD.cmd do the same by hand.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title AlgoGuard
set "PYTHON_VERSION=3.14.6"
py -3.14 -c "import platform, sys, tkinter; sys.exit(platform.python_version() != '%PYTHON_VERSION%')" >nul 2>&1
if errorlevel 1 (
    echo AlgoGuard needs Python %PYTHON_VERSION% ^(64-bit^), which was not found.
    echo The download page will open. Install it with the default options,
    echo then double-click this file again.
    start "" "https://www.python.org/downloads/release/python-3146/"
    pause
    exit /b 1
)
start "" pyw -3.14 "%~dp0algoguard_launcher.pyw"
exit /b 0

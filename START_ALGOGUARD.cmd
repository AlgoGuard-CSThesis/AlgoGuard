@echo off
REM ---------------------------------------------------------------------
REM Starts AlgoGuard (Stage 6D.1). Close this window, or press Ctrl+C in it,
REM to stop AlgoGuard. If the Live Monitor lists live capture as unavailable,
REM right-click this file and choose "Run as administrator"; replaying
REM recorded files works either way.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title AlgoGuard (close this window to stop AlgoGuard)

if not exist "venv\Scripts\python.exe" (
    echo AlgoGuard is not set up yet. Run SETUP_ALGOGUARD.cmd first.
    pause
    exit /b 1
)
venv\Scripts\python.exe config.py
if errorlevel 1 (
    echo Run SETUP_ALGOGUARD.cmd to fix the settings.
    pause
    exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
    echo Not running as administrator. If the Live Monitor lists live capture as
    echo unavailable, close this window and start it with "Run as administrator".
)
echo Starting AlgoGuard. Open the address above in your browser once "Running on" appears.
venv\Scripts\python.exe app.py
pause

@echo off
rem Starts AlgoGuard for the manual test cases. Run as administrator to allow live capture.
cd /d "%~dp0\.."
title AlgoGuard app (close this window to stop the app)
call venv\Scripts\activate.bat
echo Starting AlgoGuard on http://127.0.0.1:5000 ...
python app.py
pause

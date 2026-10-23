@echo off
rem AlgoGuard test cycle 1 - runs the automated checks on this PC and saves results to test-results\
rem Double-click to run. Right-click > "Run as administrator" to include the Npcap capture tests.
cd /d "%~dp0\.."
title AlgoGuard automated tests
if not exist venv\Scripts\python.exe (
  echo The venv folder was not found. Create it first: py -3.14 -m venv venv
  pause
  exit /b 1
)
call venv\Scripts\activate.bat

echo Installing the two scan tools (pip-audit, bandit) into the venv if missing...
python -m pip install --quiet --disable-pip-version-check pip-audit bandit

set FLAGS=--security
net session >nul 2>&1 && (set RUN_WINDOWS_CAPTURE_TESTS=1 & echo Running as administrator: Npcap capture tests included.)
call npx --no-install supabase status >nul 2>&1 && (set FLAGS=--all & echo Local Supabase stack is running: integration tests included.)

echo.
echo === Automated test suites ===
python scripts\run_test_suites.py %FLAGS%

echo.
echo === Detection evaluation of the deployed model (training run 8, feature schema v2) ===
if not exist research\dataset\processed\flow_window_v2\holdout_eval.csv (
  python scripts\build_flow_datasets.py --evidence test-results\flow_datasets
)
python scripts\evaluate_model.py --model saved_models\run_8\stacking.joblib --compare-dir saved_models\run_8 --data research\dataset\processed\flow_window_v2\holdout_eval.csv --train-csv research\dataset\processed\flow_window_v2\training.csv

echo.
echo Finished. Results are in the test-results folder. You can close this window.
pause

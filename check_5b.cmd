@echo off
REM ---------------------------------------------------------------------
REM AlgoGuard Stage 5B — one-shot verification run.
REM
REM Runs every check for Stage 5B and writes each result to a file
REM under check-output\. Nothing here changes the cloud pilot project:
REM every step targets the local Docker stack.
REM
REM Run it from a terminal in the repo root:   check_5b.cmd
REM Docker Desktop must be running first.
REM Takes a few minutes, mostly waiting for the stack to restart.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
set "PATH=%CD%\node_modules\.bin;%PATH%"
if not exist check-output mkdir check-output
set "CHECK_FAILED=0"
>check-output\00-summary.txt echo Stage 5B check exit codes (0 = command succeeded)

if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat

echo [1/9] Installing requirements (PyJWT[crypto] is new in 5B.2)...
python -m pip install -r requirements.txt -r requirements-dev.txt -r requirements-maintainer.txt > check-output\01-pip.txt 2>&1
call :record pip %errorlevel%
if "%CHECK_FAILED%"=="1" goto finished

if not exist supabase\signing_keys.json (
    call supabase gen signing-key --algorithm ES256 > supabase\signing_keys.json
    if errorlevel 1 (
        del supabase\signing_keys.json
        set "CHECK_FAILED=1"
        goto finished
    )
    python -c "import json,pathlib; p=pathlib.Path('supabase/signing_keys.json'); k=json.loads(p.read_text(encoding='utf-8-sig')); p.write_text(json.dumps(k if isinstance(k,list) else [k]),encoding='utf-8')"
    if errorlevel 1 (
        set "CHECK_FAILED=1"
        goto finished
    )
)

echo [2/9] Restarting the local stack so config.toml changes take effect...
call supabase stop > check-output\02-supabase-stop.txt 2>&1
call :record stack-stop %errorlevel%
call supabase start > check-output\03-supabase-start.txt 2>&1
call :record stack-start %errorlevel%
if "%CHECK_FAILED%"=="1" goto finished

echo [3/9] Applying all versioned migrations from scratch...
call supabase db reset --local > check-output\04-db-reset.txt 2>&1
call :record db-reset %errorlevel%
if "%CHECK_FAILED%"=="1" goto finished

echo [4/9] Refreshing local stack credentials...
call supabase status -o env > check-output\local-env.tmp 2>check-output\05-status-err.txt
set "STATUS_RESULT=%errorlevel%"
if "%STATUS_RESULT%"=="0" copy /y check-output\local-env.tmp .env.supabase.local >nul
call :record stack-status %STATUS_RESULT%
del check-output\local-env.tmp
if "%CHECK_FAILED%"=="1" goto finished

echo [5/9] Fast unit tests...
python -m pytest -q > check-output\06-pytest.txt 2>&1
call :record unit-tests %errorlevel%

echo [6/9] Lint...
python -m ruff check . > check-output\07-ruff.txt 2>&1
call :record lint %errorlevel%

echo [7/9] Migration runner against the local stack...
set DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
python cloud_migrate.py --status > check-output\08-migrate-status.txt 2>&1
call :record migration-status %errorlevel%
set DATABASE_URL=

echo [8/9] Integration tests...
python -m pytest -m integration -v -rs > check-output\09-integration.txt 2>&1
call :record integration-tests %errorlevel%

echo [9/9] What the stack signs tokens with...
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:54321/auth/v1/.well-known/jwks.json > check-output\10-jwks.txt 2>&1
call :record jwks %errorlevel%
call supabase gen signing-key --help > check-output\11-gen-signing-key-help.txt 2>&1
call :record signing-key-help %errorlevel%

:finished
echo.
echo Done. Inspect every result in check-output\ before accepting this stage.
echo Skipped integration tests are not verification evidence.
endlocal & exit /b %CHECK_FAILED%

:record
>>check-output\00-summary.txt echo %~1: %~2
if not "%~2"=="0" set "CHECK_FAILED=1"
exit /b 0

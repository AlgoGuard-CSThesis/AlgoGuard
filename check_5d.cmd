@echo off
REM ---------------------------------------------------------------------
REM AlgoGuard Stage 5D - one-shot verification run.
REM
REM Runs every Stage 5D check and writes each result to check-output\5d\.
REM Every step targets the local Docker stack; nothing here changes the
REM cloud pilot project, and no step uses an analyst's credentials to apply
REM migrations.
REM
REM Run it from a terminal in the repo root:   check_5d.cmd
REM Docker Desktop and Npcap must be available first. Takes several minutes: the stack
REM restarts, all fourteen migrations apply from scratch, and the end-to-end
REM test trains a small model and publishes it to local Storage.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
set "PATH=%CD%\node_modules\.bin;%PATH%"
set "OUT=check-output\5d"
if not exist "%OUT%" mkdir "%OUT%"
set "CHECK_FAILED=0"
set "RUN_WINDOWS_CAPTURE_TESTS=1"
>"%OUT%\00-summary.txt" echo Stage 5D check exit codes (0 = command succeeded)

if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat

echo [1/11] Installing requirements...
python -m pip install -r requirements.txt -r requirements-dev.txt -r requirements-maintainer.txt > "%OUT%\01-pip.txt" 2>&1
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

echo [2/11] Restarting the local stack...
call supabase stop > "%OUT%\02-supabase-stop.txt" 2>&1
call :record stack-stop %errorlevel%
call supabase start > "%OUT%\03-supabase-start.txt" 2>&1
call :record stack-start %errorlevel%
if "%CHECK_FAILED%"=="1" goto finished

echo [3/11] Applying all versioned migrations from scratch...
call supabase db reset --local > "%OUT%\04-db-reset.txt" 2>&1
call :record db-reset %errorlevel%
if "%CHECK_FAILED%"=="1" goto finished

echo [4/11] Refreshing local stack credentials...
call supabase status -o env > "%OUT%\local-env.tmp" 2>"%OUT%\05-status-err.txt"
set "STATUS_RESULT=%errorlevel%"
if "%STATUS_RESULT%"=="0" copy /y "%OUT%\local-env.tmp" .env.supabase.local >nul
call :record stack-status %STATUS_RESULT%
del "%OUT%\local-env.tmp"
if "%CHECK_FAILED%"=="1" goto finished

echo [5/11] Fast unit tests (includes the offline cloud-app suite)...
python -m pytest -q > "%OUT%\06-pytest.txt" 2>&1
call :record unit-tests %errorlevel%

echo [6/11] Lint...
python -m ruff check . > "%OUT%\07-ruff.txt" 2>&1
call :record lint %errorlevel%

echo [7/11] Node polling tests...
node --test tests/monitor_poll.test.cjs > "%OUT%\08-node.txt" 2>&1
call :record node-tests %errorlevel%

echo [8/11] Migration runner status against the local stack...
set DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
python cloud_migrate.py --status > "%OUT%\09-migrate-status.txt" 2>&1
call :record migration-status %errorlevel%
set DATABASE_URL=

echo [9/11] Integration tests (real Auth, Data API, RLS, Storage, Edge Function)...
python -m pytest -m integration -v -rs > "%OUT%\10-integration.txt" 2>&1
call :record integration-tests %errorlevel%
findstr /r /c:" SKIPPED" /c:" skipped" "%OUT%\10-integration.txt" >nul
if not errorlevel 1 (
    >>"%OUT%\00-summary.txt" echo integration-skips: present - not acceptance evidence
    set "CHECK_FAILED=1"
)

echo [10/11] Stage 5D end-to-end test on its own, for a focused log...
python -m pytest -m integration -v -rs tests/integration/test_cloud_app_stack.py tests/integration/test_cloud_queries.py > "%OUT%\11-integration-5d.txt" 2>&1
call :record integration-5d %errorlevel%

echo [11/11] What the stack signs tokens with...
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:54321/auth/v1/.well-known/jwks.json > "%OUT%\12-jwks.txt" 2>&1
call :record jwks %errorlevel%

:finished
echo.
echo Done. Inspect every result in %OUT%\ before accepting this stage.
echo Skipped integration tests are not verification evidence.
echo Windows capture-exclusion evidence is a separate manual step; see
echo docs\migration\05d-integration.md section 5.
endlocal & exit /b %CHECK_FAILED%

:record
>>"%OUT%\00-summary.txt" echo %~1: %~2
if not "%~2"=="0" set "CHECK_FAILED=1"
exit /b 0

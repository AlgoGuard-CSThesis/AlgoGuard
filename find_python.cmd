@echo off
REM ---------------------------------------------------------------------
REM Finds Python 3.14.6 (64-bit, with Tcl/Tk) for AlgoGuard. ALGOGUARD.cmd
REM and SETUP_ALGOGUARD.cmd call it; it is not meant to be run by itself,
REM and it never changes anything.
REM
REM Sets PYTHON (python.exe) and PYTHONW (pythonw.exe) when it finds one,
REM or OTHER_PYTHON when only another Python 3.14 is installed. It looks
REM in this order: the Python launcher (py -3.14), the folder where
REM ALGOGUARD.cmd installs Python, and the Windows registry (PEP 514).
REM ---------------------------------------------------------------------
set "PYTHON_VERSION=3.14.6"
set "PYTHON="
set "PYTHONW="
set "OTHER_PYTHON="
set "AG_CANDIDATE="
set "AG_PROBE=%TEMP%\AlgoGuard-python-%RANDOM%.txt"

REM Input from nul: a launcher that offers to install a missing Python gets "no" at once.
py -3.14 -c "import sys; print(sys.executable)" <nul >"%AG_PROBE%" 2>nul
if errorlevel 1 goto ag_folder
set /p AG_CANDIDATE=<"%AG_PROBE%"
if defined AG_CANDIDATE call :ag_try "%AG_CANDIDATE%"
:ag_folder
if not defined PYTHON call :ag_try "%LocalAppData%\Programs\Python\Python314\python.exe"
if defined PYTHON goto ag_found
for %%k in (HKCU HKLM) do for /f "tokens=2,*" %%a in ('reg query "%%k\Software\Python\PythonCore\3.14\InstallPath" /v ExecutablePath 2^>nul') do if /i "%%a"=="REG_SZ" call :ag_try "%%b"
if defined PYTHON goto ag_found
del "%AG_PROBE%" >nul 2>&1
set "AG_PROBE="
set "AG_CANDIDATE="
exit /b 1

:ag_found
for %%i in ("%PYTHON%") do set "PYTHONW=%%~dpipythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=%PYTHON%"
set "OTHER_PYTHON="
del "%AG_PROBE%" >nul 2>&1
set "AG_PROBE="
set "AG_CANDIDATE="
exit /b 0

:ag_try
if defined PYTHON exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import platform, sys, tkinter; sys.exit(platform.python_version() != '%PYTHON_VERSION%')" <nul >nul 2>&1
if errorlevel 1 goto ag_other
set "PYTHON=%~1"
exit /b 0
:ag_other
"%~1" -c "import sys; sys.exit(sys.version_info[:2] != (3, 14))" <nul >nul 2>&1
if not errorlevel 1 set "OTHER_PYTHON=%~1"
exit /b 0

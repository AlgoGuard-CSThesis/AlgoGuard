@echo off
REM ---------------------------------------------------------------------
REM AlgoGuard: double-click this file. It opens the AlgoGuard window,
REM where you set up, enter your settings, start and stop AlgoGuard.
REM
REM If Python 3.14.6 is missing, it offers to install it first: the
REM official installer from python.org, checked against the SHA-256 that
REM python.org publishes, installed for your Windows account only (no
REM change to PATH, no Python launcher; Windows may still ask to allow the
REM installer). A Python that is already installed is never changed.
REM SETUP_ALGOGUARD.cmd and START_ALGOGUARD.cmd do the rest by hand.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title AlgoGuard
set "PYTHON_URL=https://www.python.org/ftp/python/3.14.6/python-3.14.6-amd64.exe"
set "PYTHON_SHA256=14b3e9a710a3fcf0bd9b55ab6b60412bd91227563f813fc49040cabc0209e0bd"
set "PYTHON_PAGE=https://www.python.org/downloads/release/python-3146/"
set "WINDOW_OPTIONS="

call "%~dp0find_python.cmd"
if defined PYTHON goto open_window
if defined OTHER_PYTHON goto other_python

echo AlgoGuard needs Python %PYTHON_VERSION% (64-bit), which is not on this computer yet.
echo This window can download the official installer from python.org (about 30 MB),
echo check that it is the genuine file, and install Python for your Windows account
echo only. If Windows asks you to allow the installer, allow it.
echo.
choice /c YN /n /m "Install Python %PYTHON_VERSION% and set up AlgoGuard now? [Y/N] "
if errorlevel 2 goto manual_install

set "INSTALLER=%TEMP%\AlgoGuard-python-%PYTHON_VERSION%-amd64.exe"
echo.
echo Downloading Python %PYTHON_VERSION% from python.org...
curl.exe --fail --location --proto =https --progress-bar --output "%INSTALLER%" "%PYTHON_URL%"
if errorlevel 1 goto download_failed
set "ACTUAL_SHA256="
for /f "skip=1 delims=" %%h in ('certutil -hashfile "%INSTALLER%" SHA256') do if not defined ACTUAL_SHA256 set "ACTUAL_SHA256=%%h"
if not defined ACTUAL_SHA256 goto wrong_file
set "ACTUAL_SHA256=%ACTUAL_SHA256: =%"
if /i not "%ACTUAL_SHA256%"=="%PYTHON_SHA256%" goto wrong_file
echo Checked: this is the file python.org publishes for Python %PYTHON_VERSION%.
echo Installing Python %PYTHON_VERSION% for your Windows account (a minute or two)...
start "" /wait "%INSTALLER%" /passive InstallAllUsers=0 Include_launcher=0 PrependPath=0 Shortcuts=0 Include_test=0 Include_doc=0
set "INSTALL_RESULT=%ERRORLEVEL%"
del "%INSTALLER%" >nul 2>&1
if not "%INSTALL_RESULT%"=="0" if not "%INSTALL_RESULT%"=="3010" goto install_failed
call "%~dp0find_python.cmd"
if not defined PYTHON goto install_failed
echo Python %PYTHON_VERSION% is installed. The AlgoGuard window opens and sets up AlgoGuard.
set "WINDOW_OPTIONS=--set-up"

:open_window
start "" "%PYTHONW%" "%~dp0algoguard_launcher.pyw" %WINDOW_OPTIONS%
exit /b 0

:other_python
echo This computer has a Python 3.14 that AlgoGuard cannot use: AlgoGuard needs exactly
echo Python %PYTHON_VERSION% (64-bit, with Tcl/Tk), and it does not change a Python that is
echo already installed. Install Python %PYTHON_VERSION% from the page that opens now, then
echo double-click this file again.
start "" "%PYTHON_PAGE%"
pause
exit /b 1

:manual_install
echo The download page opens. Install Python %PYTHON_VERSION% (64-bit) with the "Windows
echo installer (64-bit)" link and the default options, then double-click this file again.
start "" "%PYTHON_PAGE%"
pause
exit /b 1

:download_failed
del "%INSTALLER%" >nul 2>&1
echo.
echo The download did not finish. Check the internet connection and double-click this
echo file again, or install Python %PYTHON_VERSION% from %PYTHON_PAGE%
pause
exit /b 1

:wrong_file
del "%INSTALLER%" >nul 2>&1
echo.
echo The downloaded file is not the one python.org publishes for Python %PYTHON_VERSION%,
echo so it was deleted and nothing was installed. Try again later, or install Python
echo from %PYTHON_PAGE%
pause
exit /b 1

:install_failed
echo.
echo Python %PYTHON_VERSION% was not installed (installer result %INSTALL_RESULT%). If you
echo pressed Cancel, double-click this file again. Otherwise install Python from the page
echo that opens now, then double-click this file again.
start "" "%PYTHON_PAGE%"
pause
exit /b 1

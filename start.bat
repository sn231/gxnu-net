@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"

REM Launcher for gxnu-net - starts the system tray app with NO console window.
REM
REM Notes:
REM   1. ASCII-only on purpose: cmd.exe reads .bat using the system ANSI
REM      codepage (GBK on Chinese Windows), so non-ASCII bytes here would be
REM      mangled into garbage commands.
REM   2. Uses "if not errorlevel 1" instead of "%errorlevel%" because plain
REM      %errorlevel% is expanded when the block is parsed, not when it runs.
REM   3. Skips the WindowsApps python.exe stub, which opens the Microsoft
REM      Store instead of running the script.
REM   4. Prefers pythonw.exe so no black console window ever appears.

set "PYCMD="
set "SKIPSTUB=%LOCALAPPDATA%\Microsoft\WindowsApps"

where python >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        echo %%P | findstr /i /c:"%SKIPSTUB%" >nul
        if errorlevel 1 (
            set "PYCMD=%%P"
            goto :havepy
        )
    )
)
:havepy

if not defined PYCMD (
    where py >nul 2>nul
    if not errorlevel 1 set "PYCMD=py -3"
)

if not defined PYCMD (
    echo Python not found. Please install Python 3.8 or newer:
    echo https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM Prefer pythonw.exe in the same folder -> no console window.
for %%A in ("%PYCMD%") do set "PYDIR=%%~dpA"
if exist "%PYDIR%pythonw.exe" set "PYCMD=%PYDIR%pythonw.exe"

if "%~1"=="" (
    start "" "%PYCMD%" "%~dp0tray.pyw"
    exit /b 0
)

REM Any explicit argument (--status / --once / ...) runs in the console.
"%PYCMD%" -m gxnu_net %*

if errorlevel 2 (
    echo.
    echo Config incomplete: edit config.ini with your student ID and password.
    pause
)

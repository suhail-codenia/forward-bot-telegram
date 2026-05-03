@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ------------------------------------------------------------
REM Double-click launcher for userbot.py on Windows
REM - Creates venv if missing
REM - Installs requirements if needed
REM - Starts bot in background and writes logs
REM ------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "VENV_DIR=%SCRIPT_DIR%venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PYTHONW_EXE=%VENV_DIR%\Scripts\pythonw.exe"
set "LOG_DIR=%SCRIPT_DIR%logs"
set "LOG_FILE=%LOG_DIR%\userbot.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if not exist "%PYTHON_EXE%" (
    echo [INFO] Creating virtual environment...
    py -3 -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        echo [ERROR] Make sure Python (py launcher) is installed on Windows.
        pause
        exit /b 1
    )

    echo [INFO] Installing dependencies...
    "%PYTHON_EXE%" -m pip install --upgrade pip >nul 2>&1
    "%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%requirements.txt"
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo [INFO] Starting userbot in background (detached console)...
REM pythonw.exe: no console window. Logs go to logs\userbot.log when USERBOT_BACKGROUND=1
REM (configured in userbot.py).
set "USERBOT_BACKGROUND=1"
start "" "%PYTHONW_EXE%" "%SCRIPT_DIR%userbot.py"

REM start does not reliably propagate errorlevels for child processes here
exit /b 0

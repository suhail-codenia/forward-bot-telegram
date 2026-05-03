@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Stop background userbot (pythonw.exe) for this folder's userbot.py.
REM See scripts\stop_userbot.ps1 for the actual logic.

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if not exist "%SCRIPT_DIR%scripts\stop_userbot.ps1" (
    echo [ERROR] Missing scripts\stop_userbot.ps1
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\stop_userbot.ps1"

if errorlevel 1 (
    echo [ERROR] stop script exited with error code !ERRORLEVEL!
    pause
    exit /b !ERRORLEVEL!
)

exit /b 0

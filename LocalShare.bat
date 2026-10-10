@echo off
title LocalShare - Offline Wi-Fi File Sharing
cd /d "%~dp0"

:: Prefer standalone LocalShare.exe if present (runs on any Windows PC without Python)
if exist "LocalShare.exe" (
    start "" "LocalShare.exe"
    exit /b 0
)

:: Fallback to Python script
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Neither LocalShare.exe nor Python was found.
    echo Please keep LocalShare.exe in this folder or install Python 3.
    pause
    exit /b 1
)

python localshare.py

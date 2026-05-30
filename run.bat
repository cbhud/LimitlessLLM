@echo off
setlocal enabledelayedexpansion

echo ==================================================
echo   LimitlessLLM - Python Proxy Startup Script
echo ==================================================

:: 1. Check if Python is installed
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python 3.9 or higher and try again.
    pause
    exit /b 1
)

:: 2. Check and copy .env if it does not exist
if not exist .env (
    if exist .env.example (
        echo [INFO] .env file not found. Copying .env.example to .env...
        copy .env.example .env
        echo [WARNING] Created .env file.
        echo Please edit .env and fill in your API keys before running,
        echo otherwise providers without keys will be skipped.
        echo.
    ) else (
        echo [WARNING] Neither .env nor .env.example was found.
        echo Make sure you configure your environment variables.
    )
)

:: 3. Setup Virtual Environment
if not exist .venv (
    echo [INFO] Creating virtual environment in .venv...
    python -m venv .venv
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: 4. Activate Virtual Environment
echo [INFO] Activating virtual environment...
call .venv\Scripts\activate
if !errorlevel! neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

:: 5. Install Dependencies
echo [INFO] Checking and installing dependencies...
pip install -r requirements.txt
if !errorlevel! neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

:: 6. Launch FastAPI server via Uvicorn
echo [INFO] Starting LimitlessLLM Proxy Server on http://localhost:3001
echo Press Ctrl+C to stop the server.
echo ==================================================
uvicorn app.main:app --host 0.0.0.0 --port 3001 --reload

pause

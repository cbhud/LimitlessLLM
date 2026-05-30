#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

echo "=================================================="
echo "  LimitlessLLM - Python Proxy Startup Script"
echo "=================================================="

# 1. Check if python3 is installed
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is not installed or not in your PATH."
    echo "Please install Python 3.9 or higher and try again."
    exit 1
fi

# 2. Check and copy .env if it does not exist
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "[INFO] .env file not found. Copying .env.example to .env..."
        cp .env.example .env
        echo "[WARNING] Created .env file."
        echo "Please edit .env and fill in your API keys before running,"
        echo "otherwise providers without keys will be skipped."
        echo ""
    else
        echo "[WARNING] Neither .env nor .env.example was found."
        echo "Make sure you configure your environment variables."
    fi
fi

# 3. Setup Virtual Environment
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating virtual environment in .venv..."
    python3 -m venv .venv
fi

# 4. Activate Virtual Environment
echo "[INFO] Activating virtual environment..."
source .venv/bin/activate

# 5. Install Dependencies
echo "[INFO] Checking and installing dependencies..."
pip install -r requirements.txt

# 6. Launch FastAPI server via Uvicorn
echo "[INFO] Starting LimitlessLLM Proxy Server on http://localhost:3001"
echo "Press Ctrl+C to stop the server."
echo "=================================================="
uvicorn app.main:app --host 0.0.0.0 --port 3001 --reload

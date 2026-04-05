#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "===================================================="
echo "🚀 Initializing Cargo Monitoring Agentic System 🚀"
echo "===================================================="

# 1. Create simulated infrastructure directories
echo "[1/3] Scaffolding local storage infrastructure..."
mkdir -p data logs/compliance_audit src/agents src/tools

# 2. Setup Python Virtual Environment
echo "[2/3] Setting up Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "Virtual environment 'venv' created."
else
    echo "Virtual environment already exists. Skipping."
fi

# Activate the virtual environment
source venv/bin/activate

# 3. Install Dependencies
echo "[3/3] Installing required packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "===================================================="
echo "✅ Setup Complete!"
echo ""
echo "NOTE: Please ensure you create a '.env' file in the root"
echo "directory with your API keys (OPENAI_API_KEY, etc.)"
echo "before running the application."
echo ""
echo "To start the Human-in-the-Loop Dashboard:"
echo "  source venv/bin/activate"
echo "  streamlit run src/main_dashboard.py"
echo "===================================================="
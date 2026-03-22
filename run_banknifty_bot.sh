#!/bin/bash

# Navigate to the script's directory to resolve relative paths for cron
cd "$(dirname "$0")"

# Automatically use the virtual environment to run the bot
source venv/bin/activate

# Run the dedicated Bank Nifty agent
python3 src/main.py --banknifty

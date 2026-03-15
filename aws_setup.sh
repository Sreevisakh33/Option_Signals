#!/bin/bash
# AWS EC2 Setup Script for Option_Signals (Ubuntu 22.04)

echo "🚀 Starting AWS EC2 Setup for Option_Signals..."

# 1. Update system packages
sudo apt-get update && sudo apt-get upgrade -y

# 2. Install Python and Pip if not present
sudo apt-get install -y python3-pip python3-venv git

# 3. Install Playwright system dependencies (CRITICAL for Headless Chrome)
# This installs the libraries needed by Chromium to run on a bare-bones Linux server
sudo npx playwright install-deps

# 4. Create Virtual Environment
python3 -m venv venv
source venv/bin/activate

# 5. Install Python Project Dependencies
pip install -r requirements.txt
pip install fyers-apiv3 python-dotenv certifi

# 6. Install Playwright Browsers
playwright install chromium

# 7. Set Timezone to IST (Important for Crontab scheduling)
sudo timedatectl set-timezone Asia/Kolkata

echo "✅ Environment configured! Next steps:"
echo "1. Add your .env file to the root directory."
echo "2. Run 'python src/evaluator/fyers_auth.py' to generate your initial token."
echo "3. Load the crontab using: crontab crontab.txt"

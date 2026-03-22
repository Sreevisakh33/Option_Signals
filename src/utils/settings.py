import os
import yaml
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
load_dotenv()

# Base paths
# __file__ is src/utils/settings.py -> parent is src/utils -> parent is src -> parent is root
BASE_DIR = Path(__file__).parent.parent.parent
CONFIG_DIR = BASE_DIR / "prompts.yaml" # Wait, prompts.yaml is in config/ but settings says it's in root? Let me check BASE_DIR logic.
# Re-checking BASE_DIR: Path(__file__) is src/utils/settings.py
# parent 1: src/utils
# parent 2: src
# parent 3: root
# So BASE_DIR is root.
DOWNLOAD_DIR = BASE_DIR / "market_snapshots"
ARCHIVE_DIR = BASE_DIR / "snapshots_archive"
CONFIG_DIR = BASE_DIR / "config"

# API Keys
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")

# TradingView credentials (for loading saved chart with custom indicators)
TV_USERNAME = os.getenv("TV_USERNAME", "")
TV_PASSWORD = os.getenv("TV_PASSWORD", "")

# TradingView chart layout IDs
NIFTY_CHART_ID = os.getenv("NIFTY_CHART_ID", "Nchart")
BANKNIFTY_CHART_ID = os.getenv("BANKNIFTY_CHART_ID", "BNChart")

# Base URL for any chart - identifiers will be injected at runtime
TRADINGVIEW_CHART_BASE_URL = "https://in.tradingview.com/chart"

# Kept for backward compatibility (defaults to Nifty)
TRADINGVIEW_CHART_URL = f"{TRADINGVIEW_CHART_BASE_URL}/{NIFTY_CHART_ID}/?symbol=NSE%3ANIFTY1%21"

NSE_OC_URL = "https://www.nseindia.com/option-chain"
FORCE_FETCH = os.getenv("FORCE_FETCH", "false").lower() == "true"

def load_prompt(prompt_name: str = "system_prompt") -> str:
    """Loads a prompt from the config/prompts.yaml file."""
    yaml_path = CONFIG_DIR / "prompts.yaml"
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            prompts = yaml.safe_load(f) or {}
            return prompts.get(prompt_name, "")
    except Exception as e:
        print(f"Error loading prompt: {e}")
        return ""

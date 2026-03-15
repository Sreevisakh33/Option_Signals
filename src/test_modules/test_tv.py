import sys
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.tools.tv_fetcher import TradingViewFetcher
from src.utils.logger_config import get_logger

logger = get_logger("TestTV")

def test_tv_screenshot():
    """Test basic TradingView screenshot using TradingViewFetcher logic (simplified)."""
    logger.info("Starting TradingView capture test...")
    # capture_charts handles login and stitch
    combined_paths = TradingViewFetcher.capture_charts(intervals=[5])
    
    if combined_paths:
        logger.info("Successfully captured TradingView charts: %s", combined_paths)
    else:
        logger.error("Failed to capture TradingView charts.")

if __name__ == "__main__":
    test_tv_screenshot()

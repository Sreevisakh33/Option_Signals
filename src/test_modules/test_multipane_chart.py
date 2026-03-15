import sys
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.tools.tv_fetcher import TradingViewFetcher
from src.utils.logger_config import get_logger

logger = get_logger("TestMultipane")

def test_multipane_chart():
    """Test multi-pane chart creation using TradingViewFetcher."""
    logger.info("Starting Multi-pane Chart Test...")
    
    # This uses the full production logic: login -> capture 3 TFs -> stitch
    chart_paths = TradingViewFetcher.capture_charts(intervals=[3, 5, 15])
    
    if chart_paths:
        logger.info("Multi-pane chart(s) created: %s", chart_paths)
    else:
        logger.error("Failed to create multi-pane chart.")

if __name__ == "__main__":
    test_multipane_chart()

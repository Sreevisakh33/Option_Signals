import sys
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.tools.nse_fetcher import NSEFetcher
from src.utils.logger_config import get_logger
from src.utils.settings import DOWNLOAD_DIR
import json

logger = get_logger("TestNSEDownload")

def test_download_option_chain():
    """Test using the refactored NSEFetcher."""
    logger.info("Starting test: Using refactored NSEFetcher")
    
    json_data, spot_price = NSEFetcher.fetch_json()
    
    if json_data:
        logger.info("Successfully captured JSON data!")
        logger.info("Spot Price: %s", spot_price)
        logger.info("Data Timestamp: %s", json_data.get("records", {}).get("timestamp"))
        logger.info("Records count: %s", len(json_data.get("records", {}).get("data", [])))
        
        # Save it to a file for manual inspection
        output_file = DOWNLOAD_DIR / "option_chain_test_output.json"
        with open(output_file, "w") as f:
            json.dump(json_data, f, indent=2)
        logger.info("Data saved locally to: %s", output_file)
    else:
        logger.error("Failed: Could not fetch NSE data.")

if __name__ == "__main__":
    test_download_option_chain()

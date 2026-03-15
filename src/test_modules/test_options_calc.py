import sys
import json
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.options_calculator import OptionsCalculator
from src.utils.logger_config import get_logger

logger = get_logger("TestOptionsCalc")

def test_options_calculation():
    """Test the OptionsCalculator logic with archived JSON data."""
    logger.info("Starting OptionsCalculator Test...")
    
    # Path to sample JSON
    json_path = Path(project_root) / "snapshots_archive" / "nse_option_chain.json"
    
    if not json_path.exists():
        logger.error("Sample JSON not found at %s. Run the bot once first.", json_path)
        return

    with open(json_path, "r") as f:
        json_data = json.load(f)

    # Spot price is usually in json_data['records']['underlyingValue']
    spot_price = json_data.get("records", {}).get("underlyingValue", 23150.0)
    
    calc = OptionsCalculator()
    output_text = calc.process_chain_data(json_data, spot_price)
    
    logger.info("--- CALCULATION OUTPUT ---\n%s\n--------------------------", output_text)
    
    # Verify presence of COI data in output (Checking for "(+" or "(-")
    if "CE_OI:" in output_text and "(" in output_text:
        logger.info("Success: COI data detected in output string.")
    else:
        logger.warning("COI data potentially missing from output string.")

if __name__ == "__main__":
    test_options_calculation()

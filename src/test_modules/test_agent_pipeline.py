import sys
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.agents.nifty_options_agent import NiftyOptionsAgent
from src.utils.logger_config import get_logger

logger = get_logger("TestAgentPipeline")

def test_agent_pipeline_dry_run():
    """Test the NiftyOptionsAgent pipeline (Dry Run - verify acquisition and data flow)."""
    logger.info("Starting Agent Pipeline Dry Run...")
    
    agent = NiftyOptionsAgent()
    
    # 1. Test acquisition (Parallel check)
    logger.info("Testing parallel data acquisition...")
    json_data, chart_paths, spot_price = agent.acquire_data()
    
    if not json_data:
        logger.error("Data acquisition failed.")
        return

    logger.info("Acquisition successful. Spot: %s, Charts: %s", spot_price, chart_paths)
    
    # 2. Test data processing block
    logger.info("Testing data processing block...")
    chain_text = agent.process_data(json_data, spot_price)
    logger.info("Processed data block length: %s", len(chain_text))
    
    # 3. Decision making (Optional: only if API key is set)
    if agent.llm_client:
        logger.info("Testing combined AI strategy call...")
        # We don't want to actually send alerts in a test unless explicitly asked, 
        # so we just capture the response
        combined_response = agent.run_strategy(chain_text, chart_paths, prompt_name="master_combined_prompt")
        logger.info("AI Response received (first 100 chars): %s...", combined_response[:100])
        
        if "---END_OF_SIGNAL_1---" in combined_response:
            logger.info("Success: Analysis splitting delimiter found.")
        else:
            logger.warning("Warning: Analysis splitting delimiter NOT found.")
    else:
        logger.info("Skipping AI call (API key not configured).")

if __name__ == "__main__":
    test_agent_pipeline_dry_run()

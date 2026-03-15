import sys
from pathlib import Path

# Add project root to sys.path so 'src' can be imported anywhere
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.agents.nifty_options_agent import NiftyOptionsAgent
from src.utils.logger_config import get_logger

logger = get_logger("Main")

def main():
    """
    Entry point for the Trading Bot Framework.
    """
    # Instantiate the specialized agent
    agent = NiftyOptionsAgent()
    
    # Run the end-to-end multi-step reasoning pipeline
    logger.info("Starting Trading Bot Pipeline...")
    agent.run_pipeline()
    logger.info("Pipeline execution finished.")

if __name__ == "__main__":
    main()

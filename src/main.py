import sys
import argparse
from pathlib import Path

# Add project root to sys.path so 'src' can be imported anywhere
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.agents.nifty_options_agent import NiftyOptionsAgent
from src.agents.bank_nifty_options_agent import BankNiftyOptionsAgent
from src.utils.logger_config import get_logger

logger = get_logger("Main")

def main():
    """
    Entry point for the Trading Bot Framework.
    """
    parser = argparse.ArgumentParser(description="Multi-Index Options Trading Bot")
    parser.add_argument("--banknifty", action="store_true", help="Run the Bank Nifty Agent")
    parser.add_argument("--nifty", action="store_true", default=True, help="Run the Nifty 50 Agent (default)")
    
    # If both are provided, or if --banknifty is provided, prioritise based on args
    args = parser.parse_args()

    if args.banknifty:
        logger.info("Initializing Bank Nifty Agent...")
        agent = BankNiftyOptionsAgent()
    else:
        logger.info("Initializing Nifty 50 Agent...")
        agent = NiftyOptionsAgent()
    
    # Run the end-to-end multi-step reasoning pipeline
    logger.info("Starting Trading Bot Pipeline...")
    agent.run_pipeline()
    logger.info("Pipeline execution finished.")

if __name__ == "__main__":
    main()

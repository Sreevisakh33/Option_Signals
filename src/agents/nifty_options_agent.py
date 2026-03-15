import time
import base64
import shutil
import json
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from src.core.agent import BaseAgent
from src.utils.settings import OPENAI_API_KEY, load_prompt, DOWNLOAD_DIR, ARCHIVE_DIR
from src.utils.options_calculator import OptionsCalculator
from src.tools.nse_fetcher import NSEFetcher
from src.tools.tv_fetcher import TradingViewFetcher
from src.tools.telegram_notifier import TelegramNotifier
from src.utils.logger_config import get_logger

logger = get_logger("NiftyOptionsAgent")

class NiftyOptionsAgent(BaseAgent):
    """
    Orchestrates the entire Nifty options trading pipeline:
    Data Scrape -> Pandas Analysis -> GPT-4o MultiModal Inference -> Telegram Broadcast.
    """
    
    def __init__(self):
        if OPENAI_API_KEY and OPENAI_API_KEY != "YOUR_OPENAI_API_KEY":
            self.llm_client = OpenAI(api_key=OPENAI_API_KEY)
        else:
            self.llm_client = None
            logger.warning("OPENAI_API_KEY is not set.")
            
        self.system_prompt = load_prompt("system_prompt")
        self.options_calc = OptionsCalculator()
        
    def acquire_data(self) -> tuple[dict, list[str], float]:
        """Fetch the JSON Option Chain and the TradingView Images in parallel."""
        logger.info("Starting parallel data acquisition...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            tv_future = executor.submit(TradingViewFetcher.capture_charts, intervals=[3, 5, 15])
            nse_future = executor.submit(NSEFetcher.fetch_json)
            
            chart_paths = tv_future.result()
            json_data, spot_price = nse_future.result()
            
        if json_data:
            # Save a copy of the JSON data to downloads for archiving
            json_path = DOWNLOAD_DIR / "nse_option_chain.json"
            with open(json_path, "w") as f:
                json.dump(json_data, f, indent=2)
            logger.info("JSON data saved to downloads.")

        logger.info("Data acquisition complete.")
        return json_data, chart_paths, spot_price

    def process_data(self, json_data: dict, spot_price: float) -> str:
        """Format the Option Chain into a neat text block for LLM inference."""
        return self.options_calc.process_chain_data(json_data, spot_price)

    def run_strategy(self, chain_text: str, chart_paths: list[str], prompt_name: str = "system_prompt") -> str:
        """Runs the processed data and images through GPT-4o using a specified prompt."""
        prompt_text = load_prompt(prompt_name)
        if not self.llm_client:
            return "Failed to generate plan. OpenAI API key is missing."
        if not prompt_text:
            return f"Failed to generate plan. Prompt '{prompt_name}' not found in yaml config."
            
        logger.info("Initializing OpenAI API...")
        try:
            prompt = f"{prompt_text}\n\n{chain_text}"
            content_list = [{"type": "text", "text": prompt}]
            
            # Append each screenshot dynamically to the multimodel context
            for path in chart_paths:
                with open(path, "rb") as image_file:
                    base64_image = base64.b64encode(image_file.read()).decode('utf-8')
                    content_list.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}"
                        }
                    })

            logger.info("Sending query to GPT-4o for '%s'... Please wait.", prompt_name)
            
            response = self.llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a professional quantitative analyst specializing in Nifty 50 derivatives on the NSE India. "
                            "Your role is to analyze provided chart images and option chain data to produce objective, research-based trade plans for institutional reporting. "
                            "You strictly follow the provided structured output format and guidelines to ensure data-driven consistency."
                        )
                    },
                    {
                        "role": "user",
                        "content": content_list
                    }
                ],
                max_tokens=1500,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("Error querying OpenAI: %s", e)
            return "Failed to generate plan."

    def run_pipeline(self):
        """Main execution sequence."""
        try:
            logger.info("--- Nifty Options Agent Started ---")
            
            # Step 1: Collect Data
            json_data, chart_paths, spot_price = self.acquire_data()
            if not json_data:
                logger.error("Failed to acquire json data. Exiting pipeline.")
                return

            # Step 2: Calculate Max Pain, PCR, and Format Data
            chain_text = self.process_data(json_data, spot_price)
            
            # Step 3: Run Combined Strategy (Efficient Single Call)
            logger.info("Executing master combined strategy query...")
            combined_response = self.run_strategy(chain_text, chart_paths, prompt_name="master_combined_prompt")
            
            # Step 4: Split and Broadcast
            if "---END_OF_SIGNAL_1---" in combined_response:
                main_signal, breakout_signal = combined_response.split("---END_OF_SIGNAL_1---", 1)
                
                # Clean up whitespace and broadcast
                TelegramNotifier.send_alert(f"📊 NIFTY TRADING SIGNAL\n\n{main_signal.strip()}")
                TelegramNotifier.send_alert(breakout_signal.strip())
            else:
                # Fallback if something went wrong with the split logic
                logger.warning("Combined response did not contain expected delimiter. Sending as raw.")
                TelegramNotifier.send_alert(combined_response)
            
            logger.info("--- Nifty Options Agent Completed ---")
            
        except Exception as e:
            logger.error("Fatal error in agent execution: %s", e)
        finally:
            self.archive_downloads()

    def archive_downloads(self):
        """
        Moves all files from DOWNLOAD_DIR to ARCHIVE_DIR.
        Clears ARCHIVE_DIR first so it only has one 'copy' of the latest run.
        """
        try:
            logger.info("Archiving market snapshots and cleaning up...")
            
            # 1. Clear Archive
            if ARCHIVE_DIR.exists():
                shutil.rmtree(ARCHIVE_DIR)
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            
            # 2. Move files from Snapshots to Archive
            if DOWNLOAD_DIR.exists():
                for item in DOWNLOAD_DIR.iterdir():
                    if item.is_file():
                        shutil.move(str(item), str(ARCHIVE_DIR / item.name))
                    elif item.is_dir():
                        shutil.move(str(item), str(ARCHIVE_DIR / item.name))
            
            logger.info("Archive complete. Snapshot folder is now empty.")
        except Exception as e:
            logger.error("Error during archiving: %s", e)

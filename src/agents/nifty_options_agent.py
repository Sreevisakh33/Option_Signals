import time
import base64
import shutil
import json
import re
import csv
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from src.core.agent import BaseAgent
from src.utils.settings import OPENAI_API_KEY, load_prompt, DOWNLOAD_DIR, ARCHIVE_DIR, BASE_DIR
from src.utils.options_calculator import OptionsCalculator
from src.tools.nse_fetcher import NSEFetcher
from src.tools.tv_fetcher import TradingViewFetcher
from src.tools.telegram_notifier import TelegramNotifier
from src.tools.vix_fetcher import get_india_vix
from src.tools.atr_calculator import get_nifty_atr_15m
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

    def get_oi_momentum(self, live_data: dict, spot_price: float) -> str:
        """Calculates 15-minute OI momentum by comparing live data with the archived snapshot."""
        archive_file = ARCHIVE_DIR / "last_chain_snapshot.json"
        download_file = DOWNLOAD_DIR / "last_chain_snapshot.json"
        
        # 1. State Management (Check Archive)
        if not archive_file.exists():
            try:
                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                with open(download_file, "w") as f:
                    json.dump(live_data, f, indent=2)
            except Exception as e:
                logger.error("Failed to save initial momentum snapshot: %s", e)
                
            # Check for Opening Drive (09:15 - 09:20 AM)
            current_time = datetime.now().time()
            start_time = datetime.strptime("09:15", "%H:%M").time()
            end_time = datetime.strptime("09:20", "%H:%M").time()
            
            if start_time <= current_time <= end_time:
                try:
                    live_records = live_data.get("filtered", {}).get("data", [])
                    valid_records = [r for r in live_records if "strikePrice" in r]
                    valid_records.sort(key=lambda x: x["strikePrice"])
                    
                    if valid_records:
                        atm_idx = min(range(len(valid_records)), key=lambda i: abs(valid_records[i]["strikePrice"] - spot_price))
                        start_idx = max(0, atm_idx - 1)
                        end_idx = min(len(valid_records), atm_idx + 2)
                        target_records = valid_records[start_idx:end_idx]
                        
                        total_ce_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in target_records)
                        total_pe_oi = sum(r.get("PE", {}).get("openInterest", 0) for r in target_records)
                        atm_strike = valid_records[atm_idx]["strikePrice"]
                        
                        sentiment = "Balanced / Mixed"
                        imbalance_type = "balanced"
                        if total_pe_oi >= 2 * total_ce_oi and total_ce_oi > 0:
                            sentiment = "Bullish sentiment"
                            imbalance_type = "massive absolute"
                        elif total_ce_oi >= 2 * total_pe_oi and total_pe_oi > 0:
                            sentiment = "Bearish sentiment"
                            imbalance_type = "massive absolute"
                            
                        time_str = current_time.strftime("%I:%M %p").lstrip("0")
                        
                        if imbalance_type == "massive absolute":
                            return f"OPENING DRIVE DETECTED ({time_str}). ATM Strike {atm_strike} shows {imbalance_type} imbalance. Live Put OI: {total_pe_oi} | Live Call OI: {total_ce_oi}. Immediate {sentiment} dominating the open. No previous 15-min data available."
                        else:
                            return f"OPENING DRIVE DETECTED ({time_str}). ATM Strike {atm_strike} shows {imbalance_type} open. Live Put OI: {total_pe_oi} | Live Call OI: {total_ce_oi}. {sentiment} at the open. No previous 15-min data available."
                except Exception as e:
                    logger.error("Error during Opening Drive calculation: %s", e)
                    
            return "No previous 15-minute data available. First snapshot saved. HOLD until momentum is established."
        
        # 2. Calculate the Delta
        try:
            with open(archive_file, "r") as f:
                archived_data = json.load(f)
                
            archived_records = archived_data.get("filtered", {}).get("data", [])
            archived_map = {r.get("strikePrice"): r for r in archived_records if r.get("strikePrice")}
            
            live_records = live_data.get("filtered", {}).get("data", [])
            momentum_strings = []
            
            for live_row in live_records:
                strike = live_row.get("strikePrice")
                if not strike:
                    continue
                    
                # 3. Filter for Noise: Near ATM (within +/- 500)
                if abs(strike - spot_price) > 500:
                    continue
                    
                archived_row = archived_map.get(strike)
                if not archived_row:
                    continue
                    
                live_ce_oi = live_row.get("CE", {}).get("openInterest", 0)
                live_pe_oi = live_row.get("PE", {}).get("openInterest", 0)
                
                archived_ce_oi = archived_row.get("CE", {}).get("openInterest", 0)
                archived_pe_oi = archived_row.get("PE", {}).get("openInterest", 0)
                
                ce_diff = live_ce_oi - archived_ce_oi
                pe_diff = live_pe_oi - archived_pe_oi
                
                # Filter for significance (> 10000)
                if abs(ce_diff) > 10000 or abs(pe_diff) > 10000:
                    parts = [f"Strike {strike}:"]
                    if abs(ce_diff) > 10000:
                        action = "Short covering" if ce_diff < 0 else "Resistance building"
                        direction = "decreased" if ce_diff < 0 else "increased"
                        parts.append(f"Call OI {direction} by {abs(ce_diff)} ({action}).")
                    
                    if abs(pe_diff) > 10000:
                        action = "Long unwinding/Weakness" if pe_diff < 0 else "Support building"
                        direction = "decreased" if pe_diff < 0 else "increased"
                        parts.append(f"Put OI {direction} by {abs(pe_diff)} ({action}).")
                        
                    # 4. Format for the LLM
                    momentum_strings.append(" ".join(parts))
            
            # 5. Overwrite the Archive (crucial) -> saving to DOWNLOAD_DIR so finally block moves it to archive
            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            with open(download_file, "w") as f:
                json.dump(live_data, f, indent=2)
                
            if not momentum_strings:
                return "15-Minute OI Momentum: No significant OI changes (>10000 contracts) near ATM."
                
            return "15-Minute OI Momentum:\n" + "\n".join(momentum_strings)
            
        except Exception as e:
            logger.error("Error calculating OI momentum: %s", e)
            return "15-Minute OI Momentum: Calculation failed due to data error."

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
                logger.error("Failed to acquire live market data. Market may not be open yet (starts at 09:15 AM IST). Aborting for safety.")
                return
            
            # Log Data Freshness
            nse_ts = json_data.get("records", {}).get("timestamp", "Unknown")
            logger.info(f">>> DATA SNAPSHOT: NSE Timestamp: {nse_ts} | Spot: {spot_price}")

            # Step 2: Calculate Max Pain, PCR, and Format Data
            chain_text = self.process_data(json_data, spot_price)
            
            # Add India VIX (Batch 35)
            vix_value = get_india_vix()
            logger.info(f"India VIX: {vix_value}")
            
            # Add Nifty 15m ATR (Batch 36)
            atr_value = get_nifty_atr_15m()
            logger.info(f"Nifty 15m ATR: {atr_value}")
            
            chain_text = f"{chain_text}\n\nMARKET VOLATILITY DATA:\n{{\"india_vix\": {vix_value}, \"atr_15m\": {atr_value}}}"

            # Add OI Momentum Component
            oi_momentum_text = self.get_oi_momentum(json_data, spot_price)
            chain_text = f"{chain_text}\n\n{oi_momentum_text}"
            logger.info("OI Momentum evaluated and appended.")
            
            # Step 3: Run Strategy (Single JSON Call)
            logger.info("Executing system strategy query (JSON Mode)...")
            raw_response = self.run_strategy(chain_text, chart_paths, prompt_name="system_prompt")
            
            # Step 4: Parse JSON & Broadcast
            # The AI returns a JSON object as per system_prompt
            try:
                # Cleaner JSON extraction in case of markdown blocks
                json_str = raw_response.strip()
                if "```json" in json_str:
                    json_str = json_str.split("```json")[-1].split("```")[0].strip()
                elif "```" in json_str:
                    json_str = json_str.split("```")[1].strip()
                
                signal_data = json.loads(json_str)
                decision = signal_data.get("decision", "HOLD").upper()
                
                if decision in ["BUY_CALL", "BUY_PUT"]:
                    # Log for paper trading evaluator
                    nearest_expiry = json_data.get("records", {}).get("expiryDates", [""])[0]
                    self.log_json_signal(signal_data, nearest_expiry)
                    
                    # Format Telegram Message
                    emoji = "🚀" if decision == "BUY_CALL" else "🔻"
                    trade_type = "CALL" if decision == "BUY_CALL" else "PUT"
                    msg = (
                        f"{emoji} *SYSTEM SIGNAL: BUY {trade_type}*\n\n"
                        f"🎯 *Strike:* {signal_data.get('strike')}\n"
                        f"💰 *Entry:* {signal_data.get('entry_price')}\n"
                        f"🛑 *SL:* {signal_data.get('stop_loss')}\n"
                        f"🏁 *Target:* {signal_data.get('target')}\n"
                        f"🔥 *Confidence:* {signal_data.get('confidence_score')}%\n\n"
                        f"📝 *Reasoning:* {signal_data.get('reasoning')}"
                    )
                    TelegramNotifier.send_alert(msg)
                else:
                    logger.info("Decision: HOLD. Reasoning: %s", signal_data.get("reasoning", "No trade setup detected."))
                    # Optionally send a 'HOLD' notification if confidence is low but relevant
                    if signal_data.get("confidence_score", 0) > 30:
                         TelegramNotifier.send_alert(f"⚖️ *SYSTEM STATUS: HOLD*\n\nReasoning: {signal_data.get('reasoning')}")

            except json.JSONDecodeError as je:
                logger.error("Failed to parse AI JSON response: %s", raw_response)
                TelegramNotifier.send_alert(f"⚠️ *ERROR:* AI returned malformed JSON.\n\nRaw: {raw_response[:200]}...")
            except Exception as e:
                logger.error("Error processing AI response: %s", e)
            
            logger.info("--- Nifty Options Agent Completed ---")
            
        except Exception as e:
            logger.error("Fatal error in agent execution: %s", e)
        finally:
            self.archive_downloads()

    def log_json_signal(self, signal_data: dict, nearest_expiry: str = ""):
        """Logs the structured JSON signal to paper_trades.csv."""
        try:
            decision = signal_data.get("decision", "").upper()
            strike = signal_data.get("strike")
            option_type = "CE" if decision == "BUY_CALL" else "PE"
            
            if not strike:
                return

            # Format expiry: 24-Mar-2026 -> 24MAR
            expiry_prefix = ""
            if nearest_expiry:
                try:
                    expiry_dt = datetime.strptime(nearest_expiry, "%d-%b-%Y")
                    expiry_prefix = expiry_dt.strftime("%d%b").upper()
                except:
                    expiry_prefix = nearest_expiry.split("-")[0] + nearest_expiry.split("-")[1].upper() if "-" in nearest_expiry else ""

            instrument = f"NIFTY {expiry_prefix}{strike}{option_type}"
            entry_price = float(signal_data.get("entry_price", 0))
            target_price = float(signal_data.get("target", 0))
            sl_price = float(signal_data.get("stop_loss", 0))
            
            # Append to CSV
            log_dir = BASE_DIR / "logs"
            log_dir.mkdir(exist_ok=True)
            csv_path = log_dir / "paper_trades.csv"
            
            file_exists = csv_path.exists()
            with open(csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Persona", "Instrument", "Entry_Price", "Target", "Stop_Loss", "Status"])
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Using 'System AI' as the persona for Batch 39
                writer.writerow([timestamp, "System AI", instrument, entry_price, target_price, sl_price, "PENDING"])
                
            logger.info("Logged PENDING paper trade: [System AI] %s | Entry: %s | Tgt: %s | SL: %s", 
                        instrument, entry_price, target_price, sl_price)
        except Exception as e:
            logger.error("Error logging JSON signal for paper trading: %s", e)

    def parse_and_log_signal(self, block_text: str, persona: str, nearest_expiry: str = ""):
        """Deprecated: Replaced by log_json_signal. Kept for backward compatibility during transition."""
        pass

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

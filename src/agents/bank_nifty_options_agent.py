import time
import base64
import shutil
import json
import re
import csv
import yfinance as yf
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
from src.tools.atr_calculator import get_atr_15m
from src.utils.logger_config import get_logger

logger = get_logger("BankNiftyOptionsAgent")

class BankNiftyOptionsAgent(BaseAgent):
    """
    Orchestrates the Bank Nifty options trading pipeline:
    Data Scrape (BANKNIFTY) -> Analysis -> GPT-4o Inference (with Nifty 50 Context) -> Telegram.
    """
    
    def __init__(self):
        if OPENAI_API_KEY and OPENAI_API_KEY != "YOUR_OPENAI_API_KEY":
            self.llm_client = OpenAI(api_key=OPENAI_API_KEY)
        else:
            self.llm_client = None
            logger.warning("OPENAI_API_KEY is not set.")
            
        self.system_prompt = load_prompt("bank_nifty_system_prompt")
        self.options_calc = OptionsCalculator()
        
    def acquire_data(self) -> tuple[dict, list[str], float]:
        """Fetch the BANKNIFTY JSON Option Chain and specifically 5m/15m charts."""
        logger.info("Starting parallel Bank Nifty data acquisition...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Bank Nifty uses 5m and 15m only
            # 2. Capture TradingView Charts (5m, 15m)
            try:
                tv_future = executor.submit(TradingViewFetcher.capture_charts, intervals=[5, 15], symbol="BANKNIFTY", prefix="BN_")
            except Exception as e:
                logger.error(f"Error submitting TradingViewFetcher task: {e}")
                tv_future = None # Ensure tv_future is defined even if submission fails
            nse_future = executor.submit(NSEFetcher.fetch_json, symbol="BANKNIFTY")
            
            chart_paths = tv_future.result() if tv_future else []
            json_data, spot_price = nse_future.result()
            
        if json_data:
            json_path = DOWNLOAD_DIR / "bank_nifty_option_chain.json"
            with open(json_path, "w") as f:
                json.dump(json_data, f, indent=2)
            logger.info("Bank Nifty JSON data saved.")

        logger.info("Data acquisition complete.")
        return json_data, chart_paths, spot_price

    def process_data(self, json_data: dict, spot_price: float) -> str:
        """Format the Bank Nifty Option Chain into text."""
        # OptionsCalculator is already symbol-agnostic in its processing
        return self.options_calc.process_chain_data(json_data, spot_price)

    def get_oi_momentum(self, live_data: dict, spot_price: float) -> str:
        """Calculates Bank Nifty 15-minute OI momentum with dedicated state file."""
        # Separate state file for Bank Nifty
        archive_file = ARCHIVE_DIR / "bank_nifty_last_chain.json"
        download_file = DOWNLOAD_DIR / "bank_nifty_last_chain.json"
        
        if not archive_file.exists():
            try:
                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                with open(download_file, "w") as f:
                    json.dump(live_data, f, indent=2)
            except Exception as e:
                logger.error("Failed to save Bank Nifty momentum snapshot: %s", e)
                
            # Opening Drive logic (shared with Nifty but using Bank Nifty spot)
            current_time = datetime.now().time()
            start_time = datetime.strptime("09:15", "%H:%M").time()
            end_time = datetime.strptime("09:20", "%H:%M").time()
            
            if start_time <= current_time <= end_time:
                try:
                    live_records = live_data.get("filtered", {}).get("data", [])
                    valid_records = [r for r in live_records if "strikePrice" in r]
                    valid_records.sort(key=lambda x: x["strikePrice"])
                    
                    if valid_records:
                        # Bank Nifty strikes are usually 100 points apart
                        atm_idx = min(range(len(valid_records)), key=lambda i: abs(valid_records[i]["strikePrice"] - spot_price))
                        start_idx = max(0, atm_idx - 1)
                        end_idx = min(len(valid_records), atm_idx + 2)
                        target_records = valid_records[start_idx:end_idx]
                        
                        total_ce_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in target_records)
                        total_pe_oi = sum(r.get("PE", {}).get("openInterest", 0) for r in target_records)
                        atm_strike = valid_records[atm_idx]["strikePrice"]
                        
                        sentiment = "Balanced"
                        if total_pe_oi >= 2 * total_ce_oi and total_ce_oi > 0:
                            sentiment = "Bullish sentiment"
                        elif total_ce_oi >= 2 * total_pe_oi and total_pe_oi > 0:
                            sentiment = "Bearish sentiment"
                            
                        time_str = current_time.strftime("%I:%M %p").lstrip("0")
                        return f"BANK NIFTY OPENING DRIVE ({time_str}): ATM {atm_strike} | Put OI: {total_pe_oi} | Call OI: {total_ce_oi}. {sentiment} detected."
                except Exception as e:
                    logger.error("Error during Bank Nifty Opening Drive: %s", e)
                    
            return "No previous Bank Nifty momentum data available. Snapshot saved."
        
        try:
            with open(archive_file, "r") as f:
                archived_data = json.load(f)
                
            archived_records = archived_data.get("filtered", {}).get("data", [])
            archived_map = {r.get("strikePrice"): r for r in archived_records if r.get("strikePrice")}
            
            live_records = live_data.get("filtered", {}).get("data", [])
            momentum_strings = []
            
            for live_row in live_records:
                strike = live_row.get("strikePrice")
                if not strike: continue
                # Bank Nifty near ATM filter (+/- 1000)
                if abs(strike - spot_price) > 1000: continue
                    
                archived_row = archived_map.get(strike)
                if not archived_row: continue
                    
                ce_diff = live_row.get("CE", {}).get("openInterest", 0) - archived_row.get("CE", {}).get("openInterest", 0)
                pe_diff = live_row.get("PE", {}).get("openInterest", 0) - archived_row.get("PE", {}).get("openInterest", 0)
                
                # Bank Nifty significance filter (> 5000 lots/contracts)
                if abs(ce_diff) > 5000 or abs(pe_diff) > 5000:
                    parts = [f"BN Strike {strike}:"]
                    if abs(ce_diff) > 5000:
                        parts.append(f"Call OI {'decreased' if ce_diff < 0 else 'increased'} by {abs(ce_diff)}.")
                    if abs(pe_diff) > 5000:
                        parts.append(f"Put OI {'decreased' if pe_diff < 0 else 'increased'} by {abs(pe_diff)}.")
                    momentum_strings.append(" ".join(parts))
            
            with open(download_file, "w") as f:
                json.dump(live_data, f, indent=2)
                
            if not momentum_strings:
                return "15-Minute Bank Nifty Momentum: No significant OI changes."
                
            return "15-Minute Bank Nifty Momentum:\n" + "\n".join(momentum_strings)
            
        except Exception as e:
            logger.error("Error calculating Bank Nifty OI momentum: %s", e)
            return "Bank Nifty OI Momentum: Calculation failed."

    def run_strategy(self, chain_text: str, chart_paths: list[str], prompt_name: str = "system_prompt") -> str:
        """GPT-4o MultiModal Inference for Bank Nifty."""
        prompt_text = load_prompt(prompt_name)
        if not self.llm_client: return "OpenAI API key missing."
        if not prompt_text: return f"Prompt '{prompt_name}' not found."
            
        try:
            prompt = f"{prompt_text}\n\n{chain_text}"
            content_list = [{"type": "text", "text": prompt}]
            
            for path in chart_paths:
                with open(path, "rb") as image_file:
                    base64_image = base64.b64encode(image_file.read()).decode('utf-8')
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                    })

            logger.info("Querying GPT-4o for Bank Nifty signal...")
            response = self.llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a professional quantitative analyst specializing in BANK NIFTY derivatives on the NSE India. You must coordinate your Bank Nifty trades with the provided Nifty 50 context."
                    },
                    {"role": "user", "content": content_list}
                ],
                max_tokens=1500,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("Error querying OpenAI for Bank Nifty: %s", e)
            return "Failed to generate Bank Nifty plan."

    def run_pipeline(self):
        """Main execution sequence for Bank Nifty."""
        try:
            logger.info("--- Bank Nifty Options Agent Started ---")
            
            # Step 1: Collect Bank Nifty Data
            json_data, chart_paths, spot_price = self.acquire_data()
            if not json_data:
                logger.error("Failed to acquire Bank Nifty data. Aborting for safety.")
                # We skip archiving if we didn't get data to preserve charts for manual inspection
                return
            
            logger.info(f">>> BANK NIFTY DATA: Spot: {spot_price}")

            # Step 2: Format Data & Context
            chain_text = self.process_data(json_data, spot_price)
            
            # Add India VIX
            vix_value = get_india_vix()
            
            # Add Bank Nifty 15m ATR
            atr_value = get_atr_15m(ticker="^NSEBANK")
            
            # Step 2b: Add Nifty Context (Batch 40 - Two Brain)
            try:
                nifty_yf = yf.Ticker("^NSEI")
                nifty_hist = nifty_yf.history(period="2d")
                if len(nifty_hist) >= 2:
                    prev_close = nifty_hist['Close'].iloc[-2]
                    current_nifty = nifty_hist['Close'].iloc[-1]
                    pct_change = ((current_nifty - prev_close) / prev_close) * 100
                    nifty_context = f"Nifty 50 at {current_nifty:.0f} ({pct_change:+.2f}%)"
                else:
                    nifty_context = "Nifty 50 context unavailable"
            except Exception as e:
                logger.error("Failed to fetch Nifty context: %s", e)
                nifty_context = "Nifty 50 context unavailable"
            
            logger.info(f"Two-Brain Context: {nifty_context}")
            
            vol_data = {"india_vix": vix_value, "atr_15m": atr_value, "nifty_context": nifty_context}
            chain_text = f"{chain_text}\n\nMARKET CONTEXT DATA:\n{json.dumps(vol_data, indent=2)}"

            # Add OI Momentum
            oi_momentum_text = self.get_oi_momentum(json_data, spot_price)
            chain_text = f"{chain_text}\n\n{oi_momentum_text}"
            
            # Step 3: Run Strategy
            raw_response = self.run_strategy(chain_text, chart_paths, prompt_name="system_prompt")
            
            if raw_response is None:
                logger.error("Bank Nifty Strategy returned None.")
                return

            # Step 4: Parse & Broadcast
            try:
                json_str = raw_response.strip()
                if "```json" in json_str:
                    json_str = json_str.split("```json")[-1].split("```")[0].strip()
                elif "```" in json_str:
                    json_str = json_str.split("```")[1].strip()
                
                signal_data = json.loads(json_str)
                decision = signal_data.get("decision", "HOLD").upper()
                
                if decision in ["BUY_CALL", "BUY_PUT"]:
                    nearest_expiry = json_data.get("records", {}).get("expiryDates", [""])[0]
                    self.log_json_signal(signal_data, nearest_expiry)
                    
                    emoji = "🚀" if decision == "BUY_CALL" else "🔻"
                    trade_type = "CALL" if decision == "BUY_CALL" else "PUT"
                    msg = (
                        f"{emoji} *BANK NIFTY SIGNAL: BUY {trade_type}*\n\n"
                        f"🎯 *Strike:* {signal_data.get('strike')}\n"
                        f"💰 *Entry:* {signal_data.get('entry_price')}\n"
                        f"🛑 *SL:* {signal_data.get('stop_loss')}\n"
                        f"🏁 *Target:* {signal_data.get('target')}\n"
                        f"🔥 *Confidence:* {signal_data.get('confidence_score')}%\n\n"
                        f"📝 *Reasoning:* {signal_data.get('reasoning')}\n"
                        f"🌍 *Market Context:* {nifty_context}"
                    )
                    TelegramNotifier.send_alert(msg)
                else:
                    logger.info("Bank Nifty Decision: HOLD. Reasoning: %s", signal_data.get("reasoning"))
                    if signal_data.get("confidence_score", 0) > 30:
                         TelegramNotifier.send_alert(f"⚖️ *BANK NIFTY STATUS: HOLD*\n\nReasoning: {signal_data.get('reasoning')}")

            except Exception as e:
                logger.error("Error processing Bank Nifty response: %s", e)
            
            logger.info("--- Bank Nifty Options Agent Completed ---")
            
        except Exception as e:
            logger.error("Fatal error in Bank Nifty agent: %s", e)
        finally:
            # Only archive if we actually successfully acquired data, 
            # otherwise keep snapshots in place for manual debugging.
            if 'json_data' in locals() and json_data:
                self.archive_downloads()

    def log_json_signal(self, signal_data: dict, nearest_expiry: str = ""):
        """Logs Bank Nifty structured signal to paper_trades.csv."""
        try:
            decision = signal_data.get("decision", "").upper()
            strike = signal_data.get("strike")
            option_type = "CE" if decision == "BUY_CALL" else "PE"
            if not strike: return

            expiry_prefix = ""
            if nearest_expiry:
                try:
                    expiry_dt = datetime.strptime(nearest_expiry, "%d-%b-%Y")
                    expiry_prefix = expiry_dt.strftime("%d%b").upper()
                except:
                    expiry_prefix = nearest_expiry.split("-")[0] + nearest_expiry.split("-")[1].upper() if "-" in nearest_expiry else ""

            instrument = f"BANKNIFTY {expiry_prefix}{strike}{option_type}"
            
            log_dir = BASE_DIR / "logs"
            log_dir.mkdir(exist_ok=True)
            csv_path = log_dir / "paper_trades.csv"
            
            file_exists = csv_path.exists()
            with open(csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Persona", "Instrument", "Entry_Price", "Target", "Stop_Loss", "Status"])
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow([timestamp, "Bank Nifty AI", instrument, signal_data.get("entry_price"), signal_data.get("target"), signal_data.get("stop_loss"), "PENDING"])
                
            logger.info("Logged PENDING Bank Nifty trade: %s", instrument)
        except Exception as e:
            logger.error("Error logging Bank Nifty signal: %s", e)

    def archive_downloads(self):
        """
        Symbol-specific archiving for Bank Nifty.
        Only moves/deletes Bank Nifty labeled files (BN_ prefix).
        """
        try:
            logger.info("Archiving Bank Nifty market snapshots...")
            
            # 1. Identify Bank Nifty specific files
            # Bank Nifty patterns: BN_ (charts), bank_nifty_ (json chains)
            bn_patterns = ["BN_", "bank_nifty_"]
            
            # 2. Selective cleanup of ARCHIVE_DIR (only BN files)
            if ARCHIVE_DIR.exists():
                for item in ARCHIVE_DIR.iterdir():
                    if any(p in item.name for p in bn_patterns):
                        if item.is_file():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item)
            else:
                ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            
            # 3. Selective move from DOWNLOAD_DIR to ARCHIVE_DIR
            if DOWNLOAD_DIR.exists():
                for item in DOWNLOAD_DIR.iterdir():
                    if any(p in item.name for p in bn_patterns):
                        shutil.move(str(item), str(ARCHIVE_DIR / item.name))
            
            logger.info("Bank Nifty Archiving complete.")
        except Exception as e:
            logger.error("Error during Bank Nifty archiving: %s", e)

if __name__ == "__main__":
    agent = BankNiftyOptionsAgent()
    agent.run_pipeline()

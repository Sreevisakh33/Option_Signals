import os
import csv
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to python path so 'src' can be imported
BASE_DIR = Path(__file__).parent.parent.parent
sys.path.append(str(BASE_DIR))

from dotenv import load_dotenv
from fyers_apiv3 import fyersModel
from src.utils.logger_config import get_logger

logger = get_logger("PaperEvaluator")

# Paths
BASE_DIR = Path(__file__).parent.parent.parent
LOGS_DIR = BASE_DIR / "logs"
CSV_PATH = LOGS_DIR / "paper_trades.csv"
DOTENV_PATH = BASE_DIR / ".env"

# Load environment variables
load_dotenv(dotenv_path=DOTENV_PATH)
CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN")

class PaperEvaluator:
    def __init__(self):
        if not CLIENT_ID or not ACCESS_TOKEN:
            logger.error("FYERS credentials missing in .env. Run fyers_auth.py first.")
            self.fyers = None
            return
            
        self.fyers = fyersModel.FyersModel(
            client_id=CLIENT_ID, 
            is_async=False, 
            token=ACCESS_TOKEN, 
            log_path=str(LOGS_DIR)
        )
        logger.info("FyersModel initialized for evaluation.")

    def get_expiry_details(self, date_obj):
        """Calculates Today, next Thursday (Weekly) and last Thursday (Monthly) for symbols."""
        # Symbol format for Today (useful if today is expiry)
        today_year_2digit = str(date_obj.year)[-2:]
        m_today = date_obj.month
        today_month_code = str(m_today) if m_today <= 9 else {"10": "O", "11": "N", "12": "D"}[str(m_today)]
        today_day_str = date_obj.strftime("%d")

        # Next Tuesday (Nifty 50 moved to Tuesday expiries in 2026)
        days_ahead = 1 - date_obj.weekday()
        if days_ahead < 0:
            days_ahead += 7
        next_tue = date_obj + timedelta(days=days_ahead)
        
        # Monthly symbol formatting
        month_year_2digit = str(next_tue.year)[-2:]
        month_name = next_tue.strftime("%b").upper()
        
        # Weekly month code (1-9, O, N, D)
        m = next_tue.month
        month_code = str(m) if m <= 9 else {"10": "O", "11": "N", "12": "D"}[str(m)]
        day_str = next_tue.strftime("%d")
        
        return {
            "today": f"{today_year_2digit}{today_month_code}{today_day_str}",
            "weekly": f"{month_year_2digit}{month_code}{day_str}",
            "monthly": f"{month_year_2digit}{month_name}"
        }

    def format_fyers_symbol(self, instrument_str: str, trade_date: datetime) -> str:
        """
        Converts 'NIFTY 23150 CE' to Fyers format.
        Logic: Try Weekly first, fall back to Monthly if requested or if weekly fails.
        """
        parts = instrument_str.split()
        if len(parts) != 3 or parts[0] != "NIFTY":
            return ""
            
        strike = parts[1]
        opt_type = parts[2]
        
        expiry = self.get_expiry_details(trade_date)
        
        # Defaulting to Weekly format as it is most common for these signals
        fyers_symbol = f"NSE:NIFTY{expiry['weekly']}{strike}{opt_type}"
        return fyers_symbol

    def fetch_historical_data(self, fyers_symbol: str, start_time: datetime):
        """Fetches 5-minute historical data from signal time to EOD."""
        if not self.fyers:
            return []

        # Fyers requires epoch timestamp or YYYY-MM-DD format based on range_from/to
        range_from = start_time.strftime("%Y-%m-%d")
        range_to = datetime.now().strftime("%Y-%m-%d")
        
        data = {
            "symbol": fyers_symbol,
            "resolution": "5",
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": "1"
        }

        try:
            response = self.fyers.history(data=data)
            if response.get("s") == "ok":
                candles = response.get("candles", [])
                # Filter candles starting from the BEGINNING of the 5m candle where signal occurred
                # Subtracting 300 seconds to ensure we capture the full 5m block containing start_time
                start_epoch = int(start_time.timestamp()) - 300
                valid_candles = [c for c in candles if c[0] >= start_epoch]
                return valid_candles
            else:
                logger.warning(f"Failed to fetch data for {fyers_symbol}: {response}")
                return []
        except Exception as e:
            logger.error(f"Error fetching history for {fyers_symbol}: {e}")
            return []

    def evaluate_trade(self, row, valid_candles):
        """
        Steps through 5-min candles to see if Entry was hit, 
        and subsequently if Target or SL was hit.
        Returns (status, exit_time).
        """
        status = row["Status"]
        exit_time = ""
        try:
            entry_price = float(row["Entry_Price"])
            target = float(row["Target"])
            sl = float(row["Stop_Loss"])
        except ValueError:
            return "INVALID", ""
            
        if entry_price <= 0:
            return "NO_SIGNAL", ""
        
        # Immediate Breach Check: Was SL or Target already hit at the moment of logging?
        # (Using the logged entry price which is the LTP at signal time)
        if status in ["PENDING", "ERROR"]:
            if entry_price <= sl:
                return "HIT_SL", row["Timestamp"]
            if entry_price >= target:
                return "HIT_TARGET", row["Timestamp"]
            
            if valid_candles:
                status = "ACTIVE"
        
        for candle in valid_candles:
            epoch_ts, open_p, high_p, low_p, close_p, vol = candle
            
            # Trade is Active, looking for Exit
            if status == "ACTIVE":
                # CONSERVATIVE RULE: If both hit in the same candle, assume SL hit FIRST.
                if low_p <= sl:
                    status = "HIT_SL"
                    exit_time = datetime.fromtimestamp(epoch_ts).strftime("%Y-%m-%d %H:%M:%S")
                    break
                elif high_p >= target:
                    status = "HIT_TARGET"
                    exit_time = datetime.fromtimestamp(epoch_ts).strftime("%Y-%m-%d %H:%M:%S")
                    break
        return status, exit_time

    def run_eod_evaluation(self):
        """Reads CSV, evaluates trades, and writes results to a DAILY file."""
        if not CSV_PATH.exists():
            logger.info("No paper_trades.csv found. Nothing to evaluate.")
            return

        # Generate today's result filename
        today_str = datetime.now().strftime("%Y-%m-%d")
        RESULT_CSV_PATH = LOGS_DIR / f"result_{today_str}.csv"
        
        logger.info(f"Starting End-of-Day Paper Trade Evaluation for {today_str}...")
        logger.info(f"Output will be saved to: {RESULT_CSV_PATH.name}")
        
        results_to_save = []
        stats = {"HIT_TARGET": 0, "HIT_SL": 0, "EOD_OPEN": 0, "STILL_PENDING": 0, "NO_SIGNAL": 0, "ERROR": 0}
        
        try:
            with open(CSV_PATH, "r") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames)
                
                # Add new columns for the result file
                if "Result" not in fieldnames:
                    fieldnames.append("Result")
                if "Exit_Time" not in fieldnames:
                    fieldnames.append("Exit_Time")
                
                for row in reader:
                    # Initialize columns
                    if "Result" not in row: row["Result"] = ""
                    if "Exit_Time" not in row: row["Exit_Time"] = ""

                    trade_time = datetime.strptime(row["Timestamp"], "%Y-%m-%d %H:%M:%S")
                    
                    # Log trial
                    if row["Status"] in ["PENDING", "ACTIVE", "ERROR"]:
                        expiry = self.get_expiry_details(trade_time)
                        
                        try:
                            # Handling both formats:
                            # New: NIFTY 24MAR23900CE (2 parts)
                            # Old: NIFTY 23900 CE (3 parts)
                            parts = row["Instrument"].split()
                            if len(parts) == 3:
                                strike = parts[1]
                                opt_type = parts[2]
                            else:
                                # Regex to extract last 5 digits and type (CE/PE)
                                combined = parts[1]
                                match = re.search(r"(\d{5})(CE|PE)$", combined)
                                if match:
                                    strike = match.group(1)
                                    opt_type = match.group(2)
                                else:
                                    raise ValueError(f"Could not parse instrument: {row['Instrument']}")
                        except (IndexError, AttributeError, ValueError) as parse_err:
                            logger.warning(f"Skipping row due to parse error: {parse_err}")
                            row["Status"] = "INVALID_INST"
                            results_to_save.append(row)
                            continue

                        symbols_to_try = [
                            f"NSE:NIFTY{expiry['today']}{strike}{opt_type}",
                            f"NSE:NIFTY{expiry['weekly']}{strike}{opt_type}",
                            f"NSE:NIFTY{expiry['monthly']}{strike}{opt_type}"
                        ]
                        
                        symbol_hit = ""
                        data_fetch_error = False
                        candles = []
                        
                        for sym in symbols_to_try:
                            time.sleep(0.5) # Avoid Fyers rate limit
                            logger.info(f"Trying symbol: {sym}")
                            data = {
                                "symbol": sym,
                                "resolution": "1",
                                "date_format": "1",
                                "range_from": trade_time.strftime("%Y-%m-%d"),
                                "range_to": datetime.now().strftime("%Y-%m-%d"),
                                "cont_flag": "1"
                            }
                            try:
                                response = self.fyers.history(data=data)
                                if response.get("s") == "ok":
                                    all_candles = response.get("candles", [])
                                    # Start evaluation strictly 1 minute AFTER signal to ensure logical Exit_Time
                                    start_epoch = int(trade_time.timestamp()) + 60
                                    candles = [c for c in all_candles if c[0] >= start_epoch]
                                    
                                    # --- ENTRY MATCH GUARD ---
                                    if candles:
                                        first_candle_open = candles[0][1] # Open of first valid candle
                                        try:
                                            row_entry = float(row["Entry_Price"])
                                        except:
                                            row_entry = 0
                                        
                                        if row_entry > 0:
                                            diff = abs(first_candle_open - row_entry) / row_entry
                                            if diff > 0.15: # 15% tolerance
                                                logger.warning(f"Price mismatch for {sym}: CSV={row_entry}, Market={first_candle_open} ({diff:.1%}). Skipping symbol.")
                                                candles = []
                                                continue
                                    # --------------------------

                                    if candles:
                                        symbol_hit = sym
                                        break
                                elif response.get("code") in [-300, -16]:
                                    continue
                                else:
                                    logger.warning(f"API Error for {sym}: {response.get('message')}")
                                    data_fetch_error = True
                                    break
                            except Exception as e:
                                logger.error(f"Exc fetching {sym}: {e}")
                                data_fetch_error = True
                                break
                        
                        if candles:
                            logger.info(f"Data found for {symbol_hit}. Evaluating...")
                            new_status, exit_time = self.evaluate_trade(row, candles)
                            row["Status"] = new_status
                            row["Exit_Time"] = exit_time
                            
                            # Update Result
                            if new_status == "HIT_TARGET":
                                row["Result"] = "PROFIT"
                                stats["HIT_TARGET"] += 1
                            elif new_status == "HIT_SL":
                                row["Result"] = "LOSS"
                                stats["HIT_SL"] += 1
                            elif new_status == "ERROR":
                                row["Result"] = "EVAL_ERROR"
                                stats["ERROR"] += 1
                            elif new_status == "ACTIVE":
                                stats["EOD_OPEN"] += 1
                            elif new_status == "PENDING":
                                stats["STILL_PENDING"] += 1
                            elif new_status == "NO_SIGNAL":
                                stats["NO_SIGNAL"] += 1
                            
                            logger.info(f"Result for {symbol_hit}: {new_status} at {exit_time or 'N/A'}")
                        elif data_fetch_error:
                            row["Status"] = "ERROR"
                            row["Result"] = "API_ERROR"
                            stats["ERROR"] += 1
                        else:
                            logger.warning(f"No data found for symbols {symbols_to_try}")
                    
                    results_to_save.append(row)
                    
            # Write to DAILY result file, NOT paper_trades.csv
            with open(RESULT_CSV_PATH, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results_to_save)
            
            # Final Report
            print("\n" + "="*50)
            print(f"📊 PAPER TRADING REPORT - {today_str}")
            print("="*50)
            print(f"✅ Targets Hit : {stats['HIT_TARGET']}")
            print(f"❌ Stop Losses : {stats['HIT_SL']}")
            print(f"⏳ Left Open   : {stats['EOD_OPEN']}")
            print(f"💤 Never Trig. : {stats['STILL_PENDING']}")
            print(f"🚫 No Signals  : {stats['NO_SIGNAL']}")
            print(f"⚠️ Errors      : {stats['ERROR']}")
            print("="*50 + "\n")
            logger.info(f"Evaluation Complete. Results saved to {RESULT_CSV_PATH.name}")

        except Exception as e:
            logger.error(f"Error during evaluation: {e}")

        except Exception as e:
            logger.error(f"Error during evaluation: {e}")

if __name__ == "__main__":
    evaluator = PaperEvaluator()
    if evaluator.fyers:
        evaluator.run_eod_evaluation()

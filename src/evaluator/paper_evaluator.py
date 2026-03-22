import os
import csv
import sys
import time
import re
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
        """Calculates potential weekly symbols (Tue/Mon/Wed) to handle holiday shifts."""
        def format_expiry(d_obj):
            y_2d = str(d_obj.year)[-2:]
            m = d_obj.month
            m_code = str(m) if m <= 9 else {"10": "O", "11": "N", "12": "D"}[str(m)]
            day_s = d_obj.strftime("%d")
            return f"{y_2d}{m_code}{day_s}"

        # Current Tuesday candidate
        days_to_tue = (1 - date_obj.weekday()) % 7
        next_tue = date_obj + timedelta(days=days_to_tue)
        
        # Holiday shifts: checks Tue, Mon, Wed
        # We also keep 'today' as a top candidate
        y_2d_today = str(date_obj.year)[-2:]
        m_today = date_obj.month
        m_code_today = str(m_today) if m_today <= 9 else {"10": "O", "11": "N", "12": "D"}[str(m_today)]
        day_s_today = date_obj.strftime("%d")
        
        month_name = next_tue.strftime("%b").upper()
        
        return {
            "today": f"{y_2d_today}{m_code_today}{day_s_today}",
            "tue": format_expiry(next_tue),
            "mon": format_expiry(next_tue - timedelta(days=1)),
            "wed": format_expiry(next_tue + timedelta(days=1)),
            "monthly": f"{str(next_tue.year)[-2:]}{month_name}"
        }

    def format_fyers_symbol(self, instrument_str: str, trade_date: datetime) -> str:
        """
        Converts 'NIFTY 23150 CE' or 'BANKNIFTY 53000 PE' to Fyers format.
        """
        parts = instrument_str.split()
        if len(parts) < 2:
            return ""
            
        symbol = parts[0]
        if symbol not in ["NIFTY", "BANKNIFTY"]:
            return ""
            
        # Extract strike/type from either 3-part or 2-part instrument names
        if len(parts) == 3:
            strike = parts[1]
            opt_type = parts[2]
        else:
            combined = parts[1]
            match = re.search(r"(\d{5})(CE|PE)$", combined)
            if match:
                strike = match.group(1)
                opt_type = match.group(2)
            else:
                return ""
        
        expiry = self.get_expiry_details(trade_date)
        return f"NSE:{symbol}{expiry['tue']}{strike}{opt_type}"

    def fetch_historical_data(self, fyers_symbol: str, start_time: datetime):
        """Fetches historical data from signal time to EOD, with resolution fallback."""
        if not self.fyers:
            return []

        range_from = start_time.strftime("%Y-%m-%d")
        range_to = datetime.now().strftime("%Y-%m-%d")
        
        for res in ["1", "5"]:
            data = {
                "symbol": fyers_symbol,
                "resolution": res,
                "date_format": "1",
                "range_from": range_from,
                "range_to": range_to,
                "cont_flag": "1"
            }
            try:
                response = self.fyers.history(data=data)
                if response and response.get("s") == "ok":
                    candles = response.get("candles", [])
                    if candles:
                        # Precision filtering: only candles AFTER start_time
                        start_epoch = int(start_time.timestamp())
                        # If resolution is 5m, include the candle containing the signal
                        if res == "5":
                            start_epoch -= 300
                            logger.info(f"Using 5m fallback for {fyers_symbol}")
                        
                        return [c for c in candles if c[0] >= start_epoch]
            except Exception as e:
                logger.error(f"Error fetching {fyers_symbol} at res {res}: {e}")
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
                        
                            # Handling both formats:
                            # New: NIFTY 24MAR23900CE (2 parts)
                            # Old: NIFTY 23900 CE (3 parts)
                            # NEW Bank Nifty: BANKNIFTY 20MAR53000CE (2 parts)
                            parts = row["Instrument"].split()
                            symbol_prefix = parts[0]
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
                            f"NSE:{symbol_prefix}{expiry['today']}{strike}{opt_type}",
                            f"NSE:{symbol_prefix}{expiry['tue']}{strike}{opt_type}",
                            f"NSE:{symbol_prefix}{expiry['mon']}{strike}{opt_type}",
                            f"NSE:{symbol_prefix}{expiry['wed']}{strike}{opt_type}",
                            f"NSE:{symbol_prefix}{expiry['monthly']}{strike}{opt_type}"
                        ]
                        # De-duplicate while keeping search order (Weekly -> Monthly)
                        symbols_to_try = list(dict.fromkeys(symbols_to_try))
                        
                        symbol_hit = ""
                        final_candles = []
                        
                        for sym in symbols_to_try:
                            time.sleep(0.5) # Protect Fyers Rate Limit
                            logger.info(f"Trying symbol: {sym}")
                            candles = self.fetch_historical_data(sym, trade_time)
                            
                            if candles:
                                # Start evaluation with a 60s offset to ensure logical Exit_Times
                                start_epoch = int(trade_time.timestamp()) + 60
                                filtered_candles = [c for c in candles if c[0] >= start_epoch]
                                
                                if not filtered_candles:
                                    continue

                                # --- ENTRY MATCH GUARD ---
                                # Check if Market Open is within 15% of recorded Signal Entry
                                market_open = filtered_candles[0][1]
                                try:
                                    entry_price = float(row["Entry_Price"])
                                except:
                                    entry_price = 0
                                    
                                if entry_price > 0:
                                    diff = abs(market_open - entry_price) / entry_price
                                    if diff > 0.15:
                                        logger.warning(f"Price mismatch for {sym}: CSV={entry_price}, Market={market_open} ({diff:.1%}). Skipping.")
                                        continue
                                
                                # Found valid symbol and price match!
                                symbol_hit = sym
                                final_candles = filtered_candles
                                break
                        
                        if final_candles:
                            logger.info(f"Data found for {symbol_hit}. Evaluating...")
                            new_status, exit_time = self.evaluate_trade(row, final_candles)
                            row["Status"] = new_status
                            row["Exit_Time"] = exit_time
                            
                            # Increment stats
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
                        else:
                            # If all attempts failed, mark as SYNC_MISMATCH or NO_DATA
                            row["Status"] = "NO_DATA"
                            logger.warning(f"No valid data/symbol match found for {row['Instrument']} at {row['Timestamp']}")
                    
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

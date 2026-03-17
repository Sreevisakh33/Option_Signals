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

        # Next Thursday
        days_ahead = 3 - date_obj.weekday()
        if days_ahead < 0:
            days_ahead += 7
        next_thursday = date_obj + timedelta(days=days_ahead)
        
        # Monthly symbol formatting
        month_year_2digit = str(next_thursday.year)[-2:]
        month_name = next_thursday.strftime("%b").upper()
        
        # Weekly month code (1-9, O, N, D)
        m = next_thursday.month
        month_code = str(m) if m <= 9 else {"10": "O", "11": "N", "12": "D"}[str(m)]
        day_str = next_thursday.strftime("%d")
        
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
        """
        status = row["Status"]
        try:
            entry_price = float(row["Entry_Price"])
            target = float(row["Target"])
            sl = float(row["Stop_Loss"])
        except ValueError:
            return "INVALID"
            
        if entry_price <= 0:
            return "NO_SIGNAL"
        
        # Market Order Logic: If we have candles, the trade is triggered immediately 
        # at the logged entry price (which is the LTP at the time of signal).
        if status == "PENDING" and valid_candles:
            status = "ACTIVE"
            # We check the first candle for immediate SL/Target hits too
        
        for candle in valid_candles:
            timestamp, open_p, high_p, low_p, close_p, vol = candle
            
            # Trade is Active, looking for Exit
            if status == "ACTIVE":
                if low_p <= sl:
                    status = "HIT_SL"
                    break
                elif high_p >= target:
                    status = "HIT_TARGET"
                    break
                    
        return status

    def run_eod_evaluation(self):
        """Reads CSV, evaluates PENDING/ACTIVE trades, and rewrites CSV."""
        if not CSV_PATH.exists():
            logger.info("No paper_trades.csv found. Nothing to evaluate.")
            return

        logger.info("Starting End-of-Day Paper Trade Evaluation...")
        
        updated_rows = []
        stats = {"HIT_TARGET": 0, "HIT_SL": 0, "EOD_OPEN": 0, "STILL_PENDING": 0, "NO_SIGNAL": 0}
        
        try:
            with open(CSV_PATH, "r") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames)
                if "Result" not in fieldnames:
                    fieldnames.append("Result")
                
                for row in reader:
                    # Initialize Result if not present
                    if "Result" not in row:
                        row["Result"] = ""

                    trade_time = datetime.strptime(row["Timestamp"], "%Y-%m-%d %H:%M:%S")
                    
                    if row["Status"] in ["PENDING", "ACTIVE"]:
                        expiry = self.get_expiry_details(trade_time)
                        
                        try:
                            strike = row["Instrument"].split()[1]
                            opt_type = row["Instrument"].split()[2]
                        except (IndexError, AttributeError):
                            row["Status"] = "INVALID_INST"
                            updated_rows.append(row)
                            continue

                        # Order of Trial: Today's Expiry -> Next Weekly (Thursday) -> Monthly
                        symbols_to_try = [
                            f"NSE:NIFTY{expiry['today']}{strike}{opt_type}",
                            f"NSE:NIFTY{expiry['weekly']}{strike}{opt_type}",
                            f"NSE:NIFTY{expiry['monthly']}{strike}{opt_type}"
                        ]
                        
                        candles = []
                        symbol_hit = ""
                        for sym in symbols_to_try:
                            logger.info(f"Trying symbol: {sym}")
                            candles = self.fetch_historical_data(sym, trade_time)
                            if candles:
                                symbol_hit = sym
                                break
                        
                        if candles:
                            logger.info(f"Data found for {symbol_hit}. Evaluating...")
                            new_status = self.evaluate_trade(row, candles)
                            row["Status"] = new_status
                            
                            # Update Result
                            if new_status == "HIT_TARGET":
                                row["Result"] = "PROFIT"
                                stats["HIT_TARGET"] += 1
                            elif new_status == "HIT_SL":
                                row["Result"] = "LOSS"
                                stats["HIT_SL"] += 1
                            elif new_status == "ACTIVE":
                                stats["EOD_OPEN"] += 1
                            elif new_status == "PENDING":
                                stats["STILL_PENDING"] += 1
                            elif new_status == "NO_SIGNAL":
                                stats["NO_SIGNAL"] += 1
                            
                            logger.info(f"Result for {symbol}: {new_status}")
                        else:
                            logger.warning(f"No data found for {symbol_w} or {expiry['monthly']} since {row['Timestamp']}")
                    
                    updated_rows.append(row)
                    
            # Rewrite CSV with updated statuses and Result column
            tmp_path = CSV_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)
            
            # Replace old file (atomic)
            tmp_path.replace(CSV_PATH)
            
            # Print Summary Report
            print("\n" + "="*50)
            print("📊 CUMULATIVE PAPER TRADING REPORT")
            print("="*50)
            print(f"✅ Targets Hit : {stats['HIT_TARGET']}")
            print(f"❌ Stop Losses : {stats['HIT_SL']}")
            print(f"⏳ Left Open   : {stats['EOD_OPEN']}")
            print(f"💤 Never Trig. : {stats['STILL_PENDING']}")
            print(f"🚫 No Signals  : {stats['NO_SIGNAL']}")
            print("="*50 + "\n")
            logger.info("Evaluation Complete.")

        except Exception as e:
            logger.error(f"Error during evaluation: {e}")

if __name__ == "__main__":
    evaluator = PaperEvaluator()
    if evaluator.fyers:
        evaluator.run_eod_evaluation()

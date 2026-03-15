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

    def format_fyers_symbol(self, instrument_str: str) -> str:
        """
        Converts 'NIFTY 23150 CE' to Fyers format.
        Assuming current month expiry for simplicity in this paper evaluator.
        Example Fyers format: 'NSE:NIFTY26MAR23150CE'
        """
        parts = instrument_str.split()
        if len(parts) != 3 or parts[0] != "NIFTY":
            return ""
            
        strike = parts[1]
        opt_type = parts[2]
        
        # Determine Current Month and Year for Fyers Symbol
        now = datetime.now()
        year_str = str(now.year)[-2:]
        month_str = now.strftime("%b").upper()
        
        # Construct Symbol (Approximation for current month expiry)
        # Note: In a production environment, you would map this to the exact weekly expiry
        fyers_symbol = f"NSE:NIFTY{year_str}{month_str}{strike}{opt_type}"
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
                # Filter candles strictly AFTER the signal was generated
                start_epoch = int(start_time.timestamp())
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
        entry_price = float(row["Entry_Price"])
        target = float(row["Target"])
        sl = float(row["Stop_Loss"])
        
        for candle in valid_candles:
            timestamp, open_p, high_p, low_p, close_p, vol = candle
            
            # Phase 1: Waiting for Entry
            if status == "PENDING":
                # Did it breach the entry price during this 5m candle?
                if low_p <= entry_price <= high_p or open_p >= entry_price:
                    status = "ACTIVE"
                    # If it activates in the same candle, we must check if SL/Target also hit in this candle.
                    # We assume worst case: SL hit before Target if both are within the candle range.
                    if low_p <= sl:
                        status = "HIT_SL"
                        break
                    elif high_p >= target:
                        status = "HIT_TARGET"
                        break
                        
            # Phase 2: Trade is Active, looking for Exit
            elif status == "ACTIVE":
                if low_p <= sl:
                    status = "HIT_SL"
                    break
                elif high_p >= target:
                    status = "HIT_TARGET"
                    break
                    
        # EOD Check
        if status == "ACTIVE":
            status = "EOD_OPEN"
            
        return status

    def run_eod_evaluation(self):
        """Reads CSV, evaluates PENDING/ACTIVE trades, and rewrites CSV."""
        if not CSV_PATH.exists():
            logger.info("No paper_trades.csv found. Nothing to evaluate.")
            return

        logger.info("Starting End-of-Day Paper Trade Evaluation...")
        
        updated_rows = []
        stats = {"HIT_TARGET": 0, "HIT_SL": 0, "EOD_OPEN": 0, "STILL_PENDING": 0}
        
        try:
            with open(CSV_PATH, "r") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                
                for row in reader:
                    # Only evaluate trades from TODAY that aren't already closed
                    trade_time = datetime.strptime(row["Timestamp"], "%Y-%m-%d %H:%M:%S")
                    is_today = trade_time.date() == datetime.now().date()
                    
                    if is_today and row["Status"] in ["PENDING", "ACTIVE"]:
                        symbol = self.format_fyers_symbol(row["Instrument"])
                        logger.info(f"Evaluating: {row['Persona']} -> {symbol}")
                        
                        candles = self.fetch_historical_data(symbol, trade_time)
                        if candles:
                            new_status = self.evaluate_trade(row, candles)
                            row["Status"] = new_status
                            
                            # Track stats
                            if new_status == "HIT_TARGET": stats["HIT_TARGET"] += 1
                            elif new_status == "HIT_SL": stats["HIT_SL"] += 1
                            elif new_status == "EOD_OPEN": stats["EOD_OPEN"] += 1
                            elif new_status == "PENDING": stats["STILL_PENDING"] += 1
                            
                            logger.info(f"Result for {symbol}: {new_status}")
                        else:
                            logger.warning(f"No valid candles found for {symbol} after {trade_time}. Skipping.")
                    
                    updated_rows.append(row)
                    
            # Rewrite CSV with updated statuses
            tmp_path = CSV_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)
            
            # Replace old file (atomic)
            tmp_path.replace(CSV_PATH)
            
            # Print Summary Report
            print("\n" + "="*50)
            print("📊 DAILY PAPER TRADING REPORT")
            print("="*50)
            print(f"✅ Targets Hit : {stats['HIT_TARGET']}")
            print(f"❌ Stop Losses : {stats['HIT_SL']}")
            print(f"⏳ Left Open   : {stats['EOD_OPEN']}")
            print(f"💤 Never Trig. : {stats['STILL_PENDING']}")
            print("="*50 + "\n")
            logger.info("Evaluation Complete.")

        except Exception as e:
            logger.error(f"Error during EOD evaluation: {e}")

if __name__ == "__main__":
    evaluator = PaperEvaluator()
    if evaluator.fyers:
        evaluator.run_eod_evaluation()

import yfinance as yf
import logging
from src.utils.logger_config import get_logger

logger = get_logger("VixFetcher")

def get_india_vix():
    """
    Fetches the current India VIX value from Yahoo Finance.
    Ticker: ^INDIAVIX
    Returns: Rounded float (e.g., 14.25) or "UNAVAILABLE" on error.
    """
    try:
        # Fetching data for India VIX
        vix = yf.Ticker("^INDIAVIX")
        # Get the latest price (LTP)
        data = vix.fast_info
        if data and 'lastPrice' in data:
            current_vix = round(data['lastPrice'], 2)
            return current_vix
        
        # Fallback to history if fast_info fails
        hist = vix.history(period="1d")
        if not hist.empty:
            current_vix = round(hist['Close'].iloc[-1], 2)
            return current_vix
            
        return "UNAVAILABLE"
    except Exception as e:
        logger.error(f"Error fetching India VIX: {e}")
        return "UNAVAILABLE"

if __name__ == "__main__":
    # Standalone test
    print(f"Current India VIX: {get_india_vix()}")

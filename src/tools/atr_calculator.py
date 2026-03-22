import yfinance as yf
import pandas as pd
import logging
from src.utils.logger_config import get_logger

logger = get_logger("AtrCalculator")

def get_nifty_atr_15m(period=14):
    """
    Calculates the 15-minute ATR (Average True Range) for Nifty 50.
    Ticker: ^NSEI
    Returns: Rounded float (e.g., 28.50) or 30.0 as fallback.
    """
    try:
        # Fetch last 5 days of 15-minute data to ensure enough bars for ATR(14)
        nifty = yf.Ticker("^NSEI")
        df = nifty.history(period="5d", interval="15m")
        
        if df.empty or len(df) < period + 1:
            logger.warning(f"Insufficient data for ATR({period}). Using fallback 30.0.")
            return 30.0
            
        # Calculate True Range (TR)
        # TR = max(H-L, abs(H-PC), abs(L-PC))
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        
        # Calculate ATR using simple moving average of TR
        # Standard ATR often uses Wilder's smoothed moving average, but SMA is a common & robust implementation
        df['ATR'] = df['TR'].rolling(window=period).mean()
        
        latest_atr = df['ATR'].iloc[-1]
        
        if pd.isna(latest_atr):
            return 30.0
            
        return round(float(latest_atr), 2)
        
    except Exception as e:
        logger.error(f"Error calculating Nifty ATR: {e}")
        return 30.0

if __name__ == "__main__":
    # Standalone test
    print(f"Current Nifty 15m ATR(14): {get_nifty_atr_15m()}")

import sys
import os
from pathlib import Path

# Add project root to sys.path for imports
sys.path.append(str(Path(__file__).parent.parent.parent))

from playwright.sync_api import sync_playwright
from src.utils.settings import BASE_DIR

STORAGE_STATE_PATH = BASE_DIR / ".tv_storage_state.json"

def run_auth_helper():
    """
    Launches a visible browser window for the user to log in to TradingView.
    Saves the storage state (cookies, etc.) to a file once the user is finished.
    """
    print("\n" + "="*60)
    print("TRADINGVIEW AUTHENTICATION HELPER")
    print("="*60)
    print("\n1. A browser window will open.")
    print("2. Please log in to your TradingView account manually.")
    print("3. After you are logged in and your chart loads, close the browser window.")
    print(f"4. Your session will be saved to: {STORAGE_STATE_PATH}")
    print("\n" + "="*60 + "\n")

    input("Press Enter to launch the browser...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        page = context.new_page()
        page.goto("https://www.tradingview.com/#signin", wait_until="load")
        
        print("Waiting for you to log in and close the browser...")
        
        # Keep the script running until the browser window is closed by the user
        browser.on("disconnected", lambda: print("\nBrowser closed. Saving state..."))
        
        # We wait for the browser to disconnect (manual close)
        while browser.is_connected():
            try:
                # Optional: check if we just logged in to give user feedback
                if "tradingview.com" in page.url and page.get_by_text("Sign in").count() == 0:
                    pass
                page.wait_for_timeout(1000)
            except:
                break
        
        # Save state before exiting
        context.storage_state(path=STORAGE_STATE_PATH)
        print(f"Success! Authentication state saved to {STORAGE_STATE_PATH}")

if __name__ == "__main__":
    run_auth_helper()

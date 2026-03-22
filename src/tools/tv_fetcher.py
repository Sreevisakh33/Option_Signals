import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from src.utils.settings import TRADINGVIEW_CHART_URL, TV_USERNAME, TV_PASSWORD, DOWNLOAD_DIR, BASE_DIR
from src.utils.logger_config import get_logger

logger = get_logger("TradingViewFetcher")

INTERVALS = [3, 5, 15]
COMBINED_CHART_PATH = str(DOWNLOAD_DIR / "chart_multipane.png")
STORAGE_STATE_PATH = str(BASE_DIR / ".tv_storage_state.json")


class TradingViewFetcher:
    """
    Handles visual data acquisition from TradingView.
    Logs in with saved credentials so custom indicators load,
    captures each timeframe in fullscreen, then stitches into
    a single multi-pane image.
    """


    @staticmethod
    def _is_logged_in(page) -> bool:
        """Check if we are already logged in by looking for a profile element."""
        try:
            # Check for profile icon path or user menu button
            return page.locator("path[d*='M17.5 9']").count() > 0 or page.locator("[data-name='user-menu-button']").count() > 0
        except:
            return False

    @staticmethod
    def _stitch_charts(chart_paths: list[str], intervals: list[int], symbol: str = "NIFTY") -> str:
        """Stitch individual chart images side-by-side with timeframe labels."""
        images = [Image.open(p) for p in chart_paths]
        label_h = 40
        total_w = sum(img.width for img in images)
        max_h   = max(img.height for img in images) + label_h

        combined = Image.new("RGB", (total_w, max_h), color=(18, 18, 18))
        draw     = ImageDraw.Draw(combined)

        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
        except:
            font = ImageFont.load_default()

        x = 0
        for img, tf in zip(images, intervals):
            draw.rectangle([x, 0, x + img.width, label_h], fill=(30, 30, 30))
            draw.text((x + 10, 10), f"  {symbol.upper()}  |  {tf} MIN", fill=(200, 200, 200), font=font)
            combined.paste(img, (x, label_h))
            x += img.width

        combined.save(COMBINED_CHART_PATH)
        logger.info("Multi-pane chart saved → %s (%sx%spx)", COMBINED_CHART_PATH, combined.width, combined.height)
        return COMBINED_CHART_PATH

    @staticmethod
    def capture_charts(intervals: list[int] = INTERVALS, symbol: str = "NIFTY") -> list[str]:
        """
        Log in to TradingView, capture each timeframe in fullscreen mode
        (Shift+F hides sidebars), stitch all panes into one combined image,
        and return [combined_path] for the agent to pass to GPT-4o.
        """
        individual_paths = []

        with sync_playwright() as p:
            logger.info("Launching headless Chromium for TradingView (with login)...")
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1080",
                    "--start-maximized",
                ]
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                storage_state=STORAGE_STATE_PATH if os.path.exists(STORAGE_STATE_PATH) else None
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = context.new_page()

            try:
                # Strictly use storage state — if it doesn't exist, we error out
                if not os.path.exists(STORAGE_STATE_PATH):
                    raise FileNotFoundError(
                        f"Authentication state file not found at {STORAGE_STATE_PATH}. "
                        "Please run 'python3 src/tools/tv_auth_helper.py' first."
                    )

                # Check if we are logged in
                page.goto("https://in.tradingview.com/", wait_until="domcontentloaded", timeout=60000)
                if not TradingViewFetcher._is_logged_in(page):
                    logger.error("TradingView session expired. Please re-run the auth helper.")
                    return []

                logger.info("Authenticated via persistent session.")

                from src.utils.settings import NIFTY_CHART_ID, BANKNIFTY_CHART_ID, TRADINGVIEW_CHART_BASE_URL
                
                for interval in intervals:
                    # Select Layout ID and Symbol based on target index
                    if "BANK" in symbol.upper():
                         chart_id = BANKNIFTY_CHART_ID
                         tv_symbol = "NSE%3ABANKNIFTY1%21"
                    else:
                         chart_id = NIFTY_CHART_ID
                         tv_symbol = "NSE%3ANIFTY1%21"
                    
                    url = f"{TRADINGVIEW_CHART_BASE_URL}/{chart_id}/?symbol={tv_symbol}&interval={interval}"
                    logger.info("Navigating to %s %sm chart (Layout: %s)...", symbol.upper(), interval, chart_id)
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("canvas", timeout=30000)
                    time.sleep(5)   # Allow CPR/EMA/Volume indicators to fully render

                    # Dismiss popups
                    try:
                        page.locator("button[aria-label='Close']").click(timeout=2000)
                    except:
                        pass

                    # Focus canvas then Shift+F → fullscreen (hides all sidebars)
                    try:
                        page.locator("canvas").first.click()
                    except:
                        pass
                    page.keyboard.press("Shift+F")
                    time.sleep(1.5)

                    out_path = DOWNLOAD_DIR / f"chart_{interval}m.png"
                    out = str(out_path)
                    page.screenshot(path=out, full_page=False)
                    logger.info("Saved: %s", out)
                    individual_paths.append(out)

                    page.keyboard.press("Shift+F")   # exit fullscreen before next nav
                    time.sleep(0.5)

            except Exception as e:
                logger.error("Error capturing charts: %s", e)
            finally:
                page.close()
                browser.close()

        # Stitch all frames for archiving/debugging, but return individual frames for AI analysis
        if individual_paths:
            TradingViewFetcher._stitch_charts(individual_paths, intervals, symbol=symbol)
            return individual_paths

        return individual_paths

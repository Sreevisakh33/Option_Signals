from datetime import datetime
from playwright.sync_api import sync_playwright
from src.utils.settings import NSE_OC_URL
from src.utils.logger_config import get_logger

logger = get_logger("NSEFetcher")

class NSEFetcher:
    """Handles fetching and intercepting the NSE Option Chain JSON API."""

    @staticmethod
    def fetch_json(symbol: str = "NIFTY") -> tuple[dict, float]:
        """
        Navigates to the NSE Option Chain and intercepts the background JSON API request.
        Returns the parsed JSON dictionary and the underlying Spot Price.
        Includes retry logic to ensure data is not stale.
        """
        json_data = None
        spot_price = None
        max_attempts = 3

        with sync_playwright() as p:
            logger.info("Launching headless Chromium browser for NSE...")
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"macOS"',
                    "Upgrade-Insecure-Requests": "1"
                }
            )

            # Inject an evasion script
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
                })
            """)

            page = context.new_page()
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"Acquisition Attempt {attempt}/{max_attempts}...")
                    
                    # Use expect_response with a more specific filter to ensure we get the full data
                    def is_full_data(response):
                        if "api/option-chain" in response.url and symbol.upper() in response.url and response.status == 200:
                            try:
                                data = response.json()
                                # Ensure we have records and actual option chain data rows
                                has_records = "records" in data and "data" in data["records"]
                                if has_records:
                                    # Ensure the data list isn't empty (common at night/maintenance)
                                    return len(data["records"]["data"]) > 0
                                return False
                            except:
                                return False
                        return False

                    # We clear cookies/cache on retry to be safe
                    if attempt > 1:
                        context.clear_cookies()

                    with page.expect_response(is_full_data, timeout=45000) as response_info:
                        target_url = f"{NSE_OC_URL}?symbol={symbol.upper()}"
                        logger.info("Navigating to NSE Option Chain: %s", target_url)
                        # Adding a timestamp-based query param to bypass some caches
                        page.goto(f"{target_url}&refresh={int(datetime.now().timestamp())}", wait_until="load", timeout=60000)
                        
                        # Explicitly select the symbol from the dropdown to ensure the site's internal state updates
                        try:
                            # Wait for the select element to be available
                            page.wait_for_selector("#equity_optionchain_select", timeout=10000)
                            # Select the target symbol
                            page.select_option("#equity_optionchain_select", value=symbol.upper())
                            logger.info("Explicitly selected '%s' from NSE dropdown.", symbol.upper())
                        except Exception as sel_err:
                            logger.warning(f"Failed to explicitly select symbol from dropdown: {sel_err}. Relying on URL parameter.")
                        
                        # Capture the data
                        response = response_info.value
                        json_data = response.json()
                        
                        # VALIDATE TIMESTAMP
                        ts_str = json_data.get("records", {}).get("timestamp", "")
                        
                        if ts_str:
                            # Format: "18-Mar-2026 10:20:00"
                            try:
                                data_date = datetime.strptime(ts_str.split()[0], "%d-%b-%Y").date()
                                today_date = datetime.now().date()
                                
                                if data_date < today_date:
                                    from src.utils.settings import FORCE_FETCH
                                    if FORCE_FETCH:
                                        logger.warning(f"FORCE_FETCH is ENABLED. Proceeding with STALE data: {ts_str}")
                                        break # Override: Proceed anyway
                                    
                                    logger.warning(f"Detected STALE data! Data Date: {data_date} | Today: {today_date} (Note: Live market starts at 09:15 AM IST). Refreshing...")
                                    if attempt == max_attempts:
                                        logger.error("Data is still stale after all retries. Market may not be open yet. Aborting Fetch.")
                                        return None, None
                                    continue # Try again
                                else:
                                    logger.info(f"Intercepted FRESH NSE API response: {ts_str}")
                                    break # Success
                            except Exception as parse_err:
                                logger.error(f"Error parsing NSE timestamp '{ts_str}': {parse_err}")
                        
                        if "records" in json_data and "underlyingValue" in json_data["records"]:
                            spot_price = json_data["records"]["underlyingValue"]
                            break # Assume success if no timestamp but we have data (fallback)

                except Exception as e:
                    logger.error("Error acquiring NSE Option Chain on attempt %d: %s", attempt, e)
                    if attempt == max_attempts:
                        break
            
            if json_data and "records" in json_data and "underlyingValue" in json_data["records"]:
                spot_price = json_data["records"]["underlyingValue"]

            # Small sleep to disarm any race conditions in Playwright listeners before closing
            time.sleep(1)
            page.close()
            browser.close()

        return json_data, spot_price

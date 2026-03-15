from playwright.sync_api import sync_playwright
from src.utils.settings import NSE_OC_URL
from src.utils.logger_config import get_logger

logger = get_logger("NSEFetcher")

class NSEFetcher:
    """Handles fetching and intercepting the NSE Option Chain JSON API."""

    @staticmethod
    def fetch_json() -> tuple[dict, float]:
        """
        Navigates to the NSE Option Chain and intercepts the background JSON API request.
        Returns the parsed JSON dictionary and the underlying Spot Price.
        """
        json_data = None
        spot_price = None

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
            try:
                # Use expect_response with a more specific filter to ensure we get the full data
                def is_full_data(response):
                    if "api/option-chain" in response.url and "NIFTY" in response.url and response.status == 200:
                        try:
                            data = response.json()
                            return "records" in data and "data" in data["records"]
                        except:
                            return False
                    return False

                with page.expect_response(is_full_data, timeout=45000) as response_info:
                    logger.info("Navigating to NSE Option Chain: %s", NSE_OC_URL)
                    page.goto(NSE_OC_URL, wait_until="load", timeout=60000)
                    
                    # Capture the data
                    response = response_info.value
                    json_data = response.json()
                    logger.info("Intercepted full NSE API response successfully.")
                    
                    if "records" in json_data and "underlyingValue" in json_data["records"]:
                        spot_price = json_data["records"]["underlyingValue"]

            except Exception as e:
                logger.error("Error acquiring NSE Option Chain: %s", e)
            finally:
                page.close()
                browser.close()

        return json_data, spot_price

import requests
from src.utils.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.utils.logger_config import get_logger

logger = get_logger("TelegramNotifier")

class TelegramNotifier:
    """Handles broadcasting strategy signals to Telegram."""

    @staticmethod
    def send_alert(message_text: str):
        """Sends a text message to the configured Telegram chat."""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("Skipping Telegram Alert: Bot Token or Chat ID is missing.")
            return
            
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message_text
        }
        
        try:
            logger.info("Sending output to Telegram...")
            response = requests.post(url, json=payload)
            response.raise_for_status()
            logger.info("Successfully delivered to Telegram.")
        except requests.exceptions.RequestException as e:
            logger.error("Failed to send Telegram message: %s", e)
            if e.response:
                logger.debug("Response: %s", e.response.text)

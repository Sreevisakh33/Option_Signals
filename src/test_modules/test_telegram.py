import sys
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.tools.telegram_notifier import TelegramNotifier
from src.utils.logger_config import get_logger

logger = get_logger("TestTelegram")

def test_telegram_message():
    """Test Telegram message delivery using TelegramNotifier."""
    logger.info("Testing Telegram message delivery via TelegramNotifier...")
    
    test_message = "🚀 *Test Message*\nIf you see this, your refactored Telegram integration is working perfectly!"
    TelegramNotifier.send_alert(test_message)

if __name__ == "__main__":
    test_telegram_message()

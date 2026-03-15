import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Create a custom logger
logger = logging.getLogger("OptionSignals")
logger.setLevel(logging.INFO)

# Create handlers
c_handler = logging.StreamHandler(sys.stdout)
# Ensure logs directory exists if we want to log to file
log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(exist_ok=True)
# f_handler = logging.FileHandler(log_dir / "app.log")
f_handler = TimedRotatingFileHandler(
    log_dir / "app.log",
    when="midnight",
    interval=1,
    backupCount=2,
    encoding="utf-8"
)

c_handler.setLevel(logging.INFO)
f_handler.setLevel(logging.INFO)

# Create formatters and add it to handlers
log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
c_handler.setFormatter(log_format)
f_handler.setFormatter(log_format)

# Add handlers to the logger
if not logger.handlers:
    logger.addHandler(c_handler)
    logger.addHandler(f_handler)

def get_logger(name: str):
    return logger.getChild(name)

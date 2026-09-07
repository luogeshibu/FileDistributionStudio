import logging
from logging.handlers import RotatingFileHandler
from .paths import logs_dir


def setup_logging():
    log_file = logs_dir() / "app.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        fh = RotatingFileHandler(
            log_file,
            maxBytes=20 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
        root.addHandler(fh)
    return log_file

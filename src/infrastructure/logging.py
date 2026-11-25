"""Rotating per-user runtime logs, without board contents or license payloads."""
import logging
from logging.handlers import RotatingFileHandler


def configure_logging():
    from src.diagnostics import log_path
    logger = logging.getLogger("mpa_dfm")
    if not logger.handlers:
        handler = RotatingFileHandler(log_path().with_name("runtime.log"),
                                      maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger

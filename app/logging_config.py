"""
Central logging configuration.

Two named loggers are used across the app:
  - access_logger: normal operational events (alert created, notification
    dispatched, user logged in, etc.) — the "what happened" trail.
  - error_logger: failures that don't crash the request but are still
    worth flagging.
"""
import logging
import sys

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def _make_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:  # avoid duplicate handlers on module reload / test re-import
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


access_logger = _make_logger("healthtrack.access")
error_logger = _make_logger("healthtrack.error")

from __future__ import annotations

import logging
import os


_LOGGING_INITIALIZED = False


def setup_logging() -> None:
    """Configure a simple shared logging format once per process."""
    global _LOGGING_INITIALIZED
    if _LOGGING_INITIALIZED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    _LOGGING_INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


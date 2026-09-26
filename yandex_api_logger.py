"""Shared logger for Yandex API and tool execution diagnostics."""

import logging
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "api_debug.txt"

api_logger = logging.getLogger("yandex_api_debug")
api_logger.setLevel(logging.DEBUG)
api_logger.propagate = False

if not api_logger.handlers:
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
    api_logger.addHandler(file_handler)

# Emit a deterministic startup marker immediately after logger initialization.
# FileHandler flushes each emitted record, so the marker is available on disk
# before the first API request is handled.
api_logger.info("[STARTUP] yandex_api_debug logger initialized: %s", LOG_FILE)

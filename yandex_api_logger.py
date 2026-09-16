"""Shared logger for Yandex API and tool execution diagnostics."""

import logging
import os


os.makedirs("logs", exist_ok=True)

api_logger = logging.getLogger("yandex_api_debug")
api_logger.setLevel(logging.DEBUG)
api_logger.propagate = False

if not api_logger.handlers:
    file_handler = logging.FileHandler(
        "logs/api_debug.txt", encoding="utf-8", mode="a"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    )
    api_logger.addHandler(file_handler)

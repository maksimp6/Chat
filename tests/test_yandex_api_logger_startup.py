from pathlib import Path
import logging
import yandex_api_logger


def test_api_logger_uses_project_root_log_path():
    expected = Path(yandex_api_logger.__file__).resolve().parent / "logs" / "api_debug.txt"
    assert yandex_api_logger.LOG_FILE == expected


def test_api_logger_has_file_handler_and_startup_marker():
    logger = logging.getLogger("yandex_api_debug")
    assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    text = yandex_api_logger.LOG_FILE.read_text(encoding="utf-8")
    assert "[STARTUP] yandex_api_debug logger initialized" in text

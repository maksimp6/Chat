"""
logger.py - Централизованная система логирования для Alice Pro
"""
import logging
import os
import json
from datetime import datetime
from functools import wraps
from logging.handlers import RotatingFileHandler
from yc_logging import yc_logger

os.makedirs('logs', exist_ok=True)

LOG_FORMAT = '%(asctime)s | %(levelname)-7s | %(name)s | %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

class YCLoggingHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            yc_logger.emit(record.levelname, msg, stream_name=record.name)
        except Exception:
            pass

class AliceLogger:
    def __init__(self):
        self.loggers = {}
        self._setup_loggers()

    def _setup_loggers(self):
        self.loggers['app'] = self._create_logger('alice_pro', 'logs/app.txt')
        self.loggers['api'] = self._create_logger('yandex_api', 'logs/api_debug.txt')
        self.loggers['voice'] = self._create_logger('voice', 'logs/voice.txt')
        self.loggers['chat'] = self._create_logger('chat', 'logs/chat.txt')
        self.loggers['db'] = self._create_logger('database', 'logs/database.txt')
        self.loggers['export'] = self._create_logger('export', 'logs/export.txt')
        self.loggers['search'] = self._create_logger('search', 'logs/search.txt')
        self.loggers['prompts'] = self._create_logger('prompts', 'logs/prompts.txt')
        self.loggers['stats'] = self._create_logger('stats', 'logs/stats.txt')
        self.loggers['error'] = self._create_logger('errors', 'logs/errors.txt')

    def _create_logger(self, name, filename):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        if logger.handlers:
            logger.handlers.clear()
        fh = RotatingFileHandler(filename, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.ERROR)
        ch.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(ch)

        yc_h = YCLoggingHandler()
        yc_h.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(yc_h)
        return logger

    def get(self, name):
        return self.loggers.get(name, self.loggers['app'])

alice_logger = AliceLogger()

def log_function(logger_name='app'):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = alice_logger.get(logger_name)
            logger.info(f"→ {func.__name__}() вызвана")
            try:
                result = func(*args, **kwargs)
                logger.info(f"← {func.__name__}() завершена успешно")
                return result
            except Exception as e:
                logger.error(f"✗ {func.__name__}() ошибка: {str(e)}", exc_info=True)
                raise
        return wrapper
    return decorator

def log_request(logger_name='app'):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = alice_logger.get(logger_name)
            from flask import request
            logger.info(f"→ {request.method} {request.path}")
            if request.json:
                body = json.dumps(request.json, ensure_ascii=False).replace("\\r\\n", " ").replace("\\n", " ").replace("\\r", " ")
                logger.debug(f"  Body: {body[:500]}")
            start_time = datetime.now()
            try:
                result = func(*args, **kwargs)
                duration = (datetime.now() - start_time).total_seconds()
                logger.info(f"← {request.method} {request.path} - {duration:.3f}s")
                return result
            except Exception as e:
                duration = (datetime.now() - start_time).total_seconds()
                logger.error(f"✗ {request.method} {request.path} - {duration:.3f}s - {str(e)}", exc_info=True)
                raise
        return wrapper
    return decorator

def log_error(error_type, context=None):
    logger = alice_logger.get('error')
    error_msg = f"[{error_type}]"
    if context:
        error_msg += f" {context}"
    logger.error(error_msg, exc_info=True)

def log_voice(message, level='info'):
    logger = alice_logger.get('voice')
    getattr(logger, level)(f"[VOICE] {message}")

def log_chat(message, level='info'):
    logger = alice_logger.get('chat')
    getattr(logger, level)(f"[CHAT] {message}")

def log_db(message, level='info'):
    logger = alice_logger.get('db')
    getattr(logger, level)(f"[DB] {message}")

def log_search(message, level='info'):
    logger = alice_logger.get('search')
    getattr(logger, level)(f"[SEARCH] {message}")

def log_prompt(message, level='info'):
    logger = alice_logger.get('prompts')
    getattr(logger, level)(f"[PROMPT] {message}")

def log_export(message, level='info'):
    logger = alice_logger.get('export')
    getattr(logger, level)(f"[EXPORT] {message}")

def log_stats(message, level='info'):
    logger = alice_logger.get('stats')
    getattr(logger, level)(f"[STATS] {message}")

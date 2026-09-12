"""
logger.py - Централизованная система логирования для Alice Pro
"""
import logging
import os
import json
from datetime import datetime
from functools import wraps

# Создаем папку logs если её нет
os.makedirs('logs', exist_ok=True)

# Формат логирования
LOG_FORMAT = '%(asctime)s | %(levelname)-7s | %(name)s | %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

class AliceLogger:
    """Централизованный логгер для всех компонентов"""
    
    def __init__(self):
        self.loggers = {}
        self._setup_loggers()
    
    def _setup_loggers(self):
        """Настройка всех логгеров"""
        
        # Основной логгер приложения
        self.loggers['app'] = self._create_logger('alice_pro', 'logs/app.txt')
        
        # Логгер API запросов
        self.loggers['api'] = self._create_logger('yandex_api', 'logs/api_debug.txt')
        
        # Логгер голосового режима
        self.loggers['voice'] = self._create_logger('voice', 'logs/voice.txt')
        
        # Логгер текстового чата
        self.loggers['chat'] = self._create_logger('chat', 'logs/chat.txt')
        
        # Логгер базы данных
        self.loggers['db'] = self._create_logger('database', 'logs/database.txt')
        
        # Логгер экспорта/импорта
        self.loggers['export'] = self._create_logger('export', 'logs/export.txt')
        
        # Логгер поиска
        self.loggers['search'] = self._create_logger('search', 'logs/search.txt')
        
        # Логгер промптов
        self.loggers['prompts'] = self._create_logger('prompts', 'logs/prompts.txt')
        
        # Логгер статистики
        self.loggers['stats'] = self._create_logger('stats', 'logs/stats.txt')
        
        # Логгер ошибок
        self.loggers['error'] = self._create_logger('errors', 'logs/errors.txt')
    
    def _create_logger(self, name, filename):
        """Создание логгера с файловым обработчиком"""
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        
        # Удаляем старые обработчики если есть
        if logger.handlers:
            logger.handlers.clear()
        
        # Файловый обработчик
        fh = logging.FileHandler(filename, encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(fh)
        
        # Консольный обработчик (только для ошибок)
        ch = logging.StreamHandler()
        ch.setLevel(logging.ERROR)
        ch.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(ch)
        
        return logger
    
    def get(self, name):
        """Получить логгер по имени"""
        return self.loggers.get(name, self.loggers['app'])

# Глобальный экземпляр логгера
alice_logger = AliceLogger()

# Декораторы для логирования
def log_function(logger_name='app'):
    """Декоратор для логирования вызовов функций"""
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
    """Декоратор для логирования HTTP запросов"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = alice_logger.get(logger_name)
            from flask import request
            logger.info(f"→ {request.method} {request.path}")
            if request.json:
                logger.debug(f"  Body: {json.dumps(request.json, ensure_ascii=False)[:500]}")
            
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
    """Логирование ошибки в отдельный файл"""
    logger = alice_logger.get('error')
    error_msg = f"[{error_type}]"
    if context:
        error_msg += f" {context}"
    logger.error(error_msg, exc_info=True)

# Функции быстрого логирования
def log_voice(message, level='info'):
    """Быстрое логирование голосовых событий"""
    logger = alice_logger.get('voice')
    getattr(logger, level)(f"[VOICE] {message}")

def log_chat(message, level='info'):
    """Быстрое логирование событий чата"""
    logger = alice_logger.get('chat')
    getattr(logger, level)(f"[CHAT] {message}")

def log_db(message, level='info'):
    """Быстрое логирование операций с БД"""
    logger = alice_logger.get('db')
    getattr(logger, level)(f"[DB] {message}")

def log_search(message, level='info'):
    """Быстрое логирование операций поиска"""
    logger = alice_logger.get('search')
    getattr(logger, level)(f"[SEARCH] {message}")

def log_prompt(message, level='info'):
    """Быстрое логирование операций с промптами"""
    logger = alice_logger.get('prompts')
    getattr(logger, level)(f"[PROMPT] {message}")

def log_export(message, level='info'):
    """Быстрое логирование операций экспорта/импорта"""
    logger = alice_logger.get('export')
    getattr(logger, level)(f"[EXPORT] {message}")

def log_stats(message, level='info'):
    """Быстрое логирование статистики"""
    logger = alice_logger.get('stats')
    getattr(logger, level)(f"[STATS] {message}")

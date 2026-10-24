"""Yandex API diagnostics logger.

Routed through the central system log; request/response detail lives in the
Execution Trace, so this logger carries only operational messages.
"""

from observability.system_log import get_logger

api_logger = get_logger("yandex_api")

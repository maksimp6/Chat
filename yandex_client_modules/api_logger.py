"""Yandex API diagnostics logger.

Routed through the central system log; request/response detail lives in the
Execution Trace, so this logger carries only operational messages.
"""

import logging

api_logger = logging.getLogger("logos.yandex_api")

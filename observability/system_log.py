"""Centralized process-level system log.

Execution Trace is the source of truth for invocation diagnostics. This module
covers only what a trace cannot: server startup and failures before a trace is
bound. Records keep their real severity and, when a trace is bound to the
current context, carry its ``trace_id``/``invocation_id`` and WARNING+ records
are mirrored into that trace as ``system_log`` events.
"""

from __future__ import annotations
from core.env import env_get

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from observability.trace_manager import get_current_trace
from observability.trace_security import redact_text

ROOT_LOGGER_NAME = "logos"
DEFAULT_LOG_FILE = "system.jsonl"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 3
_lock = threading.Lock()
_mirroring = threading.local()


def log_dir() -> Path:
    return Path(env_get("LOGOS_LOG_DIR", "logs"))


class TraceContextFilter(logging.Filter):
    """Attach trace_id/invocation_id of the bound Execution Trace to records."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = invocation_id = None
        trace = get_current_trace()
        if trace is not None:
            trace_id = getattr(trace, "trace_id", None)
            context = trace.trace.get("context") or {}
            invocation_id = context.get("invocation_id") or trace_id
        record.trace_id = trace_id
        record.invocation_id = invocation_id
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "trace_id": getattr(record, "trace_id", None),
            "invocation_id": getattr(record, "invocation_id", None),
        }
        if record.exc_info:
            entry["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


class TraceMirrorHandler(logging.Handler):
    """Mirror WARNING+ records into the bound, still-open Execution Trace."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_mirroring, "active", False):
            return
        trace = get_current_trace()
        if trace is None or trace.finalized:
            return
        _mirroring.active = True
        try:
            trace.add_event(
                "system_log",
                {"level": record.levelname, "logger": record.name, "message": record.getMessage()},
            )
        except Exception:
            pass
        finally:
            _mirroring.active = False


def configure(level: int | str | None = None, *, directory: Path | None = None) -> logging.Logger:
    """Install file, console and trace handlers once; safe to call repeatedly."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    with _lock:
        if getattr(root, "_logos_configured", False):
            return root
        resolved = level or env_get("LOGOS_LOG_LEVEL", "INFO")
        root.setLevel(resolved)
        root.propagate = False
        formatter = JsonFormatter()
        context_filter = TraceContextFilter()

        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        console.addFilter(context_filter)
        root.addHandler(console)

        target = directory or log_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                target / DEFAULT_LOG_FILE,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUPS,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(context_filter)
            root.addHandler(file_handler)
        except OSError:
            root.warning("system log file unavailable; using console only")

        mirror = TraceMirrorHandler()
        mirror.addFilter(context_filter)
        root.addHandler(mirror)
        root._logos_configured = True  # type: ignore[attr-defined]
    return root


def get_logger(name: str = "") -> logging.Logger:
    """Return a child of the ``ROOT_LOGGER_NAME`` logger (configures handlers on first use)."""
    configure()
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}" if name else ROOT_LOGGER_NAME)

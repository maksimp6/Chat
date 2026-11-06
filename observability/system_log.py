"""Centralized process-level system log.

Execution Trace is the source of truth for invocation diagnostics. This module
covers only what a trace cannot: server startup and failures before a trace is
bound. Records keep their real severity and, when a trace is bound to the
current context, carry its ``trace_id``/``invocation_id`` and WARNING+ records
are mirrored into that trace as ``system_log`` events.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from observability.trace_manager import get_current_trace
from observability.trace_security import redact_text

ROOT_LOGGER_NAME = "logos"
DEFAULT_LOG_FILE = "system.jsonl"
# One page of the log window; older records come page by page, so all of them can be read.
PAGE_ENTRIES = 200
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 3
_READ_BLOCK = 256 * 1024
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
# The log window asks for new records every few seconds. Its answered requests stay out of the
# plain list, which they would bury; a search finds them, and one that failed stays in the list.
_OWN_POLL = re.compile(r'"GET /api/logs(\?\S*)? HTTP/[\d.]+" 200 ')
_lock = threading.Lock()
_mirroring = threading.local()


def log_dir() -> Path:
    return Path(os.environ.get("LOGOS_LOG_DIR", "logs"))


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
            "timestamp": datetime.fromtimestamp(record.created, UTC).strftime(
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
                {
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                },
            )
        except Exception:
            pass
        finally:
            _mirroring.active = False


def configure(
    level: int | str | None = None, *, directory: Path | None = None
) -> logging.Logger:
    """Install file, console and trace handlers once; safe to call repeatedly."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    with _lock:
        if getattr(root, "_logos_configured", False):
            return root
        resolved = level or os.environ.get("LOGOS_LOG_LEVEL", "INFO")
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


def _log_spans() -> list[tuple[Path, str, int, int]]:
    """The log files, oldest first: path, inode and byte range in the whole history."""
    current = log_dir() / DEFAULT_LOG_FILE
    rotated = [current.with_name(f"{current.name}.{n}") for n in range(_BACKUPS, 0, -1)]
    spans: list[tuple[Path, str, int, int]] = []
    position = 0
    for path in (*rotated, current):
        try:
            stat = path.stat()
        except OSError:
            continue
        spans.append((path, str(stat.st_ino), position, position + stat.st_size))
        position += stat.st_size
    return spans


def _read_span(spans: list[tuple[Path, str, int, int]], start: int, end: int) -> bytes:
    """Bytes ``start`` to ``end`` of the whole history."""
    parts = []
    for path, _inode, first, last in spans:
        low, high = max(start, first), min(end, last)
        if low < high:
            with path.open("rb") as stream:
                stream.seek(low - first)
                parts.append(stream.read(high - low))
    return b"".join(parts)


def _entry(line: bytes, floor: int, needle: str) -> dict[str, Any] | None:
    """The record on ``line``, or None when the filters leave it out."""
    raw = line.decode("utf-8", "replace").strip()
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except ValueError:
        entry = None
    if not isinstance(entry, dict):
        entry = {"message": redact_text(raw)}
    message = str(entry.get("message") or "")
    if _LEVELS.get(str(entry.get("level")), 0) < floor:
        return None
    poll = _OWN_POLL.search(message)
    if not needle:
        return None if poll else entry
    # The search is repeated in the query of the window's own polls, which must not match it.
    query = poll.group(1) if poll else None
    logger = str(entry.get("logger") or "").removeprefix(f"{ROOT_LOGGER_NAME}.")
    held = (
        entry.get("level"),
        logger,
        message.replace(query, "") if query else message,
        entry.get("exception"),
        entry.get("trace_id"),
        entry.get("invocation_id"),
    )
    return entry if needle in " ".join(str(value) for value in held if value).casefold() else None


def read_entries(
    *,
    log: str = "",
    before: int | None = None,
    after: int | None = None,
    level: str = "",
    text: str = "",
) -> dict[str, Any]:
    """Records of the system log, newest first, with the positions to read on from.

    ``after`` gives every record written since that position. Otherwise it is one page of
    ``PAGE_ENTRIES`` records ending at ``before`` (the end of the log when absent); ``before`` in
    the answer ends the next older page and is None at the start of the log. ``level`` keeps that
    severity and above, ``text`` keeps records that hold it, in any case.

    Positions count bytes through the rotated files and the current one, oldest first. They hold
    while the log grows, and a rotation that drops the oldest file renumbers them. So every
    answer names its history in ``log`` (the oldest file's inode, as text: a browser would round
    a large number), and a position sent with another ``log`` gets ``{"reset": True}``: the
    reader starts again from the newest records.
    """
    spans = _log_spans()
    total = spans[-1][3] if spans else 0
    history = spans[0][1] if spans else ""
    if (before is not None or after is not None) and log != history:
        return {"reset": True}
    floor = _LEVELS.get(level.upper(), 0)
    needle = text.casefold()

    if after is not None:
        start = max(0, min(after, total))
        data = _read_span(spans, start, total)
        # A line still being written is read by the next call.
        data = data[: data.rfind(b"\n") + 1]
        found = (_entry(line, floor, needle) for line in reversed(data.splitlines()))
        return {
            "entries": [entry for entry in found if entry],
            "log": history,
            "after": start + len(data),
        }

    tail = _read_span(spans, max(0, total - _READ_BLOCK), total)
    newest = total - len(tail) + tail.rfind(b"\n") + 1
    end = newest if before is None else min(before, newest)
    entries: list[dict[str, Any]] = []
    block = _READ_BLOCK
    while end > 0:
        start = max(0, end - block)
        data = _read_span(spans, start, end)
        if start:
            # The block starts inside a line: that line is read with the next block. A newline
            # at the very end only closes the last line, which then did not fit the block.
            cut = data.find(b"\n", 0, len(data) - 1) + 1
            if not cut:
                block *= 2
                continue
            start, data = start + cut, data[cut:]
        position = end
        for line in reversed(data.splitlines(keepends=True)):
            position -= len(line)
            entry = _entry(line, floor, needle)
            if entry:
                entries.append(entry)
                if len(entries) == PAGE_ENTRIES:
                    return {
                        "entries": entries,
                        "log": history,
                        "before": position or None,
                        "after": newest,
                    }
        end = start
    return {"entries": entries, "log": history, "before": None, "after": newest}


def get_logger(name: str = "") -> logging.Logger:
    """Return a child of the ``ROOT_LOGGER_NAME`` logger (configures handlers on first use)."""
    configure()
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}" if name else ROOT_LOGGER_NAME)

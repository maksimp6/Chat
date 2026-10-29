"""Terminal sessions of the shared tool registry (category ``tty``).

``TTY_TOOLS`` holds six tools: ``tty_open``, ``tty_write``, ``tty_read``,
``tty_resize``, ``tty_close`` and ``tty_list``. A session is one shell on a
pseudo-terminal of the host that runs the Logos backend, started as the app
user: no su, no sudo, no root escalation. Opening a session needs the owner's
approval unless the built-in approval gates are lifted (``restrictions_lifted``,
the owner's decision, #1706); the other tools need the caller to be the
session's owner and no second approval.

Sessions live in this process only. A background reader keeps each
pseudo-terminal drained into a buffer that ``tty_read`` takes from. The atexit
hook closes the sessions still open; ``tty_close`` closes one.

The PTY path has not been verified on the Redmi (Termux) yet.
"""

from __future__ import annotations

import atexit
import codecs
import fcntl
import logging
import os
import selectors
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid
from collections.abc import Mapping
from typing import Any

from tool_providers._vendor.restrictions import restrictions_lifted
from tool_providers.schema import object_schema

logger = logging.getLogger("logos.tool_providers.tty")

_SESSIONS: dict[str, "TtySession"] = {}
_SESSIONS_LOCK = threading.Lock()
# Process-wide one-time step: the exit hook, registered with the first session.
_PROCESS_STATE: dict[str, bool] = {"atexit": False}

# How long a blocked reader or writer waits before it checks its session again.
_WAIT_SECONDS = 0.2
_READ_BYTES = 65536
# The largest window size the kernel's winsize structure can carry.
_MAX_WINDOW = 65535
_ENV_NAMES = ("PATH", "HOME", "LANG", "LC_ALL", "USER")
# local_agent is left out: the local agent runs jobs with approved=True, which would skip the
# owner's approval for opening a terminal.
_TRANSPORTS = ["responses_api", "mcp"]
# Runs first in the child. start_new_session already made the child a session
# leader, so the shim only makes the pseudo-terminal its controlling terminal
# and then replaces itself with the shell.
_SHIM = (
    "import fcntl, os, sys, termios\n"
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "os.execvpe(sys.argv[1], [sys.argv[1]], os.environ)\n"
)

_APPROVAL_REQUIRED = (
    "Opening a terminal needs the owner's approval: the owner must approve "
    "this call before a shell starts"
)
_NO_OWNER = "The call has no owner, so Logos cannot tell whose terminal this is"
_CLOSED = "The terminal session has ended or was closed"


class TtyUnavailable(Exception):
    """The session cannot take the operation now: its shell ended or it was closed."""


class TtySession:
    """One shell on a pseudo-terminal: its owner, process, output buffer and window size."""

    def __init__(
        self,
        session_id: str,
        owner: str,
        process: subprocess.Popen,
        master_fd: int,
        cwd: str,
        cols: int,
        rows: int,
    ) -> None:
        self.id = session_id
        self.owner = owner
        self.process = process
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.created_at = time.time()
        self.lock = threading.Lock()
        # None once closed. The descriptor is used only while the lock is held,
        # so it is never read or written after it has been closed.
        self.master_fd: int | None = master_fd
        self.buffer = bytearray()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.closed = False
        self.reader = threading.Thread(
            target=self._read_loop, name=f"tty-{session_id[:8]}", daemon=True
        )

    def exit_code(self) -> int | None:
        return self.process.poll()

    def _read_loop(self) -> None:
        """Move the shell's output into the buffer until the session closes or every terminal end is gone."""
        with self.lock:
            fd = self.master_fd
        if fd is None:
            return
        with selectors.DefaultSelector() as selector:
            try:
                selector.register(fd, selectors.EVENT_READ)
            except (OSError, ValueError):
                return
            while True:
                selector.select(_WAIT_SECONDS)
                with self.lock:
                    if self.master_fd is None:
                        return
                    try:
                        chunk = os.read(self.master_fd, _READ_BYTES)
                    except BlockingIOError:
                        continue
                    except OSError:
                        # EIO: every slave end is closed, so the shell and its children have exited.
                        return
                    if not chunk:
                        return
                    self.buffer.extend(chunk)

    def write(self, data: bytes) -> None:
        """Send bytes to the shell's input, waiting while the terminal input is full."""
        if self.process.poll() is not None:
            raise TtyUnavailable("The terminal process has ended")
        view = memoryview(data)
        while view:
            with self.lock:
                fd = self.master_fd
                if fd is None:
                    raise TtyUnavailable(_CLOSED)
                try:
                    written = os.write(fd, view)
                except BlockingIOError:
                    written = 0
                except OSError as exc:
                    raise TtyUnavailable(_CLOSED) from exc
            if written:
                view = view[written:]
            else:
                _wait_ready(fd, selectors.EVENT_WRITE)

    def resize(self, cols: int, rows: int) -> None:
        with self.lock:
            if self.master_fd is None:
                raise TtyUnavailable(_CLOSED)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, _winsize(cols, rows))
            self.cols, self.rows = cols, rows

    def read(self, max_bytes: int | None) -> dict[str, Any]:
        """Take the output buffered since the last read, at most max_bytes when given."""
        with self.lock:
            code = self.process.poll()
            count = (
                len(self.buffer)
                if max_bytes is None
                else min(max_bytes, len(self.buffer))
            )
            chunk = bytes(self.buffer[:count])
            del self.buffer[:count]
            ended = code is not None
            text = self.decoder.decode(chunk, final=ended and not self.buffer)
            return {
                "output": text,
                "ended": ended,
                "exit_code": code,
                "remaining_bytes": len(self.buffer),
            }

    def close(self) -> None:
        """Kill the shell's process group and release the terminal; a second call does nothing."""
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # the group has already exited
            finally:
                fd, self.master_fd = self.master_fd, None
                if fd is not None:
                    os.close(fd)
        self.process.wait()


# ---------------------------------------------------------------------------
# Session store


def get_session(session_id: str) -> TtySession | None:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(session_id)


def list_sessions() -> list[TtySession]:
    with _SESSIONS_LOCK:
        return list(_SESSIONS.values())


def _add_session(session: TtySession) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS[session.id] = session
        if not _PROCESS_STATE["atexit"]:
            _PROCESS_STATE["atexit"] = True
            atexit.register(close_all)
    session.reader.start()


def drop_session(session_id: str) -> bool:
    """Remove a session from the store and close it; False when no such session is stored."""
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(session_id, None)
    if session is None:
        return False
    session.close()
    return True


def close_all() -> None:
    """Close every session (process exit)."""
    with _SESSIONS_LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in sessions:
        try:
            session.close()
        except Exception as exc:
            logger.debug(
                "closing terminal session %s failed: %s", session.id, type(exc).__name__
            )


# ---------------------------------------------------------------------------
# Pseudo-terminal plumbing


def _winsize(cols: int, rows: int) -> bytes:
    return struct.pack("HHHH", rows, cols, 0, 0)


def _wait_ready(fd: int, event: int) -> None:
    """Wait until the descriptor is ready or the wait interval passes."""
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(fd, event)
            selector.select(_WAIT_SECONDS)
    except (OSError, ValueError):
        # The descriptor was closed meanwhile; the caller's next check sees that.
        time.sleep(_WAIT_SECONDS)


def _shell_path() -> str:
    shell = os.environ.get("SHELL", "")
    return shell if os.path.isabs(shell) else "/bin/sh"


def _shell_environment() -> dict[str, str]:
    """Only what a shell needs. The backend's own environment holds provider and deployment secrets."""
    env = {name: os.environ[name] for name in _ENV_NAMES if name in os.environ}
    env["TERM"] = "xterm-256color"
    return env


def _spawn(shell: str, cwd: str, cols: int, rows: int) -> tuple[int, subprocess.Popen]:
    """Start the shell on a new pseudo-terminal; returns the master descriptor and the process."""
    master, slave = os.openpty()
    try:
        fcntl.ioctl(master, termios.TIOCSWINSZ, _winsize(cols, rows))
        os.set_blocking(master, False)
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _SHIM, shell],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=_shell_environment(),
            start_new_session=True,
            close_fds=True,
        )
    except BaseException:
        os.close(master)
        raise
    finally:
        # The child holds its own copies of the slave end; the parent keeps only the master.
        os.close(slave)
    return master, process


# ---------------------------------------------------------------------------
# Caller context and argument checks


def _call_of(cfg: Mapping[str, Any] | None) -> Any:
    context = cfg.get("_universal_context") if isinstance(cfg, Mapping) else None
    return context.get("call") if isinstance(context, Mapping) else None


def _approved(cfg: Mapping[str, Any] | None) -> bool:
    return bool(getattr(_call_of(cfg), "approved", False))


def _text_of(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _owner_of(cfg: Mapping[str, Any] | None) -> str | None:
    """The caller's owner: the call's user_id, else the conversation owner on the execution trace."""
    call = _call_of(cfg)
    if call is None:
        return None
    owner = _text_of(getattr(call, "user_id", None))
    if owner:
        return owner
    metadata = getattr(call, "metadata", None)
    trace = metadata.get("execution_trace") if isinstance(metadata, Mapping) else None
    data = getattr(trace, "trace", None)
    context = data.get("context") if isinstance(data, Mapping) else None
    if not isinstance(context, Mapping):
        return None
    for key in ("owner_id", "user_id"):
        owner = _text_of(context.get(key))
        if owner:
            return owner
    return None


def _failure(message: str, phase: str) -> dict[str, Any]:
    return {"success": False, "error": message, "metadata": {"phase": phase}}


def _integer_arg(args: Mapping[str, Any], name: str) -> int:
    value = args.get(name)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _window_arg(args: Mapping[str, Any], name: str) -> int:
    value = _integer_arg(args, name)
    if not 1 <= value <= _MAX_WINDOW:
        raise ValueError(f"{name} must be between 1 and {_MAX_WINDOW}")
    return value


def _session_id_arg(args: Mapping[str, Any]) -> str:
    value = args.get("session_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("session_id is required")
    return value.strip()


def _cwd_arg(args: Mapping[str, Any]) -> str | None:
    value = args.get("cwd")
    if value is None:
        return None
    if not isinstance(value, str) or not os.path.isabs(value) or not os.path.isdir(value):
        raise ValueError("cwd must be an existing absolute directory")
    return value


def _max_bytes_arg(args: Mapping[str, Any]) -> int | None:
    if args.get("max_bytes") is None:
        return None
    count = _integer_arg(args, "max_bytes")
    if count < 0:
        raise ValueError("max_bytes must not be negative")
    return None if count == 0 else count


def _owned_session(
    args: Mapping[str, Any], cfg: Mapping[str, Any] | None
) -> tuple[TtySession | None, dict[str, Any] | None]:
    """The session named in args when the caller owns it; otherwise the failure to return."""
    try:
        session_id = _session_id_arg(args)
    except ValueError as exc:
        return None, _failure(str(exc), "validation")
    owner = _owner_of(cfg)
    if owner is None:
        return None, _failure(_NO_OWNER, "authorization")
    session = get_session(session_id)
    if session is None:
        return None, _failure(f"Terminal session {session_id} was not found", "execution")
    if session.owner != owner:
        return None, _failure(
            "The terminal session belongs to another owner", "authorization"
        )
    return session, None


# ---------------------------------------------------------------------------
# Tool functions


def tty_open(
    args: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Start a shell on a new pseudo-terminal for the caller.

    The owner's approval is checked only while the built-in approval gates stand:
    with them lifted, as every other gated tool already treats them, tty_open
    runs at once (#1706: the approval card was removed, so an approval could
    never arrive and tty_open always refused)."""
    if not _approved(cfg) and not restrictions_lifted():
        return _failure(_APPROVAL_REQUIRED, "approval_required")
    arguments = dict(args or {})
    try:
        cols = _window_arg(arguments, "cols")
        rows = _window_arg(arguments, "rows")
        requested = _cwd_arg(arguments)
    except ValueError as exc:
        return _failure(str(exc), "validation")
    owner = _owner_of(cfg)
    if owner is None:
        return _failure(_NO_OWNER, "authorization")
    cwd = requested or os.environ.get("HOME") or os.path.expanduser("~")
    shell = _shell_path()
    try:
        master, process = _spawn(shell, cwd, cols, rows)
    except (OSError, ValueError) as exc:
        return _failure(f"Unable to start the terminal: {exc}", "execution")
    session = TtySession(uuid.uuid4().hex, owner, process, master, cwd, cols, rows)
    try:
        _add_session(session)
    except Exception:
        session.close()
        raise
    return {
        "success": True,
        "session_id": session.id,
        "pid": process.pid,
        "shell": shell,
        "cwd": cwd,
        "cols": cols,
        "rows": rows,
    }


def tty_write(
    args: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Send input to a session of the caller: a command line or a control character."""
    arguments = dict(args or {})
    session, failure = _owned_session(arguments, cfg)
    if failure is not None:
        return failure
    data = arguments.get("data")
    if not isinstance(data, str):
        return _failure("data must be a string", "validation")
    try:
        payload = data.encode("utf-8")
    except UnicodeEncodeError as exc:
        return _failure(f"data is not valid UTF-8 text: {exc.reason}", "validation")
    try:
        session.write(payload)
    except TtyUnavailable as exc:
        return _failure(str(exc), "execution")
    return {"success": True, "bytes_written": len(payload)}


def tty_read(
    args: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return the output buffered since the last read, and whether the shell has ended."""
    arguments = dict(args or {})
    session, failure = _owned_session(arguments, cfg)
    if failure is not None:
        return failure
    try:
        max_bytes = _max_bytes_arg(arguments)
    except ValueError as exc:
        return _failure(str(exc), "validation")
    return {"success": True, **session.read(max_bytes)}


def tty_resize(
    args: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Change the window size of a session of the caller."""
    arguments = dict(args or {})
    session, failure = _owned_session(arguments, cfg)
    if failure is not None:
        return failure
    try:
        cols = _window_arg(arguments, "cols")
        rows = _window_arg(arguments, "rows")
    except ValueError as exc:
        return _failure(str(exc), "validation")
    try:
        session.resize(cols, rows)
    except TtyUnavailable as exc:
        return _failure(str(exc), "execution")
    except OSError as exc:
        return _failure(f"Unable to resize the terminal: {exc}", "execution")
    return {"success": True, "cols": cols, "rows": rows}


def tty_close(
    args: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Close a session of the caller: its process group is killed and the terminal released."""
    session, failure = _owned_session(dict(args or {}), cfg)
    if failure is not None:
        return failure
    try:
        drop_session(session.id)
    except OSError as exc:
        return _failure(f"Unable to close the terminal: {exc}", "execution")
    return {
        "success": True,
        "session_id": session.id,
        "exit_code": session.process.returncode,
    }


def tty_list(
    args: Mapping[str, Any] | None = None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """List the caller's sessions with their window size and whether the shell has ended."""
    owner = _owner_of(cfg)
    if owner is None:
        return _failure(_NO_OWNER, "authorization")
    sessions = []
    for session in list_sessions():
        if session.owner != owner:
            continue
        code = session.exit_code()
        sessions.append(
            {
                "session_id": session.id,
                "pid": session.process.pid,
                "cwd": session.cwd,
                "cols": session.cols,
                "rows": session.rows,
                "created_at": int(session.created_at),
                "ended": code is not None,
                "exit_code": code,
            }
        )
    return {"success": True, "sessions": sessions}


# ---------------------------------------------------------------------------
# Tool definitions


def _optional(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _window_schema(description: str) -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": 1,
        "maximum": _MAX_WINDOW,
        "description": description,
    }


_SESSION_ID = {
    "type": "string",
    "minLength": 1,
    "maxLength": 64,
    "description": "Terminal session id from tty_open or tty_list",
}


def _definition(
    *,
    title: str,
    description: str,
    parameters: dict[str, Any],
    risk_level: str,
    read_only: bool,
    requires_approval: bool,
    func: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "parameters": parameters,
        "capabilities": ["tty", "shell", "local_execution"],
        "risk_level": risk_level,
        "read_only": read_only,
        "requires_approval": requires_approval,
        "supported_transports": list(_TRANSPORTS),
        "executor": {"type": "local"},
        "metadata": metadata or {},
        "func": func,
    }


TTY_TOOLS: dict[str, dict[str, Any]] = {
    "tty_open": _definition(
        title="Terminal: open session",
        description=(
            "Start a shell on a new pseudo-terminal of the Logos host, as the app user, "
            "and return its session_id. Send input with "
            "tty_write and read the output with tty_read."
        ),
        parameters=object_schema(
            {
                "cols": _window_schema("Terminal width in columns"),
                "rows": _window_schema("Terminal height in rows"),
                "cwd": _optional(
                    {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4096,
                        "description": "Existing absolute directory to start in; not given: HOME",
                    }
                ),
            },
            ["cols", "rows"],
        ),
        risk_level="high",
        read_only=False,
        requires_approval=True,
        func=tty_open,
    ),
    "tty_write": _definition(
        title="Terminal: write input",
        description=(
            "Send text to a terminal session of the caller, such as a command line ending "
            "in \\n or a control character (\\u0003 is Ctrl+C). No approval of its own: "
            "it works on a session the caller opened."
        ),
        parameters=object_schema(
            {
                "session_id": _SESSION_ID,
                "data": {
                    "type": "string",
                    "description": "Text to send to the terminal, as written",
                },
            },
            ["session_id", "data"],
        ),
        risk_level="high",
        read_only=False,
        requires_approval=False,
        func=tty_write,
        metadata={"trace_redact_arguments": ["data"]},
    ),
    "tty_read": _definition(
        title="Terminal: read output",
        description=(
            "Return the terminal output produced since the last read, decoded as UTF-8 "
            "with invalid bytes replaced, with whether the shell has ended and its exit "
            "code. max_bytes limits one read; not given or 0 reads everything buffered."
        ),
        parameters=object_schema(
            {
                "session_id": _SESSION_ID,
                "max_bytes": _optional(
                    {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Most bytes to return in this read; not given or 0: all",
                    }
                ),
            },
            ["session_id"],
        ),
        risk_level="low",
        read_only=False,
        requires_approval=False,
        func=tty_read,
        metadata={"trace_redact_result_fields": ["output"]},
    ),
    "tty_resize": _definition(
        title="Terminal: resize window",
        description="Change the window size (columns and rows) of a terminal session of the caller.",
        parameters=object_schema(
            {
                "session_id": _SESSION_ID,
                "cols": _window_schema("Terminal width in columns"),
                "rows": _window_schema("Terminal height in rows"),
            },
            ["session_id", "cols", "rows"],
        ),
        risk_level="low",
        read_only=False,
        requires_approval=False,
        func=tty_resize,
    ),
    "tty_close": _definition(
        title="Terminal: close session",
        description=(
            "Close a terminal session of the caller: its process group is killed and the "
            "pseudo-terminal is released."
        ),
        parameters=object_schema({"session_id": _SESSION_ID}, ["session_id"]),
        risk_level="medium",
        read_only=False,
        requires_approval=False,
        func=tty_close,
    ),
    "tty_list": _definition(
        title="Terminal: list sessions",
        description=(
            "List the caller's terminal sessions with their pid, directory, window size, "
            "and whether the shell has ended."
        ),
        parameters=object_schema({}, []),
        risk_level="low",
        read_only=True,
        requires_approval=False,
        func=tty_list,
    ),
}


__all__ = ["TTY_TOOLS"]

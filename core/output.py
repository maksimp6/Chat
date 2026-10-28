"""Command output: bounded reading, text decoding, and byte-bounded text with a visible truncation marker."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from typing import IO, cast


def limit_output(value: str | bytes | None, max_bytes: int = 1048576) -> str:
    if value is None:
        return ""
    text = (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value)
    )
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    suffix = "\n...[output truncated]"
    suffix_bytes = suffix.encode("utf-8")
    budget = max(0, max_bytes - len(suffix_bytes))
    return raw[:budget].decode("utf-8", errors="ignore") + suffix


def newline_text(value: bytes) -> str:
    """Decode command output as subprocess text mode does: universal newlines."""
    text = value.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_output(
    proc: subprocess.Popen[bytes], timeout: float, limit: int
) -> tuple[bytes, bytes]:
    """Read stdout and stderr to the end and wait for the process, keeping the first ``limit`` bytes of each.

    On POSIX the rest is read and dropped, so a large output neither fills memory nor blocks the
    pipe. A deadline passed while reading raises TimeoutExpired with the bytes kept so far, as
    communicate() does.
    Elsewhere pipes cannot be polled, and communicate() reads the output.
    """
    if os.name != "posix":
        return proc.communicate(timeout=timeout)
    # Callers open both pipes; the contract above reads stdout and stderr.
    stdout_pipe = cast("IO[bytes]", proc.stdout)
    stderr_pipe = cast("IO[bytes]", proc.stderr)
    deadline = time.monotonic() + timeout
    kept = {stdout_pipe.fileno(): bytearray(), stderr_pipe.fileno(): bytearray()}
    with selectors.DefaultSelector() as selector:
        for fd in kept:
            selector.register(fd, selectors.EVENT_READ)
        while selector.get_map() and time.monotonic() < deadline:
            for key, _events in selector.select(deadline - time.monotonic()):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fd)
                buffer = kept[key.fd]
                buffer += chunk[: max(0, limit - len(buffer))]
        timed_out = bool(selector.get_map())
    stdout = bytes(kept[stdout_pipe.fileno()])
    stderr = bytes(kept[stderr_pipe.fileno()])
    if not timed_out:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            # As communicate() does: a timeout after both pipes closed carries no output.
            exc.timeout = timeout
            raise
    if timed_out:
        raise subprocess.TimeoutExpired(
            proc.args, timeout, output=stdout, stderr=stderr
        )
    return stdout, stderr

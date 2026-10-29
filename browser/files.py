"""Local files for downloads, captures and uploads.

Destinations are resolved with the same rule as the filesystem tools (through
the browser host port): relative paths are taken from the project root and
absolute paths as given. Every write goes through ``AtomicWriter``: the bytes
land in a hidden temporary file next to the destination and replace it only
when complete, so an interrupted download never leaves a half-written file
under the requested name.
"""

from __future__ import annotations

import email.message
import errno
import hashlib
import logging
import mimetypes
import os
import posixpath
import secrets
import shutil
from urllib.parse import unquote, urlsplit

from browser.errors import (
    E_FILE_EXISTS,
    E_FILE_MISSING,
    E_PATH_IS_DIR,
    E_PATH_REQUIRED,
    BrowserBlocked,
    BrowserError,
    BrowserNotFound,
    disk_full_message,
)
from browser.host import resolve_tool_path
from browser.paging import human_bytes

logger = logging.getLogger("logos.browser.files")

NAME_MAX = 255
_FORBIDDEN_CHARS = set('<>:"|?*')
_COMPOUND_EXTENSIONS = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz", ".tar.lzma")

_EXTENSIONS = {
    "text/html": ".html",
    "application/xhtml+xml": ".xhtml",
    "text/plain": ".txt",
    "text/css": ".css",
    "text/csv": ".csv",
    "text/xml": ".xml",
    "application/xml": ".xml",
    "application/rss+xml": ".xml",
    "application/atom+xml": ".xml",
    "application/json": ".json",
    "application/feed+json": ".json",
    "application/ld+json": ".json",
    "application/javascript": ".js",
    "text/javascript": ".js",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/gzip": ".gz",
    "application/x-gzip": ".gz",
    "application/x-tar": ".tar",
    "application/x-7z-compressed": ".7z",
    "application/vnd.rar": ".rar",
    "application/x-rar-compressed": ".rar",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.android.package-archive": ".apk",
    "application/epub+zip": ".epub",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".weba",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
    "application/wasm": ".wasm",
    "application/octet-stream": ".bin",
}


def _tool_path(path: str) -> str:
    try:
        return resolve_tool_path(path)
    except PermissionError as exc:
        raise BrowserBlocked(str(exc)) from None


def extension_for(content_type: str | None) -> str:
    """File extension (with the dot) for a Content-Type, or "" when unknown."""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if not mime:
        return ""
    if mime in _EXTENSIONS:
        return _EXTENSIONS[mime]
    try:
        guessed = mimetypes.guess_extension(mime, strict=False)
    except Exception:
        guessed = None
    if guessed:
        return guessed
    if mime.endswith("+json"):
        return ".json"
    if mime.endswith("+xml"):
        return ".xml"
    return ""


def _split_extension(name: str) -> tuple[str, str]:
    lowered = name.lower()
    for compound in _COMPOUND_EXTENSIONS:
        if lowered.endswith(compound) and len(name) > len(compound):
            return name[: -len(compound)], name[-len(compound) :]
    stem, ext = os.path.splitext(name)
    if not stem:
        return name, ""
    return stem, ext


def _fit_bytes(text: str, limit: int) -> str:
    """Longest prefix of ``text`` whose UTF-8 form fits ``limit`` bytes."""
    encoded = text.encode("utf-8", "surrogatepass")
    if len(encoded) <= limit:
        return text
    return encoded[: max(0, limit)].decode("utf-8", "ignore")


def _bound_name(stem: str, ext: str, limit: int = NAME_MAX) -> str:
    ext_bytes = len(ext.encode("utf-8", "surrogatepass"))
    if ext_bytes >= limit:
        ext = ""
        ext_bytes = 0
    return _fit_bytes(stem, limit - ext_bytes) + ext


def sanitize_filename(name: str | None) -> str:
    """A safe single path component: no separators, control or reserved characters."""
    text = str(name or "").replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    cleaned = []
    for char in text:
        code = ord(char)
        if code < 32 or code == 127:
            continue
        cleaned.append("_" if char in _FORBIDDEN_CHARS else char)
    result = "".join(cleaned).lstrip(". ").rstrip(". ")
    if not result:
        return "download"
    stem, ext = _split_extension(result)
    bounded = _bound_name(stem, ext)
    return bounded.rstrip(". ") or "download"


def unique_name(directory: str, name: str) -> str:
    """``name`` or ``name (2).ext``, ``name (3).ext``… so it does not exist in ``directory``."""
    candidate = os.path.join(directory, name)
    if not os.path.lexists(candidate):
        return candidate
    stem, ext = _split_extension(name)
    number = 2
    while True:
        suffix = f" ({number})"
        tail = suffix + ext
        if len(tail.encode("utf-8", "surrogatepass")) >= NAME_MAX:
            tail = suffix
        room = NAME_MAX - len(tail.encode("utf-8", "surrogatepass"))
        candidate = os.path.join(directory, _fit_bytes(stem, room) + tail)
        if not os.path.lexists(candidate):
            return candidate
        number += 1


def resolve_target(
    path: str | None,
    *,
    suggested_name: str | None,
    content_type: str | None,
    overwrite: bool,
) -> str:
    """Absolute destination for a write: a file path, or a name inside a directory.

    Directory mode applies when ``path`` ends with a separator or names an existing
    directory: the file name comes from ``suggested_name`` (or "download" plus an
    extension for ``content_type``), with a " (2)" suffix when it exists and
    ``overwrite`` is false.
    """
    raw = str(path or "")
    if not raw.strip():
        raise BrowserError(E_PATH_REQUIRED)
    directory_hint = raw.endswith("/") or raw.endswith(os.sep)
    absolute = _tool_path(raw)
    if directory_hint or os.path.isdir(absolute):
        if os.path.lexists(absolute) and not os.path.isdir(absolute):
            raise BrowserError(
                f"По пути {absolute} находится файл, а не папка; укажите папку или имя файла без / на конце"
            )
        os.makedirs(absolute, exist_ok=True)
        name = sanitize_filename(
            suggested_name or ("download" + extension_for(content_type))
        )
        candidate = os.path.join(absolute, name)
        if os.path.isdir(candidate) or (os.path.lexists(candidate) and not overwrite):
            return unique_name(absolute, name)
        return candidate
    if os.path.isdir(absolute):
        raise BrowserError(E_PATH_IS_DIR.format(path=absolute))
    if os.path.lexists(absolute) and not overwrite:
        raise BrowserError(E_FILE_EXISTS.format(path=absolute))
    parent = os.path.dirname(absolute)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return absolute


def resolve_input_path(path: str | None) -> str:
    """Absolute path of an existing local file to upload."""
    raw = str(path or "")
    if not raw.strip():
        raise BrowserNotFound(E_FILE_MISSING.format(path=raw))
    absolute = _tool_path(raw)
    if not os.path.isfile(absolute):
        raise BrowserNotFound(E_FILE_MISSING.format(path=absolute))
    return absolute


def disk_free(path: str | None) -> int | None:
    """Free bytes on the file system holding ``path`` (its nearest existing ancestor)."""
    current = os.path.abspath(str(path or os.curdir))
    while True:
        if os.path.exists(current):
            try:
                return int(shutil.disk_usage(current).free)
            except OSError:
                return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def disk_full_error(path: str, need: int | None = None) -> BrowserError:
    """E_DISK_FULL for ``path`` with the free space of its file system."""
    free = disk_free(os.path.dirname(path) or path)
    return BrowserError(
        disk_full_message(path, human_bytes(free), human_bytes(need) if need else None)
    )


class AtomicWriter:
    """Write a file through a hidden temporary sibling and replace the target on commit."""

    def __init__(self, final_path: str, *, overwrite: bool) -> None:
        self.final_path = final_path
        self.overwrite = bool(overwrite)
        directory, name = os.path.split(final_path)
        self.directory = directory or os.curdir
        temp_name = f".{_fit_bytes(name, 200)}.logos-part-{secrets.token_hex(6)}"
        self.temp_path = os.path.join(self.directory, temp_name)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self.temp_path, flags, 0o644)
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise disk_full_error(final_path) from None
            raise
        self._file = os.fdopen(fd, "wb")
        self._sha = hashlib.sha256()
        self.bytes = 0
        self._finished = False

    def write(self, data: bytes) -> int:
        if self._finished:
            raise ValueError("AtomicWriter is already finished")
        try:
            self._file.write(data)
        except OSError as exc:
            self.abort()
            if exc.errno == errno.ENOSPC:
                raise disk_full_error(self.final_path) from None
            raise
        self.bytes += len(data)
        self._sha.update(data)
        return len(data)

    def commit(self) -> tuple[int, str]:
        """Flush, fsync and move into place; returns (bytes, sha256 hex)."""
        if self._finished:
            raise ValueError("AtomicWriter is already finished")
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
        except OSError as exc:
            self.abort()
            if exc.errno == errno.ENOSPC:
                raise disk_full_error(self.final_path) from None
            raise
        if not self.overwrite and os.path.lexists(self.final_path):
            self.abort()
            raise BrowserError(E_FILE_EXISTS.format(path=self.final_path))
        try:
            os.replace(self.temp_path, self.final_path)
        except OSError:
            self.abort()
            raise
        self._finished = True
        logger.debug("wrote %s (%d bytes)", self.final_path, self.bytes)
        return self.bytes, self._sha.hexdigest()

    def abort(self) -> None:
        """Drop the temporary file; safe to call more than once."""
        self._finished = True
        try:
            self._file.close()
        except OSError:
            pass
        try:
            os.unlink(self.temp_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("could not remove %s: %s", self.temp_path, exc)

    def __enter__(self) -> "AtomicWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._finished:
            self.abort()


def _repair_latin1(name: str) -> str:
    """Header values arrive decoded as latin-1; recover a UTF-8 file name sent raw."""
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def filename_from_response(
    content_disposition: str | None, url: str | None, content_type: str | None
) -> tuple[str, str]:
    """File name for a response and its source: content-disposition, url or generated."""
    if content_disposition:
        message = email.message.Message()
        try:
            message["content-disposition"] = content_disposition
            name = message.get_filename()
        except Exception:
            name = None
        if name:
            name = _repair_latin1(str(name)).strip()
            if name:
                return sanitize_filename(name), "content-disposition"
    if url:
        try:
            path = urlsplit(str(url)).path
        except ValueError:
            path = ""
        base = posixpath.basename(unquote(path or "")).replace("\\", "/")
        base = base.rsplit("/", 1)[-1]
        if base.strip(". "):
            name = sanitize_filename(base)
            if not os.path.splitext(name)[1]:
                ext = extension_for(content_type)
                if ext and ext != ".bin":
                    name = sanitize_filename(name + ext)
            return name, "url"
    return sanitize_filename("download" + extension_for(content_type)), "generated"


__all__ = [
    "AtomicWriter",
    "NAME_MAX",
    "disk_free",
    "disk_full_error",
    "extension_for",
    "filename_from_response",
    "resolve_input_path",
    "resolve_target",
    "sanitize_filename",
    "unique_name",
]

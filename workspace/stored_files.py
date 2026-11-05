"""The file manager's files: kept on the phone and written into the vector database.

Logos has no Yandex API (the owner deleted the Yandex project on 05.11.2026), so files no
longer go to Yandex Files and its vector stores. A file is kept in the folder ``files``
next to the memory file, in the state folder a deploy keeps: ``<id>`` holds its bytes
and ``<id>.json`` its record ``{id, filename, bytes, created_at, purpose, vectors}``. The
id is 32 hex digits; a file sent in pieces keeps the id of its upload, so a completion
repeated after a stop in the middle finds the file it already kept.

Every file is a document of the vector database collection ``files`` (AGENTS.md: all data
of the app is also in vector_db), so Logos finds it by meaning with ``remember`` and
``vectors``. The document id names the file, ``<filename> (<id>)``, because a found chunk
shows its id and not its metadata. The text is the file's own for the text formats, the
paragraphs of a .docx and the rows of an .xlsx. Logos has no PDF reader (no parser
library), so a PDF's document holds only its name and size, as does a file whose text
cannot be read.

Ollama on the owner's PC embeds the chunks, which takes minutes for a big file, so that
runs in a thread, one file at a time, and ``vectors`` in the record says how far it got:
``{"state": "indexing"}``, then ``{"state": "done", "id", "chunks", "text": "full" |
"name_only"}`` or ``{"state": "failed", "error"}``. A file that is not in the vector
database and has no thread of this process on it (its embedding failed, or the process
restarted meanwhile) is embedded again whenever the list is read; the error of the last
attempt stays in ``last_error`` until it succeeds.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from workspace.host import bound

__all__ = ["COLLECTION", "data_path", "get", "keep", "keep_bytes", "listing", "remove"]

logger = logging.getLogger("logos.workspace.stored_files")

COLLECTION = "files"
_ID = re.compile(r"[0-9a-f]{32}")
_PLAIN = {".txt", ".md", ".csv", ".json", ".jsonl"}
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_SHEET_NAME = re.compile(r"xl/worksheets/sheet(\d+)\.xml")
# Records change and threads start under this lock; one file is embedded at a time.
_lock = threading.Lock()
_embedding = threading.Lock()
_indexing: set[str] = set()


def _root() -> Path:
    return bound.memory_file_path().parent / "files"


def _paths(file_id: str) -> tuple[Path, Path]:
    root = _root()
    return root / file_id, root / f"{file_id}.json"


def _valid(file_id: Any) -> bool:
    return isinstance(file_id, str) and bool(_ID.fullmatch(file_id))


def _read(file_id: str) -> dict[str, Any] | None:
    _, record_path = _paths(file_id)
    try:
        record = json.loads(record_path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _write(record: dict[str, Any]) -> None:
    _, record_path = _paths(record["id"])
    temporary = record_path.with_name(f".{record_path.name}.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), "utf-8")
    os.replace(temporary, record_path)


def _doc_id(record: dict[str, Any]) -> str:
    return f"{record['filename']} ({record['id']})"


def keep(source: Path, filename: str, *, file_id: str, purpose: str) -> dict[str, Any]:
    """Moves ``source`` into the store as the file ``file_id`` and starts its embedding.

    A file already kept under this id answers its record.
    """
    if not _valid(file_id):
        raise ValueError("file id must be 32 hex digits")
    filename = os.path.basename(str(filename or "")) or "file"
    _root().mkdir(mode=0o700, parents=True, exist_ok=True)
    data, _ = _paths(file_id)
    with _lock:
        record = _read(file_id)
        if record is None:
            if not data.exists():
                # Moved under a temporary name first, so a stop in the middle of a copy leaves no half file as the
                # file. A source that is gone was moved whole before such a stop (a move removes it last).
                temporary = data.with_name(f".{file_id}.tmp")
                if source.exists():
                    shutil.move(str(source), temporary)
                os.replace(temporary, data)
            record = {
                "id": file_id,
                "filename": filename,
                "bytes": data.stat().st_size,
                "created_at": int(time.time()),
                "purpose": purpose,
                "vectors": {"state": "indexing"},
            }
            _write(record)
            logger.info("[FILES] Kept '%s' (%d bytes) as %s", filename, record["bytes"], file_id)
    _start(file_id)
    return record


def keep_bytes(content: bytes, filename: str, *, purpose: str) -> dict[str, Any]:
    """Keeps ``content`` as a new file and starts its embedding."""
    file_id = uuid.uuid4().hex
    _root().mkdir(mode=0o700, parents=True, exist_ok=True)
    data, _ = _paths(file_id)
    temporary = data.with_name(f".{file_id}.upload")
    try:
        temporary.write_bytes(content)
        return keep(temporary, filename, file_id=file_id, purpose=purpose)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def get(file_id: Any) -> dict[str, Any] | None:
    return _read(file_id) if _valid(file_id) else None


def data_path(file_id: Any) -> Path | None:
    """Where the bytes of a kept file are; None for an id that is not kept."""
    if get(file_id) is None:
        return None
    data, _ = _paths(file_id)
    return data


def listing() -> list[dict[str, Any]]:
    """Every kept file, newest first; a file not yet in the vector database is embedded again."""
    try:
        names = os.listdir(_root())
    except FileNotFoundError:
        return []
    records = []
    for name in names:
        file_id = name[: -len(".json")] if name.endswith(".json") else ""
        record = _read(file_id) if _valid(file_id) else None
        if record is not None:
            records.append(record)
    for record in records:
        if (record.get("vectors") or {}).get("state") != "done":
            started = _start(record["id"])
            if started is not None:
                record["vectors"] = started
    records.sort(key=lambda record: record.get("created_at") or 0, reverse=True)
    return records


def remove(file_id: Any) -> bool:
    """Deletes a kept file and its document of the vector database; False when it is not kept."""
    if not _valid(file_id):
        return False
    with _lock:
        record = _read(file_id)
        if record is None:
            return False
        data, record_path = _paths(file_id)
        # The record goes first: from now on nobody reads the file as kept.
        record_path.unlink()
        try:
            data.unlink()
        except FileNotFoundError:
            pass
    _forget(record)
    logger.info("[FILES] Deleted '%s' (%s)", record.get("filename"), file_id)
    return True


def _forget(record: dict[str, Any]) -> None:
    """Drops the file's document from the vector database (a thread still embedding it drops it once it ends)."""
    try:
        bound.vector_delete(COLLECTION, _doc_id(record))
    except Exception as error:
        logger.error("[FILES] Document of %s not dropped from the vector database: %s", record.get("id"), error)


def _start(file_id: str) -> dict[str, Any] | None:
    """Starts embedding ``file_id`` unless a thread of this process is on it; the new ``vectors``, or None."""
    with _lock:
        if file_id in _indexing:
            return None
        record = _read(file_id)
        if record is None:
            return None
        previous = record.get("vectors") or {}
        vectors: dict[str, Any] = {"state": "indexing"}
        last_error = previous.get("error") or previous.get("last_error")
        if last_error:
            vectors["last_error"] = last_error
        if previous != vectors:
            record["vectors"] = vectors
            _write(record)
        _indexing.add(file_id)
    threading.Thread(target=_index, args=(file_id,), name=f"logos-file-vectors-{file_id[:8]}", daemon=True).start()
    return vectors


def _index(file_id: str) -> None:
    result: dict[str, Any] | None = None
    vectors: dict[str, Any] = {"state": "failed", "error": "the embedding was stopped"}
    try:
        with _embedding:
            record = _read(file_id)
            if record is None:
                return
            data, _ = _paths(file_id)
            text, kind = _document(data, record)
            result = bound.vector_add(
                COLLECTION,
                text,
                doc_id=_doc_id(record),
                metadata={
                    "source": "file_manager",
                    "file_id": file_id,
                    "filename": record["filename"],
                    "bytes": record["bytes"],
                },
            )
        vectors = {"state": "done", "id": result["id"], "chunks": result["chunks"], "text": kind}
    except Exception as error:
        # Ollama not reachable, a refusal, a file that went: the list shows it and the next read tries again.
        logger.warning("[FILES] %s not written into the vector database: %s", file_id, error)
        vectors = {"state": "failed", "error": str(error)[:300] or type(error).__name__}
    finally:
        still_kept = _finish(file_id, vectors)
    if not still_kept and result is not None:
        # The file was deleted while it was embedded: the document it left goes too.
        try:
            bound.vector_delete(COLLECTION, result["id"])
        except Exception as error:
            logger.error("[FILES] Document of deleted %s not dropped: %s", file_id, error)


def _finish(file_id: str, vectors: dict[str, Any]) -> bool:
    """Records how the embedding ended; False when the file was deleted meanwhile."""
    with _lock:
        _indexing.discard(file_id)
        record = _read(file_id)
        if record is None:
            return False
        record["vectors"] = vectors
        try:
            _write(record)
        except OSError as error:
            logger.error("[FILES] Record of %s not written after its embedding: %s", file_id, error)
        return True


def _document(data: Path, record: dict[str, Any]) -> tuple[str, str]:
    """The text the vector database keeps for a file, and whether it is the file's text or only its name."""
    name = record["filename"]
    suffix = os.path.splitext(name)[1].lower()
    head = f"Файл «{name}» из менеджера файлов Logos, {record['bytes']} байт."
    try:
        body = _text(data, suffix)
    except (ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        logger.warning("[FILES] Text of %s not read: %s", record["id"], error)
        body = ""
    if body.strip():
        return f"{head}\n\n{body}", "full"
    if suffix == ".pdf":
        return f"{head} Текст PDF не прочитан: у Logos нет программы для чтения PDF.", "name_only"
    return f"{head} Текст файла не прочитан.", "name_only"


def _text(data: Path, suffix: str) -> str:
    if suffix in _PLAIN:
        return data.read_text("utf-8", errors="replace")
    if suffix == ".html":
        page = _PageText()
        page.feed(data.read_text("utf-8", errors="replace"))
        page.close()
        return "".join(page.parts)
    if suffix == ".docx":
        with zipfile.ZipFile(data) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        return "\n\n".join(
            "".join(node.text or "" for node in paragraph.iter(_WORD + "t"))
            for paragraph in root.iter(_WORD + "p")
        )
    if suffix == ".xlsx":
        return _sheets(data)
    return ""


def _sheets(data: Path) -> str:
    """The rows of every sheet, one row per paragraph with its cells apart by tabs."""
    rows = []
    with zipfile.ZipFile(data) as archive:
        names = archive.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")).iter(_SHEET + "si"):
                shared.append("".join(node.text or "" for node in item.iter(_SHEET + "t")))
        numbered = [(int(match.group(1)), name) for name in names if (match := _SHEET_NAME.fullmatch(name))]
        for _, name in sorted(numbered):
            for row in ElementTree.fromstring(archive.read(name)).iter(_SHEET + "row"):
                cells = [_cell(cell, shared) for cell in row.iter(_SHEET + "c")]
                if any(cells):
                    rows.append("\t".join(cells))
    return "\n\n".join(rows)


def _cell(cell: ElementTree.Element, shared: list[str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_SHEET + "t"))
    value = cell.find(_SHEET + "v")
    text = value.text if value is not None and value.text else ""
    if cell.get("t") == "s" and text.isdigit() and int(text) < len(shared):
        return shared[int(text)]
    return text


class _PageText(HTMLParser):
    """The text of an HTML page: blocks apart as paragraphs, scripts and styles left out."""

    _BLOCKS = {
        "address", "article", "blockquote", "br", "dd", "div", "dt", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "footer", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._hidden += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._hidden = max(0, self._hidden - 1)
        elif tag in self._BLOCKS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)

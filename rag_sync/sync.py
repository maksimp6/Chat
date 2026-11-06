"""Append new git history and GitHub data to the knowledge store; nothing is rebuilt.

Each sync reads only what is newer than the ledger's cursors (rag_sync.git_source,
rag_sync.github_source), embeds every record into the vector database as it is read and
writes it into new files ``logos-rag-<kind>-<time>-NNN.md`` of at most PACK_LIMIT bytes.
A full file moves that source's cursor past the records it holds, then goes to Files and
the knowledge store. A file that fails ends the sync, and the next sync goes on after its
records: they are in the vector database and are not embedded again.

The ledger (Kristall ``rag_sync/ledger``) holds the cursors and every file written; the
state of the running or last sync is ``rag_sync/state``. One sync runs at a time, in a
background thread: a whole history takes longer than one HTTP request.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from rag_sync import git_source, github_source
from rag_sync.host import bound, get_store, knowledge_vector_store_ids, vector_add

__all__ = ["PACK_LIMIT", "OLD_PREFIXES", "PREFIX", "reset", "start", "status"]

log = logging.getLogger("logos.rag_sync")

LEDGER = "rag_sync/ledger"
STATE = "rag_sync/state"
PREFIX = "logos-rag-"
# The packs of the scripts this unit replaced; reset removes them with this unit's own.
OLD_PREFIXES = ("gh-", "chat-repository-archive-")
# The pack size the replaced ingest used (scripts/rag_ingest_via_app.py PACK_LIMIT).
PACK_LIMIT = 4 * 1024 * 1024
_SEPARATOR = "\n\n---\n\n"

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ledger() -> dict[str, Any]:
    ledger = get_store().get(LEDGER)
    return ledger if isinstance(ledger, dict) else {"cursors": {}, "edges": {}, "files": []}


def _save_ledger(ledger: dict[str, Any]) -> None:
    with get_store().transaction() as tx:
        tx.set(LEDGER, ledger)


def _set_state(**fields: Any) -> None:
    with get_store().transaction() as tx:
        current = tx.get(STATE)
        tx.set(STATE, {**(current if isinstance(current, dict) else {}), **fields, "updated_at": int(time.time())})


def _client():
    return bound.YandexResponsesClient(bound.Config)


def _store_id() -> str:
    return knowledge_vector_store_ids()[0]


class _Writer:
    """Packs records of one source into files; each full file is stored before the next is begun."""

    def __init__(self, kind: str, client, store_id: str, ledger: dict, totals: dict):
        self.kind, self.client, self.store_id, self.ledger, self.totals = kind, client, store_id, ledger, totals
        self.stamp, self.part = _now(), 0
        self.parts: list[str] = []
        self.size = 0
        self.last: tuple[str, list[str]] | None = None  # cursor and the keys stored at it

    def add(self, cursor: str, key: str, text: str) -> None:
        # Every record goes into the app's own vector database (vector_db) as it is read,
        # one document per record; the same key replaces its document, so a record
        # written again is not doubled.
        embedded = vector_add(
            f"repository-{self.kind}", text, key.replace("/", "_"), {"kind": self.kind, "key": key}
        )
        self.totals["embedded_records"] = self.totals.get("embedded_records", 0) + 1
        self.totals["embedded_chunks"] += embedded["chunks"]
        self.totals["embedding_tokens"] += embedded["tokens"]
        _set_state(totals=dict(self.totals), stage=f"{self.kind}: {key} embedded")
        encoded = len(text.encode()) + len(_SEPARATOR)
        if self.parts and self.size + encoded > PACK_LIMIT:
            self.flush()
        self.parts.append(text)
        self.size += encoded
        if self.last and self.last[0] == cursor:
            self.last[1].append(key)
        else:
            self.last = (cursor, [key])

    def flush(self) -> None:
        if not self.parts:
            return
        self.part += 1
        name = f"{PREFIX}{self.kind}-{self.stamp}-{self.part:03d}.md"
        body = (f"# {self.kind} · {github_source.repository()}\n\n" + _SEPARATOR.join(self.parts) + "\n").encode()
        cursor, keys = self.last
        self.ledger["cursors"][self.kind] = cursor
        self.ledger["edges"][self.kind] = keys
        _save_ledger(self.ledger)
        stored = self.client.upload_file(body, name, purpose="assistants")
        file_id = stored.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise RuntimeError(f"Files returned no id for {name}")
        self.client.add_file_to_vs(self.store_id, file_id)
        self.ledger["files"].append(
            {"file_id": file_id, "name": name, "kind": self.kind, "records": len(self.parts),
             "bytes": len(body), "cursor": cursor, "at": int(time.time())}
        )
        _save_ledger(self.ledger)
        self.totals["files"] += 1
        self.totals["records"] += len(self.parts)
        self.parts, self.size = [], 0
        _set_state(totals=dict(self.totals), stage=f"{self.kind}: {name} stored")


def _sync_git(client, store_id: str, ledger: dict, totals: dict) -> None:
    root = git_source.repository_root()
    writer = _Writer("git", client, store_id, ledger, totals)
    since = ledger["cursors"].get("git")
    _set_state(stage="git: listing new commits")
    for sha, text, masked in git_source.records(root, since):
        totals["masked"] += masked
        totals["read_records"] = totals.get("read_records", 0) + 1
        _set_state(totals=dict(totals), stage=f"git: {sha[:12]} read ({len(text)} chars)")
        writer.add(sha, sha, text)
    writer.flush()


def _sync_github(client, store_id: str, ledger: dict, totals: dict, token: str | None) -> None:
    repo = github_source.repository()
    for kind in github_source.KINDS:
        writer = _Writer(kind, client, store_id, ledger, totals)
        cursor = ledger["cursors"].get(kind)
        edge = set(ledger["edges"].get(kind) or [])
        try:
            _set_state(stage=f"{kind}: listing since {cursor or 'the beginning'}")
            for at, key, text, masked in github_source.records(kind, repo, cursor, edge, token):
                totals["masked"] += masked
                totals["read_records"] = totals.get("read_records", 0) + 1
                writer.add(at, key, text)
        finally:
            # What was read before a rate limit or an error is still stored.
            writer.flush()


def _run(token: str | None, sources: tuple[str, ...]) -> None:
    totals = {"files": 0, "records": 0, "masked": 0, "embedded_chunks": 0, "embedding_tokens": 0}
    try:
        client, store_id = _client(), _store_id()
        ledger = _ledger()
        ledger.setdefault("cursors", {}), ledger.setdefault("edges", {}), ledger.setdefault("files", [])
        _set_state(running=True, started_at=int(time.time()), finished_at=None, error=None,
                   totals=totals, store_id=store_id, stage="started")
        if "git" in sources:
            _sync_git(client, store_id, ledger, totals)
        if "github" in sources:
            _sync_github(client, store_id, ledger, totals, token or github_source.env_token())
        _set_state(running=False, finished_at=int(time.time()), totals=totals, stage="done")
    except github_source.RateLimited as exc:
        _set_state(running=False, finished_at=int(time.time()), totals=totals, stage="rate_limited", error=str(exc))
    except Exception as exc:
        log.exception("rag sync failed")
        _set_state(running=False, finished_at=int(time.time()), totals=totals, stage="failed",
                   error=f"{type(exc).__name__}: {exc}")
    finally:
        _lock.release()


def start(token: str | None = None, sources: tuple[str, ...] = ("git", "github")) -> dict[str, Any]:
    """Start a sync in the background; a sync already running is reported, not doubled."""
    if not _lock.acquire(blocking=False):
        return {"started": False, "reason": "sync_running", **status()}
    _set_state(running=True, stage="starting", error=None)
    threading.Thread(target=_run, args=(token, sources), name="logos-rag-sync", daemon=True).start()
    return {"started": True, **status()}


def status() -> dict[str, Any]:
    ledger = _ledger()
    state = get_store().get(STATE)
    return {
        "state": state if isinstance(state, dict) else {},
        "cursors": ledger.get("cursors", {}),
        "files": len(ledger.get("files", [])),
        "last_files": ledger.get("files", [])[-5:],
    }


def _all_files(client) -> list[dict]:
    rows, after = [], None
    while True:
        page = client.list_files(limit=100, after=after)
        batch = page.get("data", []) if isinstance(page, dict) else []
        rows += batch
        if not (isinstance(page, dict) and page.get("has_more")) or not batch:
            return rows
        after = page.get("last_id") or batch[-1].get("id")


def _gone(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 404


def reset() -> dict[str, Any]:
    """Remove every pack this unit and the scripts it replaced stored, and forget the cursors.

    The next sync then appends the whole history again, in this unit's format."""
    if not _lock.acquire(blocking=False):
        return {"reset": False, "reason": "sync_running", **status()}
    try:
        client, store_id = _client(), _store_id()
        removed, failed = [], []
        for row in _all_files(client):
            name, file_id = str(row.get("filename") or ""), row.get("id")
            if not isinstance(file_id, str) or not name.startswith((PREFIX, *OLD_PREFIXES)):
                continue
            try:
                try:
                    client.remove_file_from_vs(store_id, file_id)
                except bound.YandexClientError as exc:
                    if not _gone(exc):
                        raise
                try:
                    client.delete_file(file_id)
                except bound.YandexClientError as exc:
                    if not _gone(exc):
                        raise
                removed.append(name)
            except bound.YandexClientError as exc:
                failed.append({"name": name, "error": str(exc)})
        if not failed:
            _save_ledger({"cursors": {}, "edges": {}, "files": []})
        _set_state(running=False, stage="reset" if not failed else "reset_incomplete",
                   error=None if not failed else f"{len(failed)} file(s) not removed")
        return {"reset": not failed, "removed": len(removed), "failed": failed}
    finally:
        _lock.release()

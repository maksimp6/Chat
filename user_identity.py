"""Durable Alice user identity backed by the file-native Memory DB.

The public API stays stable while users, GitHub links and token hashes are stored
as one atomic identity aggregate. Legacy SQL is consulted only by the bounded
migration module before the first durable aggregate is committed.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from threading import RLock
import time
import uuid
from typing import Any, Mapping, Optional

from agent_memory.runtime_store import get_runtime_memory_db
from agent_memory.user_identity_migration import ensure_user_identity_migrated
from agent_memory.user_identity_store import (
    GitHubAccountRecord,
    IdentityState,
    UserIdentityStore,
    UserRecord,
)
from db import memory_file_path
from db_backend import IntegrityError

_INSTALLATION_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)",
    re.IGNORECASE,
)
_IDENTITY_LOCK = RLock()


def _now() -> int:
    return int(time.time())


def _hash_auth_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_auth_token() -> str:
    return secrets.token_urlsafe(32)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    return value


def _store() -> UserIdentityStore:
    store = UserIdentityStore(get_runtime_memory_db(memory_file_path()))
    ensure_user_identity_migrated(store)
    return store


def _state() -> tuple[UserIdentityStore, IdentityState]:
    store = _store()
    state = store.load()
    if state is None:
        raise RuntimeError("user identity migration did not produce durable state")
    return store, state


def init_user_identity_table() -> None:
    with _IDENTITY_LOCK:
        _store()


def init_github_accounts_table() -> None:
    with _IDENTITY_LOCK:
        _store()


def _find_user_by_installation(
    state: IdentityState, installation_id: str
) -> tuple[str, UserRecord] | None:
    return next(
        (
            (user_id, record)
            for user_id, record in state["users"].items()
            if record["installation_id"] == installation_id
        ),
        None,
    )


def _find_user_by_token_hash(
    state: IdentityState, token_hash: str
) -> tuple[str, UserRecord] | None:
    return next(
        (
            (user_id, record)
            for user_id, record in state["users"].items()
            if record["auth_token_hash"] == token_hash
        ),
        None,
    )


def register_anonymous_user(
    installation_id: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    installation_id = str(installation_id or "").strip()
    if not _INSTALLATION_RE.fullmatch(installation_id):
        raise ValueError("invalid installation_id")

    sanitized = _sanitize(dict(metadata or {}))
    now = _now()
    auth_token = _new_auth_token()
    auth_token_hash = _hash_auth_token(auth_token)

    with _IDENTITY_LOCK:
        store, state = _state()
        existing = _find_user_by_installation(state, installation_id)
        if existing is None:
            user_id = str(uuid.uuid4())
            state["users"][user_id] = {
                "installation_id": installation_id,
                "status": "anonymous",
                "metadata_json": json.dumps(sanitized, ensure_ascii=False),
                "auth_token_hash": auth_token_hash,
                "created_at": now,
                "updated_at": now,
            }
            store.save(state)
            return {
                "user_id": user_id,
                "installation_id": installation_id,
                "status": "anonymous",
                "new_user": True,
                "created_at": now,
                "auth_token": auth_token,
            }

        user_id, record = existing
        if record["status"] != "anonymous":
            raise ValueError("installation is linked to a signed-in account")

        record["metadata_json"] = json.dumps(sanitized, ensure_ascii=False)
        record["auth_token_hash"] = auth_token_hash
        record["updated_at"] = now
        store.save(state)
        return {
            "user_id": user_id,
            "installation_id": installation_id,
            "status": record["status"],
            "new_user": False,
            "created_at": record["created_at"],
            "auth_token": auth_token,
        }


def authenticate_user_token(token: str) -> Optional[str]:
    init_user_identity_table()
    return lookup_user_token(token)


def lookup_user_token(token: str) -> Optional[str]:
    token = str(token or "").strip()
    if not token:
        return None

    with _IDENTITY_LOCK:
        _store_object, state = _state()
        found = _find_user_by_token_hash(state, _hash_auth_token(token))
        return found[0] if found is not None else None


def get_anonymous_user(user_id: str) -> Optional[dict[str, Any]]:
    with _IDENTITY_LOCK:
        _store_object, state = _state()
        record = state["users"].get(str(user_id))
        if record is None:
            return None

        try:
            metadata = json.loads(record["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}

        return {
            "user_id": str(user_id),
            "installation_id": record["installation_id"],
            "status": record["status"],
            "metadata": metadata,
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }


def _github_installation_id(github_id: str) -> str:
    return f"github-{github_id}-{uuid.uuid4().hex}"


def _linked_github_account(
    state: IdentityState, user_id: str
) -> tuple[str, GitHubAccountRecord] | None:
    return next(
        (
            (github_id, account)
            for github_id, account in state["github_accounts"].items()
            if account["user_id"] == user_id
        ),
        None,
    )


def _link_github_account(
    github_id: str,
    login: str,
    current_user_id: Optional[str],
    auth_token: str,
) -> tuple[str, bool]:
    now = _now()

    with _IDENTITY_LOCK:
        store, state = _state()
        existing_link = state["github_accounts"].get(github_id)
        new_user = False

        user_id: str | None
        if existing_link is not None:
            user_id = existing_link["user_id"]
            existing_link["login"] = login
            existing_link["updated_at"] = now
        else:
            user_id = None
            if current_user_id:
                candidate = state["users"].get(str(current_user_id))
                if (
                    candidate is not None
                    and candidate["status"] == "anonymous"
                    and _linked_github_account(state, str(current_user_id)) is None
                ):
                    candidate["status"] = "github"
                    candidate["installation_id"] = _github_installation_id(github_id)
                    user_id = str(current_user_id)

            if user_id is None:
                user_id = str(uuid.uuid4())
                if user_id in state["users"]:
                    raise IntegrityError("duplicate generated user id")
                new_user = True
                state["users"][user_id] = {
                    "installation_id": _github_installation_id(github_id),
                    "status": "github",
                    "metadata_json": "{}",
                    "auth_token_hash": None,
                    "created_at": now,
                    "updated_at": now,
                }

            state["github_accounts"][github_id] = {
                "user_id": user_id,
                "login": login,
                "created_at": now,
                "updated_at": now,
            }

        user = state["users"].get(user_id)
        if user is None:
            raise ValueError("github account references missing user")
        user["status"] = "github"
        user["auth_token_hash"] = _hash_auth_token(auth_token)
        user["updated_at"] = now
        store.save(state)
        return user_id, new_user


def sign_in_with_github(
    github_id: Any,
    login: str,
    current_user_id: Optional[str] = None,
) -> dict[str, Any]:
    github_id = str(github_id or "").strip()
    login = str(login or "").strip()
    if not github_id.isdigit() or not login:
        raise ValueError("invalid github account")

    auth_token = _new_auth_token()
    try:
        user_id, new_user = _link_github_account(
            github_id,
            login,
            current_user_id,
            auth_token,
        )
    except IntegrityError:
        user_id, new_user = _link_github_account(
            github_id,
            login,
            current_user_id,
            auth_token,
        )
    return {
        "user_id": user_id,
        "github_login": login,
        "new_user": new_user,
        "auth_token": auth_token,
    }


def get_github_login(user_id: str) -> Optional[str]:
    with _IDENTITY_LOCK:
        _store_object, state = _state()
        links = [
            account
            for account in state["github_accounts"].values()
            if account["user_id"] == str(user_id)
        ]
        if not links:
            return None
        latest = max(links, key=lambda account: account["updated_at"])
        return latest["login"]

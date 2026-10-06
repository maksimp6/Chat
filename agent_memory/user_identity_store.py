"""Typed durable aggregate for Alice user identity state."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, TypedDict

from agent_memory.file_memory_db import FileMemoryDB

IDENTITY_KEY = "user_identity:v1"
IDENTITY_SCHEMA = 1


class UserRecord(TypedDict):
    installation_id: str
    status: str
    metadata_json: str
    auth_token_hash: str | None
    created_at: int
    updated_at: int


class GitHubAccountRecord(TypedDict):
    user_id: str
    login: str
    created_at: int
    updated_at: int


class IdentityMigration(TypedDict):
    schema: int
    source: str
    user_count: int
    github_account_count: int
    digest: str


class IdentityState(TypedDict):
    schema: int
    users: dict[str, UserRecord]
    github_accounts: dict[str, GitHubAccountRecord]
    migration: IdentityMigration


def identity_digest(
    users: Mapping[str, UserRecord],
    github_accounts: Mapping[str, GitHubAccountRecord],
) -> str:
    payload = {
        "users": [
            {
                "id": user_id,
                **users[user_id],
            }
            for user_id in sorted(users)
        ],
        "github_accounts": [
            {
                "github_id": github_id,
                **github_accounts[github_id],
            }
            for github_id in sorted(github_accounts)
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class UserIdentityStore:
    def __init__(self, db: FileMemoryDB) -> None:
        self.db = db

    @staticmethod
    def _user_record(raw: object) -> UserRecord:
        if not isinstance(raw, dict):
            raise ValueError("invalid user identity record")
        installation_id = raw.get("installation_id")
        status = raw.get("status")
        metadata_json = raw.get("metadata_json")
        auth_token_hash = raw.get("auth_token_hash")
        created_at = raw.get("created_at")
        updated_at = raw.get("updated_at")
        if (
            not isinstance(installation_id, str)
            or not installation_id
            or status not in {"anonymous", "github"}
            or not isinstance(metadata_json, str)
            or (auth_token_hash is not None and not isinstance(auth_token_hash, str))
            or not isinstance(created_at, int)
            or not isinstance(updated_at, int)
        ):
            raise ValueError("invalid user identity record")
        return {
            "installation_id": installation_id,
            "status": status,
            "metadata_json": metadata_json,
            "auth_token_hash": auth_token_hash,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    @staticmethod
    def _github_record(raw: object) -> GitHubAccountRecord:
        if not isinstance(raw, dict):
            raise ValueError("invalid github identity record")
        user_id = raw.get("user_id")
        login = raw.get("login")
        created_at = raw.get("created_at")
        updated_at = raw.get("updated_at")
        if (
            not isinstance(user_id, str)
            or not user_id
            or not isinstance(login, str)
            or not login
            or not isinstance(created_at, int)
            or not isinstance(updated_at, int)
        ):
            raise ValueError("invalid github identity record")
        return {
            "user_id": user_id,
            "login": login,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    @staticmethod
    def _migration(raw: object) -> IdentityMigration:
        if not isinstance(raw, dict):
            raise ValueError("invalid user identity migration marker")
        schema = raw.get("schema")
        source = raw.get("source")
        user_count = raw.get("user_count")
        github_account_count = raw.get("github_account_count")
        digest = raw.get("digest")
        if (
            schema != 1
            or not isinstance(source, str)
            or not source
            or not isinstance(user_count, int)
            or user_count < 0
            or not isinstance(github_account_count, int)
            or github_account_count < 0
            or not isinstance(digest, str)
            or not digest
        ):
            raise ValueError("invalid user identity migration marker")
        return {
            "schema": schema,
            "source": source,
            "user_count": user_count,
            "github_account_count": github_account_count,
            "digest": digest,
        }

    def load(self) -> IdentityState | None:
        raw = self.db.get(IDENTITY_KEY)
        if raw is None:
            return None
        if not isinstance(raw, dict) or raw.get("schema") != IDENTITY_SCHEMA:
            raise ValueError("invalid user identity aggregate")

        raw_users = raw.get("users")
        raw_accounts = raw.get("github_accounts")
        if not isinstance(raw_users, dict) or not isinstance(raw_accounts, dict):
            raise ValueError("invalid user identity aggregate")

        users = {
            str(user_id): self._user_record(record)
            for user_id, record in raw_users.items()
            if isinstance(user_id, str) and user_id
        }
        accounts = {
            str(github_id): self._github_record(record)
            for github_id, record in raw_accounts.items()
            if isinstance(github_id, str) and github_id
        }
        if len(users) != len(raw_users) or len(accounts) != len(raw_accounts):
            raise ValueError("invalid user identity aggregate")

        state: IdentityState = {
            "schema": IDENTITY_SCHEMA,
            "users": users,
            "github_accounts": accounts,
            "migration": self._migration(raw.get("migration")),
        }
        self._validate_invariants(state)
        return state

    def save(self, state: IdentityState) -> int:
        normalized: IdentityState = {
            "schema": IDENTITY_SCHEMA,
            "users": {
                user_id: self._user_record(record) for user_id, record in state["users"].items()
            },
            "github_accounts": {
                github_id: self._github_record(record)
                for github_id, record in state["github_accounts"].items()
            },
            "migration": self._migration(state["migration"]),
        }
        self._validate_invariants(normalized)
        return self.db.put(IDENTITY_KEY, normalized)

    @staticmethod
    def _validate_user_uniqueness(users: Mapping[str, UserRecord]) -> None:
        installation_ids: set[str] = set()
        token_hashes: set[str] = set()

        for user_id, record in users.items():
            if not user_id:
                raise ValueError("invalid user identity record")
            installation_id = record["installation_id"]
            if installation_id in installation_ids:
                raise ValueError("duplicate user installation id")
            installation_ids.add(installation_id)

            token_hash = record["auth_token_hash"]
            if token_hash is None:
                continue
            if token_hash in token_hashes:
                raise ValueError("duplicate user auth token hash")
            token_hashes.add(token_hash)

    @staticmethod
    def _validate_github_links(
        users: Mapping[str, UserRecord],
        accounts: Mapping[str, GitHubAccountRecord],
    ) -> None:
        linked_users: set[str] = set()
        for account in accounts.values():
            user_id = account["user_id"]
            if user_id not in users:
                raise ValueError("github account references missing user")
            if user_id in linked_users:
                raise ValueError("user has multiple github accounts")
            linked_users.add(user_id)

    @staticmethod
    def _validate_migration_counts(state: IdentityState) -> None:
        migration = state["migration"]
        if migration["user_count"] > len(state["users"]):
            raise ValueError("invalid user identity migration counts")
        if migration["github_account_count"] > len(state["github_accounts"]):
            raise ValueError("invalid user identity migration counts")

    @classmethod
    def _validate_invariants(cls, state: IdentityState) -> None:
        cls._validate_user_uniqueness(state["users"])
        cls._validate_github_links(state["users"], state["github_accounts"])
        cls._validate_migration_counts(state)


def migrated_identity_state(
    users: dict[str, UserRecord],
    github_accounts: dict[str, GitHubAccountRecord],
) -> IdentityState:
    return {
        "schema": IDENTITY_SCHEMA,
        "users": users,
        "github_accounts": github_accounts,
        "migration": {
            "schema": 1,
            "source": "legacy-sql",
            "user_count": len(users),
            "github_account_count": len(github_accounts),
            "digest": identity_digest(users, github_accounts),
        },
    }


__all__ = [
    "GitHubAccountRecord",
    "IDENTITY_KEY",
    "IDENTITY_SCHEMA",
    "IdentityMigration",
    "IdentityState",
    "UserIdentityStore",
    "UserRecord",
    "identity_digest",
    "migrated_identity_state",
]

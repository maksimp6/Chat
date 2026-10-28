"""Typed durable aggregate for Logos user identity state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

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


class IdentityState(TypedDict):
    schema: int
    users: dict[str, UserRecord]
    github_accounts: dict[str, GitHubAccountRecord]


class UserIdentityStore:
    def __init__(self, db: Any) -> None:
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
        }
        self._validate_invariants(state)
        return state

    def save(self, state: IdentityState) -> int:
        normalized: IdentityState = {
            "schema": IDENTITY_SCHEMA,
            "users": {
                user_id: self._user_record(record)
                for user_id, record in state["users"].items()
            },
            "github_accounts": {
                github_id: self._github_record(record)
                for github_id, record in state["github_accounts"].items()
            },
        }
        self._validate_invariants(normalized)
        with self.db.transaction() as tx:
            tx.set(IDENTITY_KEY, normalized)
        return self.db.last_commit.sequence

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

    @classmethod
    def _validate_invariants(cls, state: IdentityState) -> None:
        cls._validate_user_uniqueness(state["users"])
        cls._validate_github_links(state["users"], state["github_accounts"])


__all__ = [
    "IDENTITY_KEY",
    "IDENTITY_SCHEMA",
    "GitHubAccountRecord",
    "IdentityState",
    "UserIdentityStore",
    "UserRecord",
]

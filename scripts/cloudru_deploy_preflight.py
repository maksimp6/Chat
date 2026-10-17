#!/usr/bin/env python3
"""Validate Logos deployment inputs without rendering or sending secret values.

No cloud API calls. --check-db verifies an existing native journal with its writer lock.
This is a configuration check, not a provisioner or a deployment approval gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


def filled(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not re.search(r"REPLACE|CHANGEME|<[^>]+>", value, re.IGNORECASE)
    )


def uuid_ok(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def https_origin(value: object) -> bool:
    if not filled(value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not parsed.path
        )
    except ValueError:
        return False


def database_parameters(path: str, database: dict) -> tuple[dict, list[str]]:
    """Validate native storage scope without opening any database or network."""
    errors = []
    allowed = {
        "engine",
        "path",
        "data_root",
        "writer_processes",
        "persistence_evidence",
        "restore_evidence",
    }
    if set(database) != allowed or database.get("engine") != "memory_engine.MemoryStore":
        errors.append("database: native MemoryStore schema required")
    if type(database.get("writer_processes")) is not int or database.get("writer_processes") != 1:
        errors.append("database: exactly one writer process required")
    if (
        not filled(path)
        or "\x00" in path
        or not Path(path).is_absolute()
        or ".." in Path(path).parts
    ):
        errors.append("ALICE_MEMORY_PATH: absolute canonical path required")
    root = database.get("data_root")
    if not filled(root) or not Path(root).is_absolute() or ".." in Path(root).parts:
        errors.append("database.data_root: absolute persistent root required")
    elif path != database.get("path") or Path(root) not in Path(path).parents:
        errors.append("ALICE_MEMORY_PATH: must match journal inside persistent data_root")
    for field in ("persistence_evidence", "restore_evidence"):
        if not filled(database.get(field)):
            errors.append("database." + field + ": explicit evidence required")
    return {"path": path, "data_root": root}, errors


def validate(config: object, env: dict, *, workflow: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["deployment config must be an object"]
    allowed = {
        "schema_version",
        "project_id",
        "source_commit",
        "image",
        "workflow_application_id",
        "public_origin",
        "container",
        "database",
    }
    if (
        set(config) != allowed
        or type(config.get("schema_version")) is not int
        or config.get("schema_version") != 2
    ):
        errors.append("deployment config: unexpected schema")
    if not uuid_ok(config.get("project_id")):
        errors.append("project_id: real UUID required")
    if not re.fullmatch(r"[0-9a-f]{40}", str(config.get("source_commit", ""))):
        errors.append("source_commit: full master SHA required")
    image = config.get("image", "")
    if not filled(image) or not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
        errors.append("image: registry reference pinned by digest required")
    if not https_origin(config.get("public_origin")):
        errors.append("public_origin: HTTPS origin without path or credentials required")
    container = config.get("container")
    expected = {
        "name": "alice-pro",
        "cpu": "0.5",
        "memory": "1024Mi",
        "port": 8080,
        "min_instances": 0,
        "max_instances": 1,
        "timeout": "300s",
        "idle_timeout": "600s",
    }
    if (
        not isinstance(container, dict)
        or container != expected
        or any(
            type(container.get(k)) is not int for k in ("port", "min_instances", "max_instances")
        )
    ):
        errors.append(
            "container: expected initial Logos profile (8080, 0.5 CPU, 1024Mi, scale 0..1)"
        )
    database = config.get("database")
    if not isinstance(database, dict):
        return errors + ["database: object required"]
    if env.get("ALICE_DATABASE_URL"):
        errors.append("ALICE_DATABASE_URL: retired setting; native MemoryStore required")

    required = (
        "ALICE_SHORT_TOKEN",
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        "ALICE_GITHUB_CLIENT_ID",
        "ALICE_GITHUB_CLIENT_SECRET",
        "ALICE_MEMORY_PATH",
    )
    for name in required:
        if not filled(env.get(name)):
            errors.append(f"{name}: missing or placeholder")
    if env.get("ALICE_REQUIRE_SHORT_TOKEN") != "1":
        errors.append("ALICE_REQUIRE_SHORT_TOKEN: must be 1")
    if not re.fullmatch(r"[1-9][0-9]*(,[1-9][0-9]*)*", env.get("ALICE_GITHUB_ALLOWED_IDS", "")):
        errors.append("ALICE_GITHUB_ALLOWED_IDS: explicit numeric owner IDs required")
    if (
        env.get("ALICE_GITHUB_REDIRECT_URI")
        != str(config.get("public_origin")) + "/auth/github/callback"
    ):
        errors.append("ALICE_GITHUB_REDIRECT_URI: must match public origin callback")
    _, db_errors = database_parameters(env.get("ALICE_MEMORY_PATH", ""), database)
    errors.extend(db_errors)
    if workflow:
        if not uuid_ok(config.get("workflow_application_id")):
            errors.append("workflow_application_id: existing application UUID required")
        if env.get("EDS_PROJECT_ID") != config.get("project_id"):
            errors.append("EDS_PROJECT_ID: must match deployment project")
        if not filled(env.get("EDS_API_KEY")):
            errors.append("EDS_API_KEY: missing or placeholder")
        defaults = {
            "EDS_REPO_API_URL": "https://devtools.api.cloud.ru/repo/api/v1",
            "EDS_REPO_GIT_HOST": "https://repo.cloud.ru/",
            "EDS_WF_API_URL": "https://pipeline.cloud.ru/public-api/v1",
        }
        if any(env.get(k, v) != v for k, v in defaults.items()):
            errors.append("EDS endpoints: this profile requires official production endpoints")
    return errors


def probe_database(path: str, database: dict) -> str | None:
    """Verify an existing native journal under its exclusive writer lock."""
    try:
        _, errors = database_parameters(path, database)
        if errors:
            return "database probe: invalid native storage configuration"
        journal = Path(path)
        root = Path(database["data_root"]).resolve()
        if not journal.is_file() or journal.is_symlink() or root not in journal.resolve().parents:
            return "database probe: existing private journal required"
        from kristall import Kristall

        with Kristall(journal) as store:
            store.info()
    except Exception:  # noqa: BLE001 - secret-safe CLI error boundary
        return "database probe failed; verify journal integrity and exclusive writer ownership"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--workflow", action="store_true", help="also require existing EDS target/env"
    )
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="verify an existing native journal; no network, source replacement or repair",
    )
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        errors = validate(config, dict(os.environ), workflow=args.workflow)
    except Exception:  # noqa: BLE001 - secret-safe CLI error boundary
        errors = ["configuration could not be read or validated"]
    probed = False
    if not errors and args.check_db:
        error = probe_database(os.environ["ALICE_MEMORY_PATH"], config["database"])
        if error:
            errors.append(error)
        else:
            probed = True
    print(
        json.dumps(
            {
                "status": "INVALID" if errors else "CONFIG_VALID",
                "errors": errors,
                "database_probe": "PASSED" if probed else "NOT_PASSED_OR_NOT_RUN",
                "deployment_ready": False,
            }
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

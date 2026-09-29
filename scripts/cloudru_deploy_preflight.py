#!/usr/bin/env python3
"""Validate Alice deployment inputs without rendering or sending secret values.

No cloud API calls. --check-db additionally performs a read-only PostgreSQL probe.
This is a configuration check, not a provisioner or a deployment approval gate.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import ssl
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
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


def database_parameters(dsn: str, database: dict) -> tuple[dict, list[str]]:
    """Support one explicit libpq URI; reject query overrides and duplicates."""
    errors: list[str] = []
    try:
        if (
            not filled(dsn)
            or not dsn.startswith(("postgres://", "postgresql://"))
            or re.search(r"%(?![0-9A-Fa-f]{2})", dsn)
            or "\x00" in dsn
            or re.search(r"%00", dsn, re.IGNORECASE)
        ):
            return {}, ["ALICE_DATABASE_URL: invalid URI"]
        uri = urlsplit(dsn)
        pairs = parse_qsl(uri.query, keep_blank_values=True, strict_parsing=True, errors="strict")
        params = dict(pairs)
        if (
            uri.scheme not in {"postgres", "postgresql"}
            or uri.fragment
            or not uri.password
            or not filled(unquote(uri.password, errors="strict"))
            or uri.hostname != database.get("host")
            or (uri.port or 5432) != database.get("port")
            or unquote(uri.path, errors="strict") != "/" + str(database.get("name"))
            or unquote(uri.username or "", errors="strict") != database.get("user")
        ):
            errors.append("ALICE_DATABASE_URL: explicit endpoint/database/role/password required")
        allowed = {
            "sslmode",
            "sslrootcert",
            "connect_timeout",
            "application_name",
            "ssl_min_protocol_version",
            "gssencmode",
            "channel_binding",
        }
        if len(params) != len(pairs) or set(params) - allowed:
            errors.append("ALICE_DATABASE_URL: duplicate or unsupported query options")
        if params.get("sslmode") != "verify-full":
            errors.append("ALICE_DATABASE_URL: sslmode=verify-full required")
        ca = params.get("sslrootcert", "")
        if not filled(ca) or not Path(ca).is_absolute():
            errors.append("ALICE_DATABASE_URL: absolute sslrootcert file required")
        if params.get("ssl_min_protocol_version") not in {"TLSv1.2", "TLSv1.3"}:
            errors.append("ALICE_DATABASE_URL: explicit TLS minimum required")
        if params.get("gssencmode") != "disable" or params.get("channel_binding") != "require":
            errors.append(
                "ALICE_DATABASE_URL: require TLS SCRAM channel binding; disable GSS negotiation"
            )
        if params.get("connect_timeout") not in {str(n) for n in range(2, 11)}:
            errors.append("ALICE_DATABASE_URL: connect_timeout must be 2..10 seconds")
        return params, errors
    except (TypeError, ValueError, UnicodeError):
        return {}, ["ALICE_DATABASE_URL: invalid URI"]


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
        or config.get("schema_version") != 1
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
            "container: expected initial Alice profile (8080, 0.5 CPU, 1024Mi, scale 0..1)"
        )
    database = config.get("database")
    if not isinstance(database, dict):
        return errors + ["database: object required"]
    if set(database) != {"host", "port", "name", "user", "allowed_client_cidrs", "egress_evidence"}:
        errors.append("database: unexpected schema")
    if not filled(database.get("host")) or not re.fullmatch(
        r"[a-zA-Z0-9.-]+", database.get("host", "")
    ):
        errors.append("database.host: explicit DNS name or IPv4 required")
    if type(database.get("port")) is not int or database.get("port") != 5432:
        errors.append("database.port: expected 5432")
    if database.get("name") != "alice" or database.get("user") != "alice_app":
        errors.append("database: expected dedicated alice database and alice_app role")
    networks = database.get("allowed_client_cidrs")
    if not isinstance(networks, list) or not networks:
        errors.append("database.allowed_client_cidrs: verified egress CIDRs required")
    else:
        try:
            parsed = [ipaddress.ip_network(n, strict=True) for n in networks if isinstance(n, str)]
            # This starter profile permits only narrow, globally routable ranges.
            if len(parsed) != len(networks) or any(
                n.prefixlen < (24 if n.version == 4 else 120)
                or not n.network_address.is_global
                or not n.broadcast_address.is_global
                or n.is_multicast
                for n in parsed
            ):
                raise ValueError
        except (ValueError, TypeError):
            errors.append(
                "database.allowed_client_cidrs: narrow public ranges required; no all-internet rule"
            )
    if not filled(database.get("egress_evidence")):
        errors.append("database.egress_evidence: provider guarantee/reference required")

    required = (
        "ALICE_SHORT_TOKEN",
        "ALICE_PROVIDER_CREDENTIAL_KEY",
        "ALICE_GITHUB_CLIENT_ID",
        "ALICE_GITHUB_CLIENT_SECRET",
        "ALICE_DATABASE_URL",
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
    _, db_errors = database_parameters(env.get("ALICE_DATABASE_URL", ""), database)
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


def probe_database(dsn: str, database: dict) -> str | None:
    """Return a static error only; never expose libpq/server exception text."""
    try:
        params, errors = database_parameters(dsn, database)
        if errors:
            return "database probe: invalid TLS configuration"
        ssl.create_default_context(cafile=params["sslrootcert"])
        import psycopg

        with psycopg.connect(
            dsn,
            connect_timeout=5,
            application_name="alice-preflight",
            options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000",
        ) as conn:
            row = conn.execute(
                "SELECT current_database(), current_user, s.ssl, s.version, "
                "r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication, r.rolbypassrls, "
                "EXISTS (SELECT 1 FROM pg_roles p WHERE p.oid <> r.oid "
                "AND p.rolname <> 'pg_database_owner' "
                "AND pg_has_role(current_user, p.oid, 'MEMBER')) "
                "FROM pg_stat_ssl s JOIN pg_roles r ON r.rolname = current_user "
                "WHERE s.pid = pg_backend_pid()"
            ).fetchone()
            if not row or row[:2] != (database["name"], database["user"]):
                return "database probe: unexpected database or role"
            if (
                row[2] is not True
                or row[3] not in {"TLSv1.2", "TLSv1.3"}
                or any(row[4:9])
                or row[9] is not False
            ):
                return "database probe: TLS or role policy failed"
    except Exception:  # noqa: BLE001 - secret-safe CLI error boundary
        return "database probe failed; verify CA, network, credentials and server policy in a protected session"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--workflow", action="store_true", help="also require existing EDS target/env"
    )
    parser.add_argument(
        "--check-db", action="store_true", help="connect and run read-only TLS/role checks"
    )
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        errors = validate(config, dict(os.environ), workflow=args.workflow)
    except Exception:  # noqa: BLE001 - secret-safe CLI error boundary
        errors = ["configuration could not be read or validated"]
    probed = False
    if not errors and args.check_db:
        error = probe_database(os.environ["ALICE_DATABASE_URL"], config["database"])
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

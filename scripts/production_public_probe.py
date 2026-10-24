#!/usr/bin/env python3
"""Read-only public baseline for #938. Success is NOT authenticated acceptance."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit

import requests

PRODUCTION = "https://maxxxpavlov.ru"
MAX_BODY = 16384


def checked_origin(value: str) -> str:
    try:
        url = urlsplit(value)
        local = url.hostname in {"127.0.0.1", "localhost", "::1"}
        origin = value.rstrip("/")
        if (
            any(char in value for char in "\\\r\n\t ?#")
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or (origin != PRODUCTION and not (local and url.scheme == "http"))
        ):
            raise ValueError
        _ = url.port
        return origin
    except (TypeError, ValueError):
        raise ValueError("invalid_probe_origin") from None


def health_matches(response: requests.Response) -> bool:
    if (
        response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        return False
    data = bytearray()
    for chunk in response.iter_content(chunk_size=4096):
        data.extend(chunk)
        if len(data) > MAX_BODY:
            return False
    try:
        payload = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


def check(session: requests.Session, origin: str, path: str, expected: int) -> dict:
    row = {
        "path": path,
        "method": "GET",
        "expected_http": expected,
        "http_status": None,
        "result": "failed",
        "error": None,
    }
    try:
        with session.get(
            origin + path, allow_redirects=False, timeout=(3, 5), stream=True
        ) as response:
            row["http_status"] = response.status_code
            if response.status_code != expected:
                row["error"] = "unexpected_http_status"
            elif path == "/healthz" and not health_matches(response):
                row["error"] = "invalid_health_contract"
            else:
                row["result"] = "passed"
    except requests.exceptions.SSLError:
        row["error"] = "tls_failed"
    except requests.exceptions.Timeout:
        row["error"] = "timeout"
    except requests.exceptions.RequestException:
        row["error"] = "transport_failed"
    return row


def probe_public(origin: str = PRODUCTION) -> dict:
    origin = checked_origin(origin)
    with requests.Session() as session:
        # Never inherit netrc credentials, proxy credentials or stored cookies.
        session.trust_env = False
        session.headers["User-Agent"] = "Alice-Public-Acceptance/1.0"
        rows = [check(session, origin, "/healthz", 200)]
        session.cookies.clear()
        rows.append(check(session, origin, "/", 401))
    passed = all(row["result"] == "passed" for row in rows)
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "origin": origin,
        "scope": "public_http_only",
        "status": "public_contract_passed" if passed else "public_contract_failed",
        "accepted": False,
        "credential_used": False,
        "authenticated_ui": "not_checked",
        "api": "not_checked",
        "browser": "not_checked",
        "checks": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default=PRODUCTION)
    args = parser.parse_args(argv)
    try:
        report = probe_public(args.origin)
    except ValueError:
        print(
            json.dumps(
                {
                    "status": "public_contract_failed",
                    "error": "invalid_probe_origin",
                    "accepted": False,
                }
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "public_contract_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

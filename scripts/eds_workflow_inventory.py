"""Read all Workflow Studio applications with bounded, secret-safe reporting."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from uuid import UUID

PAGE_SIZE = 50
STATUSES = {"for_create", "publishing", "running", "error", "deleted", "deleting", "stopped"}


def identifier(value):
    if not isinstance(value, str):
        raise ValueError("invalid identifier")
    return str(UUID(value))


def label(value, secrets):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./ -]{0,99}", value):
        return "[omitted]"
    return "[omitted]" if any(secret and secret in value for secret in secrets) else value


def inventory(query, *, clock=time.monotonic, secrets=()):
    deadline = clock() + 29
    rows = []
    seen = set()
    expected_total = None
    offset = 0
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("inventory deadline")
        page = query(offset, remaining)
        if not isinstance(page, dict):
            raise ValueError("invalid page")
        total = page.get("total")
        applications = page.get("applications")
        if "applications" not in page and type(total) is int and total == 0:
            applications = []
        if not isinstance(applications, list) or len(applications) > PAGE_SIZE:
            raise ValueError("invalid applications")
        if total is not None:
            if type(total) is not int or total < 0:
                raise ValueError("invalid total")
            if expected_total is not None and total != expected_total:
                raise ValueError("inventory changed")
            expected_total = total
        for app in applications:
            if not isinstance(app, dict):
                raise ValueError("invalid application")
            app_id = identifier(app.get("id"))
            if app_id in seen:
                raise ValueError("repeated application")
            seen.add(app_id)
            status = app.get("status")
            rows.append(
                {
                    "id": app_id,
                    "name": label(app.get("name"), secrets),
                    "branch": label(app.get("branch"), secrets),
                    "status": status
                    if isinstance(status, str) and status in STATUSES
                    else "unknown",
                }
            )
        offset += len(applications)
        if expected_total is not None and offset > expected_total:
            raise ValueError("inconsistent total")
        if expected_total == offset or (expected_total is None and len(applications) < PAGE_SIZE):
            return {"complete": True, "count": len(rows), "applications": rows}
        if not applications:
            raise ValueError("incomplete inventory")


def query_page(offset, remaining):
    result = subprocess.run(
        [
            "eds",
            "wf",
            "app",
            "list",
            "--sort",
            "created_at_asc",
            "--limit",
            str(PAGE_SIZE),
            "--offset",
            str(offset),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=remaining,
    )
    return json.loads(result.stdout)


def main():
    secret = os.environ.get("EDS_API_KEY", "")
    if not secret or not os.environ.get("EDS_PROJECT_ID"):
        print("Missing EDS_API_KEY or EDS_PROJECT_ID.")
        return 1
    try:
        report = inventory(query_page, secrets=(secret,))
    except Exception:  # noqa: BLE001 - never publish secret-bearing CLI errors
        print("EDS Workflow inventory failed or exceeded 30 seconds; no partial result published.")
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

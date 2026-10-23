#!/usr/bin/env python3
"""Auditable, read-only SQL dependency inventory and non-growth guard for #776.

Scan *all tracked textual files*; report source locations, never line contents or
secret values. The guard detects new runtime SQL coupling while legacy consumers
are migrated wave-by-wave. It never changes a database or deployment.
"""

from __future__ import annotations

import argparse
from collections import Counter
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from typing import Any

SCHEMA_VERSION = 1
MAX_SOURCE_BYTES = 8 * 1024 * 1024
DIRECT_RULES = frozenset({"sqlite_driver", "postgres_driver", "sql_connection", "sql_statement"})

# Signal names deliberately distinguish a direct dependency from a reference.
# Regexes are lexical evidence, not proof that the line is reachable at runtime.
RULES = {
    "sqlite_driver": re.compile(r"\bsqlite3\b|\baiosqlite\b|sqlite://|\bsqlite_master\b", re.I),
    "postgres_driver": re.compile(r"\bpsycopg\b|\basyncpg\b|\bpg8000\b|\bconnect_postgres\b", re.I),
    "sql_connection": re.compile(
        r"\bget_conn\s*\(|\bget_connection\s*\(|\bconnect\s*\(\s*[\"']postgres(?:ql)?://", re.I
    ),
    "sql_statement": re.compile(
        r"\b(?:SELECT\s+\S+|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|PRAGMA\s+\w+)\b",
        re.I,
    ),
    "sql_config": re.compile(
        r"\bALICE_DATABASE_URL\b|\bDATABASE_URL\b|\bPOSTGRES_[A-Z_]+\b|postgresql://", re.I
    ),
    "postgres_reference": re.compile(
        r"\bpostgres(?:ql)?\b|\bpg_dump\b|\bpg_restore\b|\bpg_ctl\b", re.I
    ),
    "sqlite_reference": re.compile(r"\bSQLite\b|\b\.sqlite3?\b", re.I),
}

# Explicit ownership, not guessed from keyword frequencies. Unknowns remain visible.
WAVE_1 = frozenset(
    {
        "user_identity.py",
        "conversation_ownership.py",
        "session_manager.py",
        "memory_manager.py",
        "memory_extractor.py",
        "session_profiles.py",
        "agent_memory/user_identity_migration.py",
        "agent_memory/conversation_ownership_migration.py",
        "agent_memory/user_identity_store.py",
        "agent_memory/conversation_ownership_store.py",
        "agent_memory/runtime_store.py",
        "agent_memory/file_memory_db.py",
        "agent_memory/backup.py",
    }
)
WAVE_2 = frozenset(
    {
        "environment_manager.py",
        "runtime_migrations.py",
        "observability_migrations.py",
        "alice_agent_runner.py",
        "mcp_storage.py",
        "local_agent_gateway.py",
        "agent_memory/repository.py",
        "app.py",
        "yandex_client_modules/request_mixin.py",
        "mcp_server/runtime_bridge.py",
    }
)
WAVE_3 = frozenset(
    {
        "treasury.py",
        "budget_repository.py",
        "departments.py",
        "government.py",
        "partner_relations.py",
        "provider_quotas.py",
        "provider_credentials.py",
        "provider_credentials_routes.py",
        "key_manager.py",
        "archiver.py",
    }
)
WAVE_4 = frozenset({"db.py", "db_backend.py", "memory_db.py", "mcp_migration.sql"})
OWNER_GROUPS = {"wave-1": WAVE_1, "wave-2": WAVE_2, "wave-3": WAVE_3, "wave-4": WAVE_4}


def category(path: str) -> str:
    """Separate authoritative runtime code from frontend, CI, tests and docs."""
    if path.startswith("tests/"):
        area = "test"
    elif (
        path.startswith(".github/")
        or path.startswith("scripts/ci_")
        or path.startswith("scripts/check_")
    ):
        area = "ci"
    elif path.startswith("docs/") or path.endswith((".md", ".rst", ".adoc")):
        area = "docs"
    elif path.startswith("migrations/") or path.endswith(".sql"):
        area = "migration"
    elif path.startswith(("static/", "templates/")):
        area = "frontend"
    elif path.startswith(("deploy/", "scripts/", ".codex/", ".agents/")):
        area = "infra"
    elif path.endswith((".py", ".js", ".ts", ".tsx", ".java", ".kt", ".sh")):
        area = "runtime"
    else:
        area = "other"
    return area


def wave(path: str, area: str) -> str:
    if area != "runtime":
        return "wave-4" if area in {"ci", "docs", "test", "migration", "infra"} else "not-runtime"
    if path in WAVE_1 or path.startswith("agent_memory/"):
        return "wave-1"
    if path in WAVE_2 or path.startswith(("invocation/", "runtime/", "mcp_server/")):
        return "wave-2"
    if path in WAVE_3 or path.startswith("printing3d/"):
        return "wave-3"
    if path in WAVE_4:
        return "wave-4"
    return "triage"


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, timeout=90
    ).stdout


def _tracked_worktree(root: Path) -> list[tuple[str, bytes]]:
    paths = sorted(
        path.decode("utf-8") for path in _git(root, "ls-files", "-z").split(b"\0") if path
    )
    rows: list[tuple[str, bytes]] = []
    for path in paths:
        item = root / path
        if item.is_symlink():
            rows.append((path, b"\0"))
        elif item.is_file():
            rows.append((path, item.read_bytes()))
    return rows


def _tracked_ref(root: Path, ref: str) -> list[tuple[str, bytes]]:
    # git archive reads committed objects only; no checkout, mutation or network.
    archive = _git(root, "archive", "--format=tar", ref)
    files: list[tuple[str, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for item in tar:
            if item.issym() or item.islnk():
                files.append((item.name, b"\0"))
            elif item.isfile():
                stream = tar.extractfile(item)
                if stream is not None:
                    files.append((item.name, stream.read()))
    return sorted(files)


def _scan_one(path: str, payload: bytes) -> dict[str, Any] | None:
    # Binary or oversized tracked artifacts are counted in the scan ledger.
    if len(payload) > MAX_SOURCE_BYTES or b"\0" in payload[:4096]:
        return {"path": path, "skipped": "binary_or_large"}
    try:
        content = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"path": path, "skipped": "non_utf8"}
    found: dict[str, list[int]] = {}
    for line_number, line in enumerate(content.splitlines(), 1):
        if line.lstrip().startswith("#") and category(path) == "runtime":
            continue
        for rule, pattern in RULES.items():
            if pattern.search(line):
                found.setdefault(rule, []).append(line_number)
    if path.endswith(".sql"):
        found.setdefault("sql_file", [1])
    if not found:
        return None
    area = category(path)
    return {"path": path, "category": area, "wave": wave(path, area), "signals": found}


def build_inventory(files: list[tuple[str, bytes]], source: str) -> dict[str, Any]:
    matches = []
    skipped = []
    for path, data in files:
        result = _scan_one(path, data)
        if result is None:
            continue
        if "skipped" in result:
            skipped.append(result)
        else:
            matches.append(result)
    areas = Counter(row["category"] for row in matches)
    waves = Counter(row["wave"] for row in matches if row["category"] == "runtime")
    direct = [
        row
        for row in matches
        if row["category"] == "runtime" and any(x in DIRECT_RULES for x in row["signals"])
    ]
    owners = []
    tracked = {path for path, _ in files}
    by_path = {row["path"]: row for row in matches}
    for group, paths in OWNER_GROUPS.items():
        for path in sorted(paths):
            if path not in tracked:
                status = "not_tracked"
            elif path not in by_path:
                status = "no_sql_marker"
            elif any(x in DIRECT_RULES for x in by_path[path]["signals"]):
                status = "direct_sql_evidence"
            else:
                status = "reference_only"
            owners.append({"path": path, "wave": group, "status": status})
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "tracked_files": len(files),
        "scanned_text_files": len(files) - len(skipped),
        "skipped_files": skipped,
        "matched_files": len(matches),
        "direct_runtime_files": len(direct),
        "matched_by_category": dict(sorted(areas.items())),
        "runtime_by_wave": dict(sorted(waves.items())),
        "unknown_runtime_direct": [row["path"] for row in direct if row["wave"] == "triage"],
        "owners": owners,
        "matches": matches,
    }


def debt_counter(inv: dict[str, Any]) -> Counter[tuple[str, str]]:
    debt: Counter[tuple[str, str]] = Counter()
    for row in inv["matches"]:
        if row["category"] == "runtime":
            for rule, locations in row["signals"].items():
                if rule in DIRECT_RULES:
                    debt[(row["path"], rule)] += len(locations)
    return debt


def new_debt(baseline: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject new direct runtime SQL uses without blocking existing debt removal."""
    before, after = debt_counter(baseline), debt_counter(current)
    return [
        {"path": path, "rule": rule, "baseline": before[(path, rule)], "current": count}
        for (path, rule), count in sorted(after.items())
        if count > before[(path, rule)]
    ]


def render_markdown(inv: dict[str, Any], increases: list[dict[str, Any]] | None = None) -> str:
    summary = [
        "# Issue #776: tracked SQL dependency inventory",
        "",
        f"Source: `{inv['source']}` · schema v{inv['schema_version']} · tracked files: **{inv['tracked_files']}** · scanned text files: **{inv['scanned_text_files']}**.",
        f"SQL marker files: **{inv['matched_files']}**; direct runtime SQL-marker files: **{inv['direct_runtime_files']}**.",
        "",
        "Lexical inventory, **not** a proof of reachable SQL execution. A no-marker file may still call another SQL-dependent module.",
        "This report contains paths, marker IDs and line numbers only; no source contents, keys or credentials.",
        "",
        "## Dependency classification",
        "",
        "| Area | Files with markers |",
        "|---|---:|",
    ]
    summary += [
        f"| {area} | {count} |" for area, count in sorted(inv["matched_by_category"].items())
    ]
    summary += [
        "",
        "## Current runtime marker ownership",
        "",
        "| Wave | Files with markers |",
        "|---|---:|",
    ]
    summary += [f"| {owner} | {count} |" for owner, count in sorted(inv["runtime_by_wave"].items())]
    summary += [
        "",
        "## Migration owner checkpoints",
        "",
        "| File | Wave | Marker status |",
        "|---|---|---|",
    ]
    summary += [f"| `{row['path']}` | {row['wave']} | {row['status']} |" for row in inv["owners"]]
    if increases is not None:
        summary += [
            "",
            "## Guard result",
            "",
            f"New direct SQL debt entries: **{len(increases)}**.",
        ]
        summary += [
            f"- `{row['path']}` {row['rule']}: {row['baseline']} → {row['current']}"
            for row in increases
        ]
    summary += ["", "## Marker inventory by tracked file", ""]
    for row in inv["matches"]:
        signals = "; ".join(
            f"{rule}: {','.join(map(str, locations))}"
            for rule, locations in sorted(row["signals"].items())
        )
        summary.append(f"- `{row['path']}` [{row['category']}/{row['wave']}]: {signals}")
    summary += ["", "## Unreadable or binary tracked files", ""]
    summary += [f"- `{row['path']}`: {row['skipped']}" for row in inv["skipped_files"]]
    summary += ["", "## Unassigned direct runtime dependencies", ""]
    summary += [f"- `{path}`" for path in inv["unknown_runtime_direct"]]
    summary += [
        "",
        "Do not remove SQL tests/CI until every runtime owner has migrated with verified crash recovery, backup/restore and shadow checks.",
        "",
    ]
    return "\n".join(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--ref", help="Scan exactly this committed git ref (e.g. origin/master)")
    parser.add_argument("--base-ref", help="Compare current inventory against this git ref")
    parser.add_argument(
        "--guard", action="store_true", help="Fail on newly introduced direct runtime SQL debt"
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args(argv)
    if args.guard and not args.base_ref:
        parser.error("--guard requires --base-ref")
    try:
        root = args.root.resolve()
        source = (
            f"{args.ref}@{_git(root, 'rev-parse', args.ref).decode().strip()}"
            if args.ref
            else _git(root, "rev-parse", "HEAD").decode().strip()
        )
        inventory = build_inventory(
            _tracked_ref(root, args.ref) if args.ref else _tracked_worktree(root), source
        )
        increases = None
        if args.base_ref:
            base = build_inventory(_tracked_ref(root, args.base_ref), args.base_ref)
            increases = new_debt(base, inventory)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(inventory, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if args.markdown_out:
            args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_out.write_text(render_markdown(inventory, increases), encoding="utf-8")
        print(
            json.dumps(
                {
                    k: inventory[k]
                    for k in (
                        "source",
                        "tracked_files",
                        "scanned_text_files",
                        "matched_files",
                        "direct_runtime_files",
                        "unknown_runtime_direct",
                    )
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if args.guard and increases:
            print(
                json.dumps(
                    {"new_runtime_sql_dependencies": increases}, ensure_ascii=False, sort_keys=True
                ),
                file=sys.stderr,
            )
            return 1
        return 0
    except (OSError, subprocess.CalledProcessError, tarfile.TarError, UnicodeError) as exc:
        print(f"inventory failed closed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

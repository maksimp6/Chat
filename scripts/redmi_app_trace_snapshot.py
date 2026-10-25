"""Private input for the existing age diagnostics channel; never an uploader.

No application writer is opened. Native journal verification rejects a torn tail;
source changes fail this attempt rather than silently exporting a moving target.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from invocation.manager import _read_chunked_value
from kristall.store import _JournalStore


def signature(path: Path) -> tuple[int, ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("source must be a regular non-symlink file")
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def write_json(path: Path, value) -> bytes:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return data


def export_records(values: dict, destination: Path) -> dict:
    entries = []
    statuses = Counter()
    empty = 0
    created = []
    for key, source in sorted(values.items()):
        if not key.startswith("runtime_invocation:"):
            continue
        record = deepcopy(source)
        identifier = key.removeprefix("runtime_invocation:")
        if not isinstance(record, dict) or record.get("id") != identifier:
            raise ValueError("invalid invocation record")
        for field in ("trace", "result", "error"):
            if field + "_chunks" in record:
                record[field] = _read_chunked_value(
                    identifier, field, record[field + "_chunks"], store=values
                )
        if not isinstance(record.get("trace"), dict):
            raise ValueError("invalid stored trace")
        empty += not bool(record["trace"])
        statuses[str(record.get("status"))] += 1
        if type(record.get("created_at")) is not int:
            raise ValueError("missing invocation time")
        created.append(record["created_at"])
        # Names cannot escape the private output tree, even for corrupt IDs.
        name = f"invocation-{len(entries):08d}.json"
        data = write_json(destination / name, record)
        entries.append({"path": name, "invocation_id": identifier,
                        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return {"invocations": len(entries), "empty_traces": empty,
            "status_counts": dict(statuses), "records": entries,
            "created_at_min": min(created) if created else None,
            "created_at_max": max(created) if created else None}


def snapshot(memory: Path, log_directory: Path, backend: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = signature(memory)
    state, sequence, digest = _JournalStore._inspect_log(memory)
    if signature(memory) != before:
        raise ValueError("journal changed during snapshot; retry in a new attempt")
    manifest = export_records(state.get("values", {}), destination)
    # Do not export the database, credentials, unrelated records or orphan chunks.
    del state
    paths = sorted(log_directory.glob("system.jsonl*"))
    allowed = {"system.jsonl", "system.jsonl.1", "system.jsonl.2", "system.jsonl.3"}
    if any(path.name not in allowed for path in paths):
        raise ValueError("unexpected system log rotation file")
    if log_directory / "system.jsonl" not in paths:
        raise ValueError("system log missing; completeness cannot be established")
    paths.append(backend)
    signatures = {path: signature(path) for path in paths}
    files = []
    for number, path in enumerate(paths):
        name = f"log-{number:02d}.bin"
        digest_hash = hashlib.sha256()
        size = 0
        with path.open("rb") as source, (destination / name).open("xb") as target:
            while block := source.read(1024 * 1024):
                target.write(block)
                digest_hash.update(block)
                size += len(block)
            target.flush()
            os.fsync(target.fileno())
        files.append({"path": name, "source": path.name, "bytes": size,
                      "sha256": digest_hash.hexdigest()})
    if (sorted(log_directory.glob("system.jsonl*")) != paths[:-1]
            or any(signature(path) != signatures[path] for path in paths)):
        raise ValueError("logs changed during snapshot; retry in a new attempt")
    manifest.update(
        schema="redmi-app-traces-v1", journal_sequence=sequence, journal_digest=digest,
        snapshot_at=datetime.now(timezone.utc).isoformat(), logs=files,
        coverage="all currently retained invocation records and configured logs",
        limitations=["already sanitized/truncated values cannot be recovered",
                     "deleted records and rotated-away logs are unavailable",
                     "in-memory and non-persisted UI/API traces are not covered",
                     "journal and logs have separate snapshot boundaries"],
    )
    # Even a zero-record snapshot is explicit; no success marker on partial output.
    write_json(destination / "manifest.json", manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--backend-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        snapshot(args.memory, args.logs, args.backend_log, args.output)
    except Exception:
        # Exception text/paths may contain private values. Keep partial files local.
        print("APP_TRACE_SNAPSHOT_FAILED: private partial output retained; no publication", file=sys.stderr)
        return 1
    print("APP_TRACE_SNAPSHOT_CREATED: private manifest written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded Actions inventories with inclusive UTC-second windows and visible gaps.

Saved pages are hash-verified observations of mutable API records within an epoch;
they are never an assertion that run status or the repository is immutable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from scripts.repository_corpus_github import CollectionError, MAX_PAGE_BYTES

FILTERED_LIMIT = 1000
MAX_WINDOWS = 10000
MAX_STATE_BYTES = 16 * 1024 * 1024
INVENTORY_KEY = "actions_run:/actions/runs"


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _second(value, *, floor=False):
    if not isinstance(value, str):
        raise ValueError("actions_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("actions_timestamp_invalid") from None
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("actions_timestamp_invalid")
    if parsed.microsecond and not floor:
        raise ValueError("actions_timestamp_invalid")
    return int(parsed.timestamp())


def _utc(second):
    return datetime.fromtimestamp(second, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(raw)
    temporary.replace(path)


def _window(lo, hi):
    return {"lo": lo, "hi": hi}


def _gap(record, reason, **metadata):
    record.setdefault("gaps", []).append({"reason": reason, "metadata": metadata})


class _Windows:
    def __init__(self, collector, lower, cutoff, epoch):
        self.collector = collector
        self.directory = Path(collector.progress.path).parent / "actions-inventory"
        self.path = self.directory / "state.json"
        self.state = {
            "schema": 1,
            "repository": collector.repository,
            "epoch": epoch,
            "lower": lower,
            "cutoff": cutoff,
            "pending": [_window(lower, cutoff)],
            "windows": [],
        }
        self.rows = {}
        self.cross_gaps = []
        self._restore(epoch)

    def _validate_state(self, data):
        if (
            not isinstance(data, dict)
            or data.get("schema") != 1
            or data.get("repository") != self.collector.repository
            or type(data.get("lower")) is not int
            or type(data.get("cutoff")) is not int
            or data["lower"] != self.state["lower"]
            or not data["lower"] <= data["cutoff"] <= self.state["cutoff"]
            or not isinstance(data.get("epoch"), str)
            or not isinstance(data.get("pending"), list)
            or not isinstance(data.get("windows"), list)
            or len(data["pending"]) + len(data["windows"]) > MAX_WINDOWS
        ):
            raise ValueError("actions_inventory_state_invalid")
        if _second(data["epoch"], floor=True) - 1 != data["cutoff"]:
            raise ValueError("actions_inventory_state_invalid")
        self._validate_coverage(data)

    @staticmethod
    def _validate_coverage(data):
        coverage = list(data["pending"])
        for record in data["windows"]:
            if record.get("status") not in {
                "split",
                "collected",
                "overflow",
                "partial",
                "deferred",
            }:
                raise ValueError("actions_inventory_state_invalid")
            if record["status"] in {"collected", "overflow"}:
                coverage.append(record)
        for record in data["pending"] + data["windows"]:
            if (
                type(record.get("lo")) is not int
                or type(record.get("hi")) is not int
                or not data["lower"] <= record["lo"] <= record["hi"] <= data["cutoff"]
            ):
                raise ValueError("actions_inventory_state_invalid")
        position = data["lower"]
        for record in sorted(coverage, key=lambda item: item["lo"]):
            if record["lo"] != position:
                raise ValueError("actions_inventory_state_invalid")
            position = record["hi"] + 1
        if position != data["cutoff"] + 1:
            raise ValueError("actions_inventory_state_invalid")

    def _restore(self, epoch):
        if not self.path.exists():
            return
        try:
            if self.path.is_symlink() or self.path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError("actions_inventory_state_invalid")
            envelope = json.loads(self.path.read_bytes())
            data = envelope["data"]
            if envelope.get("sha256") != _hash(_encoded(data)):
                raise ValueError("actions_inventory_state_invalid")
            self._validate_state(data)
            # A completed traversal refreshes for a new epoch. An unfinished
            # traversal keeps its explicit original cutoff rather than restarting.
            if not data["pending"] and data["epoch"] != epoch:
                return
            for record in data["windows"]:
                identities = []
                for page in record.get("pages", []):
                    identities.extend(self._restore_page(page, record))
                if record["status"] == "collected" and (
                    type(record.get("expected")) is not int
                    or not 0 <= record["expected"] < FILTERED_LIMIT
                    or record.get("gaps")
                    or len(set(identities)) != len(identities)
                    or len(identities) != record["expected"]
                    or record.get("count") != record["expected"]
                ):
                    raise ValueError("actions_inventory_state_invalid")
                if record["status"] == "overflow" and (
                    type(record.get("expected")) is not int
                    or record["expected"] < FILTERED_LIMIT
                    or record["lo"] != record["hi"]
                ):
                    raise ValueError("actions_inventory_state_invalid")
            self.state = data
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            self.rows = {}
            self.cross_gaps = []
            self.collector.gap("actions_run", "/actions/runs", "actions_inventory_state_invalid")

    def _restore_page(self, page, record):
        digest = page.get("sha256")
        size = page.get("bytes")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int
            or not 0 <= size <= MAX_PAGE_BYTES
        ):
            raise ValueError("actions_inventory_page_invalid")
        path = self.directory / (digest + ".json")
        if path.is_symlink() or path.stat().st_size != size:
            raise ValueError("actions_inventory_page_invalid")
        raw = path.read_bytes()
        rows = json.loads(raw)
        if (
            _hash(raw) != digest
            or not isinstance(rows, list)
            or len(rows) > 100
            or page.get("count") != len(rows)
        ):
            raise ValueError("actions_inventory_page_invalid")
        for row in rows:
            self._validate_row(row, record)
            self._observe(row)
        return [row["id"] for row in rows]

    @staticmethod
    def _validate_row(row, record):
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] < 1:
            raise ValueError("actions_run_identity_invalid")
        second = _second(row.get("created_at"))
        if not record["lo"] <= second <= record["hi"]:
            raise ValueError("actions_run_outside_window")

    def _observe(self, row):
        previous = self.rows.get(row["id"])
        if previous is not None and previous != row:
            self.cross_gaps.append({"reason": "actions_run_changed_during_epoch", "id": row["id"]})
        self.rows[row["id"]] = row

    def _page(self, record, per_page, page):
        path = (
            f"{self.collector.base}/actions/runs?created={_utc(record['lo'])}..{_utc(record['hi'])}"
            f"&per_page={per_page}&page={page}"
        )
        response = self.collector.reader.json(path)
        if (
            not isinstance(response, dict)
            or type(response.get("total_count")) is not int
            or response["total_count"] < 0
            or not isinstance(response.get("workflow_runs"), list)
            or len(response["workflow_runs"]) > per_page
        ):
            raise CollectionError("actions_window_response_invalid")
        record.setdefault("responses", []).append(
            {
                "page": page,
                "per_page": per_page,
                "total_count": response["total_count"],
                "sha256": _hash(_encoded(response)),
            }
        )
        return response["total_count"], response["workflow_runs"]

    def _save_page(self, record, rows):
        raw = _encoded(rows)
        if len(raw) > MAX_PAGE_BYTES:
            raise CollectionError("actions_inventory_page_limit")
        digest = _hash(raw)
        _write(self.directory / (digest + ".json"), raw)
        record.setdefault("pages", []).append(
            {"sha256": digest, "bytes": len(raw), "count": len(rows)}
        )
        for row in rows:
            self._observe(row)

    def _leaf(self, record, expected):
        identities = set()
        for page in range(1, min(self.collector.max_pages, 10) + 1):
            total, rows = self._page(record, 100, page)
            if total != expected:
                _gap(record, "inventory_changed_during_collection", before=expected, after=total)
            valid = []
            for row in rows:
                try:
                    self._validate_row(row, record)
                except ValueError as error:
                    _gap(record, str(error))
                    continue
                if row["id"] in identities:
                    _gap(record, "duplicate_id_during_pagination", id=row["id"])
                else:
                    identities.add(row["id"])
                    valid.append(row)
            self._save_page(record, valid)
            if len(rows) < 100:
                break
        else:
            _gap(record, "pagination_limit_exceeded")
        record["count"] = len(identities)
        if expected != len(identities):
            _gap(record, "inventory_count_mismatch", expected=expected, collected=len(identities))
        record["status"] = "partial" if record.get("gaps") else "collected"

    def _visit(self, record):
        total, probe = self._page(record, 1, 1)
        record["expected"] = total
        if len(probe) != min(total, 1):
            raise CollectionError("actions_window_probe_count_mismatch")
        for row in probe:
            self._validate_row(row, record)
        if total >= FILTERED_LIMIT:
            if record["lo"] == record["hi"]:
                record["status"] = "overflow"
                _gap(
                    record,
                    "actions_single_second_limit",
                    reported_count=total,
                    limit=FILTERED_LIMIT,
                )
                self._save_page(record, probe)
                return []
            middle = (record["lo"] + record["hi"]) // 2
            record["status"] = "split"
            return [_window(record["lo"], middle), _window(middle + 1, record["hi"])]
        self._leaf(record, total)
        return []

    def _checkpoint(self):
        raw = _encoded({"data": self.state, "sha256": _hash(_encoded(self.state))})
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError("actions_inventory_state_limit")
        _write(self.path, raw)

    def _frontier(self):
        terminal = [
            (record["lo"], record["hi"], record["status"])
            for record in self.state["windows"]
            if record["status"] in {"collected", "overflow"}
        ]
        pending = [(record["lo"], record["hi"]) for record in self.state["pending"]]
        return sorted(terminal), sorted(pending)

    def collect(self):
        self.frontier_before = self._frontier()
        pending = self.state["pending"]
        deferred = []
        while (
            pending and len(self.state["windows"]) + len(deferred) + len(pending) + 2 <= MAX_WINDOWS
        ):
            record = {**pending.pop(0), "observed_at": datetime.now(timezone.utc).isoformat()}
            try:
                children = self._visit(record)
                pending[:0] = children
            except (CollectionError, ValueError) as error:
                record["status"] = (
                    "deferred" if getattr(self.collector.reader, "stop_reason", None) else "partial"
                )
                _gap(record, str(error))
            if record["status"] in {"partial", "deferred"}:
                deferred.append(_window(record["lo"], record["hi"]))
            self.state["windows"].append(record)
            self.state["pending"] = deferred + pending
            self._checkpoint()
            if getattr(self.collector.reader, "stop_reason", None):
                break
        self.state["pending"] = deferred + pending
        self._checkpoint()
        return self._report()

    def _report(self):
        collector = self.collector
        for record in self.state["windows"]:
            for gap in record.get("gaps", []):
                collector.gap(
                    "actions_run",
                    "/actions/runs",
                    gap["reason"],
                    {"lower": _utc(record["lo"]), "upper": _utc(record["hi"]), **gap["metadata"]},
                )
        for gap in self.cross_gaps:
            collector.gap("actions_run", str(gap["id"]), gap["reason"])
        if self.state["pending"]:
            collector.gap(
                "actions_run",
                "/actions/runs",
                "actions_inventory_windows_pending",
                {
                    "windows": self.state["pending"],
                    "cutoff": _utc(self.state["cutoff"]),
                    "stop_reason": getattr(collector.reader, "stop_reason", None)
                    or "window_retry_required",
                },
            )
        leaves = [
            record
            for record in self.state["windows"]
            if record["status"] in {"collected", "overflow"}
        ]
        expected = sum(record["expected"] for record in leaves)
        if not self.state["pending"] and expected != len(self.rows):
            collector.gap(
                "actions_run",
                "/actions/runs",
                "actions_inventory_reconciliation_failed",
                {"leaf_reported_count": expected, "observed_unique_count": len(self.rows)},
            )
        complete = (
            not self.state["pending"]
            and all(record["status"] == "collected" for record in leaves)
            and not self.cross_gaps
            and expected == len(self.rows)
        )
        collector.inventory[INVENTORY_KEY] = {
            "complete": complete,
            "count": len(self.rows),
            "expected": None if self.state["pending"] else expected,
            "terminal_window_reported_count": expected,
            "count_scope": "observed_unique_run_ids_in_epoch",
            "strategy": "created_time_windows",
            "epoch": self.state["epoch"],
            "lower_bound": _utc(self.state["lower"]),
            "cutoff": _utc(self.state["cutoff"]),
            "snapshot": "mutable_observations_not_atomic",
            "windows": self.state["windows"],
            "pending_windows": self.state["pending"],
            "frontier_advanced_this_pass": self.frontier_before != self._frontier(),
        }
        return sorted(self.rows.values(), key=lambda row: (row["created_at"], row["id"]))


def collect_actions_runs(collector, repository_metadata, epoch):
    """Return observed run rows, retaining a compatible overall inventory key."""
    try:
        lower = _second((repository_metadata or {}).get("created_at"))
        cutoff = _second(epoch, floor=True) - 1
        if not 0 <= lower <= cutoff:
            raise ValueError("actions_inventory_bounds_invalid")
    except (TypeError, ValueError, OverflowError):
        collector.gap("actions_run", "/actions/runs", "actions_inventory_bounds_unverified")
        rows = collector.pages("actions_run", "/actions/runs", key="workflow_runs")
        collector.inventory[INVENTORY_KEY].update(
            complete=False,
            strategy="unfiltered_unverified",
            snapshot="mutable_observations_not_atomic",
        )
        return rows
    return _Windows(collector, lower, cutoff, epoch).collect()

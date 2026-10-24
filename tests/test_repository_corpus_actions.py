"""Realistic filtered GitHub pagination without network or provider mutations."""

import bisect
import copy
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import repository_corpus_actions as actions
from scripts import repository_corpus_github as github

BASE = actions._second("2026-09-01T00:00:00Z")


def run(identity, second):
    return {
        "id": identity,
        "created_at": actions._utc(second),
        "status": "completed",
        "run_attempt": 1,
    }


class WindowReader:
    stop_reason = None

    def __init__(self, rows, *, limit=10000, change=None):
        self.rows = sorted(rows, key=lambda row: (row["created_at"], row["id"]))
        self.times = [actions._second(row["created_at"]) for row in self.rows]
        self.limit = limit
        self.change = change
        self.calls = []

    def json(self, path, *, immutable=False):
        assert not immutable
        if len(self.calls) >= self.limit:
            self.stop_reason = "request_budget_exhausted"
            raise github.CollectionError(self.stop_reason)
        query = parse_qs(urlsplit(path).query)
        assert set(query) == {"created", "per_page", "page"}
        lo, hi = map(actions._second, query["created"][0].split(".."))
        per_page, page = int(query["per_page"][0]), int(query["page"][0])
        self.calls.append((lo, hi, per_page, page))
        left, right = bisect.bisect_left(self.times, lo), bisect.bisect_right(self.times, hi)
        selected = list(reversed(self.rows[left:right]))[:1000]
        # Emulate the documented result ceiling, including a clamped exact1000.
        response = {
            "total_count": min(right - left, 1000),
            "workflow_runs": copy.deepcopy(selected[(page - 1) * per_page : page * per_page]),
        }
        return self.change(response, self.calls[-1]) if self.change else response


class Collector:
    repository = "owner/repo"
    base = "/repos/owner/repo"
    max_pages = 10000

    def __init__(self, tmp_path, reader):
        self.reader = reader
        self.progress = SimpleNamespace(path=tmp_path / "collection-progress.json")
        self.inventory = {}
        self.gaps = []

    def gap(self, kind, source_id, reason, metadata=None):
        self.gaps.append(
            {"kind": kind, "source_id": source_id, "reason": reason, "metadata": metadata}
        )


def collect(collector, *, end=BASE + 100, epoch=None):
    return actions.collect_actions_runs(
        collector,
        {"created_at": actions._utc(BASE)},
        epoch or actions._utc(end + 1),
    )


def inventory(collector):
    return collector.inventory[actions.INVENTORY_KEY]


def reasons(collector):
    return {gap["reason"] for gap in collector.gaps}


def test_inclusive_second_windows_recover_more_than_40000_with_clamped_filtered_counts(tmp_path):
    rows = [run(index + 1, BASE + index) for index in range(42500)]
    reader = WindowReader(rows)
    collector = Collector(tmp_path, reader)
    recovered = collect(collector, end=BASE + 42499)
    result = inventory(collector)
    assert {row["id"] for row in recovered} == set(range(1, 42501))
    assert result["complete"] and result["count"] == result["expected"] == 42500
    assert result["snapshot"] == "mutable_observations_not_atomic"
    assert result["cutoff"] == actions._utc(BASE + 42499)
    leaves = sorted(
        (row for row in result["windows"] if row["status"] == "collected"),
        key=lambda row: row["lo"],
    )
    assert leaves[0]["lo"] == BASE and leaves[-1]["hi"] == BASE + 42499
    assert all(left["hi"] + 1 == right["lo"] for left, right in zip(leaves, leaves[1:]))
    assert all(row["expected"] < 1000 and row["responses"] and row["pages"] for row in leaves)
    assert len(reader.calls) < 700
    assert not collector.gaps


def test_fixed_cutoff_excludes_current_second_and_reuses_completed_epoch_only(tmp_path):
    first_epoch = "2026-09-01T00:00:02.900000+00:00"
    reader = WindowReader([run(1, BASE), run(2, BASE + 1), run(3, BASE + 2)])
    first = Collector(tmp_path, reader)
    assert [row["id"] for row in collect(first, epoch=first_epoch)] == [1, 2]
    second = Collector(tmp_path, WindowReader(reader.rows, limit=0))
    assert [row["id"] for row in collect(second, epoch=first_epoch)] == [1, 2]
    assert not second.reader.calls and inventory(second)["complete"]
    third = Collector(tmp_path, WindowReader(reader.rows))
    assert [row["id"] for row in collect(third, end=BASE + 2)] == [1, 2, 3]
    assert third.reader.calls and inventory(third)["cutoff"] == actions._utc(BASE + 2)


def test_budget_frontier_resumes_without_repeating_completed_windows_or_changing_cutoff(tmp_path):
    rows = [run(number + 1, BASE + number) for number in range(1200)]
    first = Collector(tmp_path, WindowReader(rows, limit=10))
    initial = collect(first, end=BASE + 1199)
    result = inventory(first)
    assert not result["complete"] and 0 < len(initial) < 1200
    completed = {
        (row["lo"], row["hi"]) for row in result["windows"] if row["status"] == "collected"
    }
    assert completed and result["pending_windows"]
    assert "actions_inventory_windows_pending" in reasons(first)
    second = Collector(tmp_path, WindowReader(rows + [run(1201, BASE + 1200)]))
    recovered = collect(second, end=BASE + 1200)
    assert len(recovered) == 1200 and inventory(second)["complete"]
    assert inventory(second)["cutoff"] == actions._utc(BASE + 1199)
    assert all((lo, hi) not in completed for lo, hi, _, _ in second.reader.calls)
    assert any(row["status"] == "deferred" for row in inventory(second)["windows"])


def test_single_second_saturation_is_explicit_and_never_complete(tmp_path):
    collector = Collector(tmp_path, WindowReader([run(number + 1, BASE) for number in range(1000)]))
    recovered = collect(collector, end=BASE)
    assert len(recovered) == 1
    assert "actions_single_second_limit" in reasons(collector)
    assert not inventory(collector)["complete"]
    assert inventory(collector)["windows"][0]["status"] == "overflow"


def test_split_frontier_is_progress_before_any_source_but_failed_retry_churn_is_not(tmp_path):
    from scripts import repository_rag as rag

    rows = [run(number + 1, BASE + number) for number in range(1200)]
    first = Collector(tmp_path, WindowReader(rows, limit=1))
    assert collect(first, end=BASE + 1199) == []
    assert inventory(first)["frontier_advanced_this_pass"]
    assert rag._github_stop(first.reader, 0, {}, {}, True) == (None, None)
    second = Collector(tmp_path, WindowReader(rows, limit=1))
    assert collect(second, end=BASE + 1199) == []
    assert not inventory(second)["frontier_advanced_this_pass"]
    assert rag._github_stop(second.reader, 0, {}, {}, False) == (
        "no_collection_progress",
        "no_progress",
    )


@pytest.mark.parametrize("bad_total", [None, -1, True, "2"])
def test_invalid_reported_count_keeps_window_pending(tmp_path, bad_total):
    def change(response, call):
        response["total_count"] = bad_total
        return response

    collector = Collector(tmp_path, WindowReader([run(1, BASE)], change=change))
    assert collect(collector) == []
    assert "actions_window_response_invalid" in reasons(collector)
    assert inventory(collector)["pending_windows"] and not inventory(collector)["complete"]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda row: row.update(id=True), "actions_run_identity_invalid"),
        (lambda row: row.update(created_at="not-a-date"), "actions_timestamp_invalid"),
        (
            lambda row: row.update(created_at=actions._utc(BASE + 1000)),
            "actions_run_outside_window",
        ),
    ],
)
def test_invalid_identity_or_ignored_created_filter_is_not_accepted(tmp_path, mutation, reason):
    def change(response, call):
        mutation(response["workflow_runs"][0])
        return response

    collector = Collector(tmp_path, WindowReader([run(1, BASE)], change=change))
    assert collect(collector) == []
    assert reason in reasons(collector)
    assert not inventory(collector)["complete"]


def test_changed_counts_and_repeated_ids_remain_visible_and_retryable(tmp_path):
    rows = [run(number + 1, BASE) for number in range(101)]

    def change(response, call):
        if call[2:] == (100, 2):
            response["total_count"] = 102
            response["workflow_runs"] = [run(101, BASE)]
        return response

    collector = Collector(tmp_path, WindowReader(rows, change=change))
    assert len(collect(collector)) == 100
    assert {
        "inventory_changed_during_collection",
        "duplicate_id_during_pagination",
        "inventory_count_mismatch",
    } <= reasons(collector)
    assert inventory(collector)["pending_windows"]
    retry = Collector(tmp_path, WindowReader(rows))
    assert len(collect(retry)) == 101 and inventory(retry)["complete"]


@pytest.mark.parametrize(
    "damage", ["state_hash", "overlap", "page_hash", "page_path", "false_count"]
)
def test_corrupt_state_or_observation_fails_closed_and_recollects(tmp_path, damage):
    rows = [run(1, BASE)]
    first = Collector(tmp_path, WindowReader(rows))
    collect(first)
    directory = tmp_path / "actions-inventory"
    path = directory / "state.json"
    envelope = json.loads(path.read_bytes())
    data = envelope["data"]
    leaf = data["windows"][0]
    if damage == "state_hash":
        envelope["sha256"] = "0" * 64
    elif damage == "overlap":
        data["pending"].append({"lo": BASE, "hi": BASE + 100})
    elif damage == "page_hash":
        page = directory / (leaf["pages"][0]["sha256"] + ".json")
        page.write_bytes(page.read_bytes().replace(b'"id":1', b'"id":2'))
    elif damage == "page_path":
        leaf["pages"][0]["sha256"] = "../escape"
    else:
        leaf["expected"] = 2
    if damage != "state_hash":
        envelope["sha256"] = actions._hash(actions._encoded(data))
    path.write_bytes(actions._encoded(envelope))
    second = Collector(tmp_path, WindowReader(rows))
    assert len(collect(second)) == 1
    assert "actions_inventory_state_invalid" in reasons(second)
    assert second.reader.calls


def test_missing_repository_creation_time_uses_explicit_unverified_fallback(tmp_path):
    collector = Collector(tmp_path, WindowReader([]))

    def pages(kind, path, *, key):
        assert (kind, path, key) == ("actions_run", "/actions/runs", "workflow_runs")
        collector.inventory[actions.INVENTORY_KEY] = {"complete": True, "count": 1, "expected": 1}
        return [{"id": 1}]

    collector.pages = pages
    assert actions.collect_actions_runs(collector, {}, actions._utc(BASE + 10)) == [{"id": 1}]
    assert "actions_inventory_bounds_unverified" in reasons(collector)
    assert inventory(collector)["strategy"] == "unfiltered_unverified"
    assert not inventory(collector)["complete"]

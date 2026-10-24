"""
Contract tests for Issue deduplication at the real observer boundary (Issue #700).

These tests exercise agent_office.observer through its existing public interfaces:
  observer.run()            — cross-issue duplicate detection (not yet implemented → RED)
  observer.publish_digest() — find-or-create idempotency (already correct → GREEN)

All tests collect and execute on current master.  The deduplication-specific
assertions fail with cause-specific AssertionError — not ImportError — because
'duplicate_candidate', 'ambiguous_candidate', and 'canonical' are absent from
detect_findings() output and thread serialisation.

Fixture issues model the observed security-cleanup duplicate pairs without live
GitHub state:
  #688 / #692 — file-route/symlink boundary (same root cause, different wording)
  #689 / #694 — Android cryptography / Chaquopy
  #690 / #693 — Termux exception redaction

Expected RED summary on current master:
  8 tests FAIL  — 'duplicate_candidate' / 'canonical' / 'ambiguous_candidate' absent
  4 tests PASS  — existing find-or-create idempotency and no-false-positive behaviour
"""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_office import observer
from agent_office.observer import GitHub, publish_digest

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Test infrastructure — FakeGitHub / FakeResponse (zero network)


class FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeGitHub:
    """Returns canned payloads, raises on unregistered routes, records all calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request, timeout):
        path = request.full_url.replace(observer.API_ROOT, "")
        self.calls.append((request.get_method(), path, request.data))
        for prefix, response in self.routes.items():
            method, _, route = prefix.partition(" ")
            if request.get_method() == method and path.startswith(route):
                if isinstance(response, Exception):
                    raise response
                payload, headers = response if isinstance(response, tuple) else (response, {})
                return FakeResponse(payload, headers)
        raise AssertionError(f"unregistered route: {request.get_method()} {path}")


def _ts(hours_ago=0):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _issue(number, title, *, state="open", hours_ago=10, body="", labels=()):
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/o/r/issues/{number}",
        "user": {"login": "maksimp6"},
        "body": body,
        "state": state,
        "created_at": _ts(hours_ago),
        "closed_at": _ts(1) if state == "closed" else None,
        "assignees": [],
        "labels": [{"name": n} for n in labels],
    }


def _run_with_issues(open_issues, closed_issues=()):
    """Run observer.run() over fixture issues with FakeGitHub (no network, no publish)."""
    routes = {
        "GET /repos/o/r/issues?state=open": list(open_issues),
        "GET /repos/o/r/issues?state=closed": list(closed_issues),
    }
    for iss in list(open_issues) + list(closed_issues):
        routes[f"GET /repos/o/r/issues/{iss['number']}/timeline"] = []
    fake = FakeGitHub(routes)
    return observer.run(GitHub("t", "o/r", opener=fake), now=NOW)


# ---------------------------------------------------------------------------
# Issue fixtures — modelled on the three observed security-cleanup pairs.
# Closed issues have closed_at within the 24-hour recent_window so they appear
# in observer output; no dependency on live GitHub state.

# Pair 1 — file-route / symlink boundary
ISSUE_688 = _issue(
    688,
    "fix(runtime): enforce file-route/symlink boundary in RuntimeDispatcher",
    state="closed",
    hours_ago=25,
    body="closed: duplicate of #692",
)
ISSUE_692 = _issue(
    692,
    "fix(runtime): prevent symlink escape from runtime root via file-route resolution",
)

# Pair 2 — Android cryptography / Chaquopy
ISSUE_689 = _issue(
    689,
    "fix(android): restrict Chaquopy key-import to keystore boundary",
    state="closed",
    hours_ago=25,
    body="closed: duplicate of #694",
)
ISSUE_694 = _issue(
    694,
    "fix(android): enforce Chaquopy cryptography scope inside KeyStore boundary",
)

# Pair 3 — Termux exception redaction
ISSUE_690 = _issue(
    690,
    "fix(termux): redact exception details in Termux error handler",
    state="closed",
    hours_ago=25,
    body="closed: duplicate of #693",
)
ISSUE_693 = _issue(
    693,
    "fix(termux): suppress full exception trace in Termux runtime error path",
)


# ---------------------------------------------------------------------------
# Case 1 — same root cause, different wording → duplicate_candidate finding
# SEMANTIC RED: detect_findings() has no cross-issue scan; finding is absent.


def test_same_root_cause_different_wording_produces_duplicate_candidate():
    """observer.run() must flag #688 / #692 as duplicate_candidate.

    Both describe the same file-route/symlink boundary fix with different wording.
    EXPECTED FAILURE: 'duplicate_candidate' absent — no cross-issue analysis exists.
    """
    result = _run_with_issues([ISSUE_692], closed_issues=[ISSUE_688])
    kinds = [f["kind"] for f in result["findings"]]
    assert "duplicate_candidate" in kinds, (
        "Expected finding kind 'duplicate_candidate' for pair #688/#692 "
        "(same file-route/symlink root cause, different title wording); "
        f"actual findings: {sorted(kinds)}"
    )


# ---------------------------------------------------------------------------
# Case 2 — same component, distinct root cause → no duplicate_candidate
# PASSES on current master (no cross-issue analysis ⇒ no false positives).


def test_same_component_distinct_root_cause_no_duplicate_candidate():
    """observer.run() must NOT flag issues with the same component but different root causes.

    PASSES: no cross-issue analysis currently ⇒ no false-positive duplicate finding.
    """
    runtime_symlink = _issue(701, "fix(runtime): enforce file-route/symlink boundary")
    runtime_jwt = _issue(702, "fix(runtime): rotate JWT tokens on session expiry")
    result = _run_with_issues([runtime_symlink, runtime_jwt])
    kinds = [f["kind"] for f in result["findings"]]
    assert "duplicate_candidate" not in kinds, (
        "False positive: issues with distinct root causes in the same component "
        f"must not be flagged as duplicates; findings: {kinds}"
    )


# ---------------------------------------------------------------------------
# Case 3 — closed duplicate still resolves to open canonical
# SEMANTIC RED: thread records carry no 'canonical' field.


def test_closed_duplicate_resolves_to_open_canonical():
    """Thread for a closed duplicate must expose the canonical Issue number.

    EXPECTED FAILURE: observer thread serialisation has no 'canonical' key.
    Fails with: AssertionError — closed_thread.get('canonical') is None, not 692.
    """
    result = _run_with_issues([ISSUE_692], closed_issues=[ISSUE_688])
    closed_thread = next((t for t in result["threads"] if t["number"] == 688), None)
    assert closed_thread is not None, (
        "Thread for closed duplicate #688 must appear in observer output "
        "(closed_at is within the 24-hour recent_window)"
    )
    assert closed_thread.get("canonical") == 692, (
        "Thread #688 (closed duplicate) must expose canonical=692; "
        f"actual thread keys: {sorted(closed_thread.keys())}"
    )


# ---------------------------------------------------------------------------
# Case 4 — parent/child decomposition is NOT treated as a duplicate
# PASSES on current master (no cross-issue analysis ⇒ no false positives).


def test_parent_child_not_treated_as_duplicate():
    """A child Issue decomposing a parent must not be flagged as a duplicate.

    PASSES: no cross-issue analysis currently ⇒ no false-positive duplicate finding.
    """
    parent = _issue(700, "test(process): prevent duplicate Issues and require canonical Issue reuse")
    child = _issue(703, "test(process): RED contract tests for deduplication boundary", body="Parent: #700")
    result = _run_with_issues([parent, child])
    kinds = [f["kind"] for f in result["findings"]]
    assert "duplicate_candidate" not in kinds, (
        f"Parent/child pair (#700/#703) must not be flagged as duplicates; findings: {kinds}"
    )


# ---------------------------------------------------------------------------
# Case 5 — ambiguous similarity → candidates returned, no auto-create/auto-close
# SEMANTIC RED: observer has no 'ambiguous_candidate' finding kind.


def test_ambiguous_similarity_returns_candidates_no_auto_action():
    """Ambiguously similar Issues must surface as 'ambiguous_candidate', not auto-close.

    Two Issues overlap enough to be uncertain — coordinator must decide.
    EXPECTED FAILURE: 'ambiguous_candidate' absent — no cross-issue analysis exists.
    """
    issue_a = _issue(710, "fix(runtime): tighten permission check in file resolver")
    issue_b = _issue(711, "fix(runtime): restrict file resolver to permitted root paths")
    result = _run_with_issues([issue_a, issue_b])
    kinds = [f["kind"] for f in result["findings"]]
    assert "ambiguous_candidate" in kinds, (
        "Expected 'ambiguous_candidate' finding for issues with overlapping but "
        "uncertain scope (#710 tighten permission / #711 restrict root paths); "
        f"actual findings: {sorted(kinds)}"
    )


# ---------------------------------------------------------------------------
# Case 6 — repeated publish_digest call is idempotent
# PASSES on current master.


def test_publish_digest_is_idempotent_repeated_call():
    """publish_digest called twice must PATCH the existing Issue, never POST a second.

    PASSES: existing find-or-create by label already implements this correctly.
    """
    tracking = _issue(99, observer.TRACKING_TITLE, labels=(observer.TRACKING_LABEL,))
    fake = FakeGitHub({
        "GET /repos/o/r/issues?state=open&labels=": [tracking],
        "PATCH /repos/o/r/issues/99": {"html_url": "https://github.com/o/r/issues/99"},
    })
    gh = GitHub("t", "o/r", opener=fake)
    url1 = publish_digest(gh, "digest v1")
    url2 = publish_digest(gh, "digest v2")
    new_issue_posts = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/issues")]
    assert url1 == url2 == "https://github.com/o/r/issues/99", (
        f"Both calls must return the same tracking Issue URL; got {url1!r}, {url2!r}"
    )
    assert new_issue_posts == [], (
        f"No new Issue must be created on repeat call; unexpected POSTs: {new_issue_posts}"
    )


# ---------------------------------------------------------------------------
# Case 7 — no write when duplicate_candidate is detected
# SEMANTIC RED: duplicate_candidate is never detected; write-suppression unreachable.


def test_no_write_occurs_when_duplicate_is_detected():
    """When a duplicate_candidate is detected, observer.run() must emit no write calls.

    EXPECTED FAILURE (prerequisite): 'duplicate_candidate' not detected because
    observer has no cross-issue scan.  The write-suppression assertion is future-facing.
    Fails with: AssertionError — prerequisite 'duplicate_candidate' missing.
    """
    fake = FakeGitHub({
        "GET /repos/o/r/issues?state=open": [ISSUE_692],
        "GET /repos/o/r/issues?state=closed": [ISSUE_688],
        "GET /repos/o/r/issues/692/timeline": [],
        "GET /repos/o/r/issues/688/timeline": [],
    })
    result = observer.run(GitHub("t", "o/r", opener=fake), now=NOW, publish=False)
    kinds = [f["kind"] for f in result["findings"]]
    assert "duplicate_candidate" in kinds, (
        "Prerequisite: expected 'duplicate_candidate' for pair #688/#692; "
        f"actual findings: {sorted(kinds)}"
    )
    write_calls = [c for c in fake.calls if c[0] in {"POST", "PATCH", "DELETE"}]
    assert write_calls == [], f"No write calls expected when duplicate detected; got: {write_calls}"


# ---------------------------------------------------------------------------
# Case 8 — exactly one Issue created for a genuine new tracking Issue
# PASSES on current master.


def test_exactly_one_issue_created_on_first_run():
    """publish_digest must POST exactly one Issue when no tracking Issue exists.

    PASSES: existing create path already implements this correctly.
    """
    fake = FakeGitHub({
        "GET /repos/o/r/issues?state=open&labels=": [],
        "POST /repos/o/r/labels": {"name": observer.TRACKING_LABEL},
        "POST /repos/o/r/issues": {"html_url": "https://github.com/o/r/issues/100"},
    })
    url = publish_digest(GitHub("t", "o/r", opener=fake), "brand new digest")
    issue_posts = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/issues")]
    assert url == "https://github.com/o/r/issues/100"
    assert len(issue_posts) == 1, (
        f"Expected exactly one Issue POST on first run; got {len(issue_posts)}: {issue_posts}"
    )


# ---------------------------------------------------------------------------
# Case 9 — all three observed security-cleanup pairs detected (parametrised)
# SEMANTIC RED: duplicate_candidate absent for every pair.


OBSERVED_PAIRS = [
    pytest.param(ISSUE_688, ISSUE_692, id="pair-688-692-symlink-boundary"),
    pytest.param(ISSUE_689, ISSUE_694, id="pair-689-694-android-crypto"),
    pytest.param(ISSUE_690, ISSUE_693, id="pair-690-693-termux-redaction"),
]


@pytest.mark.parametrize("duplicate,canonical", OBSERVED_PAIRS)
def test_observed_security_pair_detected_as_duplicate_candidate(duplicate, canonical):
    """Each observed security-cleanup pair must trigger a duplicate_candidate finding.

    EXPECTED FAILURE: 'duplicate_candidate' absent — no cross-issue analysis exists.
    """
    result = _run_with_issues([canonical], closed_issues=[duplicate])
    kinds = [f["kind"] for f in result["findings"]]
    assert "duplicate_candidate" in kinds, (
        f"Expected 'duplicate_candidate' for observed pair "
        f"#{duplicate['number']}/#{canonical['number']} "
        f"({duplicate['title']!r} vs {canonical['title']!r}); "
        f"actual findings: {sorted(kinds)}"
    )


# ---------------------------------------------------------------------------
# Case 10 — thread records expose canonical and duplicate_candidates fields
# SEMANTIC RED: both keys absent from current thread serialisation.


def test_thread_records_expose_canonical_and_duplicate_candidates_fields():
    """Each thread in run() output must carry 'canonical' and 'duplicate_candidates' fields.

    EXPECTED FAILURE: current serialisation omits both keys.
    Fails with: AssertionError — key absent from thread dict.
    """
    result = _run_with_issues([ISSUE_692, ISSUE_693])
    for thread in result["threads"]:
        assert "canonical" in thread, (
            f"Thread #{thread['number']} missing 'canonical' key; "
            f"present keys: {sorted(thread.keys())}"
        )
        assert "duplicate_candidates" in thread, (
            f"Thread #{thread['number']} missing 'duplicate_candidates' key; "
            f"present keys: {sorted(thread.keys())}"
        )

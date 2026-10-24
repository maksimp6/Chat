"""Regression tests for the Issue-planning / creation guard (Issue #700).

These tests are the contract layer for agent_office.issue_planner, which does
not yet exist. They will be RED until an implementation is provided.

Fixtures are modelled on the duplicate pairs observed on 2026-10-01:
  - #688 / #692  file-route / symlink boundary
  - #689 / #694  Android cryptography / Chaquopy
  - #690 / #693  Termux exception redaction

No network calls. All GitHub state is provided as static fixture dicts.
Ordering is deterministic; each assertion includes a cause-specific message.

TDD lifecycle note (Issue #700):
  Phase 1 (this file) — tests only, no production code → expected RED.
  Phase 2 — implementation adds agent_office/issue_planner.py → GREEN.
"""

from __future__ import annotations

import socket

import pytest

from agent_office.issue_planner import PlanResult, find_canonical_issue

# ---------------------------------------------------------------------------
# Static fixtures — modelled on the three observed duplicate pairs
# ---------------------------------------------------------------------------

# Pair 1: file-route / symlink boundary
ISSUE_688 = {
    "number": 688,
    "title": "sec(file-routing): enforce symlink escape boundary in file-route handler",
    "state": "open",
    "html_url": "https://github.com/maksimp6/Chat/issues/688",
    "body": (
        "Root cause: the file-route handler follows symlinks without checking "
        "whether the resolved path stays inside the allowed directory boundary. "
        "A crafted symlink permits path traversal outside the sandbox.\n"
        "Component: security/file-routing"
    ),
    "labels": [{"name": "security"}],
}

ISSUE_692 = {
    "number": 692,
    "title": "bug: symlink path traversal in HTTP file serving",
    "state": "closed",
    "html_url": "https://github.com/maksimp6/Chat/issues/692",
    "body": (
        "Duplicate of #688\n\n"
        "The HTTP file server allows symlink traversal outside the allowed directory "
        "by following symlinks in the file-route handler without boundary checks."
    ),
    "labels": [{"name": "duplicate"}],
}

# Pair 2: Android Chaquopy cryptography
ISSUE_689 = {
    "number": 689,
    "title": "fix(android): Chaquopy cryptography key import fails on API < 28",
    "state": "open",
    "html_url": "https://github.com/maksimp6/Chat/issues/689",
    "body": (
        "Root cause: PyCryptodome under Chaquopy cannot load native crypto libraries "
        "on Android API level below 28. Key import raises ImportError.\n"
        "Component: android/chaquopy"
    ),
    "labels": [{"name": "android"}],
}

ISSUE_694 = {
    "number": 694,
    "title": "android: PyCryptodome fails to load under Chaquopy on older Android devices",
    "state": "closed",
    "html_url": "https://github.com/maksimp6/Chat/issues/694",
    "body": (
        "Duplicate of #689\n\n"
        "PyCryptodome raises ImportError when loaded via Chaquopy on Android "
        "devices running API < 28. Same root cause as #689."
    ),
    "labels": [{"name": "duplicate"}],
}

# Pair 3: Termux exception redaction
ISSUE_690 = {
    "number": 690,
    "title": "sec(termux): exception tracebacks leak secrets in Termux runtime",
    "state": "open",
    "html_url": "https://github.com/maksimp6/Chat/issues/690",
    "body": (
        "Root cause: the Termux runtime does not redact secrets from exception "
        "messages. Tracebacks printed to stderr expose API keys and tokens.\n"
        "Component: security/termux"
    ),
    "labels": [{"name": "security"}],
}

ISSUE_693 = {
    "number": 693,
    "title": "Termux stack traces expose API keys in error output",
    "state": "closed",
    "html_url": "https://github.com/maksimp6/Chat/issues/693",
    "body": (
        "Duplicate of #690\n\n"
        "Termux exceptions expose API keys and tokens in stack traces. "
        "The runtime does not redact secrets from error output."
    ),
    "labels": [{"name": "duplicate"}],
}

# Parent issue — not a duplicate target for its children
ISSUE_663 = {
    "number": 663,
    "title": "feat: use GitHub Issues as primary operational memory for agent coordination",
    "state": "open",
    "html_url": "https://github.com/maksimp6/Chat/issues/663",
    "body": (
        "Establish GitHub Issues as the single canonical record of all active work. "
        "Agents must search open Issues before creating new ones. "
        "This is the parent umbrella for all Issues-as-memory work."
    ),
    "labels": [],
}

# Ambiguous pair: overlapping keywords, no clear winner
ISSUE_701 = {
    "number": 701,
    "title": "fix(runtime): unhandled exception in Termux process spawning",
    "state": "open",
    "html_url": "https://github.com/maksimp6/Chat/issues/701",
    "body": "Unhandled exception when spawning a subprocess in the Termux runtime.",
    "labels": [],
}

ISSUE_702 = {
    "number": 702,
    "title": "fix(runtime): exception propagation missing in Android runtime",
    "state": "open",
    "html_url": "https://github.com/maksimp6/Chat/issues/702",
    "body": "Exception propagation is missing in the Android runtime exception handler.",
    "labels": [],
}


# ---------------------------------------------------------------------------
# Contract case 1 — same root cause, different wording → canonical reuse
# ---------------------------------------------------------------------------


def test_same_root_cause_different_wording_selects_canonical():
    """Same root cause expressed with different wording must select the existing canonical."""
    # Phrased differently than #688's title, but same component and root cause
    objective = "prevent symlink path traversal escape via file-route handler"
    result = find_canonical_issue(objective, [ISSUE_688])
    assert isinstance(result, PlanResult), "find_canonical_issue must return a PlanResult"
    assert result.action == "reuse", (
        f"expected action='reuse' for objective matching #688 root cause, "
        f"got {result.action!r}; reason: {result.reason!r}"
    )
    assert result.canonical is not None, "reuse action must include a canonical issue dict"
    assert result.canonical["number"] == 688, (
        f"canonical must be #688, got #{result.canonical['number']}"
    )


# ---------------------------------------------------------------------------
# Contract case 2 — same component, distinct root cause → create allowed
# ---------------------------------------------------------------------------


def test_same_component_distinct_root_cause_allows_new_issue():
    """Same component but genuinely distinct root cause must permit a new Issue."""
    # android/chaquopy component matches #689, but the cause is version pinning,
    # not cryptography key import failure
    objective = "pin Chaquopy to a fixed version to avoid transitive dependency conflicts"
    result = find_canonical_issue(objective, [ISSUE_689])
    assert result.action == "create", (
        f"expected action='create' for distinct root cause, "
        f"got {result.action!r}; reason: {result.reason!r}"
    )


# ---------------------------------------------------------------------------
# Contract case 3 — closed duplicate resolves to canonical open Issue
# ---------------------------------------------------------------------------


def test_closed_duplicate_resolves_to_open_canonical():
    """A request matching a closed duplicate must resolve to the canonical open Issue."""
    # #692 is closed with "Duplicate of #688" in its body; #688 is open.
    # New objective is similar to #692 → the canonical must be #688, not #692.
    objective = "symlink traversal escape in HTTP file serving"
    result = find_canonical_issue(objective, [ISSUE_688, ISSUE_692])
    assert result.action == "reuse", (
        f"expected action='reuse' via closed-duplicate chain, "
        f"got {result.action!r}; reason: {result.reason!r}"
    )
    assert result.canonical is not None, "reuse action must carry a canonical issue dict"
    assert result.canonical["number"] == 688, (
        f"canonical must be the open #688, not the closed duplicate #692; "
        f"got #{result.canonical['number']}"
    )
    assert result.canonical["state"] == "open", (
        "canonical Issue must be open, not closed"
    )


def test_closed_duplicate_resolves_independently_of_issue_list_order():
    """Canonical resolution must not depend on the order of the issues list."""
    objective = "symlink traversal escape in HTTP file serving"
    result_ab = find_canonical_issue(objective, [ISSUE_688, ISSUE_692])
    result_ba = find_canonical_issue(objective, [ISSUE_692, ISSUE_688])
    assert result_ab.action == result_ba.action, "result must not depend on list order"
    if result_ab.canonical:
        assert result_ab.canonical["number"] == result_ba.canonical["number"], (
            "canonical issue must not depend on list order"
        )


# ---------------------------------------------------------------------------
# Contract case 4 — parent/child relationship is not collapsed as duplicate
# ---------------------------------------------------------------------------


def test_parent_child_decomposition_not_treated_as_duplicate():
    """A new child Issue is not a duplicate of its declared parent Issue."""
    # #663 is the declared parent (passed explicitly).
    # The child objective is a distinct actionable slice, not a restatement.
    child_objective = "add deterministic regression tests for the Issue deduplication boundary"
    result = find_canonical_issue(
        child_objective,
        [ISSUE_663],
        parent_issue=663,
    )
    assert result.action in ("create", "ambiguous"), (
        f"parent/child must not collapse as duplicate; "
        f"got action={result.action!r}; reason: {result.reason!r}"
    )
    if result.action == "ambiguous":
        candidate_numbers = {c["number"] for c in result.candidates}
        assert 663 not in candidate_numbers, (
            "parent #663 must not appear as a duplicate candidate for its own child"
        )


def test_parent_issue_without_explicit_param_not_auto_closed_as_duplicate():
    """When parent_issue is not given, a related-but-different issue is not auto-collapsed."""
    # Without an explicit parent_issue declaration the planner should not blindly
    # collapse any related issue as a duplicate.
    child_objective = "add deterministic regression tests for the Issue deduplication boundary"
    result = find_canonical_issue(child_objective, [ISSUE_663])
    # "reuse" is wrong here — the objectives are genuinely different
    # (operational-memory policy vs. regression-test coverage).
    # The correct outcome is "create" or "ambiguous".
    assert result.action in ("create", "ambiguous"), (
        f"without explicit parent marker, planner must not auto-collapse; "
        f"got action={result.action!r}; reason: {result.reason!r}"
    )


# ---------------------------------------------------------------------------
# Contract case 5 — ambiguous match → bounded candidates, no auto action
# ---------------------------------------------------------------------------


def test_ambiguous_similarity_returns_bounded_candidates_no_auto_action():
    """Ambiguous similarity must return bounded candidates without auto-create or auto-close."""
    # "exception handling in runtime" shares keywords with both #701 and #702
    # but is not clearly a duplicate of either.
    objective = "exception handling in runtime"
    result = find_canonical_issue(objective, [ISSUE_701, ISSUE_702])
    assert result.action == "ambiguous", (
        f"expected action='ambiguous' for overlapping objectives, "
        f"got {result.action!r}; reason: {result.reason!r}"
    )
    assert result.canonical is None, (
        "ambiguous result must not auto-select a canonical issue"
    )
    assert isinstance(result.candidates, list), "candidates must be a list"
    assert len(result.candidates) > 0, "candidates must be non-empty for an ambiguous result"
    assert len(result.candidates) <= 5, (
        f"candidates must be a bounded list (≤ 5), got {len(result.candidates)}"
    )


# ---------------------------------------------------------------------------
# Contract case 6 — repeated identical request is idempotent
# ---------------------------------------------------------------------------


def test_repeated_identical_request_is_idempotent():
    """The same planning request produces the same result on every call."""
    objective = "enforce file-route symlink traversal boundary"
    existing = [ISSUE_688]
    result_first = find_canonical_issue(objective, existing)
    result_second = find_canonical_issue(objective, existing)
    assert result_first.action == result_second.action, (
        "planning must be deterministic: same action on repeated call"
    )
    if result_first.canonical and result_second.canonical:
        assert result_first.canonical["number"] == result_second.canonical["number"], (
            "planning must be deterministic: same canonical on repeated call"
        )
    assert result_first.candidates == result_second.candidates, (
        "planning must be deterministic: same candidates on repeated call"
    )


# ---------------------------------------------------------------------------
# Contract case 7 — PlanResult public type and no network in unit path
# ---------------------------------------------------------------------------


def test_plan_result_exposes_required_contract_fields():
    """PlanResult must expose action, canonical, candidates, and reason fields."""
    # This test proves the actual public module is imported and exercised —
    # there is no reimplementation of the logic inside this test file.
    result = find_canonical_issue(
        "enforce file-route symlink traversal boundary", [ISSUE_688]
    )
    assert isinstance(result, PlanResult), (
        "find_canonical_issue must return a PlanResult instance"
    )
    assert hasattr(result, "action"), "PlanResult must have an 'action' field"
    assert hasattr(result, "canonical"), "PlanResult must have a 'canonical' field"
    assert hasattr(result, "candidates"), "PlanResult must have a 'candidates' field"
    assert hasattr(result, "reason"), "PlanResult must have a 'reason' field"
    assert result.action in ("reuse", "create", "ambiguous"), (
        f"action must be one of reuse/create/ambiguous, got {result.action!r}"
    )
    assert isinstance(result.candidates, list), "candidates must be a list"
    assert isinstance(result.reason, str) and result.reason, "reason must be a non-empty string"


def test_find_canonical_issue_makes_no_network_calls():
    """find_canonical_issue must not open any network connections."""
    original_connect = socket.socket.connect

    def deny_network(self, address):
        raise AssertionError(
            f"find_canonical_issue must not make network calls; attempted to connect to {address}"
        )

    socket.socket.connect = deny_network
    try:
        find_canonical_issue("symlink escape in file-route", [ISSUE_688])
    finally:
        socket.socket.connect = original_connect


# ---------------------------------------------------------------------------
# Edge case — closed canonical: both canonical and its duplicate are closed
# ---------------------------------------------------------------------------


def test_closed_canonical_with_closed_duplicate_does_not_auto_reuse():
    """If the canonical issue itself is also closed, do not auto-reuse it."""
    issue_688_closed = {**ISSUE_688, "state": "closed"}
    objective = "symlink traversal in file-route handler"
    result = find_canonical_issue(objective, [issue_688_closed, ISSUE_692])
    # Both are closed; the planner must not claim certainty by auto-selecting a closed canonical.
    assert result.action in ("ambiguous", "create"), (
        f"when canonical is also closed, must not auto-reuse; got {result.action!r}; "
        f"reason: {result.reason!r}"
    )


# ---------------------------------------------------------------------------
# Cross-fixture coverage: all three observed duplicate pairs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "objective, canonical_issue, closed_duplicate",
    [
        (
            "prevent symlink path traversal outside sandbox via file routes",
            ISSUE_688,
            ISSUE_692,
        ),
        (
            "PyCryptodome ImportError under Chaquopy on Android API level 27",
            ISSUE_689,
            ISSUE_694,
        ),
        (
            "Termux runtime exposes secrets in exception tracebacks",
            ISSUE_690,
            ISSUE_693,
        ),
    ],
    ids=["pair-688-692", "pair-689-694", "pair-690-693"],
)
def test_observed_duplicate_pairs_resolve_to_canonical(
    objective, canonical_issue, closed_duplicate
):
    """Each observed duplicate pair resolves its objective to the open canonical Issue."""
    result = find_canonical_issue(objective, [canonical_issue, closed_duplicate])
    assert result.action == "reuse", (
        f"expected 'reuse' for objective {objective!r}; "
        f"got {result.action!r}; reason: {result.reason!r}"
    )
    assert result.canonical is not None
    assert result.canonical["number"] == canonical_issue["number"], (
        f"expected canonical #{canonical_issue['number']}, "
        f"got #{result.canonical['number']}"
    )
    assert result.canonical["state"] == "open", "canonical must be the open issue, not the duplicate"

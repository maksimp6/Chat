"""RED contract for the scoped Maintainer protected-merge admission decision."""

from scripts.maintainer_merge import admit_merge_request


def request(**overrides):
    value = {
        "repo": "maksimp6/Chat",
        "pr_number": 123,
        "expected_head_sha": "head-123",
        "request_key": "maintain:123:head-123:once",
        "role": "Maintainer",
        "action": "protected_merge",
    }
    value.update(overrides)
    return value


def snapshot(**overrides):
    value = {
        "repo": "maksimp6/Chat",
        "pr_number": 123,
        "base_ref": "master",
        "pr_base_ref": "master",
        "head_sha": "head-123",
        "base_sha": "base-123",
        "final_head_sha": "head-123",
        "final_base_sha": "base-123",
        "snapshot_changed": False,
        "pr_state": "open",
        "merged": False,
        "draft": False,
        "behind_by": 0,
        "required_checks": ["Application tests"],
        "check_runs": [
            {
                "id": 10,
                "name": "Application tests",
                "status": "completed",
                "conclusion": "success",
            }
        ],
        "review_threads": [{"isResolved": True}],
        "review_threads_truncated": False,
    }
    value.update(overrides)
    return value


def decide(req=None, snap=None, **overrides):
    options = {
        "actor_authorized": True,
        "capability_available": True,
        "consumed_requests": set(),
    }
    options.update(overrides)
    return admit_merge_request(req or request(), snap or snapshot(), **options)


def codes(result):
    return {blocker["code"] for blocker in result["blockers"]}


def test_admits_explicit_authorized_request_for_fresh_ready_head():
    result = decide()

    assert result["ready"] is True
    assert result["head_sha"] == "head-123"
    assert result["blockers"] == []


def test_requires_real_capability_and_authorized_actor():
    assert "capability_unavailable" in codes(decide(capability_available=False))
    assert "actor_unauthorized" in codes(decide(actor_authorized=False))


def test_rejects_documentary_intent_and_missing_request_identity():
    assert "invalid_intent" in codes(decide(req=request(action="documentation")))
    assert "invalid_request" in codes(decide(req=request(request_key="")))
    assert "invalid_request" in codes(decide(req=request(expected_head_sha="")))


def test_rejects_replay_and_does_not_consume_a_rejected_request():
    consumed = {"maintain:123:head-123:once"}
    assert "request_replayed" in codes(decide(consumed_requests=consumed))

    empty = set()
    result = decide(capability_available=False, consumed_requests=empty)
    assert result["ready"] is False
    assert empty == set()


def test_rejects_request_bound_to_a_different_pr_or_head():
    assert "request_mismatch" in codes(decide(req=request(pr_number=124)))
    assert "request_mismatch" in codes(decide(req=request(expected_head_sha="older-head")))
    assert "request_mismatch" in codes(decide(req=request(repo="other/repo")))


def test_reuses_readiness_rejections_for_changed_or_unreviewed_pr():
    cases = (
        ({"snapshot_changed": True}, "snapshot_changed"),
        ({"draft": True}, "draft"),
        ({"behind_by": 1}, "behind_master"),
        ({"review_threads": [{"isResolved": False}]}, "review_threads"),
        ({"review_threads_truncated": True}, "review_threads_truncated"),
        ({"required_checks": []}, "required_checks_unconfigured"),
        (
            {
                "check_runs": [
                    {
                        "id": 11,
                        "name": "Application tests",
                        "status": "in_progress",
                        "conclusion": None,
                    }
                ]
            },
            "check_pending",
        ),
    )
    for change, expected in cases:
        assert expected in codes(decide(snap=snapshot(**change)))

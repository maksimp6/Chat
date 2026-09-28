import json
from datetime import timedelta

from agent_office import kpi, labels, observer
from agent_office.observer import GitHub, build_thread, detect_findings
from tests.test_agent_office_observer import (
    NOW,
    FakeGitHub,
    comment,
    http_error,
    issue,
    pr_item,
    pull,
    review,
    run,
    ts,
)

WEEK = timedelta(days=7)


def merged_pr(number, opened_hours_ago, merged_hours_ago, ref="codex/x", reviews=0):
    item = pr_item(number, hours_ago=opened_hours_ago)
    item["state"] = "closed"
    item["closed_at"] = ts(merged_hours_ago)
    timeline = [review("Copilot", merged_hours_ago + 0.1) for _ in range(reviews)]
    timeline.append(
        {"event": "merged", "actor": {"login": "maksimp6"}, "created_at": ts(merged_hours_ago)}
    )
    return build_thread(item, timeline, pull(ref=ref, merged_at=ts(merged_hours_ago)))


def closed_pr(number, closed_hours_ago, ref="codex/x"):
    item = pr_item(number, hours_ago=closed_hours_ago + 5)
    item["state"] = "closed"
    item["closed_at"] = ts(closed_hours_ago)
    return build_thread(item, [], pull(ref=ref))


def test_kpis_per_agent_over_the_window():
    threads = [
        merged_pr(1, 10, 6, reviews=2),
        merged_pr(2, 30, 2, reviews=0),
        merged_pr(3, 400, 300),  # merged before the window
        closed_pr(4, 5),
        closed_pr(5, 500),  # closed before the window
        build_thread(pr_item(6), [], pull(ref="claude/y"), [run("tests", "failure")]),
        build_thread(pr_item(7), [], pull(ref="claude/z"), [run("tests", "success")]),
        build_thread(
            issue(8),
            [comment("maksimp6", "@codex go", 4), comment("maksimp6", "@codex again", 3)],
        ),
        build_thread(issue(9), [comment("maksimp6", "@alice go", 300)]),
        build_thread(pr_item(10), [], pull(ref="feat/human")),
    ]
    findings = [f for t in threads for f in detect_findings(t, NOW)]
    kpis = kpi.compute_kpis(threads, findings, NOW, WEEK)

    codex = kpis["codex"]
    assert (codex.merged, codex.closed_unmerged) == (2, 1)
    assert codex.median_lead_hours == 16
    assert codex.mean_reviews == 1
    assert codex.tasks_given == 1
    assert codex.stuck == 1  # issue 8: Codex silent after the task

    claude = kpis["claude"]
    assert (claude.open_prs, claude.red_ci) == (2, 1)
    assert claude.median_lead_hours is None and claude.mean_reviews is None
    assert kpis["alice"].tasks_given == 0

    table = "\n".join(kpi.render_kpi_table(kpis, WEEK))
    assert "## KPI агентов за 7 дн." in table
    assert "| Codex | 2 | 1 | 16.0 ч | 1.0 | 0 (0) | 1 | 1 |" in table
    assert "| Claude | 0 | 0 | — | — | 2 (1) | 0 |" in table
    assert kpi._duration(72) == "3.0 д"


def test_merge_time_falls_back_to_closed_at():
    item = pr_item(1, hours_ago=10)
    item["closed_at"] = ts(4)
    thread = build_thread(item, [], pull(ref="codex/x", merged_at=ts(4)))
    assert kpi._merged_at(thread) == NOW - timedelta(hours=4)


def test_label_changes_keep_exactly_the_owner_label():
    thread = build_thread(pr_item(1), [], pull(ref="codex/x"))
    thread.labels = ["agent:claude", "bug"]
    assert labels.label_changes(thread) == (["agent:codex"], ["agent:claude"])
    thread.labels = ["agent:codex"]
    assert labels.label_changes(thread) == ([], [])
    human = build_thread(pr_item(2), [], pull(ref="feat/x"))
    assert labels.label_changes(human) == ([], [])
    closed = closed_pr(3, 1)
    assert labels.label_changes(closed) == ([], [])


def test_apply_agent_labels_creates_labels_then_edits_items():
    codex = build_thread(pr_item(1), [], pull(ref="codex/x"))
    codex.labels = ["agent:claude"]
    done = build_thread(pr_item(2), [], pull(ref="claude/x"))
    done.labels = ["agent:claude"]
    fake = FakeGitHub(
        {
            "POST /repos/o/r/labels": http_error(422),
            "POST /repos/o/r/issues/1/labels": [],
            "DELETE /repos/o/r/issues/1/labels/agent%3Aclaude": [],
        }
    )
    gh = GitHub("t", "o/r", opener=fake)
    assert labels.apply_agent_labels(gh, [codex, done]) == ["#1: +agent:codex, -agent:claude"]
    assert sum(call[0] == "POST" and call[1] == "/repos/o/r/labels" for call in fake.calls) == 4
    assert json.loads(fake.calls[4][2]) == {"labels": ["agent:codex"]}
    assert labels.apply_agent_labels(GitHub("t", "o/r", opener=FakeGitHub({})), [done]) == []


def test_weekly_slot_and_snapshot_comment():
    monday_six = NOW.replace(year=2026, month=9, day=28, hour=6)
    assert monday_six.weekday() == 0
    assert observer.is_weekly_slot(monday_six)
    assert not observer.is_weekly_slot(monday_six + timedelta(hours=1))
    assert not observer.is_weekly_slot(monday_six + timedelta(days=1))

    fake = FakeGitHub({"POST /repos/o/r/issues/99/comments": {"id": 1}})
    lines = ["## KPI", "", "| table |"]
    observer.post_weekly_snapshot(
        GitHub("t", "o/r", opener=fake), "https://github.com/o/r/issues/99", lines, NOW
    )
    body = json.loads(fake.calls[0][2])["body"]
    assert body.startswith("Снимок KPI на 2026-09-28") and "| table |" in body


def test_run_labels_and_saves_weekly_snapshot():
    open_pr = pr_item(5, hours_ago=3)
    fake = FakeGitHub(
        {
            "GET /repos/o/r/issues?state=open&labels=": [issue(99)],
            "GET /repos/o/r/issues?state=open": [open_pr],
            "GET /repos/o/r/issues?state=closed": [],
            "GET /repos/o/r/issues/5/timeline": [],
            "GET /repos/o/r/pulls/5": pull(ref="copilot/x"),
            "GET /repos/o/r/commits/abc/check-runs": {"check_runs": []},
            "POST /repos/o/r/labels": {},
            "POST /repos/o/r/issues/5/labels": [],
            "PATCH /repos/o/r/issues/99": {"html_url": "https://github.com/o/r/issues/99"},
            "POST /repos/o/r/issues/99/comments": {"id": 1},
        }
    )
    gh = GitHub("t", "o/r", opener=fake)
    result = observer.run(gh, now=NOW, publish=True, apply_labels=True, weekly=True)
    assert result["label_changes"] == ["#5: +agent:copilot"]
    assert result["weekly_snapshot"] is True
    assert result["kpis"]["copilot"]["open_prs"] == 1
    assert "## KPI агентов за 7 дн." in result["digest"]


def test_main_passes_label_and_snapshot_flags(monkeypatch):
    seen = {}

    def fake_run(gh, **options):
        seen.update(options)
        return {"digest": "", "findings": [], "threads": []}

    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(observer, "run", fake_run)
    observer.main(["--repo", "o/r", "--apply-labels", "--weekly-snapshot"])
    assert seen == {"publish": False, "apply_labels": True, "weekly": True}
    observer.main(["--repo", "o/r"])
    assert seen["weekly"] is None and seen["apply_labels"] is False

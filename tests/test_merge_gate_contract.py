from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"


def test_merge_gate_requires_current_master_and_exact_head_ci() -> None:
    policy = AGENTS.read_text(encoding="utf-8")

    assert "Merge is fail-closed." in policy
    assert "behind master = 0" in policy
    assert "exact current PR head" in policy
    assert "older base" in policy


def test_merge_gate_rechecks_when_master_moves() -> None:
    policy = " ".join(AGENTS.read_text(encoding="utf-8").split())

    assert "If `master` advances before merge" in policy
    assert "Do not reuse green checks from the stale head." in policy


def test_merge_gate_uses_one_review_cycle_and_protected_merge() -> None:
    policy = " ".join(AGENTS.read_text(encoding="utf-8").split())

    assert "GitHub Copilot reviews pull requests automatically." in policy
    assert "Do not manually request a Copilot review" in policy
    assert "Request `@codex review` exactly once" in policy
    assert "do not request a second review cycle" in policy
    assert "Use protected auto-merge while required checks" in policy
    assert "reports the pull request as already `clean`" in policy
    assert "protected GitHub merge" in policy
    assert "exact current head SHA" in policy
    assert "Never force-update" in policy

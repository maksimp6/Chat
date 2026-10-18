from pathlib import Path


SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / ".codex"
    / "skills"
    / "cloudru-management"
    / "SKILL.md"
)
LEGACY_MASTER_ROUTE = "Work from fresh `master` in a focused branch; publish a PR."


def has_develop_only_branch_route(instructions: str) -> bool:
    return (
        "Work from the current `develop` HEAD" in instructions
        and "ordinary PRs target `develop` only" in instructions
        and LEGACY_MASTER_ROUTE not in instructions
    )


def test_cloudru_skill_uses_develop_only_route():
    instructions = SKILL_PATH.read_text(encoding="utf-8")

    assert has_develop_only_branch_route(instructions)


def test_negative_control_rejects_legacy_master_route():
    incorrect_instructions = (
        "Work from fresh `master` in a focused branch; publish a PR."
    )

    assert not has_develop_only_branch_route(incorrect_instructions)

import pytest

from agent_office.role_aliases import (
    AgentAliasError,
    CANONICAL_AGENT_ALIASES,
    parse_agent_aliases,
    resolve_agent_alias,
)


def test_all_canonical_agent_aliases_are_stable():
    assert set(CANONICAL_AGENT_ALIASES) == {
        "agent-test",
        "agent-contract-review",
        "agent-implement",
        "agent-review",
        "agent-maintain",
        "agent-observe",
    }


def test_parser_returns_unique_aliases_in_first_seen_order():
    text = (
        "@agent-test write the RED contract, then @agent-contract-review approve it; "
        "after that @agent-implement may work. Do not repeat @agent-test."
    )

    assert parse_agent_aliases(text) == (
        "agent-test",
        "agent-contract-review",
        "agent-implement",
    )


def test_provider_mentions_are_not_role_aliases():
    assert parse_agent_aliases("@claude @codex @copilot @alice") == ()
    assert parse_agent_aliases("@agent-implement using @claude") == ("agent-implement",)


def test_unknown_agent_alias_fails_closed():
    with pytest.raises(AgentAliasError, match="unknown agent alias"):
        parse_agent_aliases("@agent-do-everything")


def test_test_alias_has_fixed_non_implementation_role():
    dispatch = resolve_agent_alias("agent-test")

    assert dispatch.stage == "test"
    assert dispatch.role == "test-engineer"
    assert dispatch.requires_concrete_role is False
    assert dispatch.can_implement is False
    assert dispatch.can_merge is False
    assert dispatch.can_bypass_approval is False


def test_contract_review_alias_is_team_lead_and_cannot_implement():
    dispatch = resolve_agent_alias("agent-contract-review")

    assert dispatch.stage == "contract-review"
    assert dispatch.role == "team-lead"
    assert dispatch.can_implement is False
    assert dispatch.can_merge is False


def test_implementation_alias_requires_a_concrete_engineering_role():
    with pytest.raises(AgentAliasError, match="concrete implementation role"):
        resolve_agent_alias("agent-implement")

    dispatch = resolve_agent_alias("agent-implement", concrete_role="infra-engineer")
    assert dispatch.stage == "implement"
    assert dispatch.role == "infra-engineer"
    assert dispatch.can_implement is True
    assert dispatch.can_merge is False
    assert dispatch.can_bypass_approval is False


def test_implementation_alias_rejects_non_implementation_roles():
    for role in ("team-lead", "operations-observer", "security-reviewer", "release-manager"):
        with pytest.raises(AgentAliasError, match="not an implementation role"):
            resolve_agent_alias("agent-implement", concrete_role=role)


def test_review_alias_requires_a_review_role_and_cannot_merge():
    with pytest.raises(AgentAliasError, match="concrete review role"):
        resolve_agent_alias("agent-review")

    dispatch = resolve_agent_alias("agent-review", concrete_role="security-reviewer")
    assert dispatch.stage == "review"
    assert dispatch.role == "security-reviewer"
    assert dispatch.can_implement is False
    assert dispatch.can_merge is False


def test_maintain_alias_keeps_merge_and_approval_boundaries_separate():
    dispatch = resolve_agent_alias("agent-maintain")

    assert dispatch.stage == "maintain"
    assert dispatch.role == "release-manager"
    assert dispatch.can_implement is False
    assert dispatch.can_merge is True
    assert dispatch.can_bypass_approval is False


def test_observe_alias_is_read_only_supervision():
    dispatch = resolve_agent_alias("agent-observe")

    assert dispatch.stage == "observe"
    assert dispatch.role == "operations-observer"
    assert dispatch.can_implement is False
    assert dispatch.can_merge is False
    assert dispatch.can_bypass_approval is False


def test_fixed_alias_role_cannot_be_overridden():
    with pytest.raises(AgentAliasError, match="fixed role"):
        resolve_agent_alias("agent-test", concrete_role="backend-engineer")


def test_unknown_alias_cannot_be_resolved():
    with pytest.raises(AgentAliasError, match="unknown agent alias"):
        resolve_agent_alias("agent-root")

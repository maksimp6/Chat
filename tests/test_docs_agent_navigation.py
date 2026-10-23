"""Docs navigation separates Logos runtime agents from repository roles (#569)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
PAGES = ("README.md", "docs/README.md", "docs/agents/overview.md")


def _links(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    return [target for target in LINK.findall(text) if "://" not in target]


@pytest.mark.parametrize("page", PAGES)
def test_runtime_agents_and_repository_roles_are_both_linked(page):
    resolved = {((ROOT / page).parent / target).resolve() for target in _links(page)}
    assert (ROOT / "docs/agents/role-based-agent-office.md").resolve() in resolved
    if page != "docs/agents/overview.md":
        assert (ROOT / "docs/agents/overview.md").resolve() in resolved


@pytest.mark.parametrize("page", PAGES)
def test_relative_links_resolve(page):
    for target in _links(page):
        assert ((ROOT / page).parent / target).exists(), f"{page}: {target}"


def test_overview_states_the_two_agent_levels():
    text = (ROOT / "docs/agents/overview.md").read_text(encoding="utf-8")
    assert "## Два уровня агентов" in text
    assert ".github/agents/" in text

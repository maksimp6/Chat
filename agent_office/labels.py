"""Keep one `agent:<name>` label on every open issue and PR an agent owns.

The label makes the office filterable in GitHub (for example
`is:open label:agent:codex`) without reading the digest.
"""

from __future__ import annotations

import urllib.parse

from agent_office.observer import AGENT_LABELS, AGENTS, GitHub, Thread, ensure_label

PREFIX = "agent:"
COLORS = {"claude": "d97757", "codex": "10a37f", "copilot": "6e40c9", "alice": "f9d0c4"}


def agent_label(agent: str) -> str:
    return f"{PREFIX}{agent}"


def label_changes(thread: Thread) -> tuple[list[str], list[str]]:
    """Labels to add and remove so an open thread carries exactly its owner's label."""
    if thread.state != "open" or thread.owner_agent not in AGENTS:
        return [], []
    wanted = agent_label(thread.owner_agent)
    add = [] if wanted in thread.labels else [wanted]
    remove = [label for label in thread.labels if label.startswith(PREFIX) and label != wanted]
    return add, remove


def apply_agent_labels(gh: GitHub, threads: list[Thread]) -> list[str]:
    """Label every open agent-owned issue and PR; returns what changed."""
    plans = [(thread, *label_changes(thread)) for thread in threads]
    plans = [plan for plan in plans if plan[1] or plan[2]]
    if not plans:
        return []
    for agent in AGENTS:
        ensure_label(gh, agent_label(agent), COLORS[agent], f"Work owned by {AGENT_LABELS[agent]}")
    changes = []
    for thread, add, remove in plans:
        if add:
            gh.request("POST", gh.repo_path(f"/issues/{thread.number}/labels"), {"labels": add})
        for label in remove:
            quoted = urllib.parse.quote(label, safe="")
            gh.request("DELETE", gh.repo_path(f"/issues/{thread.number}/labels/{quoted}"))
        changes.append(
            f"#{thread.number}: " + ", ".join([f"+{a}" for a in add] + [f"-{r}" for r in remove])
        )
    return changes

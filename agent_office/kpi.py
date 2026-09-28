"""Per-agent KPIs for the agent office, computed from the observer's threads.

For each agent over the KPI window (a week by default):

- merged PRs, and PRs closed without a merge (work thrown away or redone);
- median time from opening a PR to its merge;
- reviews per merged PR (Copilot, Codex and people; more rounds, more rework);
- open PRs now, and how many of them have red CI;
- issues handed to the agent, and items the observer flags as stuck.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, median

from agent_office.observer import AGENT_LABELS, AGENTS, Finding, Thread, checks_state


@dataclass
class AgentKpi:
    agent: str
    merged: int = 0
    closed_unmerged: int = 0
    lead_hours: list[float] = field(default_factory=list)
    reviews_per_merge: list[int] = field(default_factory=list)
    open_prs: int = 0
    red_ci: int = 0
    tasks_given: int = 0
    stuck: int = 0

    @property
    def median_lead_hours(self) -> float | None:
        return median(self.lead_hours) if self.lead_hours else None

    @property
    def mean_reviews(self) -> float | None:
        return mean(self.reviews_per_merge) if self.reviews_per_merge else None


def _merged_at(thread: Thread) -> datetime | None:
    merges = [event.at for event in thread.events if event.kind == "merged"]
    return max(merges) if merges else thread.closed_at


def compute_kpis(
    threads: list[Thread], findings: list[Finding], now: datetime, window: timedelta
) -> dict[str, AgentKpi]:
    since = now - window
    kpis = {agent: AgentKpi(agent) for agent in AGENTS}

    for thread in threads:
        kpi = kpis.get(thread.owner_agent)
        if thread.kind == "issue":
            for agent in {agent for agent, at in thread.dispatches if at >= since}:
                kpis[agent].tasks_given += 1
            continue
        if kpi is None:
            continue
        if thread.state == "open":
            kpi.open_prs += 1
            kpi.red_ci += checks_state(thread) == "failed"
        elif thread.state == "merged":
            merged_at = _merged_at(thread)
            if merged_at and merged_at >= since:
                kpi.merged += 1
                kpi.lead_hours.append((merged_at - thread.created_at).total_seconds() / 3600)
                kpi.reviews_per_merge.append(
                    sum(event.kind == "reviewed" for event in thread.events)
                )
        elif thread.closed_at and thread.closed_at >= since:
            kpi.closed_unmerged += 1

    for agent in AGENTS:
        kpis[agent].stuck = len({f.number for f in findings if f.agent == agent})
    return kpis


def _duration(hours: float | None) -> str:
    if hours is None:
        return "—"
    return f"{hours / 24:.1f} д" if hours >= 48 else f"{hours:.1f} ч"


def render_kpi_table(kpis: dict[str, AgentKpi], window: timedelta) -> list[str]:
    days = max(1, round(window.total_seconds() / 86400))
    lines = [
        f"## KPI агентов за {days} дн.",
        "",
        "| Агент | Смержено | Закрыто без мержа | Медиана до мержа | Ревью на PR "
        "| Открыто PR (красный CI) | Задач выдано | Зависло |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for agent in AGENTS:
        kpi = kpis[agent]
        reviews = "—" if kpi.mean_reviews is None else f"{kpi.mean_reviews:.1f}"
        lines.append(
            f"| {AGENT_LABELS[agent]} | {kpi.merged} | {kpi.closed_unmerged} "
            f"| {_duration(kpi.median_lead_hours)} | {reviews} "
            f"| {kpi.open_prs} ({kpi.red_ci}) | {kpi.tasks_given} | {kpi.stuck} |"
        )
    return lines

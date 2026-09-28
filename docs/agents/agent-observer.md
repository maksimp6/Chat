# Agent observer

`agent_office/observer.py` watches the agent office: Claude, Codex, Copilot and
Alice working through issues and pull requests in this repository.

Every hour `.github/workflows/agent-observer.yml` runs it. It:

1. reads open issues and pull requests, plus the ones closed in the last day;
2. builds one chronological thread per item from its GitHub timeline, naming the
   agent behind each step (bot logins, `claude/`/`codex/`/`copilot/`/`alice/`
   branch prefixes, and the Claude Code footer on posts made with the owner's
   token);
3. flags stuck work: red CI, merge conflicts, a ready PR without a Copilot review
   or a Codex test check, a green reviewed PR nobody merged, checks pending too
   long, an agent silent after it was given a task, and PRs or agent tasks idle for a
   day (issues nobody handed to an agent are backlog, not stuck work);
4. computes KPIs per agent over the last 7 days (`agent_office/kpi.py`): merged
   PRs, PRs closed without a merge, median time from opening a PR to its merge,
   reviews per merged PR, open PRs and how many are red, issues handed to the
   agent, and items flagged as stuck;
5. keeps exactly one `agent:claude`, `agent:codex`, `agent:copilot` or
   `agent:alice` label on each open issue and PR an agent owns
   (`agent_office/labels.py`), so `is:open label:agent:codex` shows one agent's
   desk;
6. replaces the body of the open issue labelled `agent-observer` with the digest
   (the issue is created on the first run) and uploads the threads as JSON. The
   run on Monday 06:17 UTC also saves the KPI table there as a comment, which
   keeps a weekly history.

Apart from that issue and the agent labels, the observer only reads GitHub. It
never mentions agents, pushes or merges; mentions quoted in the digest are
neutralised so editing the issue cannot wake an agent. The maintainer acts on
the findings as described in `AGENTS.md`.

Run it locally (read-only unless `--publish` or `--apply-labels` is given;
`--weekly-snapshot` saves the KPI comment right away):

```sh
GITHUB_TOKEN=... python -m agent_office.observer --repo maksimp6/Chat --json-out threads.json
```

Thresholds live in `Thresholds` at the top of the module.

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
4. replaces the body of the open issue labelled `agent-observer` with the digest
   (the issue is created on the first run) and uploads the threads as JSON.

The observer only reads GitHub and edits that one issue. It never comments,
mentions agents, pushes or merges; mentions quoted in the digest are
neutralised so editing the issue cannot wake an agent. The maintainer acts on
the findings as described in `AGENTS.md`.

Run it locally (read-only unless `--publish` is given):

```sh
GITHUB_TOKEN=... python -m agent_office.observer --repo maksimp6/Chat --json-out threads.json
```

Thresholds live in `Thresholds` at the top of the module.

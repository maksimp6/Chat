# Support

Logos is actively evolving. Items the [README](README.md#current-status) lists as
experimental or roadmap may change without backward compatibility, and the open
scope lives in Issues.

Before opening a request, check the README, the documentation, existing Issues
and recent pull requests.

## Start with the documentation

| Topic | Where |
| --- | --- |
| Install, configure and run | [Installation guide](docs/setup/installation.md), [README quick start](README.md#quick-start) |
| Health check after start | [Installation guide: health check](docs/setup/installation.md#проверка-работоспособности) |
| Logs and finding errors | [Execution Trace and system log](docs/backend/logging.md) |
| Developer and diagnostic scripts | [Scripts guide](scripts/README.md) |
| Everything else | [Documentation index](docs/README.md) |

## Where to ask

| You have | Go to |
| --- | --- |
| A question, an idea or something to decide | [GitHub Discussions](https://github.com/maksimp6/Chat/discussions) |
| A bug or problem that does not fit an issue template | [GitHub Discussions](https://github.com/maksimp6/Chat/discussions) |
| A focused task for a Logos role agent or coding-agent backend | [Issues](https://github.com/maksimp6/Chat/issues/new/choose): **Agent task** template ([`agent_task.md`](.github/ISSUE_TEMPLATE/agent_task.md)) |
| One short, outcome-focused development sprint | [Issues](https://github.com/maksimp6/Chat/issues/new/choose): **Sprint** template ([`sprint.md`](.github/ISSUE_TEMPLATE/sprint.md)) |
| A security vulnerability or leaked credential | **Never in public.** Follow [SECURITY.md](SECURITY.md) and report privately. |

Issues define implementation scope and acceptance criteria; Discussions capture
questions and decisions. Blank issues are disabled, so new issues start from one
of the templates above. A discussion does not by itself mark a feature complete.

## What to include

- the `develop` commit SHA you run (`git rev-parse HEAD`);
- the platform and how you started Logos (`python app.py`, `start.sh`, Termux);
- exact steps, expected behavior and actual behavior;
- the `/healthz` result when the server does not respond;
- `trace_id` / `invocation_id` of the failing request and short, sanitized
  excerpts from ExecutionTrace or `logs/system.jsonl`;
- screenshots, if the problem is visible in the web interface.

Never paste API keys, IAM tokens, bearer tokens, passwords, private URLs,
database credentials or personal data. Review logs, traces and project dumps before sharing them: they
can contain private data.

## Contributing

To change code or documentation, read [CONTRIBUTING.md](CONTRIBUTING.md) and
[AGENTS.md](AGENTS.md). Participation follows the
[Code of Conduct](CODE_OF_CONDUCT.md).

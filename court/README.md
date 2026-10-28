# `court`

Logos Court: every error becomes a case. A failed invocation is filed as a GitHub issue `Суд Логоса: <error>` with the label `from-dialog`, the episode card of the [Court procedure](../docs/agents/logos-court-procedure.md) and a link to the canonical case #1413; a repeat of the same error is added to the open case as a comment. The rule is in [AGENTS.md](../AGENTS.md), «Ошибки и дела Суда».

## Public API

`from court import …`: `filing`.

| Module | Purpose |
| --- | --- |
| [`court.filing`](filing.py) | File a Logos Court case for every error (AGENTS.md, «Ошибки и дела Суда»). |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `court.filing.file_case` | `invocation` |

## Ports

None. The unit imports only the standard library and its own modules.

## Configuration

| Variable | Meaning |
| --- | --- |
| `LOGOS_COURT_TOKEN`, else `GITHUB_TOKEN`, else `GH_TOKEN` | Token that may create issues and comments. Without one no case is filed and the error is logged as `court case not filed`. |
| `LOGOS_COURT_REPO` | Repository of the Court, `maksimp6/Chat`. |

## Install

- `composition/wiring.py` passes `court.filing.file_case` to the `invocation` host as `file_court_case`; `invocation.manager.fail_invocation` calls it for every failed invocation.

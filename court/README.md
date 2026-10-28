# `court`

Logos Court: every error becomes a case. A failed invocation is filed as a GitHub issue `Суд Логоса: <error>` with the label `from-dialog`, the episode card of the [Court procedure](../docs/agents/logos-court-procedure.md) and a link to the canonical case #1413; a repeat of the same error is added to the open case as a comment. The rule is in [AGENTS.md](../AGENTS.md), «Ошибки и дела Суда».

The repository is public, so the issue carries no dialog text. The full error, partial answer included, is encrypted with [age](https://age-encryption.org) to the key of the current month and committed through Git LFS to the branch `data/court-evidence` (`court/YYYY-MM/<fingerprint>/<invocation>.json.age`); GitHub serves it back and its SHA-256 is checked before the card links it. A month's key is published on the same branch (`keys/YYYY-MM.agekey`) from the first day of the month after next: every piece of evidence stays sealed for at least a month, then anyone can read it.

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
| `LOGOS_COURT_KEYS_DIR` | Directory of the month keys (`YYYY-MM.agekey`, made on first use; `.published` marks a published one), `~/.config/logos/court-keys`. |

The device needs `git`, `git-lfs`, `age` and `age-keygen` on `PATH`; without them the case is still filed and its card says why the evidence was not stored.

## Install

- `composition/wiring.py` passes `court.filing.file_case` to the `invocation` host as `file_court_case`; `invocation.manager.fail_invocation` calls it for every failed invocation.

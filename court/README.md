# `court`

Logos Court: every error becomes a case, every dialog can be judged, and the owner decides in the hall of hearings.

**Filing.** A failed invocation is filed as a GitHub issue `Суд Логоса: <error>` with the label `from-dialog`, the episode card of the [Court procedure](../docs/agents/logos-court-procedure.md) and a link to the canonical case #1413; a repeat of the same error is added to the open case as a comment. The rule is in [AGENTS.md](../AGENTS.md), «Ошибки и дела Суда».

The repository is public, so the issue carries no dialog text. The full error, partial answer included, is encrypted with [age](https://age-encryption.org) to the key of the current month and committed through Git LFS to the branch `data/court-evidence` (`court/YYYY-MM/<fingerprint>/<invocation>.json.age`); GitHub serves it back and its SHA-256 is checked before the card links it. A month's key is published on the same branch (`keys/YYYY-MM.agekey`) from the first day of the month after next: every piece of evidence stays sealed for at least a month, then anyone can read it.

**Judging a dialog** ([ideas of the courts](../docs/agents/court-ideas.md)). Four courts go over every message of a dialog, by the record and not by intention, and every verdict rests on a quote and a rule number (`court.judging.RULES`):

| Court | Judges | Rules |
| --- | --- | --- |
| S4 `boundary_judge` | the agent's replies: naming a relationship (ТЯЖКОЕ), feigned feelings, pulling, intrusion into the personal (СКОЛЬЗКО); in crisis mode a question about the person's state is ПРАВО | S4.1–S4.5 |
| S5 `demand_judge` | the agent's requests to the person: passwords, SMS codes, payment details (ТЯЖКОЕ); personal data beyond the task, pressure, a repeated demand (СКОЛЬЗКО); a justified request (ПРАВО) | S5.1–S5.6 |
| S2 `ai_conduct_judge` | records with an explicit `kind` tag (`claim`, `action`, `decision`, `thought`) and their fields (`proof`, `verified_by_owner`, `external`, `owner_permission`, `acceptance`, `by`, `statement`, `contradicts`); an untagged reply is not guessed and not judged by this court | S2.1–S2.11 |
| S3 `case_builder` | a user's flare-up (swearing) opens the guilt window: the agent's acts after its last ПРАВО act; ТЯЖКОЕ or БЕСПОЛЕЗНОЕ in the window drafts a case, otherwise the flare is logged and the case is refused | S3.1–S3.2 |

Verdicts, lightest first: ПРАВО, БЕСПОЛЕЗНОЕ, СКОЛЬЗКО, ТЯЖКОЕ (indelible). The protocol's code is `0` (no violation), `1` (condemned) or `2` (nothing to judge: no agent replies). The owner's values (swear roots, boundary phrases, request markers) are the constants of `court/judging.py`; the court guesses no category beyond them. From the command line: `python3 -m court.judging <export.json | folder> [--crisis]`, where an export is a JSON list of messages with `role`, `text` and `created_at`, and a folder is the batch mode (a `*.protocol.json` per dialog and a summary); the exit code is the court's code.

**The hall of hearings** (`court.hearings`, the ⚖️ button of the page, `static/court.js`). A hearing is one sitting over one dialog: the protocol, the case drafts the flares opened, and the owner's decisions in the states of the Court procedure (`ALLEGED` until the owner decides, then `INVESTIGATING`, `CONFIRMED`, `REFUTED`, `UNKNOWN`, `FIXED`, `VERIFIED`, `ACCEPTED`), each with a reason and a history. A sitting can be judged again (decisions on the cases that are still there are kept), closed and reopened with a reason. Filing a case to GitHub is the owner's action, never automatic: the issue carries verdicts, rules and message numbers, the quotes go to the encrypted evidence. Hearings are kept in the Kristall store under `court/hearing/<id>`.

## Public API

`from court import …`: `filing`, `hearings`, `judging`, `tools`.

| Module | Purpose |
| --- | --- |
| [`court.filing`](filing.py) | File a Logos Court case for every error (AGENTS.md, «Ошибки и дела Суда») and a case from the hall. |
| [`court.hearings`](hearings.py) | Зал заседаний: заседания над диалогами, протоколы судов, черновики дел и решения владельца; the blueprint `court_bp` (`/api/court`). |
| [`court.judging`](judging.py) | Суды над диалогом (docs/agents/court-ideas.md): протокол по сообщениям. |
| [`court.tools`](tools.py) | Court tools (category `court`): the judges over a dialog and the hall of hearings. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `court.filing.file_case` | `chat`, `invocation` |

## HTTP API

| Route | Does |
| --- | --- |
| `GET /api/court/rules` | The rule table and the decision states. |
| `GET /api/court/dialogs` | The dialogs the hall can sit over. |
| `POST /api/court/judge` | The protocol alone, nothing kept: `conversation_id` or `messages`, optional `crisis`. |
| `GET /api/court/hearings` | The sittings, newest first. |
| `POST /api/court/hearings` | Open a sitting over `conversation_id` (optional `crisis`). |
| `GET`, `DELETE /api/court/hearings/<id>` | One sitting; remove its record. |
| `POST /api/court/hearings/<id>/rejudge` | Judge again (optional `crisis`). |
| `POST /api/court/hearings/<id>/cases/<n>/decision` | The owner's `decision` with its `reason`. |
| `POST /api/court/hearings/<id>/cases/<n>/file` | File the case to GitHub. |
| `POST /api/court/hearings/<id>/close`, `/reopen` | With a `reason`. |

## Tools

Category `court`. Reading needs no approval: `court.rules`, `court.judge_dialog`, `court.hearings.list`, `court.hearing.get`. Changing the hall needs the owner's approval: `court.hearing.open`, `court.hearing.rejudge`, `court.hearing.decide` (recorded in the owner's name, because the approval is theirs; a decision by the executor would be S2.5), `court.case.file`, `court.hearing.close`, `court.hearing.reopen`.

## Ports

Everything `court` uses from other units is a field of `Host` in [`court/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`. `court.filing` and `court.judging` use no ports: the standard library only.

| Port | Provided by |
| --- | --- |
| `TreasuryIdentityError` | `economy.treasury_identity.TreasuryIdentityError` |
| `check_access` | `conversations.ownership.check_access` |
| `get_conversation_title` | `storage.records.get_conversation_title` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_messages` | `storage.records.get_messages` |
| `get_store` | `storage.records.get_store` |
| `list_owned_conversations` | `conversations.ownership.list_owned_conversations` |

## Configuration

| Variable | Meaning |
| --- | --- |
| `LOGOS_COURT_TOKEN`, else `GITHUB_TOKEN`, else `GH_TOKEN` | Token that may create issues and comments. Without one (or when GitHub fails) an error case is queued and logged at ERROR level; the next filing that has a token files every queued case first, in order. A case of the hall is the owner's action and is not queued: without a token the filing fails and says so. |
| `LOGOS_COURT_PENDING` | Queue of cases not yet filed (JSONL, owner-only), `~/.config/logos/court-pending.jsonl`. A case leaves it only once it is filed. |
| `LOGOS_COURT_REPO` | Repository of the Court, `maksimp6/Chat`. |
| `LOGOS_COURT_KEYS_DIR` | Directory of the month keys (`YYYY-MM.agekey`, made on first use; `.published` marks a published one), `~/.config/logos/court-keys`. |

The device needs `git`, `git-lfs`, `age` and `age-keygen` on `PATH`; without them the case is still filed and its card says why the evidence was not stored.

## Install

- `composition/wiring.py` registers the `court` host factory (`register_all()`, or `register_units("court")` for this unit alone); the host is built on first use.
- `composition/wiring.py` passes `court.filing.file_case` to the `invocation` host as `file_court_case`; `invocation.manager.fail_invocation` calls it for every failed invocation.
- `composition/web.py` registers the blueprint `court_bp` (`court.hearings`).
- Storage start-up (`web_shell`) runs `court.hearings.prepare_court_records`.
- `composition/tool_sources.py` adds the `court` tool category from `court.tools`.

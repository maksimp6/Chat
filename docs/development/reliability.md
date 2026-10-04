# Reliability rules

Alice runs tools, spends money and changes cloud infrastructure on the user's
behalf, so its code follows flight-software discipline: NASA/JPL "Power of
Ten" adapted to Python and browser JavaScript, plus an explicit idempotency
contract. `tests/test_reliability_rules.py` enforces the mechanical rules as a
ratchet: violation counts may only go down.

## Idempotency

Every operation belongs to one of three classes, stated in its docstring when
it is not obvious from the name.

| Class | Examples | Rule |
|---|---|---|
| Read | list containers, load history, project a trace | No side effects. Safe to call any number of times. |
| Idempotent write | set config, upsert a record, reconcile a lane | Repeating the call with the same input leaves the same state. |
| Effect | send a message, execute an approved tool, charge money, deploy | Must accept an idempotency key and return the first result for a repeated key instead of acting twice. |

- **Retries.** Retry only reads and idempotent writes, or effects that carry an
  idempotency key. A retry loop never re-sends an effect without its key.
- **Reconcilers converge.** Running a reconciler twice in a row produces no
  actions on the second run. They create or update toward desired state and
  never delete without approval.
- **The UI deduplicates.** A repeated click, a re-delivered event or a history
  reload never produces a second message, card or request. Disable a control
  while its request is in flight and render records by id at most once.
- **Keys are stable.** Derive an idempotency key from the caller's intent
  (conversation, message and tool call id), never from a timestamp or random
  value generated on retry.

## Power of Ten, adapted

1. **Simple control flow.** No recursion in production paths; walk nested data
   with an explicit stack and a depth limit. Cyclomatic complexity stays at or
   below 10 (`C901`).
2. **Bounded loops.** Every loop over external input, every retry and every
   poll has a fixed upper bound: a maximum attempt count, a deadline or a size
   limit. No `while True` without a bound and an exit.
3. **Bounded resources.** Caches, queues and buffers have a maximum size.
   Reading external data (HTTP bodies, files, subprocess output) has a byte
   limit.
4. **Small functions.** A function fits on one screen: at most 50 statements
   (`PLR0915`), 12 branches (`PLR0912`) and 6 return points (`PLR0911`).
5. **Check inputs, assert invariants.** Validate at every boundary (HTTP, tool
   arguments, config, subprocess output) and fail closed with a clear error.
   Internal invariants use explicit checks that raise, not `assert`, which
   `python -O` strips.
6. **Smallest scope.** No `global` (`PLW0603`), no mutable class-level defaults
   (`RUF012`), no mutable default arguments (`B006`). State lives in objects
   passed explicitly or in `RuntimeContext`.
7. **Never swallow errors.** No bare `except` (`E722`), no `except: pass` or
   `continue` (`S110`, `S112`). Catching `Exception` (`BLE001`) is allowed only
   at a boundary that logs it and returns an explicit error result or
   re-raises; chain re-raised errors with `from` (`B904`).
8. **No dynamic code.** No `eval` or `exec` (`S307`, `S102`).
9. **Limited indirection.** Prefer plain functions and data over reflection,
   monkey-patching and `getattr` dispatch on untrusted strings.
10. **Zero warnings.** Formatter, linter and these rules pass on every push.
    CI treats a new violation as a failure.

## Working with the ratchet

`tests/test_reliability_rules.py` runs the pinned `ruff` with the rules above
and compares each rule's violation count with `RATCHET_BASELINE`.

- A new violation fails the test. Fix it, don't raise the baseline.
- Fixing violations also fails the test until you lower the baseline to the
  new count in the same change, so the improvement is locked in.
- Rules at zero stay at zero.

## Static types

Python code is checked with `mypy --strict` (configured in `pyproject.toml`,
with `no_site_packages` so results do not depend on installed packages;
pinned in `requirements-dev.txt`). `tests/test_type_checking.py` is a ratchet
with two rules:

- the error count for each mypy error code may only go down;
- files listed in `STRICT_CLEAN_FILES` have zero errors and must stay that way.
  When a file becomes clean, add it to the list in the same change.

New modules are written fully typed: annotate every function, use precise
container types (`dict[str, int]`, not `dict`) and avoid `Any` outside
boundaries that parse external JSON.

## Where the rules run

- **Locally:** `bash scripts/check_code_rules.sh` runs the naming, root-layout,
  reliability and type-checking ratchets (a few seconds; mypy caches results).
- **Pull requests:** the CI job **Code rules** runs the same script and is a
  required check in `merge-readiness.yml`.
- **Deployments:** `production-deploy.yml` and the `deploy` action of
  `cloudru-deploy.yml` run **Code rules** on the exact ref being deployed and
  ship only if it passes. Read-only Cloud.ru actions (`preflight`, `status`,
  `inventory`) skip the gate.

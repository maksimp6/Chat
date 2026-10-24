# Real API acceptance (#1246)

This is a separate, explicitly invoked test type. `pytest` and PR CI never run it.
The deterministic tests in `tests/test_real_api_acceptance.py` test its safety
guards only; their PASS is not a real API result.

## What a PASS proves

The real Flask application, copied from the clean committed Git SHA into an
isolated private runtime, imports an actual Yandex key through
`PUT /api/provider-credentials`, creates a provider conversation, sends `Привет`
through `/api/chat`, receives a nonempty completed provider response with an ID,
and reloads the exact user/assistant history and correlated ExecutionTrace after
a process restart. Provider status/HTTP 200 alone cannot pass. Only one model
operation is expected. No mocks replace the application or provider.

This backend result does **not** prove Android UI input, rendering, installed APK
configuration, or user acceptance. In particular, the separate installed-app
bootstrap failure in #1246 can remain even if an isolated backend passes.

To expose that distinction there is also `--probe-installed`: it uses the
existing application at **127.0.0.1:5000**, bootstraps a separate ordinary
anonymous identity and checks the real conversation/response/history/Trace
chain. It never imports a key, changes encryption settings or restarts the
installed application. Its `test_type=real_api_installed_backend` evidence
cannot claim key-import, at-rest audit, restart or Android UI acceptance.
A cached `connected` followed by conversation creation HTTP 502 is FAIL.
`--installed-build` is an optional operator-observed version, not verified SHA.

## Protected code and private secrets

`/tests/real_api/` is owned by `@maksimp6` in CODEOWNERS. Actual enforcement still
depends on the repository ruleset requiring owner review; a CODEOWNERS entry
alone does not enable branch protection. PRs target **develop**; never merge or
edit the owner's personal master without explicit approval.

The code is public; keys and artifacts must never enter Git or Library uploads.
The existing input JSON must contain `api_key`, be a regular nonsymlink file
owned by the runner UID with mode **0600** in a **0700** directory outside any
Git checkout. Hardlinks and unsafe writable ancestors are rejected. The runner
recognizes only Android's exact system-owned `/data` and `/data/data` (UID/GID
1000, 0771), and Termux's app-owned `files` ancestor (app UID/GID, 0771).
These platform exceptions never relax the final 0700/0600 requirements.
The runner
does not print, replace, or chmod this input. The existing Redmi 9 input is
`~/.config/logos-qwen/yandex-secret.json`; do not paste its value into a command.

Each run gets a new 0700 directory under a private state root. Its local native
MemoryStore, process logs, snapshot of committed code and stable restart
encryption key stay private. The generated key encrypts only the new isolated
test state; it never replaces any production encryption key or existing data.
The real provider key is sent only through the loopback credential API, never
in process arguments or the child environment. Local requests disable proxy
inheritance and redirects. The runtime uses the fixed HTTPS Yandex endpoint.
After shutdown the suite scans its journal/logs/state for plaintext API-key
bytes and fails on a match. A failing run may therefore contain sensitive
diagnostics in its private directory: never publish it wholesale.

Only `result.json` is designed as bounded metadata evidence; review it before
sharing. It has run/SHA/stage/check correlation, not key, raw response, prompt
history, or public process logs. Exit 0 requires the complete real chain.
Missing input, failure or disabled opt-in cannot produce PASS. Private artifacts
remain on the runner for owner-controlled inspection; this suite never clears
existing application state or revokes remote conversations/keys.

## Explicit bounded execution

Install the repository's pinned test/application dependencies first. Commit the
test branch and keep tracked files clean. Prepare an owner-only state root
outside the checkout, then run on the machine which already holds the key:

```bash
mkdir -m 700 -p "$HOME/.local/state/logos-pro/real-api"
python tests/real_api/run.py \
  --allow-paid-api \
  --secret-file "$HOME/.config/logos-qwen/yandex-secret.json" \
  --project-id '<existing-folder-id>' \
  --model logosai-llm-flash \
  --state-root "$HOME/.local/state/logos-pro/real-api" \
  --max-output-tokens 32
```

The flag authorizes a real billable request. There is no hard currency cap;
provider-confirmed spend is reported as unknown unless independently obtained.
The output ceiling is 1–64 tokens, with tools disabled and no runner retry of a
failed chat. Provider/runtime internal behavior is still observable in Trace;
unexpected extra logical model operations fail acceptance. Local HTTP calls
have a 20-second timeout, startup 45 seconds, shutdown up to 15 seconds. After a
timeout, inspect the same private run directory before authorizing another run:
timeout does not prove that a provider stopped generating or charging.

For the installed-app probe on Redmi 9, with its existing stored credential:

```bash
python tests/real_api/run.py \
  --allow-paid-api --probe-installed \
  --model logosai-llm-flash \
  --installed-build 0.1.0-cc82083 \
  --state-root "$HOME/.local/state/logos-pro/real-api"
```

The same explicit cost authorization applies. This does not move the phone's
key elsewhere or pretend to repair #1246. No key file/project input is needed
in this mode. Missing provider/session auth fails closed.

Do not run unattended/recurring paid CI or put secrets into fork/PR jobs. A
future manual CI entry must use a protected GitHub Environment, approved budget,
and a reviewed revision rather than untrusted PR code. No such environment,
workflow, secret, ruleset or production deployment is created by this change.

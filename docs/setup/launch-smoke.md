# P0 local launch smoke

This smoke test turns the launch baseline from issue #427 into a reproducible
check against Alice Pro itself before Cloud.ru deployment work continues.

It does **not** deploy production, mutate Cloud.ru resources, change GitHub
permissions, or rotate credentials.

## What is verified

The script `scripts/local_launch_smoke.py` starts the documented
`python app.py` entrypoint with an isolated SQLite database and verifies:

1. the tracked checkout is clean;
2. Flask starts and `GET /healthz` returns `{"status":"ok"}`;
3. the root UI returns non-empty HTML;
4. a local runtime session can be bootstrapped;
5. the SQLite database exists after shutdown;
6. Alice starts again against the same database;
7. the runtime session survives the restart.

Those checks are the deterministic **offline** half of the P0 baseline. The
workflow runs them on every pull request, including changes to application code,
templates and runtime dependencies. They are deliberately reported as
`PARTIAL`, not as proof that model chat works.

The optional **full** mode additionally verifies the provider-dependent half:

1. configure Yandex through the supported
   `PUT /api/provider-credentials` backend path;
2. require the provider health result to be `connected`;
3. create a real provider conversation through `POST /api/conversations`;
4. send one real request through the canonical `POST /api/chat` path;
5. require a non-empty reply, invocation id and trace id;
6. load the persisted trace through
   `GET /api/invocations/<invocation_id>/trace`;
7. restart Alice;
8. verify the conversation, provider metadata, runtime session and exact trace
   correlation are still readable.

A full result is `PASS` only if every one of those checks succeeds. Missing
provider inputs are `BLOCKED`, never silently treated as success.

## Local commands

Install the declared runtime dependencies first:

```bash
python -m pip install -r requirements.txt
```

Run the deterministic offline slice:

```bash
python scripts/local_launch_smoke.py --offline
```

For the real provider path, inject the two values through the environment. Do
not put them in shell history, Issues, pull requests, screenshots or artifacts.

```bash
export ALICE_LAUNCH_SMOKE_YANDEX_API_KEY='...'
export ALICE_LAUNCH_SMOKE_YANDEX_PROJECT_ID='...'
python scripts/local_launch_smoke.py
```

The script uses a temporary SQLite database by default. To inspect persistence
against an explicit local path:

```bash
python scripts/local_launch_smoke.py --offline --db-path /tmp/alice-launch-smoke.db
```

## GitHub Actions

`.github/workflows/launch-smoke.yml` has two jobs.

The **Offline startup and restart smoke** runs on every pull request and on
manual dispatch. It makes no provider/model call and therefore cannot consume
Yandex inference quota.

The **Live Yandex chat and persisted trace smoke** is opt-in only. It runs only
when a manual workflow dispatch sets `live_provider=true`, checks out
protected `master`, and maps the repository's existing Actions secrets into
the smoke-only environment variable names:

- `YANDEX_API_KEY` → `ALICE_LAUNCH_SMOKE_YANDEX_API_KEY`
- `YANDEX_PROJECT_ID` → `ALICE_LAUNCH_SMOKE_YANDEX_PROJECT_ID`

Those secret names are already used by `.github/workflows/alice.yml`; this
workflow does not create a second credential source. The secret mappings are
scoped only to the live smoke step. It also references the existing
`production` GitHub Environment so its approval boundary remains
separate from ordinary PR CI. Adding or changing credentials remains an
owner-controlled operation and is not performed by this change.

The full job runs `--require-master`. If the checked-out HEAD is not exactly
the fetched `origin/master`, the smoke fails before provider configuration or
model usage.

## Output and secret handling

The script prints one JSON result object. It reports identifiers, check states,
the exact git SHA, database backend and blockers. It never includes the API key,
credential encryption key or provider response bodies containing credentials.

Example offline shape:

```json
{
  "status": "PARTIAL",
  "mode": "offline",
  "database_backend": "sqlite",
  "provider_inputs_present": false,
  "checks": {
    "healthz": true,
    "root_ui": true,
    "session_bootstrap": true,
    "provider_configured": false,
    "real_chat": false,
    "trace_persisted": false,
    "restart_persistence": true
  }
}
```

## Relation to Cloud.ru launch

Passing the local full smoke proves that the current protected application
baseline can start, talk to the configured provider through the canonical path,
persist an ExecutionTrace and survive an application restart.

It does **not** prove Container Apps, EDS, external PostgreSQL, DNS/TLS, backup
restore, or production routing. Those remain separate gates in #427. This order
is intentional: debugging cloud orchestration before proving the application
runtime would merely distribute the same uncertainty across more expensive
machines.

# Redmi 9 updates through the GitHub runner

The existing `redmi9-python` runner is the only automatic application updater.
Every push to `maksimp6/Chat:develop` starts the `server` job in
`.github/workflows/redmi9-app-server.yml`. Manual dispatch with `action=deploy`
uses the same job. Its concurrency group serializes updates without cancelling
an in-flight deployment.

There is no independent Termux updater, supervisor, or 30-second Git polling loop.
The former `redmi_app_autoupdate.py` and its boot supervisor have been removed.
Termux:Boot starts the existing runner through `50-github-python-runner`; no
separate application-update boot entry is installed. If Android force-stops
Termux, reopen it. After a phone reboot, use the runner's deploy job to start the
application; this change does not claim verified autonomous reboot recovery.

## Retirement of the previous installation

Before deployment, the runner executes `scripts/redmi_remove_autoupdate.py` once
as needed. It sets the old disabled marker, waits for the existing worker's lock
instead of killing an in-flight deployment, validates and terminates only the old
supervisor, and removes `60-logos-app-autoupdate` and both active state scripts.
Their contents and prior status receipts are retained as private, non-executable
files under `~/logos-local-state/retired-autoupdate/`. The supervisor log and all
application data remain in place. Subsequent deploys report `already_removed`.

The removal receipt is `~/logos-local-state/autoupdate-removal.json`.
It records the retired paths and SHA-256 hashes, the stopped supervisor PID,
the absence of the boot entry, and backend PIDs before and after removal.
The independent RDC connection and runner lifecycle are not modified.

## Deployment and verification

The existing `scripts/redmi_app_server.sh deploy` fetches the current develop tip
and verifies LFS before stopping an outdated backend. It preserves the journal
and restores previous code if startup/readiness fails. A matching healthy backend
is reused. The controller records `trigger=github_actions` for runner operations
and `manual` for explicit maintenance commands. `local_autoupdate` is rejected.
See [the controller documentation](../redmi-app-server.md) for its exact scope.

Delivery is established by the actual runner job, removal receipt, absence of the
old boot entry and supervisor, and the phone's `/api/version` matching the delivered
revision. A workflow success alone is insufficient. Health/readiness checks do
not invoke a model or prove a complete chat or access from another phone.
Code tests are not started by this change; their status is `NOT_RUN`.

## Loaded interface version

`GET /api/version` returns `runtime_revision`, `ui_version`, `process_id` and
`started_at` captured when the backend starts. Changing the checkout does not
change an older process's revision. `ui_version` hashes static resources and
templates. The interface badge shows the version loaded by that tab.

The browser's 30-second UI-fingerprint check is separate from application
deployment. It reloads only when no request, draft, recording or editing dialog
would be interrupted. An older client without this mechanism needs one initial
reload. A matching backend SHA does not prove that an already open tab has
loaded its current scripts.

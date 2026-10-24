# Update the application on every develop push

The `Redmi 9 application server` workflow runs on every push to `develop`, without
path filters, and supports manual dispatch from that branch. One native Bash step,
`logos`, tests the controller and runs `redmi_app_server.sh deploy`. The workflow's
concurrency group serializes deployments without cancelling a running update.

Deployment fetches the current `develop` tip from the existing application's
`maksimp6/Chat` origin, including the target commit's LFS objects, before stopping
anything. A delayed or retried job uses the current branch tip, not an older event
SHA. Tracked local changes block the update. The previous commit is retained under
`refs/logos/deployments/`, and the stopped application's journal is copied into the
private `deploy-backups` directory before checkout. There is no reset or clean.

The existing `~/logos-local` virtualenv, on-phone launcher and
`~/logos-local-state` remain in use. The controller supplies `LOGOS_MEMORY_PATH`
and reads the existing encryption-key file inside the launcher process, so current
code uses the same journal and key as the older `ALICE_*` launcher. Secrets are not
placed in command-line arguments, logs or Git. No packages or credentials are
created. GitHub job credentials are excluded from the application environment.

After updating, readiness requires the owned application PID, successful health
and HTML responses, byte-for-byte matching `chat.js` and `style.css`, and a process
environment carrying the deployed commit. An already running, verified current
commit is reused. Startup/readiness failure restores the previous code and starts
it again; even a successful rollback leaves the deployment job failed. Journal
data is never automatically replaced by a backup during rollback.

The controller accepts `deploy`, `start`, `restart`, `status`, and `stop`. It signals
only a process with the expected command and working directory and refuses an
occupied untracked port. The existing launcher continues to select the bind
address and port. The launch receipt is `runner-start.json`; application and
launcher logs stay in the private state directory.

Readiness does not invoke a model or prove a model response. Automatic updates do
not change RDC processes, boot schedules, dependency installations or network
settings. Dependency installations and data-format migrations require their own
reviewed changes; the updater does not manage them.

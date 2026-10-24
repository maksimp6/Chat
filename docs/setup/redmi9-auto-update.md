# Redmi 9 automatic application updates

The installed application follows `maksimp6/Chat:develop` through a native Termux
supervisor. It checks the public branch every 30 seconds and runs the existing
`scripts/redmi_app_server.sh deploy` when the branch changes or the application
is no longer healthy. It does not depend on the GitHub Actions job queue.

The supervisor is independent of RDC. It uses the existing Python, Bash, Git,
Git LFS and flock commands and does not register another runner or install packages.
Termux:Boot starts it after a phone restart. Android force-stopping Termux still
requires reopening Termux; a shell process cannot override Android force-stop.

The deployment controller continues to serialize updates, fetch and verify LFS
before stopping the old application, preserve the durable memory journal, and
restore the previous code if the new application fails readiness. A matching
revision with a healthy owned process is left running.

The controller records `trigger=local_autoupdate` for native automatic updates,
`github_actions` for runner updates and `manual` for explicit control commands.
Automatic update status records the observed public revision and the resulting
running revision/PID. An unsuccessful deployment is an error, including a rollback;
it is retried by later checks and is never reported as the requested update succeeding.

Creating `autoupdate.disabled` in `~/logos-local-state` pauses the native updater.
Removing that marker resumes it on the next check. An intentional maintenance stop
should create the marker before stopping the application.

The GitHub server workflow remains available as a second trigger through the same
deployment lock. Full repository archives keep their push trigger and read all
branch/tag/PR refs, but redundant pending archive runs are coalesced. Android
diagnostics run automatically when their own scripts/workflow change, or manually.
They no longer run a duplicate full repository archive.

Validation must include a fresh push followed by an automatic SHA and process
transition on the physical Redmi 9, without calling the deployment command manually.
A successful workflow or localhost HTTP response alone does not prove that chain.

# Start the installed application through the Redmi runner

The `Redmi 9 application server` workflow runs one native Bash step, `logos`,
on `runs-on: redmi9`. It tests the controller, restarts only the identified
application process, and verifies readiness. Pushes to the task branch trigger
the initial deployment check; after merge, manual `workflow_dispatch` is used.
This is not an automatic application-code upgrade or a model invocation.

The controller reuses the existing installation at `~/logos-local`, its virtual
environment and `~/logos-local-state/start-logos.py`. The local launcher supplies
the existing native dependencies, persistent memory path and encryption setting.
It starts `app.py` detached from the job. No dependencies are installed, no
credentials are generated, and no RDC process or boot schedule is changed.

The application binds to `127.0.0.1:8080`. The job's GitHub credentials and other
workflow environment are excluded from the application process. The controller
checks the PID's command and working directory before signalling anything.
An occupied untracked port, foreign PID, startup exit, incorrect health response
or mismatched static asset fails the job; an open port alone never means success.

Readiness covers `/healthz`, the Logos HTML title and byte-for-byte matches for
the installed `chat.js` and `style.css`. It does not prove a model reply or LAN
authentication. Logs remain in `~/logos-local-state/backend.log`, and the safe
launch receipt is `~/logos-local-state/runner-start.json` and the Actions log.

From Termux, the same script accepts `start`, `restart`, `status`, or `stop`.
`start` reuses an already healthy process. `restart` sends TERM only to the
verified app process and refuses to replace a process that does not exit.
The installed app revision is recorded separately from the workflow revision.

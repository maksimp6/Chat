# Native Termux shell runner on Redmi 9

The repository runner `redmi9-python` (runner ID 21) executes small GitHub Actions
shell/Python jobs directly in Termux on Redmi 9 (`galahad`, ARM64). Runtime:
Python standard library and OpenSSL, with no Docker, proot, chroot, .NET, or pip
runtime packages. Pytest is used only for regression tests.

## Use

```yaml
permissions: {}
jobs:
  example:
    runs-on: redmi9
    steps:
      - shell: python
        run: |
          import platform
          print(platform.machine())
```

The dedicated label avoids routing ordinary Linux workflows to this limited
runner. Only literal `run` steps with `bash`, `sh`, or `python` are supported.
Each job gets its own workspace; files persist between steps in that job.
Repository files are not checked out automatically. Use an explicit `git` command
or download the required files at `GITHUB_SHA`. Supported conditions are
`success()`, `failure()`, `always()`, and `cancelled()`; cancellation terminates
the active process group and skips subsequent commands.

Containers, `uses:` actions (including checkout/setup/cache/artifact actions),
unresolved expressions, job outputs/defaults, and `continue-on-error` are rejected
as failures. Supported environment expressions are `${{ github.token }}` and
direct `${{ secrets.NAME }}` references. Only explicitly configured step/job
variables receive those values; other secrets are not exported. Missing secrets
resolve to empty strings, and non-secret job variables cannot be read through
the secrets context. Values are masked before log upload. No credential helper
or runner configuration stores these per-job credentials.
GitHub environment/output command files and live streaming are not implemented.
Standard Actions logs are uploaded to GitHub Results after each step and at job
completion, with token masking before upload. Upload/finalization failure fails
the job; local per-step receipts preserve the output. A redacted output tail is
also attached to annotations; a receipt contains up to 1 MiB per step. Steps default to a
5-minute limit, with a maximum of 15 minutes; the GitHub job timeout can also
cancel execution. This is a custom implementation of the current broker
protocol, not the official runner or a drop-in replacement. Compatibility is
pinned to protocol version `2.338.1` and requires maintenance when GitHub changes
that protocol; no automatic source update occurs.

## Operations on the phone

Installation directory: `~/github-python-runner`. Identity and state remain
outside the repository. Do not copy `identity.pem`, `config.json`, or live job
payloads into Git. No personal access token is stored: the registered RSA
identity obtains short-lived runner credentials.

```sh
python ~/github-python-runner/runner.py status
python ~/github-python-runner/runner.py start
python ~/github-python-runner/runner.py stop
tail -n 30 ~/github-python-runner/runner.log
```

`start` detaches the process from the terminal. A file lock prevents concurrent
listeners; the `serve` command reconnects after recoverable listener failures.
`stop` requests cancellation and graceful disconnection; a busy long poll can
delay shutdown. Termux:Boot invokes `~/.termux/boot/50-github-python-runner`, copied
from `scripts/redmi_python_runner_boot.sh`, and obtains a wake lock. The boot
entry has been installed and invoked directly; reboot recovery requires a
separate reboot test. Android must permit Termux background execution.

To update an already registered installation: stop it, wait until `status`
reports `running: false`, copy `scripts/redmi_python_runner.py` to
`~/github-python-runner/runner.py`, and start it again. Preserve the identity.
Registration provisioning is separate from the verified existing installation;
the `register` command expects an RSA-OAEP/SHA256 encrypted registration token
in `registration.enc`, encrypted to `identity.pem`'s public key.

Only trusted repository code may use this runner. It rejects fork pull requests
and foreign repository jobs, but it is not a security sandbox: scripts run with
the Termux account's access. It does not safely execute hostile contributors'
code. Output redaction is best effort; jobs must not deliberately print secrets.

## Verification

```sh
python -m compileall -q scripts/redmi_python_runner.py tests/test_redmi_python_runner.py
python -m pytest -q tests/test_redmi_python_runner.py
```

Regression tests execute real subprocesses and check success/failure wire
serialization, uppercase GitHub template maps, rejected job types, timeouts
after stdout closes, cancellation, redaction, and failed result delivery.

The first live negative control exposed an integer-enum serialization bug:
GitHub reported success despite a local failure. After correcting wire values
to strings, [attempt 2 of the negative control](https://github.com/maksimp6/Chat/actions/runs/40090101859/attempts/2)
executed `exit 17` on runner 21 and correctly concluded `failure`.
The branch-scoped acceptance workflow subsequently checks the actual device,
compares installed source with its exact commit, runs the regression tests on
the phone, and verifies both shell types and shared workspace.

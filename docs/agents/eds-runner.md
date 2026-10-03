# EDS in the Claude-Lite task runner

The task-execution job in `claude-lite.yml` installs the official EDS v0.4.0
release with `scripts/install_eds.py`. The installer verifies the pinned SHA-256
before execution. Installation has a 30-second limit and the verified binary is
added to the job's PATH. Each new hosted task run installs it again.

In GitHub Settings → Environments → production, add the DevServices key (and a project override only if needed):

| Secret | Value |
| --- | --- |
| `EDS_API_KEY` | DevServices product API key (`X-API-KEY`) |
| `EDS_PROJECT_ID` | Optional project UUID override; otherwise the existing `CLOUDRU_PROJECT_ID` environment variable is used |

The owner authorized adding these keys in the runner setup request. Obtain the
actual values through the owner's authorized secret source or direct entry into
GitHub's secret form. Do not put them in issues, commits, logs, or chat. Cloud.ru
IAM Key ID/Key Secret and model-provider keys are different credentials.

The secrets are injected only into the fixed read-only check in
`eds-runner.yml`. Neither the general Claude-Lite task action nor the tool-free
persistent dialogue receives EDS credentials. A production DevServices key can
authorize destructive operations, so it must not be exposed to model-controlled
Python or shell subprocesses. Keys are not installed into a config file or
restored from session checkpoints. General tasks can inspect the CLI and prepare
commands; authenticated operations need a separately approved, scoped workflow.

After merge and secret entry, run **EDS runner check** on `master` from GitHub
Actions. It independently installs the verified binary and performs one read-only
Repo request with a 30-second limit. Only success/failure is logged; stdout and
stderr from EDS are discarded. This proves Repo access, not complete inventory,
Workflow Studio access, a deployment, or SSH/server recovery. Missing keys fail
the check without printing values.

For operations, follow the existing Cloud.ru skill's EDS reference. Capture raw
output privately before reporting allowlisted fields. Do not run `eds config`
in logs or embed API keys in clone URLs. EDS manages Repo and Workflow Studio;
it is not a VM/SSH administration CLI.

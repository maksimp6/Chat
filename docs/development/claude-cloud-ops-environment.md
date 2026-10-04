# Claude Code cloud environment for Cloud.ru operations

A second Claude Code cloud environment for Cloud.ru sysadmin work: repository
access, full internet (including Cloud.ru documentation), the Cloud.ru CLI, the
EDS CLI, the GitHub CLI and Docker. The container exists only while a session
runs, so nothing idles and nothing is billed on Cloud.ru for the environment
itself.

## Create the environment

In Claude Code open the cloud environment menu in the session title bar and
create a new environment:

1. **Network access:** Full.
2. **Repository:** `maksimp6/Chat`.
3. **Setup script:** `bash scripts/claude_cloud_setup.sh`.
4. **Environment variables:**
   - `CLOUDRU_PROJECT_ID`, `CLOUDRU_IAM_KEY_ID`, `CLOUDRU_IAM_KEY_SECRET`;
   - `CLOUDRU_STORAGE_TENANT_ID` (Object Storage tenant, not a secret);
   - `EDS_PROJECT_ID`, `EDS_API_KEY` for EDS. The EDS product key is separate;
     the IAM key pair does not replace it.

Values are set only in the environment settings, never in the repository.

## What the setup script does

`scripts/claude_cloud_setup.sh` runs these steps in order (`CLAUDE_SETUP_STEPS`
selects a subset, which the tests use):

| Step | Action |
| --- | --- |
| `deps` | Python and Node dependencies with pip/npm caches; Chrome worker deps via `npm ci` |
| `tools` | Pinned, SHA-256-verified Cloud.ru CLI, EDS CLI v0.4.0 and GitHub CLI, reinstalled on every setup so a stale binary on `PATH` never replaces the verified version; verified archives are kept in `~/.cache` |
| `docker` | Starts `dockerd` if needed, waits up to 30 s and reports `docker: ready` or `docker: unavailable` |
| `report` | Prints which tools are installed and which variables are `set`/`missing`, never their values |

## Plans and heavy work

Self-hosted Claude Code environments (running sessions on our own Cloud.ru
containers) require a Team or Enterprise plan. On Pro and Max use this cloud
environment, and run heavy builds and deploys on Cloud.ru through GitHub
Actions workflows (for example `Cloud.ru persistent Chrome MCP`), which also
start only on demand.

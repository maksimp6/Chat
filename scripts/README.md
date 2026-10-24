# Developer and operational scripts

Run shell helpers with `bash scripts/<name>.sh` from the repository root. The
relocated helpers also resolve the checkout correctly when invoked by absolute
path from another working directory. `start.sh` remains the root startup entrypoint.
There are no compatibility copies at the old root paths; update external shortcuts.

## Relocated helpers

| Command | Purpose | Side effects / verification boundary |
| --- | --- | --- |
| `bash scripts/analyze_logs.sh` | Summarize legacy log files | Reads local logs; legacy format assumptions remain |
| `bash scripts/analyze_modules.sh` | Count module activity | Reads local logs; token counts are estimates |
| `bash scripts/debug.sh logs` | Show recent application log lines | Reads local logs |
| `bash scripts/view_logs.sh` | Interactive log viewer | Includes a separately confirmed log deletion menu; not exercised by relocation tests |
| `bash scripts/view_module_logs.sh` | View module logs | Reads local logs; use `all` at the prompt for a snapshot |
| `bash scripts/file.sh` | Create a project dump | Writes `project_dump.txt`; inspect content before sharing because tracked files/logs can contain private data |
| `bash scripts/test_api.sh` | Historical manual API exercise | Creates a conversation and sends chat; external API semantics not validated here |
| `bash scripts/crl.sh` | Historical manual Git-tool chat exercise | Calls local API, may create a conversation and invoke tools; not an offline test |
| `bash scripts/install_protobuf_env.sh` | Historical Termux SDK setup | Installs/upgrades packages; never run as part of relocation checks |
| `python scripts/send_logs.py` | Historical execution-log uploader | Reads checkout `execution_logs/`; upload endpoint and placeholder auth remain unverified |

## Canonical development checks

| Command | Purpose |
| --- | --- |
| `bash scripts/format.sh write` | Optional: apply the pinned Python formatter (ruff) |
| `bash scripts/format.sh check` | Optional: verify Python formatting with ruff |
| `bash scripts/pre_push.sh` | Required clean-worktree and whitespace gate |
| `bash scripts/check_code_rules.sh` | Naming, placement, reliability and type ratchets |
| `python scripts/local_launch_smoke.py` | Local-launch smoke check |

Other existing scripts retain their current paths. See [repository layout](../docs/architecture/repository-layout.md)
and [root file inventory](../docs/architecture/root-file-inventory.md) for the ongoing cleanup.

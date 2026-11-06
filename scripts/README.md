# Developer and operational scripts

Run shell helpers with `bash scripts/<name>.sh` from the repository root. The
relocated helpers also resolve the checkout correctly when invoked by absolute
path from another working directory. `start.sh` remains the root startup entrypoint.
There are no compatibility copies at the old root paths; update external shortcuts.

## Relocated helpers

| Command | Purpose | Side effects / verification boundary |
| --- | --- | --- |
| `bash scripts/install_protobuf_env.sh` | Historical Termux SDK setup | Installs/upgrades packages; never run as part of relocation checks |
| `python scripts/send_logs.py` | Historical execution-log uploader | Reads checkout `execution_logs/`; upload endpoint and placeholder auth remain unverified |

## Canonical development checks

| Command | Purpose |
| --- | --- |
| `bash scripts/format.sh write` | Optional: apply the pinned Python formatter (ruff) |
| `bash scripts/format.sh check` | Optional: verify Python formatting with ruff |

Other existing scripts retain their current paths. The repository layout and root
file inventory documents that tracked the cleanup are no longer in the tree; see the
[documentation index](../docs/README.md) for current documents.

## Termux device control

The standalone RDC launchers moved from `android/scripts/` to `scripts/rdc/`
when the Android application and APK tooling were removed. From the checkout:

```bash
bash scripts/rdc/termux-rdc.sh
```

It stops any running agent, starts a new one from `~/.alice-rdc/package`, names the
device `Redmi 9` (Desktop Commander uses the kernel hostname), and writes the
Termux:Boot hook `~/.termux/boot/20-alice-rdc` that runs it on every boot.

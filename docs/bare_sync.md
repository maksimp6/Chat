# local_bare synchronization — retired

Status: **retired**.

The former Android/Termux workflow mirrored Git history to a local bare repository at `/sdcard/repo/bare` using the `local_bare` remote. This workflow is no longer used and is not part of the current Alice Pro development, backup, deployment, or recovery contract.

This file is retained only so old links explain what happened to that workflow. Do not configure `local_bare` or depend on `/sdcard/repo/bare` for current repository safety.

Current repository changes use the protected GitHub workflow described in [development_workflow.md](development_workflow.md). Durable application state and recovery are separate from Git history and belong to their canonical storage/platform contracts.

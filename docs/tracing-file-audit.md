# Tracing file audit

Date: 2026-09-16
Branch: `refactor/tracing-cleanup`

## Findings

- `analyze_logs.sh`: standalone manual diagnostic script. GitHub code search found no repository references. It assumes legacy log files and message formats (`logs/chat.txt`, `logs/voice.txt`, `logs/database.txt`, `logs/search.txt`). Keep temporarily; removal requires confirming it is not used in local operational workflows.
- `analyze_modules.sh`: standalone manual diagnostic script. GitHub code search found no repository references. It assumes a set of legacy module-specific log files and formats. Keep temporarily; removal requires confirming it is not used outside the repository.
- `debug.sh`: standalone diagnostic script. It is referenced only by its own documentation/comment text in the search results; usage by external/local workflows is not verifiable from repository search. Keep pending manual confirmation.
- `crl.sh`: requires the same external-usage check before removal.
- `.safe_backups/*.bak`: backup artifacts must not be deleted until their contents and any recovery workflow are checked.
- Trace Viewer patch files (`trace_viewer.js`, `trace_viewer_timing_fix.js`, `trace_viewer_error_ui.js`, `trace_viewer_auto.js`) must be treated as a coupled runtime chain until their `window.openTraceViewer` integration is mapped.

## Decision

No files are deleted in this audit pass. The repository does not provide enough evidence to prove that the scripts and backups are unused outside GitHub. The next safe step is to map the Trace Viewer loading chain and then remove only confirmed dead artifacts in a separate commit.

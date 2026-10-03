#!/usr/bin/env bash
set -euo pipefail
umask 077
config_dir="/home/node/.claude-server-commander"
auth_dir="/home/node/.desktop-commander-device"
mkdir -p "$config_dir" "$auth_dir"
chmod 700 "$config_dir" "$auth_dir"
python3 - "$config_dir/config.json" /opt/desktop-commander/config.example.json <<'PY'
import json
import os
import sys

target, policy = map(os.path.abspath, sys.argv[1:])
try:
    with open(target, encoding="utf-8") as handle:
        current = json.load(handle)
except (FileNotFoundError, json.JSONDecodeError):
    current = {}
with open(policy, encoding="utf-8") as handle:
    repository_policy = json.load(handle)
for key in ("allowedDirectories", "telemetryEnabled", "fileReadLineLimit", "fileWriteLineLimit", "blockedCommands"):
    if key in repository_policy:
        current[key] = repository_policy[key]
tmp = target + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(current, handle, indent=2)
    handle.write("\n")
os.replace(tmp, target)
PY
exec node --require /opt/desktop-commander/pairing-handoff.cjs /opt/desktop-commander/node_modules/@wonderwhy-er/desktop-commander/dist/index.js "$@"

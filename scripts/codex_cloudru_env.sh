#!/usr/bin/env bash
# Normalize the names used by the Codex environment for Cloud.ru deployment.
#
# Codex exposes secrets to setup/maintenance scripts and removes them before
# the agent phase. Keep the hand-off in a mode-600 file outside the checkout;
# the deployment CLI reads it when it needs the control-plane pair.
# This file is sourced by codex_setup.sh and codex_maintenance.sh.

_codex_cloudru_xtrace=0
case $- in
  *x*) _codex_cloudru_xtrace=1; set +x ;;
esac

_codex_cloudru_state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/alice-pro"
_codex_cloudru_state_file="$_codex_cloudru_state_dir/cloudru-codex.json"

_codex_cloudru_key_id="${CLOUDRU_IAM_KEY_ID:-${CLOUDRU_KEY_ID:-}}"
_codex_cloudru_key_secret="${CLOUDRU_IAM_KEY_SECRET:-${CLOUDRU_KEY_SECRET:-}}"

# A missing pair is valid for the ordinary Codex bootstrap smoke test. When a
# pair is present, save only the exact values needed by the deployment CLI.
if [[ -n "$_codex_cloudru_key_id" && -n "$_codex_cloudru_key_secret" ]]; then
  mkdir -p "$_codex_cloudru_state_dir"
  chmod 700 "$_codex_cloudru_state_dir"
  CLOUDRU_CODEX_STATE_FILE="$_codex_cloudru_state_file" \
    CLOUDRU_CODEX_KEY_ID="$_codex_cloudru_key_id" \
    CLOUDRU_CODEX_KEY_SECRET="$_codex_cloudru_key_secret" \
    CLOUDRU_CODEX_PROJECT_ID="${CLOUDRU_PROJECT_ID:-}" \
    python - <<'PY'
import json
import os
from pathlib import Path
import tempfile

destination = Path(os.environ["CLOUDRU_CODEX_STATE_FILE"])
payload = {
    "CLOUDRU_IAM_KEY_ID": os.environ["CLOUDRU_CODEX_KEY_ID"],
    "CLOUDRU_IAM_KEY_SECRET": os.environ["CLOUDRU_CODEX_KEY_SECRET"],
}
project_id = os.environ.get("CLOUDRU_CODEX_PROJECT_ID", "").strip()
if project_id:
    payload["CLOUDRU_PROJECT_ID"] = project_id

fd, temporary = tempfile.mkstemp(prefix=f"{destination.name}.", dir=destination.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
  # Keep setup/maintenance commands that run in this shell compatible with
  # the canonical names expected by CloudRuIamClient.
  export CLOUDRU_IAM_KEY_ID="$_codex_cloudru_key_id"
  export CLOUDRU_IAM_KEY_SECRET="$_codex_cloudru_key_secret"
fi

unset _codex_cloudru_key_id _codex_cloudru_key_secret _codex_cloudru_state_dir
unset CLOUDRU_CODEX_STATE_FILE CLOUDRU_CODEX_KEY_ID CLOUDRU_CODEX_KEY_SECRET CLOUDRU_CODEX_PROJECT_ID

if [[ "$_codex_cloudru_xtrace" == 1 ]]; then
  set -x
fi
unset _codex_cloudru_xtrace

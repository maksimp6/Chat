#!/usr/bin/env bash
# Materialize Codex setup-only credentials for the later agent phase.
# This file is sourced by codex_setup.sh and codex_maintenance.sh after gh is
# installed. It deliberately disables xtrace while handling secret values.

_codex_credentials_xtrace=0
case $- in
  *x*) _codex_credentials_xtrace=1; set +x ;;
esac

_codex_normalize_secret_file() {
  local destination="$1"
  local value="$2"
  CODEX_SECRET_DESTINATION="$destination" CODEX_SECRET_VALUE="$value" python - <<'PY'
import os
from pathlib import Path

destination = Path(os.environ["CODEX_SECRET_DESTINATION"])
value = os.environ["CODEX_SECRET_VALUE"].replace("\\n", "\n").replace("\r\n", "\n")
destination.write_text(value.rstrip("\n") + "\n", encoding="utf-8")
destination.chmod(0o600)
PY
}

mkdir -p "$HOME/.ssh" "$HOME/.gnupg" "$HOME/.config/gh"
chmod 700 "$HOME/.ssh" "$HOME/.gnupg" "$HOME/.config" "$HOME/.config/gh"

_codex_gpg_private_key="${CODEX_GPG_PRIVATE_KEY:-${GPG_PRIVATE_KEY:-}}"
if [[ -n "$_codex_gpg_private_key" ]]; then
  _codex_gpg_file="$(mktemp)"
  if ! _codex_normalize_secret_file "$_codex_gpg_file" "$_codex_gpg_private_key"; then
    rm -f "$_codex_gpg_file"
    return 1
  fi
  if ! _codex_gpg_status="$(gpg --batch --status-fd 1 --import "$_codex_gpg_file" 2>/dev/null)"; then
    rm -f "$_codex_gpg_file"
    echo "Codex GPG private key import failed" >&2
    return 1
  fi
  rm -f "$_codex_gpg_file"
  _codex_gpg_fingerprint="$(awk '$2 == "IMPORT_OK" { print $4; exit }' <<<"$_codex_gpg_status")"
  if [[ -z "$_codex_gpg_fingerprint" ]]; then
    echo "Codex GPG private key import did not return a fingerprint" >&2
    return 1
  fi
  git config --global user.signingkey "$_codex_gpg_fingerprint"
fi

_codex_ssh_private_key="${CODEX_SSH_PRIVATE_KEY:-${SSH_PRIVATE_KEY:-${PREVIEW_SSH_PRIVATE_KEY:-}}}"
if [[ -n "$_codex_ssh_private_key" ]]; then
  _codex_ssh_file="$(mktemp "$HOME/.ssh/.id_ed25519.XXXXXX")"
  if ! _codex_normalize_secret_file "$_codex_ssh_file" "$_codex_ssh_private_key" \
      || ! ssh-keygen -y -f "$_codex_ssh_file" >/dev/null 2>&1; then
    rm -f "$_codex_ssh_file"
    echo "Codex SSH private key validation failed" >&2
    return 1
  fi
  if ! mv -f "$_codex_ssh_file" "$HOME/.ssh/id_ed25519"; then
    rm -f "$_codex_ssh_file"
    return 1
  fi
fi

_codex_ssh_known_hosts="${CODEX_SSH_KNOWN_HOSTS:-${SSH_KNOWN_HOSTS:-${PREVIEW_SSH_KNOWN_HOSTS:-}}}"
if [[ -n "$_codex_ssh_known_hosts" ]]; then
  _codex_normalize_secret_file "$HOME/.ssh/known_hosts" "$_codex_ssh_known_hosts"
else
  _codex_server_known_hosts=""
  for _codex_server_number in 1 2; do
    _codex_server_host_variable="CLOUD_RU_SERVER_${_codex_server_number}"
    _codex_server_key_variable="CLOUD_RU_SERVER_${_codex_server_number}_pub"
    _codex_server_host="${!_codex_server_host_variable:-}"
    _codex_server_key="${!_codex_server_key_variable:-}"
    if [[ -n "$_codex_server_host" && -n "$_codex_server_key" ]]; then
      if [[ "$_codex_server_host" == *$'\n'* || "$_codex_server_key" == *$'\n'* ]]; then
        echo "Codex SSH host metadata must contain one line per server" >&2
        return 1
      fi
      _codex_server_known_hosts+="${_codex_server_host} ${_codex_server_key}"$'\n'
    fi
  done
  if [[ -n "$_codex_server_known_hosts" ]]; then
    _codex_normalize_secret_file "$HOME/.ssh/known_hosts" "$_codex_server_known_hosts"
  fi
fi

_codex_github_token="${CODEX_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -n "$_codex_github_token" ]]; then
  printf '%s\n' "$_codex_github_token" | GH_TOKEN= GITHUB_TOKEN= gh auth login --hostname github.com --git-protocol https --with-token >/dev/null
  gh auth setup-git --hostname github.com >/dev/null
  if [[ -f "$HOME/.config/gh/hosts.yml" ]]; then
    chmod 600 "$HOME/.config/gh/hosts.yml"
  fi
fi

unset _codex_gpg_private_key _codex_gpg_file _codex_gpg_status _codex_gpg_fingerprint
unset _codex_ssh_private_key _codex_ssh_file _codex_ssh_known_hosts _codex_github_token
unset _codex_server_known_hosts _codex_server_number _codex_server_host_variable
unset _codex_server_key_variable _codex_server_host _codex_server_key
unset CODEX_SECRET_DESTINATION CODEX_SECRET_VALUE
unset -f _codex_normalize_secret_file

if [[ "$_codex_credentials_xtrace" == 1 ]]; then
  set -x
fi
unset _codex_credentials_xtrace

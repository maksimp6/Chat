#!/usr/bin/env bash
set -euo pipefail

force=0
if [[ "${1:-}" == "--force" ]]; then
  force=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--force]" >&2
  exit 2
fi

root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "Alice Pro hook installer: not inside a Git working tree." >&2
  exit 2
}

source_hook="$root/scripts/pre_commit_hook.sh"
if [[ ! -f "$source_hook" ]]; then
  echo "Alice Pro hook installer: missing $source_hook" >&2
  exit 2
fi

hook_path="$(git -C "$root" rev-parse --git-path hooks/pre-commit)"
mkdir -p "$(dirname "$hook_path")"

if [[ -e "$hook_path" ]] && ! grep -q "alice-pro-canonical-format-hook" "$hook_path"; then
  if [[ "$force" -ne 1 ]]; then
    echo "Alice Pro hook installer: existing foreign pre-commit hook at $hook_path" >&2
    echo "Re-run with --force only if you intentionally want to replace it." >&2
    exit 1
  fi
fi

cp "$source_hook" "$hook_path"
chmod 700 "$hook_path"

echo "Installed Alice Pro canonical formatter pre-commit hook:"
echo "  $hook_path"

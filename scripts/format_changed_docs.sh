#!/usr/bin/env bash
set -euo pipefail

mode="${1:-write}"
base="${2:-}"
head="${3:-HEAD}"

if [[ "$mode" != "write" && "$mode" != "check" ]]; then
  echo "Usage: $0 [write|check] <base-sha> [head-sha]" >&2
  exit 2
fi
if [[ -z "$base" ]]; then
  echo "base SHA is required" >&2
  exit 2
fi
if [[ ! -x "./node_modules/.bin/prettier" ]]; then
  echo "Prettier is not installed. Run npm install first." >&2
  exit 2
fi

mapfile -d '' changed < <(git diff --name-only -z "$base" "$head")
targets=()
for path in "${changed[@]}"; do
  case "$path" in
    docs/*.md|docs/*.json|*.md)
      [[ -f "$path" ]] && targets+=("$path")
      ;;
  esac
done

if [[ "${#targets[@]}" -eq 0 ]]; then
  echo "No changed documentation files require formatting."
  exit 0
fi

"./node_modules/.bin/prettier" "--$mode" "${targets[@]}"

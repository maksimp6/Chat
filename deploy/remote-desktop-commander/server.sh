#!/usr/bin/env bash
set -euo pipefail
root="${ALICE_RDC_ROOT:-$HOME/alice-preview/services/remote-desktop-commander}"
operation="${1:-preflight}"
case "$operation" in
  preflight)
    docker info >/dev/null
    docker compose version >/dev/null
    printf 'SSH, Docker and Compose available\n'
    ;;
  install)
    revision="${2:?exact SHA required}"
    [[ "$revision" =~ ^[a-f0-9]{40}$ ]] || exit 2
    archive="${ALICE_RDC_ARCHIVE:-$HOME/alice-preview/incoming/remote-desktop-commander.tar.gz}"
    test -f "$archive"
    install -d -m 700 "$root" "$root/state" "$root/workspace" "$root/releases"
    # Each attempt gets a clean immutable context; auth/workspace are never removed.
    release="$(mktemp -d "$root/releases/$revision.XXXXXX")"
    tar -xzf "$archive" -C "$release"
    source_dir="$release/deploy/remote-desktop-commander"
    for file in Dockerfile compose.yaml config.json entrypoint.sh .dockerignore package.json package-lock.json dependency-smoke.cjs; do
      test -f "$source_dir/$file"
      cp "$source_dir/$file" "$root/$file"
    done
    cd "$root"
    docker compose config --quiet
    docker compose build
    # The root initialization process sees only this service's two volumes.
    docker compose run --rm --no-deps initialize
    docker compose up -d --no-build
    docker compose exec -T commander node -e 'process.stdout.write("Node runtime reachable\\n")'
    printf 'Remote Desktop Commander installed from %s; OAuth pairing still required\n' "$revision"
    ;;
  status)
    cd "$root"
    docker compose ps --status running --services
    # Never print device.json, raw logs, pairing codes, or access tokens in Actions.
    ;;
  *) echo 'Unsupported operation' >&2; exit 2 ;;
esac

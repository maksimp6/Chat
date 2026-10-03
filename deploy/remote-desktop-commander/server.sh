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
    install -d -m 700 "$root" "$root/state" "$root/workspace" "$root/pairing" "$root/releases"
    # Each attempt gets a clean immutable context; auth/workspace are never removed.
    release="$(mktemp -d "$root/releases/$revision.XXXXXX")"
    tar -xzf "$archive" -C "$release"
    source_dir="$release/deploy/remote-desktop-commander"
    previous="$(mktemp -d "$root/releases/previous.XXXXXX")"
    trap 'rm -rf -- "$previous"' EXIT
    files=(Dockerfile compose.yaml config.json entrypoint.sh pairing-handoff.cjs .dockerignore package.json package-lock.json dependency-smoke.cjs desktop-session.cjs browser-smoke.cjs chromium-seccomp.json chromium-seccomp.LICENSE)
    for file in "${files[@]}"; do
      if [[ -f "$root/$file" ]]; then cp "$root/$file" "$previous/$file"; fi
      test -f "$source_dir/$file"
      cp "$source_dir/$file" "$root/$file"
    done
    cd "$root"
    rollback_tag="alice-remote-desktop-commander:rollback-$revision"
    had_previous=0
    started_replacement=0
    if docker image inspect alice-remote-desktop-commander:0.2.52 >/dev/null 2>&1; then
      docker tag alice-remote-desktop-commander:0.2.52 "$rollback_tag"
      had_previous=1
    fi
    restore() {
      if [[ "$started_replacement" == 1 ]]; then
        docker compose down --remove-orphans >/dev/null 2>&1 || true
      fi
      for file in "${files[@]}"; do
        if [[ -f "$previous/$file" ]]; then cp "$previous/$file" "$root/$file"; fi
      done
      if [[ "$had_previous" == 1 ]]; then
        docker tag "$rollback_tag" alice-remote-desktop-commander:0.2.52
        if [[ "$started_replacement" == 1 ]]; then docker compose up -d --no-build; fi
      fi
    }
    trap restore ERR
    docker compose config --quiet
    docker compose build
    docker compose run --rm --no-deps initialize
    started_replacement=1
    docker compose up -d --no-build
    docker compose exec -T commander node -e 'process.stdout.write("Node runtime reachable\\n")'
    timeout 30s docker compose exec -T commander node /opt/desktop-commander/desktop-session.cjs --wait-ready
    trap - ERR
    printf 'Remote Desktop Commander and Chromium ready from %s; OAuth pairing still required\n' "$revision"
    ;;
  status)
    cd "$root"
    services="$(docker compose ps --status running --services)"
    grep -qx 'commander' <<<"$services"
    timeout 30s docker compose exec -T commander node /opt/desktop-commander/desktop-session.cjs --healthcheck
    printf 'RDC container and Chromium available\n'
    # Never print device.json, raw logs, pairing codes, or access tokens in Actions.
    ;;
  *) echo 'Unsupported operation' >&2; exit 2 ;;
esac

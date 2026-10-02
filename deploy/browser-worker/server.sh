#!/usr/bin/env bash
set -euo pipefail
root="$HOME/alice-browser-worker"
container="alice-browser-worker"
action="${1:-preflight}"
case "$action" in
  preflight)
    command -v docker >/dev/null
    docker info >/dev/null
    printf 'SSH and Docker preflight passed\n'
    ;;
  install)
    archive="$root/source.tar.gz"
    revision="${2:?exact SHA required}"
    [[ "$revision" =~ ^[a-f0-9]{40}$ ]] || exit 2
    install -d -m 700 "$root/source" "$root/data"
    tar -xzf "$archive" -C "$root/source"
    # Never restart/replace Alice or the shared Traefik instance.
    if docker container inspect "$container" >/dev/null 2>&1; then
      echo 'Worker already exists; reviewed update procedure required' >&2
      exit 1
    fi
    image="alice-browser-worker:$revision"
    docker build --label "org.opencontainers.image.revision=$revision" -t "$image" -f "$root/source/deploy/browser-worker/Dockerfile" "$root/source"
    # Only the dedicated new data directory changes ownership.
    docker run --rm --user 0 --entrypoint chown -v "$root/data:/data" "$image" 10001:10001 /data
    docker run -d --name "$container" --restart unless-stopped --init \
      --shm-size 256m --memory 1g --cpus 1 --pids-limit 256 \
      -v "$root/data:/data" "$image" >/dev/null
    ready=0
    for attempt in $(seq 1 15); do
      if printf '%s' '{"action":"inspect","target":"page","value":null}' | docker exec -i "$container" python -m browser.server_worker request >/dev/null 2>&1; then
        ready=1
        break
      fi
      sleep 1
    done
    if [[ "$ready" != 1 ]]; then
      echo 'Worker browser startup failed; existing services were not changed' >&2
      exit 1
    fi
    printf 'Worker installed from %s; browser startup passed; no ports published\n' "$revision"
    ;;
  request)
    docker exec -i "$container" python -m browser.server_worker request
    ;;
  status)
    docker inspect --format '{{.State.Status}}' "$container"
    ;;
  *) echo 'Unsupported action' >&2; exit 2 ;;
esac

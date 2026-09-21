#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
KEY="${2:-}"
BASE_PATH="${3:-}"
ARCHIVE="${4:-}"
TTL_HOURS="${5:-24}"

ROOT_DIR="${PREVIEW_SERVER_BASE_DIR:-/opt/alice-preview}"
NETWORK_NAME="alice-preview"
TRAEFIK_NAME="alice-preview-traefik"
TRAEFIK_IMAGE="traefik:v3.7.13"
IMAGE_PREFIX="alice-preview"
CONTAINER_PREFIX="alice-preview"
CERT_SOURCE_DIR="${ALICE_TLS_CERT_DIR:-$ROOT_DIR/certs}"

log() { printf '[preview] %s\n' "$*"; }
die() { printf '[preview] ERROR: %s\n' "$*" >&2; exit 1; }
require_key() { [[ "$KEY" =~ ^[a-z0-9-]{1,50}$ ]] || die "invalid preview key: $KEY"; }
require_short_token() {
  if [[ -z "${ALICE_SHORT_TOKEN:-}" ]]; then IFS= read -r ALICE_SHORT_TOKEN || true; fi
  [[ -n "${ALICE_SHORT_TOKEN:-}" ]] || die "ALICE_SHORT_TOKEN is required"
}
ensure_network() { if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then docker network create "$NETWORK_NAME" >/dev/null; fi; }

ensure_traefik() {
  ensure_network
  [[ "$CERT_SOURCE_DIR" = /* ]] || die "ALICE_TLS_CERT_DIR must be an absolute path"
  mkdir -p "$ROOT_DIR/traefik" "$CERT_SOURCE_DIR"
  if docker inspect "$TRAEFIK_NAME" >/dev/null 2>&1; then
    local current_image published_http published_https current_cmd current_mounts
    current_image="$(docker inspect -f '{{.Config.Image}}' "$TRAEFIK_NAME" 2>/dev/null || true)"
    published_http="$(docker port "$TRAEFIK_NAME" 80/tcp 2>/dev/null || true)"
    published_https="$(docker port "$TRAEFIK_NAME" 443/tcp 2>/dev/null || true)"
    current_cmd="$(docker inspect -f '{{join .Config.Cmd " "}}' "$TRAEFIK_NAME" 2>/dev/null || true)"
    current_mounts="$(docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$TRAEFIK_NAME" 2>/dev/null || true)"
    if [ "$current_image" = "$TRAEFIK_IMAGE" ] && grep -Fq '0.0.0.0:80' <<<"$published_http" && grep -Fq '0.0.0.0:443' <<<"$published_https" && grep -Fq -- '--entrypoints.websecure.address=:443' <<<"$current_cmd" && grep -Fq -- '--providers.file.directory=/etc/traefik/dynamic' <<<"$current_cmd" && grep -Fqx -- "$CERT_SOURCE_DIR" <<<"$current_mounts"; then
      docker start "$TRAEFIK_NAME" >/dev/null 2>&1 || die "failed to start $TRAEFIK_NAME"; return
    fi
    log "replacing Traefik to enforce HTTP/HTTPS and dynamic TLS configuration"
    docker rm -f "$TRAEFIK_NAME" >/dev/null
  fi
  log "starting Traefik $TRAEFIK_IMAGE on 0.0.0.0:80 and :443"
  docker run -d --name "$TRAEFIK_NAME" --restart unless-stopped --network "$NETWORK_NAME" -p 0.0.0.0:80:80 -p 0.0.0.0:443:443 -v /var/run/docker.sock:/var/run/docker.sock:ro -v "$ROOT_DIR/traefik:/etc/traefik/dynamic:ro" -v "$CERT_SOURCE_DIR:/etc/traefik/certs:ro" "$TRAEFIK_IMAGE" --providers.docker=true --providers.docker.exposedbydefault=false --providers.file.directory=/etc/traefik/dynamic --providers.file.watch=true --entrypoints.web.address=:80 --entrypoints.websecure.address=:443 --api.dashboard=false --accesslog=false >/dev/null
}

cleanup_key() {
  local key="$1"; local container="${CONTAINER_PREFIX}-${key}"; local image="${IMAGE_PREFIX}:${key}"; local workdir="${ROOT_DIR}/previews/${key}"; local archive_path="${ROOT_DIR}/incoming/${key}.tar.gz"
  docker rm -f "$container" >/dev/null 2>&1 || true; docker image rm "$image" >/dev/null 2>&1 || true; rm -rf -- "$workdir"; rm -f -- "$archive_path"; log "cleaned $key"
}

deploy() {
  local key="$1" base_path="$2" archive_path="$3" ttl="$4"
  [[ "$key" =~ ^[a-z0-9-]{1,50}$ ]] || die "invalid preview key: $key"
  [[ "$base_path" =~ ^/preview/[a-z0-9-]{1,50}$ ]] || die "invalid preview path: $base_path"
  [[ "$archive_path" == "$ROOT_DIR/incoming/"*.tar.gz ]] || die "archive must be inside $ROOT_DIR/incoming"
  [[ "$ttl" =~ ^[0-9]+$ ]] && (( ttl > 0 && ttl <= 720 )) || die "invalid TTL"
  [[ -f "$archive_path" ]] || die "archive not found: $archive_path"
  require_short_token; ensure_traefik
  mkdir -p "$ROOT_DIR/incoming" "$ROOT_DIR/previews"
  local workdir="${ROOT_DIR}/previews/${key}"
  local builddir="${workdir}/build"
  local container="${CONTAINER_PREFIX}-${key}"
  local image="${IMAGE_PREFIX}:${key}"
  local expires_at="$(( $(date +%s) + ttl * 3600 ))"
  rm -rf -- "$workdir"; mkdir -p "$builddir"; tar -xzf "$archive_path" -C "$builddir"; log "building $image"; docker build --pull -t "$image" "$builddir" >/dev/null; docker rm -f "$container" >/dev/null 2>&1 || true
  log "starting $container at $base_path"
  local tokenized_prefix="/$ALICE_SHORT_TOKEN${base_path}"
  local tokenized_rule="PathPrefix(\`$tokenized_prefix\`)"
  local http_router="${container}-http"
  local https_router="${container}-https"
  local token_strip_middleware="${container}-token-strip"
  local proxy_auth_middleware="${container}-proxy-auth"
  docker run -d --name "$container" --restart unless-stopped --network "$NETWORK_NAME" \
    --label "alice.preview=true" --label "alice.preview.key=$key" --label "alice.preview.expires_at=$expires_at" --label "traefik.enable=true" --label "traefik.docker.network=$NETWORK_NAME" \
    --label "traefik.http.routers.${http_router}.rule=$tokenized_rule" --label "traefik.http.routers.${http_router}.entrypoints=web" --label "traefik.http.routers.${http_router}.priority=100" --label "traefik.http.routers.${http_router}.middlewares=$token_strip_middleware,$proxy_auth_middleware" \
    --label "traefik.http.routers.${https_router}.rule=$tokenized_rule" --label "traefik.http.routers.${https_router}.entrypoints=websecure" --label "traefik.http.routers.${https_router}.tls=true" --label "traefik.http.routers.${https_router}.priority=100" --label "traefik.http.routers.${https_router}.middlewares=$token_strip_middleware,$proxy_auth_middleware" \
    --label "traefik.http.middlewares.${container}-token-strip.stripprefixregex.regex=^/[^/]+${base_path}" \
    --label "traefik.http.middlewares.${container}-proxy-auth.headers.customrequestheaders.X-Alice-Proxy-Authenticated=true" \
    --label "traefik.http.services.${container}.loadbalancer.server.port=8080" \
    -e HOST=0.0.0.0 -e PORT=8080 -e ALICE_REQUIRE_SHORT_TOKEN=1 -e ALICE_SHORT_TOKEN="$ALICE_SHORT_TOKEN" -e ALICE_PREVIEW_BASE_PATH="/$ALICE_SHORT_TOKEN$base_path" "$image" >/dev/null
  local health_status
  for attempt in $(seq 1 30); do
    health_status="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || true)"
    if [ "$health_status" = "healthy" ]; then rm -f -- "$archive_path"; log "preview container healthy"; return 0; fi
    sleep 2
  done
  docker logs --tail 120 "$container" >&2 || true; cleanup_key "$key"; die "preview container failed Docker HEALTHCHECK"
}

cleanup_expired() { local now="$(date +%s)"; ensure_network; docker ps -aq --filter "label=alice.preview=true" | while read -r container_id; do [[ -n "$container_id" ]] || continue; local expires key; expires="$(docker inspect -f '{{ index .Config.Labels "alice.preview.expires_at" }}' "$container_id" 2>/dev/null || true)"; key="$(docker inspect -f '{{ index .Config.Labels "alice.preview.key" }}' "$container_id" 2>/dev/null || true)"; if [[ "$expires" =~ ^[0-9]+$ ]] && (( expires <= now )) && [[ "$key" =~ ^[a-z0-9-]{1,50}$ ]]; then cleanup_key "$key"; fi; done; }

case "$ACTION" in
  deploy) [[ $# -eq 5 ]] || die "usage: server.sh deploy <key> <base_path> <archive> <ttl_hours>"; deploy "$KEY" "$BASE_PATH" "$ARCHIVE" "$TTL_HOURS" ;;
  cleanup) [[ $# -eq 2 ]] || die "usage: server.sh cleanup <key>"; require_key; cleanup_key "$KEY" ;;
  cleanup-expired) [[ $# -eq 1 ]] || die "usage: server.sh cleanup-expired"; cleanup_expired ;;
  ensure-traefik) [[ $# -eq 1 ]] || die "usage: server.sh ensure-traefik"; ensure_traefik ;;
  *) die "unknown action: $ACTION" ;;
esac

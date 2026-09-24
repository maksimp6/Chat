#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
ARCHIVE="${2:-}"
ROOT_DIR="${PREVIEW_SERVER_BASE_DIR:-${ALICE_PRODUCTION_BASE_DIR:-$HOME/alice-preview}}"
TRAEFIK_NAME="alice-preview-traefik"
NETWORK_NAME="alice-preview"
CONTAINER_NAME="alice-production"
IMAGE_NAME="alice-production:current"
TRAEFIK_DYNAMIC_DIR="$ROOT_DIR/traefik"
ACME_DIR="$ROOT_DIR/keys/letsencrypt"
ACME_FILE="$ACME_DIR/acme.json"

log() { printf '[production] %s\n' "$*"; }
die() { printf '[production] ERROR: %s\n' "$*" >&2; exit 1; }

require_runtime_secret() {
  if [[ -z "${ALICE_SHORT_TOKEN:-}" ]]; then
    IFS= read -r ALICE_SHORT_TOKEN || true
  fi
  [[ -n "${ALICE_SHORT_TOKEN:-}" ]] || die "ALICE_SHORT_TOKEN is required"
}

en sure_provider_credential_key() {
  local key_file="$ROOT_DIR/keys/provider-credentials.key"
  mkdir -p "$(dirname "$key_file")"
  if [[ -z "${ALICE_PROVIDER_CREDENTIAL_KEY:-}" && -s "$key_file" ]]; then
    ALICE_PROVIDER_CREDENTIAL_KEY="$(cat "$key_file")"
  fi
  if [[ -z "${ALICE_PROVIDER_CREDENTIAL_KEY:-}" ]]; then
    ALICE_PROVIDER_CREDENTIAL_KEY="$(openssl rand -hex 32)"
    umask 077
    printf "%s\\n" "$ALICE_PROVIDER_CREDENTIAL_KEY" > "$key_file"
  fi
  [[ -n "$ALICE_PROVIDER_CREDENTIAL_KEY" ]] || die "failed to initialize provider credential key"
}

validate_traefik() {
  docker inspect "$TRAEFIK_NAME" >/dev/null 2>&1 || die "shared Traefik container $TRAEFIK_NAME is missing"
  docker inspect -f '{{.State.Running}}' "$TRAEFIK_NAME" | grep -qx true || die "shared Traefik container is not running"
  docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || die "shared Docker network $NETWORK_NAME is missing"
  local published_https current_cmd current_mounts
  published_https="$(docker port "$TRAEFIK_NAME" 443/tcp 2>/dev/null || true)"
  [[ "$published_https" == *"0.0.0.0:"* ]] || die "shared Traefik does not publish HTTPS on 0.0.0.0:443"
  current_cmd="$(docker inspect -f '{{join .Config.Cmd " "}}' "$TRAEFIK_NAME" 2>/dev/null || true)"
  current_mounts="$(docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$TRAEFIK_NAME" 2>/dev/null || true)"
  grep -Fq -- '--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json' <<<"$current_cmd" || die "shared Traefik is not configured with the letsencrypt resolver"
  grep -Fqx -- "$ACME_DIR" <<<"$current_mounts" || die "shared Traefik does not persist ACME storage at $ACME_DIR"
  [[ -f "$ACME_FILE" ]] || die "ACME storage file is missing: $ACME_FILE"
}

deploy() {
  local archive_path="$1"
  [[ -f "$archive_path" ]] || die "archive not found: $archive_path"
  require_runtime_secret
  ensure_provider_credential_key
  validate_traefik
  mkdir -p "$ROOT_DIR/incoming" "$ROOT_DIR/production"

  local workdir="$ROOT_DIR/production/build"
  rm -rf -- "$workdir"
  mkdir -p "$workdir"
  tar -xzf "$archive_path" -C "$workdir"

  local runtime_env="$ROOT_DIR/production/runtime.env"
  rm -f -- "$runtime_env"
  trap 'rm -f -- "$runtime_env"' EXIT
  (
    umask 077
    printf 'ALICE_REQUIRE_SHORT_TOKEN=1\n'
    printf 'ALICE_SHORT_TOKEN=%s\n' "$ALICE_SHORT_TOKEN"
    printf 'ALICE_PROVIDER_CREDENTIAL_KEY=%s\n' "$ALICE_PROVIDER_CREDENTIAL_KEY"
  ) > "$runtime_env"

  log "building $IMAGE_NAME"
  docker build --pull -t "$IMAGE_NAME" "$workdir" >/dev/null
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

  local host_rule='Host(\`maxxxpavlov.ru\`) || Host(\`maxxxpavlov.online\`)'
  local route_rule="$host_rule && !PathPrefix(\`/preview/\`) && !PathRegexp(\`^/[^/]+/preview/\`)"

  log "starting production container"
  docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --network "$NETWORK_NAME" \
    --env-file "$runtime_env" \
    --label "traefik.enable=true" \
    --label "traefik.docker.network=$NETWORK_NAME" \
    --label "traefik.http.routers.alice-production-http.rule=$route_rule" \
    --label "traefik.http.routers.alice-production-http.entrypoints=web" \
    --label "traefik.http.routers.alice-production-http.priority=50" \
    --label "traefik.http.routers.alice-production-http.middlewares=alice-production-https-redirect" \
    --label "traefik.http.routers.alice-production-https.rule=$route_rule" \
    --label "traefik.http.routers.alice-production-https.entrypoints=websecure" \
    --label "traefik.http.routers.alice-production-https.priority=50" \
    --label "traefik.http.routers.alice-production-https.tls=true" \
    --label "traefik.http.routers.alice-production-https.tls.certresolver=letsencrypt" \
    --label "traefik.http.routers.alice-production-https.tls.domains[0].main=maxxxpavlov.ru" \
    --label "traefik.http.routers.alice-production-https.tls.domains[0].sans[0]=maxxxpavlov.online" \
    --label "traefik.http.routers.alice-production-https.service=alice-production" \
    --label "traefik.http.middlewares.alice-production-https-redirect.redirectscheme.scheme=https" \
    --label "traefik.http.middlewares.alice-production-https-redirect.redirectscheme.permanent=true" \
    --label "traefik.http.services.alice-production.loadbalancer.server.port=8080" \
    -e HOST=0.0.0.0 \
    -e PORT=8080 \
    "$IMAGE_NAME" >/dev/null

  local health_status
  for _ in $(seq 1 30); do
    health_status="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || true)"
    if [ "$health_status" = "healthy" ]; then
      rm -f -- "$runtime_env" "$archive_path"
      trap - EXIT
      log "production container healthy"
      return 0
    fi
    sleep 2
  done

  docker logs --tail 120 "$CONTAINER_NAME" >&2 || true
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  die "production container failed Docker HEALTHCHECK"
}

case "$ACTION" in
  deploy)
    [[ $# -eq 2 ]] || die "usage: server.sh deploy <archive>"
    deploy "$ARCHIVE"
    ;;
  *)
    die "unknown action: $ACTION"
    ;;
esac

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
DOZZLE_IMAGE="amir20/dozzle:v11.1.0"
DOZZLE_CLEANUP_IMAGE="amir20/dozzle:v11.1.0-alpine"
IMAGE_PREFIX="alice-preview"
CONTAINER_PREFIX="alice-preview"
RUNTIME_IMAGE_PREFIX="alice-preview-runtime-ssh"
ACME_DIR="$ROOT_DIR/keys/letsencrypt"
ACME_FILE="$ACME_DIR/acme.json"
ACME_EMAIL="${ALICE_ACME_EMAIL:-Maxxxxpavlov@yandex.ru}"

log() { printf '[preview] %s\n' "$*"; }
die() { printf '[preview] ERROR: %s\n' "$*" >&2; exit 1; }
require_key() { [[ "$KEY" =~ ^[a-z0-9-]{1,50}$ ]] || die "invalid preview key: $KEY"; }
require_short_token() {
  if [[ -z "${ALICE_SHORT_TOKEN:-}" ]]; then IFS= read -r ALICE_SHORT_TOKEN || true; fi
  [[ -n "${ALICE_SHORT_TOKEN:-}" ]] || die "ALICE_SHORT_TOKEN is required"
}
ensure_provider_credential_key() {
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
ensure_network() { if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then docker network create "$NETWORK_NAME" >/dev/null; fi; }

ensure_traefik() {
  ensure_network
  [[ "$ACME_DIR" = /* ]] || die "ACME directory must be an absolute path"
  mkdir -p "$ROOT_DIR/traefik" "$ACME_DIR"
  touch "$ACME_FILE"
  chmod 700 "$ACME_DIR"
  chmod 600 "$ACME_FILE"
  if docker inspect "$TRAEFIK_NAME" >/dev/null 2>&1; then
    local current_image published_http published_https current_cmd current_mounts
    current_image="$(docker inspect -f '{{.Config.Image}}' "$TRAEFIK_NAME" 2>/dev/null || true)"
    published_http="$(docker port "$TRAEFIK_NAME" 80/tcp 2>/dev/null || true)"
    published_https="$(docker port "$TRAEFIK_NAME" 443/tcp 2>/dev/null || true)"
    current_cmd="$(docker inspect -f '{{join .Config.Cmd " "}}' "$TRAEFIK_NAME" 2>/dev/null || true)"
    current_mounts="$(docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$TRAEFIK_NAME" 2>/dev/null || true)"
    if [ "$current_image" = "$TRAEFIK_IMAGE" ] && grep -Fq '0.0.0.0:80' <<<"$published_http" && grep -Fq '0.0.0.0:443' <<<"$published_https" && grep -Fq -- '--entrypoints.websecure.address=:443' <<<"$current_cmd" && grep -Fq -- '--providers.file.directory=/etc/traefik/dynamic' <<<"$current_cmd" && grep -Fq -- '--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json' <<<"$current_cmd" && grep -Fq -- '--certificatesresolvers.letsencrypt.acme.httpchallenge=true' <<<"$current_cmd" && grep -Fq -- '--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web' <<<"$current_cmd" && grep -Fq -- "$ACME_EMAIL" <<<"$current_cmd" && grep -Fqx -- "$ACME_DIR" <<<"$current_mounts"; then
      docker start "$TRAEFIK_NAME" >/dev/null 2>&1 || die "failed to start $TRAEFIK_NAME"; return
    fi
    log "replacing Traefik to enforce HTTP/HTTPS and dynamic TLS configuration"
    docker rm -f "$TRAEFIK_NAME" >/dev/null
  fi
  log "starting Traefik $TRAEFIK_IMAGE on 0.0.0.0:80 and :443"
  docker run -d --name "$TRAEFIK_NAME" --restart unless-stopped --network "$NETWORK_NAME" -p 0.0.0.0:80:80 -p 0.0.0.0:443:443 -v /var/run/docker.sock:/var/run/docker.sock:ro -v "$ROOT_DIR/traefik:/etc/traefik/dynamic:ro" -v "$ACME_DIR:/letsencrypt" "$TRAEFIK_IMAGE" --providers.docker=true --providers.docker.exposedbydefault=false --providers.file.directory=/etc/traefik/dynamic --providers.file.watch=true --entrypoints.web.address=:80 --entrypoints.websecure.address=:443 --certificatesresolvers.letsencrypt.acme.email="$ACME_EMAIL" --certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json --certificatesresolvers.letsencrypt.acme.httpchallenge=true --certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web --api.dashboard=false --accesslog=false >/dev/null
}

clear_dozzle_data() {
  local dozzle_data="$1"
  mkdir -p "$dozzle_data"
  docker run --rm --user 0 -v "$dozzle_data:/data" --entrypoint sh "$DOZZLE_CLEANUP_IMAGE" -c 'rm -rf /data/* /data/.[!.]* /data/..?*' >/dev/null 2>&1 || true
}

prepare_runtime_smoke() {
  local key="$1"
  local workdir="$2"
  local runtime_dir="$workdir/runtime"
  local runtime_container="$CONTAINER_PREFIX-$key-runtime-ssh"
  local runtime_image="$RUNTIME_IMAGE_PREFIX:$key"
  local client_key="$runtime_dir/id_ed25519"
  local client_pub="$client_key.pub"
  local host_key="$runtime_dir/ssh_host_ed25519_key"
  local host_pub="$host_key.pub"
  local authorized_keys="$runtime_dir/authorized_keys"
  local known_hosts="$runtime_dir/known_hosts"
  local dockerfile="$runtime_dir/Dockerfile"
  local sshd_config="$runtime_dir/sshd_config"

  rm -rf -- "$runtime_dir"
  mkdir -p "$runtime_dir"
  umask 077

  ssh-keygen -q -t ed25519 -N "" -f "$client_key"
  ssh-keygen -q -t ed25519 -N "" -f "$host_key"
  cp "$client_pub" "$authorized_keys"
  printf '%s %s\n' "$runtime_container" "$(cut -d' ' -f1-2 "$host_pub")" > "$known_hosts"

  cat > "$sshd_config" <<'EOF'
Port 22
ListenAddress 0.0.0.0
HostKey /etc/ssh/ssh_host_ed25519_key
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin no
AllowUsers alice-runtime
AuthorizedKeysFile .ssh/authorized_keys
UsePAM no
UseDNS no
X11Forwarding no
AllowTcpForwarding no
PermitTunnel no
StrictModes yes
EOF

  cat > "$dockerfile" <<'EOF'
FROM alpine:3.22
RUN apk add --no-cache openssh-server && \
    adduser -D -h /home/alice-runtime alice-runtime && \
    mkdir -p /home/alice-runtime/.ssh /run/sshd && \
    chown -R alice-runtime:alice-runtime /home/alice-runtime
COPY sshd_config /etc/ssh/sshd_config
COPY ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key
COPY authorized_keys /home/alice-runtime/.ssh/authorized_keys
RUN chown alice-runtime:alice-runtime /home/alice-runtime/.ssh/authorized_keys && \
    chmod 700 /home/alice-runtime/.ssh && \
    chmod 600 /home/alice-runtime/.ssh/authorized_keys && \
    chmod 600 /etc/ssh/ssh_host_ed25519_key
CMD ["/usr/sbin/sshd", "-D", "-e"]
EOF

  docker rm -f "$runtime_container" >/dev/null 2>&1 || true
  docker image rm "$runtime_image" >/dev/null 2>&1 || true
  log "building isolated SSH Runtime target $runtime_container"
  docker build --pull -q -t "$runtime_image" "$runtime_dir" >/dev/null
  log "starting isolated SSH Runtime target $runtime_container"
  docker run -d --name "$runtime_container" --restart unless-stopped --network "$NETWORK_NAME" \
    --label "alice.preview=true" \
    --label "alice.preview.key=$key" \
    --label "alice.preview.runtime=true" \
    "$runtime_image" >/dev/null
}

runtime_smoke() {
  local key="$1"
  require_key
  local container="$CONTAINER_PREFIX-$key"
  local workdir="$ROOT_DIR/previews/$key"
  local runtime_dir="$workdir/runtime"
  local runtime_container="$CONTAINER_PREFIX-$key-runtime-ssh"
  local runtime_client_key="$runtime_dir/id_ed25519"
  local runtime_known_hosts="$runtime_dir/known_hosts"
  local smoke_script="$runtime_dir/smoke.py"

  docker inspect "$container" >/dev/null 2>&1 || die "preview container not found: $container"
  prepare_runtime_smoke "$key" "$workdir"

  cat > "$smoke_script" <<'PY'
from trace_manager import ExecutionTrace
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor
from tool_registry import registry

OWNER = "preview-runtime-owner"
TARGET = "preview-runtime"
PATH = "/home/alice-runtime/alice-runtime-smoke.txt"
CONTENT = "ALICE_RUNTIME_SMOKE_OK\n"


def invoke(name, arguments, approved):
    trace = ExecutionTrace()
    call = UniversalToolCall(
        tool_name=name,
        arguments=arguments,
        transport="internal",
        call_id=f"preview-smoke-{name}",
        user_id=OWNER,
        approved=approved,
    )
    result = UniversalToolExecutor(registry).execute_with_trace(call, trace)
    if not result.get("success"):
        raise AssertionError(f"{name} failed: {result.get('error')}")
    return result, trace


exec_result, exec_trace = invoke(
    "ssh_runtime_exec",
    {"target": TARGET, "timeout_seconds": 10, "command": "id -un"},
    True,
)
assert exec_result["data"]["linux_user"] == "alice-runtime"
assert exec_result["data"]["stdout"].strip() == "alice-runtime"
assert any(
    event.get("type") == "runtime_finished"
    and event.get("payload", {}).get("linux_user") == "alice-runtime"
    for event in exec_trace.trace["events"]
)
assert exec_trace.trace["tool_calls"][0]["arguments"]["command"] == "<redacted>"
assert exec_trace.trace["tool_calls"][0]["result"]["stdout"] == "<redacted>"

write_result, write_trace = invoke(
    "ssh_runtime_write_file",
    {"target": TARGET, "timeout_seconds": 10, "path": PATH, "content": CONTENT},
    True,
)
assert write_result["data"]["success"] is True
assert write_result["data"]["linux_user"] == "alice-runtime"
assert write_trace.trace["tool_calls"][0]["arguments"]["content"] == "<redacted>"
assert write_trace.trace["tool_calls"][0]["result"]["stdout"] == "<redacted>"

read_result, read_trace = invoke(
    "ssh_runtime_read_file",
    {"target": TARGET, "timeout_seconds": 10, "path": PATH},
    False,
)
assert read_result["data"]["stdout"] == CONTENT
assert read_result["data"]["linux_user"] == "alice-runtime"
assert read_trace.trace["tool_calls"][0]["result"]["stdout"] == "<redacted>"

cleanup_result, _ = invoke(
    "ssh_runtime_exec",
    {"target": TARGET, "timeout_seconds": 10, "command": f"rm -f -- {PATH}"},
    True,
)
assert cleanup_result["data"]["exit_code"] == 0
print("SSH Runtime Preview smoke passed")
PY

  cleanup_runtime_smoke() {
    docker exec "$container" rm -f \
      /tmp/alice-runtime-id_ed25519 \
      /tmp/alice-runtime-known_hosts \
      /tmp/alice-runtime-smoke.py >/dev/null 2>&1 || true
    rm -rf -- "$runtime_dir"
    docker rm -f "$runtime_container" >/dev/null 2>&1 || true
    docker image rm "$RUNTIME_IMAGE_PREFIX:$key" >/dev/null 2>&1 || true
  }
  trap cleanup_runtime_smoke EXIT

  docker cp "$runtime_client_key" "$container:/tmp/alice-runtime-id_ed25519" >/dev/null
  docker cp "$runtime_known_hosts" "$container:/tmp/alice-runtime-known_hosts" >/dev/null
  docker cp "$smoke_script" "$container:/tmp/alice-runtime-smoke.py" >/dev/null
  docker exec "$container" chmod 644 \
    /tmp/alice-runtime-id_ed25519 \
    /tmp/alice-runtime-known_hosts
  local runtime_targets_json
  runtime_targets_json="{\"preview-runtime\":{\"host\":\"$runtime_container\",\"port\":22,\"default_user\":\"alice-runtime\",\"allowed_users\":[\"alice-runtime\"],\"identity_map\":{\"preview-runtime-owner\":\"alice-runtime\"},\"workspace_root\":\"/home/alice-runtime\",\"identity_file\":\"/tmp/alice-runtime-id_ed25519\",\"known_hosts\":\"/tmp/alice-runtime-known_hosts\",\"command_timeout_seconds\":30}}"
  docker exec \
    -e "ALICE_SSH_TARGETS_JSON=$runtime_targets_json" \
    -e "ALICE_SSH_KNOWN_HOSTS=/tmp/alice-runtime-known_hosts" \
    "$container" python /tmp/alice-runtime-smoke.py
  log "SSH Runtime smoke passed for $key"
  trap - EXIT
  cleanup_runtime_smoke
}

cleanup_key() {
  local key="$1"; local container="${CONTAINER_PREFIX}-${key}"; local image="${IMAGE_PREFIX}:${key}"; local workdir="${ROOT_DIR}/previews/${key}"; local archive_path="${ROOT_DIR}/incoming/${key}.tar.gz"
  docker rm -f "$container" >/dev/null 2>&1 || true
  local runtime_container="$CONTAINER_PREFIX-$key-runtime-ssh"
  local runtime_image="$RUNTIME_IMAGE_PREFIX:$key"
  docker rm -f "$runtime_container" >/dev/null 2>&1 || true
  docker image rm "$runtime_image" >/dev/null 2>&1 || true
  local dozzle_container="${container}-dozzle"
  clear_dozzle_data "$workdir/dozzle"
  docker rm -f "$dozzle_container" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
  rm -rf -- "$workdir"
  rm -f -- "$archive_path"
  log "cleaned $key"
}

deploy() {
  local key="$1" base_path="$2" archive_path="$3" ttl="$4"
  [[ "$key" =~ ^[a-z0-9-]{1,50}$ ]] || die "invalid preview key: $key"
  [[ "$base_path" =~ ^/preview/[a-z0-9-]{1,50}$ ]] || die "invalid preview path: $base_path"
  [[ "$archive_path" == "$ROOT_DIR/incoming/"*.tar.gz ]] || die "archive must be inside $ROOT_DIR/incoming"
  [[ "$ttl" =~ ^[0-9]+$ ]] && (( ttl > 0 && ttl <= 720 )) || die "invalid TTL"
  [[ -f "$archive_path" ]] || die "archive not found: $archive_path"
  require_short_token; ensure_provider_credential_key; ensure_traefik
  mkdir -p "$ROOT_DIR/incoming" "$ROOT_DIR/previews"
  local workdir="${ROOT_DIR}/previews/${key}"
  local builddir="${workdir}/build"
  local container="${CONTAINER_PREFIX}-${key}"
  local image="${IMAGE_PREFIX}:${key}"
  local expires_at="$(( $(date +%s) + ttl * 3600 ))"
  clear_dozzle_data "$workdir/dozzle"
  rm -rf -- "$workdir"; mkdir -p "$builddir"; tar -xzf "$archive_path" -C "$builddir"; log "building $image"; docker build --pull -t "$image" "$builddir" >/dev/null; docker rm -f "$container" >/dev/null 2>&1 || true
  log "starting $container at $base_path"
  local tokenized_prefix="/$ALICE_SHORT_TOKEN${base_path}"
  local tokenized_rule="PathPrefix(\`$tokenized_prefix\`)"
  local http_router="${container}-http"
  local https_ru_router="${container}-https-ru"
  local https_online_router="${container}-https-online"
  local https_ru_rule="Host(\`maxxxpavlov.ru\`) && $tokenized_rule"
  local https_online_rule="Host(\`maxxxpavlov.online\`) && $tokenized_rule"
  local token_strip_middleware="${container}-token-strip"
  local proxy_auth_middleware="${container}-proxy-auth"
  docker run -d --name "$container" --restart unless-stopped --network "$NETWORK_NAME" \
    --label "alice.preview=true" --label "alice.preview.key=$key" --label "alice.preview.expires_at=$expires_at" --label "dev.dozzle.group=$key" --label "dev.dozzle.name=Alice Preview $key" --label "traefik.enable=true" --label "traefik.docker.network=$NETWORK_NAME" \
    --label "traefik.http.routers.${http_router}.rule=$tokenized_rule" --label "traefik.http.routers.${http_router}.entrypoints=web" --label "traefik.http.routers.${http_router}.priority=100" --label "traefik.http.routers.${http_router}.middlewares=$token_strip_middleware,$proxy_auth_middleware" \
    --label "traefik.http.routers.${http_router}.rule=$tokenized_rule" --label "traefik.http.routers.${http_router}.entrypoints=web" --label "traefik.http.routers.${http_router}.priority=100" --label "traefik.http.routers.${http_router}.middlewares=$token_strip_middleware,$proxy_auth_middleware" \
    --label "traefik.http.routers.${https_ru_router}.rule=$tokenized_rule" --label "traefik.http.routers.${https_ru_router}.entrypoints=websecure" --label "traefik.http.routers.${https_ru_router}.tls=true" --label "traefik.http.routers.${https_ru_router}.tls.certresolver=letsencrypt" --label "traefik.http.routers.${https_ru_router}.tls.domains[0].main=maxxxpavlov.ru" --label "traefik.http.routers.${https_ru_router}.priority=100" --label "traefik.http.routers.${https_ru_router}.middlewares=$token_strip_middleware,$proxy_auth_middleware" \
    --label "traefik.http.routers.${https_online_router}.rule=$https_online_rule" --label "traefik.http.routers.${https_online_router}.entrypoints=websecure" --label "traefik.http.routers.${https_online_router}.tls=true" --label "traefik.http.routers.${https_online_router}.tls.certresolver=letsencrypt" --label "traefik.http.routers.${https_online_router}.tls.domains[0].main=maxxxpavlov.online" --label "traefik.http.routers.${https_online_router}.priority=100" --label "traefik.http.routers.${https_online_router}.middlewares=$token_strip_middleware,$proxy_auth_middleware" \
    --label "traefik.http.middlewares.${container}-token-strip.stripprefixregex.regex=^/[^/]+${base_path}" \
    --label "traefik.http.middlewares.${container}-proxy-auth.headers.customrequestheaders.X-Alice-Proxy-Authenticated=true" \
    --label "traefik.http.services.${container}.loadbalancer.server.port=8080" \
    -e HOST=0.0.0.0 -e PORT=8080 -e ALICE_REQUIRE_SHORT_TOKEN=1 -e ALICE_SHORT_TOKEN="$ALICE_SHORT_TOKEN" -e ALICE_PROVIDER_CREDENTIAL_KEY="$ALICE_PROVIDER_CREDENTIAL_KEY" -e SUPABASE_URL="$SUPABASE_URL" -e SUPABASE_SECRET_KEY="$SUPABASE_SECRET_KEY" -e ALICE_PREVIEW_BASE_PATH="/$ALICE_SHORT_TOKEN$base_path" -e ALICE_MCP_ALLOW_ANONYMOUS=true "$image" >/dev/null
  local dozzle_container="${container}-dozzle"
  local dozzle_ru_router="${dozzle_container}-logs-ru"
  local dozzle_online_router="${dozzle_container}-logs-online"
  local dozzle_rule="${tokenized_prefix}/logs"
  local dozzle_base="${dozzle_rule}"
  local dozzle_data="${workdir}/dozzle"
  mkdir -p "$dozzle_data"
  docker rm -f "$dozzle_container" >/dev/null 2>&1 || true
  docker run -d --name "$dozzle_container" --restart unless-stopped --network "$NETWORK_NAME" \
    --label "alice.preview=true" --label "alice.preview.key=$key" --label "alice.preview.logger=true" \
    --label "traefik.enable=true" --label "traefik.docker.network=$NETWORK_NAME" \
    --label "traefik.http.routers.${dozzle_ru_router}.rule=Host(\`maxxxpavlov.ru\`) && PathPrefix(\`${dozzle_rule}\`)" \
    --label "traefik.http.routers.${dozzle_ru_router}.entrypoints=websecure" \
    --label "traefik.http.routers.${dozzle_ru_router}.tls=true" \
    --label "traefik.http.routers.${dozzle_ru_router}.tls.certresolver=letsencrypt" \
    --label "traefik.http.routers.${dozzle_ru_router}.tls.domains[0].main=maxxxpavlov.ru" \
    --label "traefik.http.routers.${dozzle_ru_router}.priority=110" \
    --label "traefik.http.routers.${dozzle_ru_router}.middlewares=${proxy_auth_middleware}" \
    --label "traefik.http.routers.${dozzle_online_router}.rule=Host(\`maxxxpavlov.online\`) && PathPrefix(\`${dozzle_rule}\`)" \
    --label "traefik.http.routers.${dozzle_online_router}.entrypoints=websecure" \
    --label "traefik.http.routers.${dozzle_online_router}.tls=true" \
    --label "traefik.http.routers.${dozzle_online_router}.tls.certresolver=letsencrypt" \
    --label "traefik.http.routers.${dozzle_online_router}.tls.domains[0].main=maxxxpavlov.online" \
    --label "traefik.http.routers.${dozzle_online_router}.priority=110" \
    --label "traefik.http.routers.${dozzle_online_router}.middlewares=${proxy_auth_middleware}" \
    --label "traefik.http.services.${dozzle_container}.loadbalancer.server.port=8080" \
    -e DOZZLE_BASE="$dozzle_base" \
    -e DOZZLE_FILTER="label=alice.preview.key=$key" \
    -e DOZZLE_HOSTNAME="Alice Preview $key" \
    -e DOZZLE_ENABLE_ACTIONS=false \
    -e DOZZLE_ENABLE_SHELL=false \
    -e DOZZLE_NO_ANALYTICS=true \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v "$dozzle_data:/data" \
    "$DOZZLE_IMAGE" >/dev/null
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
  runtime-smoke) [[ $# -eq 2 ]] || die "usage: server.sh runtime-smoke <key>"; runtime_smoke "$KEY" ;;
  *) die "unknown action: $ACTION" ;;
esac

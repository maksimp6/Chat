#!/usr/bin/env bash
set -euo pipefail

script="$(cd "$(dirname "$0")/.." && pwd)/deploy/preview/server.sh"

grep -Fq 'local dozzle_base="${dozzle_rule}"' "$script"
grep -Fq '    -e DOZZLE_BASE="$dozzle_base" \' "$script"
grep -Fq 'traefik.http.routers.${dozzle_ru_router}.middlewares=${proxy_auth_middleware}' "$script"
grep -Fq 'traefik.http.routers.${dozzle_online_router}.middlewares=${proxy_auth_middleware}' "$script"

if grep -Fq 'dozzle_strip_middleware' "$script"; then
  echo "Dozzle must not strip its configured base path" >&2
  exit 1
fi

# Dozzle data is explicitly cleaned with the shell-capable helper during cleanup/redeploy.
grep -Fq 'DOZZLE_CLEANUP_IMAGE="amir20/dozzle:v11.1.0-alpine"' "$script"
grep -Fq 'clear_dozzle_data() {' "$script"
grep -Fq 'clear_dozzle_data "$workdir/dozzle"' "$script"

# Preview application state must survive application-container recreation.
grep -Fq 'local data_dir="${workdir}/data"' "$script"
grep -Fq -- '-e ALICE_MEMORY_PATH=/app/data/alice.memory' "$script"
grep -Fq -- '-v "$data_dir:/app/data" \' "$script"

# Runtime launch must not retain a retired SQL storage selector.
if grep -Eq 'ALICE_DB_PATH=|ALICE_DATABASE_URL=' "$script"; then
  echo "Preview runtime must use only the native journal path" >&2
  exit 1
fi

# The Dozzle container must use only its own data directory.
grep -Fq -- '-v "$dozzle_data:/data" \' "$script"

# The obsolete profile bootstrap must not return.
if grep -Fq 'ensure_dozzle_profile' "$script"; then
  echo "Dozzle profile bootstrap is obsolete" >&2
  exit 1
fi

echo "Dozzle preview regression checks passed"

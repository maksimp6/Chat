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

echo "Dozzle subpath regression checks passed"

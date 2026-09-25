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

profile_defaults='$(grep -F 'ensure_dozzle_profile()' "$script")'
grep -Fq '"search": true' "$script"
grep -Fq '"locale": "ru"' "$script"
grep -Fq '"lightTheme": "auto"' "$script"
grep -Fq '"groupContainers": "always"' "$script"
grep -Fq 'if [[ ! -s "$profile" ]]' "$script"
if grep -Fq 'clear_dozzle_data "$workdir/dozzle"' "$script"; then echo 'Dozzle profile must survive redeploys' >&2; exit 1; fi

if grep -Fq 'rm -rf -- "$workdir"; mkdir -p "$builddir"' "$script"; then echo 'Preview deploy must not delete the Dozzle data directory' >&2; exit 1; fi

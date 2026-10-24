#!/data/data/com.termux/files/usr/bin/bash
# GitHub issues GITHUB_TOKEN; this Actions step installs its current-job copy on Redmi.
set -euo pipefail
set +x
umask 077
[[ ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == maksimp6/Chat ]] || exit 2
[[ ${GITHUB_RUN_ID:-} =~ ^[0-9]+$ && ${GITHUB_RUN_ATTEMPT:-} =~ ^[0-9]+$ ]] || exit 2
[[ -n ${GH_TOKEN:-} && $GH_TOKEN != *$'\n'* && $GH_TOKEN != *'"'* ]] || exit 2
[[ $(timeout 5 su -c '/system/bin/getprop ro.product.model') == 'Redmi 9' ]] || exit 1
base=$HOME/.config/github-actions/maksimp6-Chat
mkdir -p "$base"
chmod 700 "$HOME/.config/github-actions" "$base"
work=$(mktemp -d "$base/verify.XXXXXX")
trap 'rm -rf "$work"' EXIT
# An installation-only endpoint proves authentication; a public repo's HTTP 200 does not.
printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN" | \
  curl --fail --silent --show-error --proto '=https' --config - \
  -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
  https://api.github.com/installation/repositories -o "$work/repositories.json"
grep -Eq '"full_name"[[:space:]]*:[[:space:]]*"maksimp6/Chat"' "$work/repositories.json"
printf '%s' "$GH_TOKEN" >"$base/token.next"
chmod 600 "$base/token.next"
mv "$base/token.next" "$base/token"
printf '{"source":"github_actions_GITHUB_TOKEN","repository":"maksimp6/Chat","run_id":"%s","attempt":%s,"lifetime":"current_job_only","stored_at":"%s","authenticated":true,"permissions":"600"}\n' \
  "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "$(date -u +%FT%TZ)" >"$base/receipt.json.tmp"
mv "$base/receipt.json.tmp" "$base/receipt.json"
sync -f "$base"
echo "GITHUB_TOKEN_STORED_ON_REDMI run=$GITHUB_RUN_ID permissions=600 authenticated=true lifetime=current_job_only"

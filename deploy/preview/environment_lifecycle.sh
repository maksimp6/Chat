#!/usr/bin/env bash
set -euo pipefail

: "${ALICE_ENVIRONMENT_HOST_URL:?ALICE_ENVIRONMENT_HOST_URL is required}"
: "${PREVIEW_BRANCH:?PREVIEW_BRANCH is required}"
: "${PREVIEW_COMMIT:?PREVIEW_COMMIT is required}"

host_url="${ALICE_ENVIRONMENT_HOST_URL%/}"
environment_id=""
response_file="$(mktemp)"
trap 'rm -f "$response_file"' EXIT

curl_args=(--fail --silent --show-error --connect-timeout 5 --max-time 30)
if [[ -n "${ALICE_ENVIRONMENT_API_TOKEN:-}" ]]; then
  curl_args+=(-H "Authorization: Bearer ${ALICE_ENVIRONMENT_API_TOKEN}")
fi

api_request() {
  local method="$1"
  local path="$2"
  shift 2
  curl "${curl_args[@]}" -X "$method" "${host_url}${path}" "$@"
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "$environment_id" ]]; then
    api_request DELETE "/api/environments/${environment_id}" >/dev/null || \
      echo "warning: environment cleanup failed" >&2
  fi
  rm -f "$response_file"
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

payload="$(jq -cn --arg branch "$PREVIEW_BRANCH" --arg commit "$PREVIEW_COMMIT" \
  '{branch: $branch, commit_sha: $commit}')"
api_request POST /api/environments -H 'Content-Type: application/json' --data "$payload" >"$response_file"
environment_id="$(jq -er '.environment_id' "$response_file")"
resolved_commit="$(jq -er '.commit_sha' "$response_file")"
[[ "$resolved_commit" == "$PREVIEW_COMMIT" ]] || {
  echo "host resolved a different commit" >&2
  exit 1
}

api_request POST "/api/environments/${environment_id}/start" >"$response_file"
[[ "$(jq -er '.status' "$response_file")" == RUNNING ]]
[[ "$(jq -r '.runtime_pid' "$response_file")" == null ]]
[[ "$(jq -r '.runtime_port' "$response_file")" == null ]]

# Exercise the public gateway rather than any runtime-local transport.
curl "${curl_args[@]}" "${host_url}/environments/${environment_id}/healthz" >"$response_file"

# Keep non-secret lifecycle evidence in the Actions log.
jq -cn --arg environment_id "$environment_id" --arg commit_sha "$resolved_commit" \
  '{environment_id: $environment_id, commit_sha: $commit_sha, gateway: "healthy"}'

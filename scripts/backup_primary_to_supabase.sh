#!/bin/sh
set -eu

: "${ALICE_DATABASE_URL:?ALICE_DATABASE_URL is required}"
: "${SUPABASE_URL:?SUPABASE_URL is required}"
: "${SUPABASE_SERVICE_ROLE_KEY:?SUPABASE_SERVICE_ROLE_KEY is required}"

BACKUP_BUCKET="${SUPABASE_BACKUP_BUCKET:-alice-pro-backups}"
BACKUP_PREFIX="${SUPABASE_BACKUP_PREFIX:-database}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="/tmp/alice-pro-${STAMP}.dump"

cleanup() {
  rm -f "$FILE"
}
trap cleanup EXIT

pg_dump --format=custom --no-owner --no-privileges "$ALICE_DATABASE_URL" > "$FILE"

curl --fail-with-body --silent --show-error \
  -X POST "${SUPABASE_URL%/}/storage/v1/object/${BACKUP_BUCKET}/${BACKUP_PREFIX}/alice-pro-${STAMP}.dump" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/octet-stream" \
  --data-binary "@$FILE"

echo "Backup uploaded: ${BACKUP_PREFIX}/alice-pro-${STAMP}.dump"

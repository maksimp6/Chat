#!/bin/sh
set -eu

: "${ALICE_DATABASE_URL:?ALICE_DATABASE_URL is required}"
: "${SUPABASE_URL:?SUPABASE_URL is required}"
: "${SUPABASE_SERVICE_ROLE_KEY:?SUPABASE_SERVICE_ROLE_KEY is required}"
: "${SUPABASE_BACKUP_OBJECT:?SUPABASE_BACKUP_OBJECT is required}"
: "${CONFIRM_RESTORE:?Set CONFIRM_RESTORE=YES to perform a destructive restore}"

if [ "$CONFIRM_RESTORE" != "YES" ]; then
  echo "Restore refused: CONFIRM_RESTORE must be exactly YES" >&2
  exit 2
fi

BACKUP_BUCKET="${SUPABASE_BACKUP_BUCKET:-alice-pro-backups}"
FILE="/tmp/alice-pro-restore.dump"
trap 'rm -f "$FILE"' EXIT

curl --fail-with-body --silent --show-error \
  -X GET "${SUPABASE_URL%/}/storage/v1/object/${BACKUP_BUCKET}/${SUPABASE_BACKUP_OBJECT}" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -o "$FILE"

pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname "$ALICE_DATABASE_URL" "$FILE"

echo "Restore completed from ${SUPABASE_BACKUP_OBJECT}"

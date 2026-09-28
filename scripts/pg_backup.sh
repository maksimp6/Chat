#!/usr/bin/env bash
# Logical backup and restore check for the Alice Pro PostgreSQL backend.
#
#   scripts/pg_backup.sh backup <dump-file>   # pg_dump -Fc of ALICE_DATABASE_URL
#   scripts/pg_backup.sh verify <dump-file>   # restore into a scratch DB, compare row counts with the dump
#   scripts/pg_backup.sh restore <dump-file>  # restore into ALICE_RESTORE_TARGET_URL
#
# Environment:
#   ALICE_DATABASE_URL        source database (required for backup/verify)
#   ALICE_RESTORE_CHECK_URL   existing empty database for verify; when unset, a
#                             scratch database is created next to the source and dropped
#   ALICE_RESTORE_TARGET_URL  database that `restore` overwrites (never defaulted)
#   PG_DUMP, PG_RESTORE, PSQL command overrides, e.g. a matching-version client in docker
#
# See docs/production-deployment.md, "Backups and restore".
set -euo pipefail

PG_DUMP=${PG_DUMP:-pg_dump}
PG_RESTORE=${PG_RESTORE:-pg_restore}
PSQL=${PSQL:-psql}

die() { echo "pg_backup: $*" >&2; exit 1; }

require_url() {
  local name=$1
  [[ -n "${!name:-}" ]] || die "$name is not set"
}

# Replace the database name in a postgresql:// URL, keeping any query string.
with_database() {
  local url=$1 db=$2 base query=""
  base=${url%%\?*}
  [[ "$url" == *\?* ]] && query="?${url#*\?}"
  printf '%s/%s%s' "${base%/*}" "$db" "$query"
}

row_counts() {
  # Exact per-table row counts, sorted, for every user table.
  $PSQL "$1" -v ON_ERROR_STOP=1 -X -q -A -t <<'SQL'
SELECT format('SELECT %L || '' '' || count(*) FROM %I.%I', schemaname || '.' || tablename, schemaname, tablename)
FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY 1
\gexec
SQL
}

dump_counts() {
  # Per-table row counts from the dump's own data, i.e. the pg_dump snapshot.
  # Output matches row_counts: "schema.table count", sorted.
  $PG_RESTORE --data-only --file=- "$1" | awk '
    /^COPY .* FROM stdin;$/ {
      name = $0
      sub(/^COPY /, "", name)
      sub(/ (\(.*\) )?FROM stdin;$/, "", name)
      gsub(/"/, "", name)
      rows = 0; in_copy = 1; next
    }
    in_copy && $0 == "\\." { print name " " rows; in_copy = 0; next }
    in_copy { rows++ }
  ' | LC_ALL=C sort
}

cmd_backup() {
  local out=$1
  require_url ALICE_DATABASE_URL
  $PG_DUMP --format=custom --no-owner --no-privileges --file="$out" "$ALICE_DATABASE_URL"
  [[ -s "$out" ]] || die "dump file $out is empty"
  echo "pg_backup: wrote $out ($(wc -c <"$out") bytes)"
}

restore_into() {
  local dump=$1 target=$2
  $PG_RESTORE --no-owner --no-privileges --clean --if-exists --exit-on-error \
    --dbname="$target" "$dump"
}

cmd_verify() {
  local dump=$1 target scratch=""
  require_url ALICE_DATABASE_URL
  [[ -s "$dump" ]] || die "dump file $dump is missing or empty"
  if [[ -n "${ALICE_RESTORE_CHECK_URL:-}" ]]; then
    target=$ALICE_RESTORE_CHECK_URL
  else
    scratch="alice_restore_check_$(date +%s)_$$"
    $PSQL "$ALICE_DATABASE_URL" -v ON_ERROR_STOP=1 -X -q -c "CREATE DATABASE \"$scratch\""
    target=$(with_database "$ALICE_DATABASE_URL" "$scratch")
    trap '$PSQL "$ALICE_DATABASE_URL" -X -q -c "DROP DATABASE IF EXISTS \"'"$scratch"'\"" || true' EXIT
  fi
  restore_into "$dump" "$target"
  local expected actual
  # Compare with the dump itself, not the live source, which may have changed since.
  expected=$(dump_counts "$dump")
  actual=$(row_counts "$target" | LC_ALL=C sort)
  [[ -n "$expected" ]] || die "dump contains no table data"
  if [[ "$expected" != "$actual" ]]; then
    diff <(echo "$expected") <(echo "$actual") >&2 || true
    die "restored row counts differ from the dump"
  fi
  echo "pg_backup: restore verified, $(echo "$expected" | wc -l) tables match"
}

cmd_restore() {
  local dump=$1
  require_url ALICE_RESTORE_TARGET_URL
  [[ -s "$dump" ]] || die "dump file $dump is missing or empty"
  restore_into "$dump" "$ALICE_RESTORE_TARGET_URL"
  echo "pg_backup: restored $dump"
}

[[ $# -eq 2 ]] || die "usage: $0 backup|verify|restore <dump-file>"
case $1 in
  backup) cmd_backup "$2" ;;
  verify) cmd_verify "$2" ;;
  restore) cmd_restore "$2" ;;
  *) die "unknown command: $1" ;;
esac

#!/usr/bin/env bash
# Run by GitHub Actions in the native Termux shell. No background scheduler.
set -Eeuo pipefail
umask 077

backup_main() {
  local base="${CHAT_BACKUP_DIR:-$HOME/backups/chat-repo}"
  local mirror="$base/Chat.git"
  local remote="https://github.com/maksimp6/Chat.git"
  local sha="${GITHUB_SHA:?GitHub commit is required}"
  local run="${GITHUB_RUN_ID:?GitHub run ID is required}"
  local attempt="${GITHUB_RUN_ATTEMPT:-1}"
  [[ "$sha" =~ ^[0-9a-f]{40}$ && "$run" =~ ^[0-9]+$ && "$attempt" =~ ^[0-9]+$ ]]
  [[ "${GITHUB_REPOSITORY:-}" == maksimp6/Chat ]]
  mkdir -p "$base/runner-receipts"
  exec 9>"$base/.lock"
  flock -w 120 9
  export GIT_TERMINAL_PROMPT=0 GIT_LFS_SKIP_SMUDGE=1
  if [[ ! -d "$mirror/objects" ]]; then
    git clone --mirror "$remote" "$mirror"
  fi
  [[ "$(git -C "$mirror" rev-parse --is-bare-repository)" == true ]]
  [[ "$(git -C "$mirror" remote get-url origin)" == "$remote" ]]
  git -C "$mirror" config --local gc.auto 0
  git -C "$mirror" config --local gc.pruneExpire never
  pin_tips "$mirror"
  git -C "$mirror" fetch --atomic --no-prune origin \
    '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*' '+refs/pull/*:refs/pull/*'
  if ! git -C "$mirror" cat-file -e "$sha^{commit}"; then
    git -C "$mirror" fetch --no-tags origin "$sha"
  fi
  git -C "$mirror" update-ref "refs/archive/tips/$sha" "$sha"
  pin_tips "$mirror"
  git -C "$mirror" fsck --full --no-dangling
  git -C "$mirror" lfs fetch --all origin

  local inventory="$base/runner-receipts/$run-$attempt.lfs-oids"
  git -C "$mirror" lfs ls-files --all --long |
    awk '{print $1}' | sort -u > "$inventory"
  verify_lfs "$mirror" "$inventory"
  git -C "$mirror" for-each-ref --format='%(objectname) %(refname)' \
    > "$base/runner-receipts/$run-$attempt.refs"
  local refs commits receipt
  refs=$(git -C "$mirror" for-each-ref --format='%(refname)' | wc -l)
  commits=$(git -C "$mirror" rev-list --all --count)
  receipt="$base/runner-receipts/$run-$attempt.json"
  printf '{"status":"verified","run_id":"%s","attempt":%s,"commit":"%s","refs":%s,"commits":%s,"lfs_objects":%s,"lfs_bytes":%s,"verified_at":"%s"}\n' \
    "$run" "$attempt" "$sha" "$refs" "$commits" "$LFS_COUNT" "$LFS_BYTES" "$(date -u +%FT%TZ)" > "$receipt.tmp"
  mv "$receipt.tmp" "$receipt"
  cp "$receipt" "$base/runner-last-success.json.tmp"
  mv "$base/runner-last-success.json.tmp" "$base/runner-last-success.json"
  cat "$receipt"
}

pin_tips() {
  local mirror="$1"
  git -C "$mirror" for-each-ref --format='%(objectname)' refs/heads refs/tags refs/pull |
    sort -u | awk '{print "update refs/archive/tips/" $1 " " $1}' |
    git -C "$mirror" update-ref --stdin
}

verify_lfs() {
  local mirror="$1" inventory="$2" oid object
  LFS_COUNT=0
  LFS_BYTES=0
  while IFS= read -r oid; do
    [[ "$oid" =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid LFS OID' >&2; return 1; }
    object="$mirror/lfs/objects/${oid:0:2}/${oid:2:2}/$oid"
    [[ -f "$object" ]] || { echo "Missing LFS object: $oid" >&2; return 1; }
    printf '%s  %s\n' "$oid" "$object" | sha256sum --check --status || {
      echo "Corrupted LFS object: $oid" >&2; return 1;
    }
    LFS_COUNT=$((LFS_COUNT + 1))
    LFS_BYTES=$((LFS_BYTES + $(stat -c %s "$object")))
  done < "$inventory"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  backup_main "$@"
fi

#!/data/data/com.termux/files/usr/bin/bash
# Publish only encrypted archives, using this job's short-lived GitHub token.
set -euo pipefail
set +x
umask 077
[[ ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == maksimp6/Chat ]] || exit 2
[[ ${GITHUB_RUN_ID:-} =~ ^[0-9]+$ && ${GITHUB_RUN_ATTEMPT:-} =~ ^[0-9]+$ ]] || exit 2
: "${GH_TOKEN:?The workflow must provide its ephemeral github.token}"
token_dir=$HOME/.config/github-actions/maksimp6-Chat
[[ $(stat -c %a "$token_dir/token") == 600 ]]
grep -q "\"run_id\":\"$GITHUB_RUN_ID\",\"attempt\":$GITHUB_RUN_ATTEMPT," "$token_dir/receipt.json"
# Use the copy delivered by the preceding runner step, with exact-job binding.
[[ $(cat "$token_dir/token") == "$GH_TOKEN" ]]
GH_TOKEN=$(cat "$token_dir/token")
base=${ANDROID_DIAGNOSTICS_HOME:-$HOME/backups/android-diagnostics}
run=$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT
dest=$base/runs/$run
[[ -s $dest/manifest.json && -s $dest/diagnostics.tar.gz.age ]] || exit 1
grep -q '"logcat_exit":0,"perfetto_exit":0,' "$dest/manifest.json"
(cd "$dest" && sha256sum -c archive.sha256)
age -d -i "$base/keys/archive.agekey" "$dest/diagnostics.tar.gz.age" | cmp - "$dest/diagnostics.tar.gz"
exec 9>"$base/.publish.lock"
flock -w 120 9

# Disable inherited helpers/rewrite rules. Authentication lives only in this process.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=
export GIT_CONFIG_KEY_1=http.https://github.com/.extraheader
GIT_CONFIG_VALUE_1="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
export GIT_CONFIG_VALUE_1
remote=https://github.com/maksimp6/Chat.git
branch=data/redmi9-android-diagnostics
work=$(mktemp -d "$base/publish.XXXXXX")
verify=$(mktemp -d "$base/verify.XXXXXX")
trap 'rm -rf "$work" "$verify"' EXIT
git -C "$work" init -q
git -C "$work" remote add origin "$remote"
status=0
git -C "$work" ls-remote --exit-code --heads origin "refs/heads/$branch" >"$work/remote-head" || status=$?
if [[ $status == 0 ]]; then
  git -C "$work" fetch --quiet origin "refs/heads/$branch"
  GIT_LFS_SKIP_SMUDGE=1 git -C "$work" checkout -q -b "$branch" FETCH_HEAD
elif [[ $status == 2 ]]; then
  git -C "$work" checkout -q --orphan "$branch"
else
  exit "$status"
fi
git -C "$work" config user.name 'Redmi 9 diagnostics'
git -C "$work" config user.email 'redmi9-diagnostics@users.noreply.github.com'
git -C "$work" lfs install --local >/dev/null
printf '*.age filter=lfs diff=lfs merge=lfs -text\n' >"$work/.gitattributes"
path=android/redmi9/$run/diagnostics.tar.gz.age
mkdir -p "$work/android/redmi9/$run"
cp "$dest/diagnostics.tar.gz.age" "$work/$path"
cp "$dest/manifest.json" "$work/android/redmi9/$run/manifest.json"
git -C "$work" add -- .gitattributes "$path" "android/redmi9/$run/manifest.json"
expected=$(cut -d ' ' -f 1 "$dest/archive.sha256")
git -C "$work" show ":$path" | grep -qx "oid sha256:$expected"
if ! git -C "$work" diff --cached --quiet; then
  git -C "$work" commit -q -m "Archive Redmi 9 Android diagnostics $run"
fi
commit=$(git -C "$work" rev-parse HEAD)
git -C "$work" push origin "HEAD:refs/heads/$branch"

# Read from GitHub into a fresh LFS store; a local pointer/object is not upload proof.
GIT_LFS_SKIP_SMUDGE=1 git clone -q --no-checkout --single-branch --branch "$branch" "$remote" "$verify"
git -C "$verify" lfs install --local >/dev/null
git -C "$verify" config lfs.storage "$verify/.git/lfs"
GIT_LFS_SKIP_SMUDGE=1 git -C "$verify" checkout -q --detach "$commit"
git -C "$verify" lfs fetch --include="$path" --exclude='' origin "$commit"
git -C "$verify" lfs checkout "$path"
actual=$(sha256sum "$verify/$path" | cut -d ' ' -f 1)
[[ $actual == "$expected" ]]
age -d -i "$base/keys/archive.agekey" "$verify/$path" | cmp - "$dest/diagnostics.tar.gz"
printf '{"status":"remote_verified","commit":"%s","branch":"%s","path":"%s","sha256":"%s"}\n' \
  "$commit" "$branch" "$path" "$actual" >"$dest/published.json.tmp"
mv "$dest/published.json.tmp" "$dest/published.json"
sync -f "$base"
echo "ANDROID_LFS_REMOTE_VERIFIED commit=$commit path=$path sha256=$actual"

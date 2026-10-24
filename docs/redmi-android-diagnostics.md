# Android diagnostics on Redmi 9

The `Redmi 9 Android diagnostics` Actions workflow runs ordinary Bash in Termux
on pushes to `develop` or `ops/redmi9-python-runner` that contain the workflow.
It can also be dispatched manually. No background collector is installed.

Each run captures the last 10,000 logcat entries from all available buffers,
an Android/device/memory snapshot, battery/CPU/memory dumps, and a ten-second
Perfetto scheduling/frequency/Android trace. Root is required on this device.
These are bounded snapshots, not a continuous history of every Android log.
Capture failures have nonzero status; partial archives stay on the phone.

Local files are under `~/backups/android-diagnostics/runs/RUN_ID-ATTEMPT/`.
Raw files and the compressed archive remain there. The full archive is encrypted
with the existing `age` tool and the phone's archive encryption identity at
`~/backups/android-diagnostics/keys/archive.agekey` (mode 600; directories 700).
This is separate from GitHub authentication.
It is never uploaded, printed, committed, or stored in LFS. Keep this identity:
the remote ciphertext alone cannot restore the logs if the key is lost.

Only `diagnostics.tar.gz.age` and a small manifest go to branch
`data/redmi9-android-diagnostics`, under `android/redmi9/RUN_ID-ATTEMPT/`.
The encrypted archive uses Git LFS. The job's temporary `github.token` supplies
write access in an explicitly configured environment variable. The first shell
step verifies it against GitHub's installation-only API and stores it on Redmi
at `~/.config/github-actions/maksimp6-Chat/token` (mode 600), with a non-secret
receipt identifying the issuing job. The publisher uses that exact-job copy.
GitHub revokes the token when the job ends; it is not a permanent PAT and cannot
authenticate later manual commands. Each new job receives a fresh token.
No credential helper is created. Pushes never target `develop` or `master`.

The publisher verifies the LFS pointer, pushes, downloads into a fresh LFS
store, checks SHA-256, decrypts, and compares the archive byte for byte before
writing `published.json`. It then runs the repository/LFS backup job inline so
the new data branch is archived locally without depending on another trigger.
Standard Actions logs contain status, sizes, and hashes, not raw Android data.

Restore on the phone after obtaining the `.age` file with `git lfs pull`:

```sh
umask 077
age -d -i "$HOME/backups/android-diagnostics/keys/archive.agekey" \
  diagnostics.tar.gz.age > diagnostics.tar.gz
mkdir -p restored
tar -xzf diagnostics.tar.gz -C restored
```

The archive includes `system.perfetto-trace` for a local Perfetto viewer and
`logcat.txt`. `capture-status.tsv` records individual snapshot command failures.
Repository-wide automatic capture starts only after the workflow is merged
with owner approval; an unmerged PR does not change `develop` behavior.

# Redmi 9: repository archive from a Termux shell job

The job in `.github/workflows/redmi9-backup.yml` targets `runs-on: redmi9`
and executes ordinary Bash commands in Termux. The runner's Python process
transports GitHub jobs; it does not replace the device or its shell. Backup
uses Git, Git LFS, flock, and sha256sum. There is no new background polling
service, Docker, or alternate Linux environment.

Archive location: `~/backups/chat-repo/Chat.git`. The existing bare mirror is
reused. Branches, tags, and PR refs are fetched; old and new tips are pinned
under `refs/archive/tips/<oid>` before and after updates. Fetched history stays
reachable after force updates or branch deletion. Git garbage collection is
disabled locally. Nothing is pushed by the backup job and no LFS pruning occurs.

`git lfs fetch --all origin` downloads every LFS object referenced by the
available Git history, including older versions. The shell enumerates all
LFS OIDs and checks each local file's SHA-256. It also runs Git fsck and pins
the triggering `GITHUB_SHA`. Missing/corrupt objects or failed downloads fail
the job; success is recorded only after verification. Receipts and ref lists
are retained under `~/backups/chat-repo/runner-receipts/` with the GitHub run ID.

The workflow runs on each push to a branch containing this workflow file and
can be dispatched manually once available on the default branch. PR-only
publication does not activate it for pushes to develop or branches that lack
the workflow. Repository-wide activation requires the owner's approved merge
and propagation to the branches concerned. No promise is made about changes
already erased on the server before they could be fetched, LFS objects never
uploaded to GitHub, or unreachable server objects not exposed by Git refs.

The older daily backup and its existing files are preserved during review;
its shared lock serializes access with the runner job. It should be retired
only after the runner workflow is activated for the desired branches. A Git
bundle alone does not contain LFS bytes: preserve the entire mirror including
its `lfs/objects` directory when copying or restoring this archive.

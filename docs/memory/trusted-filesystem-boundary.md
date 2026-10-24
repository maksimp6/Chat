# Memory DB trusted filesystem boundary (security design)

Status: **design proposal, not an implemented isolation guarantee**. Applies to integration PR #1139 only. No merge/cutover.

## Threat model

An adversary may create, rename, unlink, hard-link, or replace files in an untrusted directory, including between verification and publication. Symlink rejection, inode comparison and pinned dirfds are necessary but **insufficient** when that adversary can still write to the final directory after a check. An adversary with the same effective UID as the Memory DB process generally shares DAC privileges; mode bits alone cannot isolate them. Root, CAP_DAC_OVERRIDE, writable mount namespaces and hostile filesystem implementations are out of scope unless isolated by stronger OS controls.

## Required boundary

1. Provision a dedicated service identity (UID/GID), distinct from interactive/agent/tool identities. A privileged provisioning step creates a private root directory owned by that UID, with mode 0700; files are mode 0600. All ancestors must be non-writable by untrusted identities. Verify owner, mode, file type and expected device via fstat on opened descriptors, not via pre-open path checks. Reject group/other write permissions and symlinks. Do not silently chmod or chown an untrusted existing path.
2. Open and retain the trusted root fd; all journal, lock, staging, snapshot, backup and restore operations must resolve beneath it using descriptor-relative operations. On Linux prefer openat2 RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS for untrusted relative paths; reject absolute paths and dot-dot traversal. Where openat2 is unavailable, require a trusted pre-provisioned single directory and explicit component-wise O_DIRECTORY | O_NOFOLLOW handling.
3. Stage copies in a private, service-owned directory under the trusted root. Verify the *opened inode*, fsync it, then publish only into a destination directory with the same ownership boundary. A verified inode can be linked via Linux procfd only when /proc is trusted/mounted and the inode remains linked; otherwise fail closed. Never fall back to an attacker-controlled staging basename. No-overwrite publication must be atomic.
4. After publication, verify inode identity and fsync the destination directory. This detects some races but does not protect against **post-check rename**. The security guarantee comes from denying adversarial write access to the parent directory throughout the operation. In the presence of an untrusted writer, report the operation unsafe rather than claiming durable publication.
5. Restore only into a new, private, empty store root. Pin and verify the source backup fd; require trusted ownership and a digest/sequence match. Keep live journal and lock identities stable. A failed or ambiguous publish fences writes until explicit recovery. No restore should overwrite an existing journal, symlink or other regular file.
6. Avoid unbounded memory copies; stream journal frames under the 1 MiB frame limit and enforce a separately tested total backup size / disk quota. Restrict service file descriptors and mount access. Treat NFS/overlayfs and other filesystems as unsupported until tested for fsync/link semantics.

## Acceptance evidence required

- Permissions: trusted 0700 directory / 0600 files; reject wrong UID, group-writable parents, symlinks, and untrusted paths.
- Concurrency: barrier-controlled inode swaps before open, after fsync, before link, after link, after verification, and directory rename/recreate. Verify no victim modification, no unverified bytes published, correct failure fencing and no descriptor leaks.
- Crash consistency: SIGKILL before and after journal fsync, directory fsync and publication; replay/restore from fresh process, preserving last acknowledged commit.
- Separate attacker identity test: untrusted UID cannot write/rename inside trusted root; confirm on actual deployment filesystem. Same-UID attacker is explicitly **not** isolated by POSIX permissions.
- Exact-head GitHub CI, CodeQL, Memory DB contract and 100% diff coverage; independent RDC E2E and security review. SKIPPED is not FAILED.

## Current implementation gap

PR #1139 has O_NOFOLLOW, pinned dirfds, inode checks, procfd publication and targeted regressions. It does **not** provision or enforce a separate service UID and trusted directory ownership; it cannot guarantee that an attacker with write access will not replace a destination after the final check. This design does not authorize merge.

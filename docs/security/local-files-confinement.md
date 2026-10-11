# Local-files directory boundary

`GET /api/local-files` lists directory metadata beneath the configured
`ALICE_LOCAL_REPO_DIR` (default `/sdcard/repo`). It does not read file contents.
Authentication remains the existing application middleware's responsibility.

The endpoint resolves the configured root and requested path before checking
containment. Relative paths and symlinks which resolve inside that root remain
navigable. Absolute input, embedded NULs and symlinks resolving outside the root
are rejected. Successful responses retain the existing `success`, absolute
canonical `path`, and `items` fields. Each item retains `name`, `is_dir`, and
`size`; symlink entries themselves are reported as non-directories with size 0,
without reading target metadata. A known in-root directory alias may still be
requested directly.

After resolution, the endpoint opens the root and each relative directory
component with `O_DIRECTORY | O_NOFOLLOW`, using the preceding directory
file descriptor. Listing uses the final descriptor, not the previously checked
pathname. Substituting a symlink during that walk is rejected; replacing the
pathname after opening does not redirect the listing. Descriptors are closed on
success and failure. Missing paths return 404; invalid paths return 400; other
failures return a fixed 500 Problem Details response without raw exceptions or
server paths. No path-based fallback runs on platforms lacking these POSIX
operations. Linux/Android support must be checked in the target runtime before
release; the regression suite exercises Linux, not an Android device.

This is a directory-listing boundary, not a complete filesystem sandbox. The
configured root and its ancestors are trusted deployment paths. Protect them
from untrusted replacement; do not put attacker-controlled mounts beneath the
root. An actor able to rename already-open directories outside the root can
change their namespace location while they remain open. Mounts, privileged
filesystem manipulation and a consistent snapshot during concurrent file
changes are not prevented. Successful absolute `path` output remains for API
compatibility; this change does not claim to finish every historical error/path
privacy item in issue #692 or change authentication policy.

Regression tests cover ordinary and nested symlink escape, in-root aliases,
traversal/absolute/NUL input, metadata non-following, deterministic substitution
before component open and after final open, missing paths, symlink loops,
unsupported flags, sanitized failures and descriptor cleanup.

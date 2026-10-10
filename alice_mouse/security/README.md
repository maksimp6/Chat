# Alice Input Security v1 — isolated protocol

Refs #1085 and #650. This module defines owner/device-bound signed grants shared by mouse and keyboard. It is a test-only protocol boundary, **not a production root service**.

- Trusted Live Server authenticates an owner/controller and creates a session lease.
- TrustedSigner signs a bounded action grant with HMAC-SHA256, epoch, session, nonce, monotonic sequence, timestamp and device identity.
- ProtectedVerifier rejects unsigned, expired, revoked, wrong-device, replayed and malformed grants before invoking a backend callback.
- No raw plaintext input endpoint, shell command, or client-selected role is part of this API.
- The `provision()` method is **lab-only direct memory handoff**. A production root-owned authenticated handoff and key isolation are NOT implemented.
- Action payload is bounded in bytes but still needs strict action-specific schema validation before physical dispatch. Do not attach a real backend until those validators, foreground focus checks and sensitive-operation policy are proven.
- This Python test module is not independently secure against same-UID memory access or modification. Production root binary, protected keys, peer identity, cancellation, watchdog and physical device tests remain blocking.

Run `python -m pytest -q tests/alice_input_security/test_grants.py`.

No SELinux changes, no root cutover and no merge until independent review and exact-head CI.

## RED→GREEN progress (2026-10-10)

Root-side action-specific payload validation now rejects unknown fields, invalid coordinates, forbidden power keys, invalid package names and unsafe text fields. 6 of 7 security RED tests are now GREEN; **forged controller identity is intentionally still RED** until authenticated Live Server identity issuance is designed and verified. Do not equate the public dataclass `AuthenticatedPrincipal` with proof of login. Physical dispatch remains disconnected.

## Identity verifier contract (GREEN local, still not deployed)

`SessionAuthority` now fails closed unless a **trusted Live Server-provided** `identity_verifier` callback positively authenticates the controller identity. Matching public `AuthenticatedPrincipal` fields alone is insufficient. The callback is an integration boundary, not a standalone authentication system: a caller controlling authority construction or callback can bypass it. Production must instantiate it only inside the trusted server with verified login/session context and protect root key handoff independently. Test fixture uses object identity solely to prove same-field forgery is rejected.

## Isolated Unix transport (2026-10-10)

`SignedInputSocket` is a lab-only Unix `SOCK_SEQPACKET` boundary: it refuses pre-existing socket paths, requires an owned private directory, restricts the socket to mode 0600, checks Linux `SO_PEERCRED` UID and accepts only bounded signed grants through `ProtectedVerifier`. It has no sign/provision endpoint, no plaintext fallback, no uinput access, and cleans up only its own socket inode. Eight socket tests plus existing security tests pass locally.

**Not a production root trust boundary:** these tests run under the Termux UID. A real root-owned immutable deployment, credential/key handoff, trusted Live Server authentication, action-level confirmation, crash/restart E2E, independent review and rollback remain blocking. Do not replace the live root bridge with this code yet.

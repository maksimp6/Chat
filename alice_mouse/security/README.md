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

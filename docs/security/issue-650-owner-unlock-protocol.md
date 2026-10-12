# Issue #650: owner-authorized unlock protocol (design, no device unlock)

Status: DESIGN ONLY. No production unlock endpoint, PIN entry, keyguard bypass, merge, or cutover.

## Security boundary

Live Server's existing Bearer token is **not** unlock authority. Keep the existing fail-closed Keyguard restrictions in place. A new privileged unlock controller must be isolated from generic /action verbs and from browser-originated requests.

The unlock flow requires:
1. Authenticated owner identity, verified by a trusted server-side identity provider. No public SIGN command, no root signing key delivered to clients.
2. Device enrollment bound to a device-generated, nonexportable Android Keystore key (prefer hardware-backed with attestation when available). Verify signed challenge, enrolled key ID, owner ID and exact device ID; legacy ADB serial/model/ANDROID_ID are diagnostic hints, not cryptographic identity.
3. Owner-presence approval for **each** unlock attempt through an independent trusted channel, with a visible device/owner confirmation where feasible. Never treat a Live Server Bearer credential as approval.
4. Session issuance only after approval: random 256-bit session ID, monotonic epoch, narrow scope `unlock:request`, device ID, owner ID, audience, issue/expiry times (<= 30 seconds), nonce and one-use invocation ID. Sign by trusted issuer. Bind to device key challenge and a TLS-authenticated transport. Deny stale epoch, mismatched audience or device.
5. Atomic replay ledger keyed by owner/device/epoch/session/nonce/invocation. Consume BEFORE action dispatch, persist across process restart, reject duplicates and concurrent races. Fail closed if durable state is unavailable.
6. Rate limit across sessions, clients and restarts: at most one pending attempt/device, at most 3 owner-approved requests per 15 minutes, cooldown after failures, no retry loops. Lockout must not block normal manual device use.
7. Never collect, store, transmit, log, or inject PIN/password/pattern/biometrics. Android lock credentials stay within Android's native trust boundary. If device is securely locked, return `OWNER_INTERACTION_REQUIRED` and ask the owner to unlock locally using system UI. No simulated touches, ADB key events, root bypass or accessibility-based credential entry.
8. Return states `DENIED`, `APPROVED`, `SUBMITTED`, `OBSERVED`, `VERIFIED`, `OWNER_INTERACTION_REQUIRED`, `EXPIRED`. `SUBMITTED` or HTTP 200 is not unlock success. `VERIFIED` requires independent fresh Keyguard state observation on the enrolled device; missing/ambiguous state => fail closed.
9. Log only redacted event IDs, outcome, timestamps and policy decision; no tokens, UI hierarchy, screenshots, serials, credentials or raw challenge material.
10. Preserve existing RDC channel. No service restart, promotion or rollback without an independent recovery route.

## State machine

IDLE -> OWNER_AUTHENTICATED -> CHALLENGE_ISSUED -> OWNER_APPROVED -> SESSION_ISSUED -> CONSUMED -> OBSERVED -> VERIFIED.
Any invalid identity, policy failure, nonce reuse, timeout, disconnect, keyguard ambiguity or epoch mismatch -> DENIED/EXPIRED. Never automatically retry. A securely locked Android device remains OWNER_INTERACTION_REQUIRED.

## E2E acceptance matrix (safe harness first)

- Owner A/device A happy path on synthetic verifier, without any Android unlock action.
- Wrong owner/device/key/audience/epoch/expiry rejected.
- Duplicate nonce, concurrent duplicate invocation, replay after process restart rejected.
- Tampered signature and missing Keystore attestation rejected or explicit downgraded policy blocks unlock.
- Burst requests, reconnect, timeout, cancellation and crash-in-between-consume-and-dispatch fail closed.
- Real Redmi 9 read-only: inspect Keyguard and server status; locked state denies tap/open and privacy-sensitive screen APIs; wake may be separately permitted. Never send unlock, PIN, touch or key commands.
- Verify action result only from independent device state, never request acknowledgment.
- Verify RDC remains online and 25062RN2DY is not targeted.

## Outstanding blockers

Trusted owner/session issuer and device key enrollment; durable atomic replay ledger; independent owner approval channel; verified Keyguard state read; real-device read-only E2E; review of stale /sdcard/alice-ui.xml privacy residue; exact-head CI and independent security review.

## Release gates

No implementation of credential injection or Keyguard bypass. Protocol tests RED->GREEN, adversarial review, real-device E2E, explicit owner acceptance, then separate PR review. **NO MERGE** without permission.

# Alice Live Server — on-device review baseline

Refs: #650, #1161; staging input PR #1162.

This is a **source-controlled snapshot** of the private Redmi 9 loopback Live Server hardened on 2026-10-11. The server is **not** a remotely accessible production service; it binds to `127.0.0.1:8765` in the Android Termux environment. Do not run it with `0.0.0.0`, proxy it without its own authenticated bridge, or assume success of these tests implies phone-unlock capability.

## Trust model and privacy
- Bearer token resides only in `~/.alice-rdc/live-token` (0600); the private policy `~/.alice-rdc/live-device.json` is **never committed**.
- The target is explicitly enrolled from an already ADB-authorized Redmi 9: a pinned ADB serial **plus** checked `model=Redmi_9`, `device=galahad`, and Android secure ID. A transport rotation must be re-enrolled by its owner; no "first online device" fallback.
- Model/Android ID are **not cryptographic attestation** and do not replace independent hardware-backed device and user authentication.
- `/status` redacts ADB serial and foreground component; `POST /action` does not accept the former raw `status` verb.
- `/screen/tree` and `/screen/dom` expose redacted roles/geometry. The text-bearing OCR state checkpoint/DOM is not loaded or persisted by this service. The private in-memory OCR recognition and authenticated screenshot endpoints remain sensitive; do not log or publish them.
- Keyguard detection is fail-closed when Android's explicit state flags are unavailable. When locked, observe/tap/open are forbidden; `wake` only wakes the screen and **does not unlock PIN**.
- Every ADB call selects `-s SERIAL` from the checked private identity. UI automation uses short-lived, per-request private `/data/local/tmp/` XML and cleans it in a finally block. Crash recovery for orphaned temp files remains a follow-up.
- No raw arbitrary-shell API, no unrestricted keycode injection, no remote signed user-session issuer. Real mouse C verifier, keyboard, Unicode IME and system clipboard are **not implemented in this Live Server**.

## Testing

```bash
python -m pytest -q tests/test_alice_live_server_hardening.py
```

Tests use synthetic ADB output, mocked input and a temporary HOME. **They do not wake, unlock, read or alter a real phone.**

The manual, authorized loopback E2E should verify `GET /health` and `GET /status` HTTP 200, unauthenticated requests HTTP 401, denied touch and app launch on Keyguard HTTP 423, and status-verb rejection HTTP 400. Do not print bearer tokens, Android IDs, serials or private UI.

## Deployment policy
Do not automatically install from a PR or CI runner. Preserve RDC as an independent bootstrap/recovery channel, test an isolated candidate server first, then perform an atomic swap of source and target policy with an independent rollback copy and health checks. Never restart RDC from its own control channel. This repository deliberately contains **no token or live device policy**.

**Merge/cutover:** this source snapshot alone is not a completed Live Server → privileged C verifier → Android InputReader → real clipboard E2E. Keep as Draft until review, exact-head CI and explicit owner acceptance. Dota/Xtra streams are excluded from this engineering scope.

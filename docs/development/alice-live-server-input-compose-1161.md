# Alice Live Server input composition — staging only

Refs #1161 #650 #1082 #1085 #1087 #1089.

**Scope:** reproducible single-checkout assembly of four independently owned staging branches for offline security, mouse, keyboard, and synthetic integration tests. This change **does not control a phone** and must **not** be used as an alternative to Alice Pro Live Server. No RDC, Termux, ADB or direct root-shell device E2E is authorized.

## Exact source provenance

- Mouse `74043cfb8dcbd146138a8eeb861e0de1776a428e` (PR #1082): `alice_mouse/{__init__.py,core.py,README.md}` plus module test.
- Security `93c49ec64dbd37807f75e62c2b634dff8ab8042f` (PR #1090): `alice_mouse/security/`, `tests/alice_input_security/`.
- Keyboard `04f5bc19027712ed21f18df3c1145eb1172a243d` (PR #1091): `alice_mouse/drivers/keyboard/`, `tests/alice_keyboard/`.
- Integration QA `d1785d8e09d2b2a34c00eeb65511060fdd9abad2` (PR #1095): `tests/alice_input_e2e/`.
- Base: `develop` `e6d1d63addd55370218a2ab35e024d24bca95143`. All implementation files copied by their existing Git blob SHA without modifications; original PR branches remain owners. This composite PR must not subsume those PRs.

## Runnable non-physical checks

```bash
python -m pytest -q tests/alice_input_security tests/alice_input_e2e tests/test_alice_mouse_module.py tests/alice_keyboard/test_process_crash_emulation.py
bash tests/alice_input_e2e/fast_gate.sh
```

The C and signed socket fixtures use mocked/synthetic input. **A green result is not Live Server E2E, device authorization, Android InputReader acceptance, OS clipboard, or user approval.** In particular `text` is a validated signed action but no trusted Android IME or clipboard handler has been shipped.

## Hard NO-MERGE / NO-CUTOVER gates

1. Connect **only** Alice Pro Live Server's authenticated owner session to an independently root-owned C verifier. No direct `su`, ADB, RDC, Termux or alternative input transport from the assistant/client.
2. Prove on Redmi 9, **through Live Server**, a signed mouse click, keyboard chord with key release, and Unicode copy → clipboard → paste, with bounded per-action trace and resulting on-screen evidence (no user secrets published).
3. Prove wrong-device/role, forged session, replay, TTL expiry, backend crash, timeout and `UI_DEV_DESTROY` fail closed; test clipboard consent and secret redaction.
4. Exact-head required CI and independent review; then explicit user acceptance. No merge/cutover until all evidence is real.

**Excluded from this project:** Twitch/Xtra/Dota 2 stream playback. This was the user's personal activity, not an Alice input blocker.

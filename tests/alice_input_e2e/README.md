# Alice Input integration contract — #1089

This is an isolated Integration & QA-owned test slice, **not a combined driver implementation**. The test compiles the real C keyboard allowlist and enumerates it, then compares every supported Linux keycode against Python Security's root-side signed-command schema. It fails if either dependency is missing or the policies differ; it does not silently skip required checks.

- Baseline `develop` without #1090/#1091: expected RED (dependencies not integrated).
- Temporary combined fixture using exact local Security head `5a48775` and Keyboard head `810738c`: **1 PASS** on Redmi 9; no production code changed.
- When #1090 and #1091 are integrated into `develop`, run this gate on exact PR head in CI. Any future policy drift must fail CI.
- No real `/dev/uinput`, root UID, keyboard events, SELinux changes, RDC changes, merge or cutover.

## Signed socket -> real C keyboard state machine

`test_signed_c_keyboard.py` builds the actual C keyboard core as a temporary shared library and exercises signed Unix socket commands through Python verifier to C `alice_keyboard_down/up`, recording `EV_KEY` and `SYN_REPORT` via a mock emitter. It verifies Ctrl+A chord order, unsigned rejection and backend `KEY_UP` failure revocation. Temporary combined fixture from Security `5a48775` and Keyboard `810738c`: 1 PASS on Redmi 9. **Not physical E2E:** no `/dev/uinput` device, root process, different UID, Android InputReader or independent C watchdog. `ctypes` storage is a test-only ABI harness; no production FFI contract is implied.

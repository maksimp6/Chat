# Alice Input integration contract — #1089

This is an isolated Integration & QA-owned test slice, **not a combined driver implementation**. The test compiles the real C keyboard allowlist and enumerates it, then compares every supported Linux keycode against Python Security's root-side signed-command schema. It fails if either dependency is missing or the policies differ; it does not silently skip required checks.

- Baseline `develop` without #1090/#1091: expected RED (dependencies not integrated).
- Temporary combined fixture using exact local Security head `5a48775` and Keyboard head `810738c`: **1 PASS** on Redmi 9; no production code changed.
- When #1090 and #1091 are integrated into `develop`, run this gate on exact PR head in CI. Any future policy drift must fail CI.
- No real `/dev/uinput`, root UID, keyboard events, SELinux changes, RDC changes, merge or cutover.

## Signed socket -> real C keyboard state machine

`test_signed_c_keyboard.py` builds the actual C keyboard core as a temporary shared library and exercises signed Unix socket commands through Python verifier to C `alice_keyboard_down/up`, recording `EV_KEY` and `SYN_REPORT` via a mock emitter. It verifies Ctrl+A chord order, unsigned rejection and backend `KEY_UP` failure revocation. Temporary combined fixture from Security `5a48775` and Keyboard `810738c`: 1 PASS on Redmi 9. **Not physical E2E:** no `/dev/uinput` device, root process, different UID, Android InputReader or independent C watchdog. `ctypes` storage is a test-only ABI harness; no production FFI contract is implied.

## Autonomous C watchdog gate

`test_c_watchdog_bridge.py` compiles the real staged C keyboard runner, calls `runner_down(KEY_A)`, stalls the client without `tick()`/`key_up()`, and asserts exactly one `KEY_UP` was observed before shutdown. On current `develop` it fails because #1091 is not integrated; on an isolated checkout containing Keyboard head `810738c`, **1 PASS** on Redmi 9. It uses a mocked event callback, not an actual `/dev/uinput` device. It does not establish crash survival, root isolation or SELinux cross-UID access.

## uinput teardown on partial KEY_UP write

`test_uinput_teardown.py` compiles the real Keyboard #1091 `keyboard.c` and `uinput_device.c` against a mocked syscall table. It injects a short `EV_KEY KEY_UP` write and checks fail-closed driver state, one `UI_DEV_DESTROY`, one `close`, no subsequent key events and idempotent repeated destroy. Isolated Keyboard head `810738c`: **1 PASS** on Redmi 9. This is a mocked syscall test only; real kernel device registration, crash/restart behavior and root-owned signed verifier E2E remain unverified.

## Independent review fix: C-owned fixture ABI

The signed-to-C test no longer casts an arbitrary Python `ctypes.create_string_buffer(1024)` to `alice_keyboard*`. `keyboard_fixture.c` now owns allocation/alignment and the exact C struct size via `calloc(sizeof(alice_keyboard_fixture))`; Python holds only an opaque pointer with declared ctypes signatures and explicit free. Combined Security+Keyboard temporary fixture: **4/4 integration tests PASS** on Redmi 9. Still not a production FFI boundary or physical root E2E.

## Review fix: exception-safe C fixture and explicit ABI

`test_signed_c_keyboard.py` now registers C close/free immediately after allocation using `ExitStack`, before identity/session setup and socket startup; a successfully started endpoint is stopped before C fixture cleanup. The fixture function signatures explicitly declare `c_int` return types and `None` for free. Combined local checkout rerun: **4/4 PASS**. Fault-injection specifically during authority setup is still recommended before calling this independently reviewed.

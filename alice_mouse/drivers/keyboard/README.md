# Alice Keyboard: C evdev core (staging)

Issue #1087; parent #650. Hardware-keyboard behavior is implemented as a small C state machine that emits EV_KEY + SYN_REPORT through an injected callback. This commit **does not create** a /dev/uinput device and does not send keys to Android.

- Explicit Linux KEY_* whitelist (letters, digits, modifiers, navigation, punctuation, F1-F12); rejects KEY_POWER and unsupported codes.
- keydown/keyup with up to 16 simultaneously held keys; allows chords such as Ctrl+A.
- Tracks pressed keys before emitting, supports a 1500 ms lease checked by a daemon-owned background watchdog thread (`alice_keyboard_runner_start`, serialized down/up, `runner_stop`). The kernel must close the uinput FD when the owning daemon exits; actual process-crash E2E is not yet proven.
- Fail-closed on write or SYN errors; `release_all` retries releases and retains held state until a successful SYN. A caller-supplied device-destroy callback is invoked after release failure; the callback is mocked in unit tests, and real UI_DEV_DESTROY is not yet integrated.
- No raw network/Unix command parser; production integration must be behind a root-owned, independently authenticated signed verifier from #1085.
- Arbitrary Unicode/Russian/emoji is not achievable with HID keycodes alone: a separately authorized Android IME text-commit adapter is still required.

## Test on Termux

```sh
clang -std=gnu11 -Wall -Wextra -Werror -O2 -pthread -Ialice_mouse/drivers/keyboard \
 alice_mouse/drivers/keyboard/keyboard.c tests/alice_keyboard/test_keyboard.c \
 -o /data/data/com.termux/files/usr/tmp/alice-keyboard-test
/data/data/com.termux/files/usr/tmp/alice-keyboard-test
```

Required before cutover: independently protected root key/session, uinput setup + UI_DEV_DESTROY, real Android InputReader recognition, physical chord/key-up/fault/restart E2E, Unicode IME, CI and owner acceptance. No SELinux changes, no live daemon replacement, no merge.

RED→GREEN evidence: 5 original scenarios plus autonomous watchdog and destroy callback, 7/7 PASS in isolated C harness. No production uinput opened.

## Staged /dev/uinput lifecycle adapter

`uinput_device.c` supports explicit EV_KEY/EV_SYN setup, key-bit registration, UI_DEV_SETUP, UI_DEV_CREATE, bounded event writes and idempotent UI_DEV_DESTROY + close. The Linux syscall adapter in `uinput_linux.c` is **not invoked by default**; it opens `/dev/uinput` only when a separately authorized root owner explicitly calls `alice_uinput_create` with these ops. Test harness injects mocked syscalls; three lifecycle tests PASS. No actual keyboard device has been created or registered with Android InputReader. Do not connect a production socket until #1090's independent root authorization is proven.

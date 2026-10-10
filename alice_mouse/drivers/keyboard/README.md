# Alice Keyboard: C evdev core (staging)

Issue #1087; parent #650. Hardware-keyboard behavior is implemented as a small C state machine that emits EV_KEY + SYN_REPORT through an injected callback. This commit **does not create** a /dev/uinput device and does not send keys to Android.

- Explicit Linux KEY_* whitelist (letters, digits, modifiers, navigation, punctuation, F1-F12); rejects KEY_POWER and unsupported codes.
- keydown/keyup with up to 16 simultaneously held keys; allows chords such as Ctrl+A.
- Tracks pressed keys before emitting, supports a 1500 ms lease checked by `alice_keyboard_tick()`; this function must be called by an **independent root daemon watchdog**. Without a watchdog, a crashed caller can leave keys held.
- Fail-closed on write or SYN errors; `release_all` retries releases and retains held state until a successful SYN.
- No raw network/Unix command parser; production integration must be behind a root-owned, independently authenticated signed verifier from #1085.
- Arbitrary Unicode/Russian/emoji is not achievable with HID keycodes alone: a separately authorized Android IME text-commit adapter is still required.

## Test on Termux

```sh
clang -std=c11 -Wall -Wextra -Werror -O2 -Ialice_mouse/drivers/keyboard \
 alice_mouse/drivers/keyboard/keyboard.c tests/alice_keyboard/test_keyboard.c \
 -o /data/data/com.termux/files/usr/tmp/alice-keyboard-test
/data/data/com.termux/files/usr/tmp/alice-keyboard-test
```

Required before cutover: independently protected root key/session, uinput setup + UI_DEV_DESTROY, real Android InputReader recognition, physical chord/key-up/fault/restart E2E, Unicode IME, CI and owner acceptance. No SELinux changes, no live daemon replacement, no merge.

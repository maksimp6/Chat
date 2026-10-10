/* Alice Keyboard — standalone Linux evdev/uinput driver core.
 * No socket, no authentication, no automatic device creation. A root-owned
 * signed verifier must authorize commands before calling this API.
 */
#ifndef ALICE_KEYBOARD_H
#define ALICE_KEYBOARD_H
#include <linux/input.h>
#include <linux/uinput.h>
#include <stdint.h>
#include <stddef.h>

#define ALICE_KEY_MAX_HELD 16
#define ALICE_KEY_LEASE_MS 1500

typedef int (*alice_key_emit_fn)(void *, unsigned short, unsigned short, int);
typedef int64_t (*alice_key_clock_fn)(void *);

typedef struct {
    void *ctx;
    alice_key_emit_fn emit;
    alice_key_clock_fn now_ms;
    unsigned short held[ALICE_KEY_MAX_HELD];
    size_t held_count;
    int64_t deadline_ms;
    int faulted;
} alice_keyboard;

int alice_keyboard_init(alice_keyboard *kb, void *ctx,
                        alice_key_emit_fn emit, alice_key_clock_fn clock);
int alice_keyboard_down(alice_keyboard *kb, unsigned short key);
int alice_keyboard_up(alice_keyboard *kb, unsigned short key);
int alice_keyboard_release_all(alice_keyboard *kb);
int alice_keyboard_tick(alice_keyboard *kb);
int alice_keyboard_close(alice_keyboard *kb);
int alice_keyboard_allowed(unsigned short key);
#endif

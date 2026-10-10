/* Test-only ABI wrapper: C owns allocation and alignment of alice_keyboard. */
#include "keyboard.h"
#include <stdlib.h>
#include <errno.h>

typedef struct {
    alice_keyboard keyboard;
} alice_keyboard_fixture;
static int fixture_allocations=0;
static int fixture_closes=0;
static int fixture_frees=0;

alice_keyboard_fixture *alice_fixture_new(void *ctx,
        alice_key_emit_fn emit, alice_key_clock_fn clock) {
    alice_keyboard_fixture *fixture=calloc(1,sizeof(*fixture));
    if(!fixture)return NULL;
    if(alice_keyboard_init(&fixture->keyboard,ctx,emit,clock)!=0) {
        free(fixture);
        return NULL;
    }
    fixture_allocations++;
    return fixture;
}

int alice_fixture_down(alice_keyboard_fixture *fixture,unsigned short key) {
    if(!fixture)return -EINVAL;
    return alice_keyboard_down(&fixture->keyboard,key);
}

int alice_fixture_up(alice_keyboard_fixture *fixture,unsigned short key) {
    if(!fixture)return -EINVAL;
    return alice_keyboard_up(&fixture->keyboard,key);
}

int alice_fixture_close(alice_keyboard_fixture *fixture) {
    if(!fixture)return -EINVAL;
    fixture_closes++;
    return alice_keyboard_close(&fixture->keyboard);
}

void alice_fixture_free(alice_keyboard_fixture *fixture) {
    if(fixture)fixture_frees++;
    free(fixture);
}

/* Test-only lifecycle counters, not part of the production keyboard API. */
int alice_fixture_live_count(void){return fixture_allocations-fixture_frees;}
int alice_fixture_close_count(void){return fixture_closes;}
int alice_fixture_free_count(void){return fixture_frees;}

/* Test-only ABI wrapper: C owns allocation and alignment of remote_input_keyboard. */
#include "keyboard.h"
#include <stdlib.h>
#include <errno.h>

typedef struct {
    remote_input_keyboard keyboard;
} remote_input_keyboard_fixture;
static int fixture_allocations=0;
static int fixture_closes=0;
static int fixture_frees=0;

remote_input_keyboard_fixture *remote_input_fixture_new(void *ctx,
        logos_key_emit_fn emit, logos_key_clock_fn clock) {
    remote_input_keyboard_fixture *fixture=calloc(1,sizeof(*fixture));
    if(!fixture)return NULL;
    if(remote_input_keyboard_init(&fixture->keyboard,ctx,emit,clock)!=0) {
        free(fixture);
        return NULL;
    }
    fixture_allocations++;
    return fixture;
}

int remote_input_fixture_down(remote_input_keyboard_fixture *fixture,unsigned short key) {
    if(!fixture)return -EINVAL;
    return remote_input_keyboard_down(&fixture->keyboard,key);
}

int remote_input_fixture_up(remote_input_keyboard_fixture *fixture,unsigned short key) {
    if(!fixture)return -EINVAL;
    return remote_input_keyboard_up(&fixture->keyboard,key);
}

int remote_input_fixture_close(remote_input_keyboard_fixture *fixture) {
    if(!fixture)return -EINVAL;
    fixture_closes++;
    return remote_input_keyboard_close(&fixture->keyboard);
}

void remote_input_fixture_free(remote_input_keyboard_fixture *fixture) {
    if(fixture)fixture_frees++;
    free(fixture);
}

/* Test-only lifecycle counters, not part of the production keyboard API. */
int remote_input_fixture_live_count(void){return fixture_allocations-fixture_frees;}
int remote_input_fixture_close_count(void){return fixture_closes;}
int remote_input_fixture_free_count(void){return fixture_frees;}

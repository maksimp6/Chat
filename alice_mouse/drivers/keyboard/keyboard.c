#include "keyboard.h"
#include <errno.h>
#include <string.h>
#include <time.h>

/* Bounded, explicitly supported physical keycodes; never KEY_POWER or
 * arbitrary system controls. HID cannot encode arbitrary Unicode text.
 */
int alice_keyboard_allowed(unsigned short k) {
    /* Linux KEY_A..KEY_Z are NOT numerically contiguous. */
    switch(k) {
    case KEY_A: case KEY_B: case KEY_C: case KEY_D: case KEY_E:
    case KEY_F: case KEY_G: case KEY_H: case KEY_I: case KEY_J:
    case KEY_K: case KEY_L: case KEY_M: case KEY_N: case KEY_O:
    case KEY_P: case KEY_Q: case KEY_R: case KEY_S: case KEY_T:
    case KEY_U: case KEY_V: case KEY_W: case KEY_X: case KEY_Y:
    case KEY_Z:
    case KEY_0: case KEY_1: case KEY_2: case KEY_3: case KEY_4:
    case KEY_5: case KEY_6: case KEY_7: case KEY_8: case KEY_9:
        return 1;
    default:break;
    }
    if (k >= KEY_F1 && k <= KEY_F12) return 1;
    switch(k) {
    case KEY_ENTER: case KEY_ESC: case KEY_BACKSPACE: case KEY_TAB:
    case KEY_SPACE: case KEY_DELETE: case KEY_INSERT:
    case KEY_HOME: case KEY_END: case KEY_PAGEUP: case KEY_PAGEDOWN:
    case KEY_UP: case KEY_DOWN: case KEY_LEFT: case KEY_RIGHT:
    case KEY_LEFTCTRL: case KEY_RIGHTCTRL:
    case KEY_LEFTSHIFT: case KEY_RIGHTSHIFT:
    case KEY_LEFTALT: case KEY_RIGHTALT:
    case KEY_LEFTMETA: case KEY_RIGHTMETA:
    case KEY_MINUS: case KEY_EQUAL: case KEY_LEFTBRACE: case KEY_RIGHTBRACE:
    case KEY_BACKSLASH: case KEY_SEMICOLON: case KEY_APOSTROPHE:
    case KEY_GRAVE: case KEY_COMMA: case KEY_DOT: case KEY_SLASH:
        return 1;
    default: return 0;
    }
}
int alice_keyboard_init(alice_keyboard *kb, void *ctx,
                        alice_key_emit_fn emit, alice_key_clock_fn clock) {
    if (!kb || !emit || !clock) return -EINVAL;
    memset(kb,0,sizeof(*kb));
    kb->ctx=ctx; kb->emit=emit; kb->now_ms=clock;
    return 0;
}
static int locate(const alice_keyboard *kb, unsigned short k) {
    for(size_t i=0;i<kb->held_count;i++)
        if(kb->held[i]==k)return (int)i;
    return -1;
}
static int emit_syn(alice_keyboard *kb) {
    return kb->emit(kb->ctx,EV_SYN,SYN_REPORT,0);
}
void alice_keyboard_set_destroy(alice_keyboard *kb, alice_key_destroy_fn destroy) {
    if(kb)kb->destroy=destroy;
}
static int destroy_on_failure(alice_keyboard *kb){
    if(!kb->destroy || kb->destroyed)return -EIO;
    if(kb->destroy(kb->ctx)<0)return -EIO;
    kb->destroyed=1;
    kb->held_count=0;
    kb->deadline_ms=0;
    return -EIO; /* a destroyed device is never a successful command */
}
int alice_keyboard_release_all(alice_keyboard *kb) {
    if (!kb) return -EINVAL;
    int failed=0;
    /* Keep every held key tracked until the release SYN is acknowledged.
     * A failed SYN must not silently lose our ability to retry KEY_UP. */
    for(size_t i=0;i<kb->held_count;i++) {
        if(kb->emit(kb->ctx,EV_KEY,kb->held[i],0)<0)failed=1;
    }
    if(emit_syn(kb)<0)failed=1;
    if(failed){kb->faulted=1;return destroy_on_failure(kb);}
    kb->held_count=0;
    kb->deadline_ms=0;
    return 0;
}
int alice_keyboard_down(alice_keyboard *kb,unsigned short key) {
    if(!kb || !alice_keyboard_allowed(key))return -EINVAL;
    if(kb->faulted || kb->destroyed)return -EIO;
    if(locate(kb,key)>=0)return -EALREADY;
    if(kb->held_count==ALICE_KEY_MAX_HELD)return -ENOSPC;
    /* Track before emitting, so partial writes can be released. */
    kb->held[kb->held_count++]=key;
    kb->deadline_ms=kb->now_ms(kb->ctx)+ALICE_KEY_LEASE_MS;
    if(kb->emit(kb->ctx,EV_KEY,key,1)<0 || emit_syn(kb)<0) {
        kb->faulted=1;
        alice_keyboard_release_all(kb);
        return -EIO;
    }
    return 0;
}
int alice_keyboard_up(alice_keyboard *kb,unsigned short key) {
    if(!kb || !alice_keyboard_allowed(key))return -EINVAL;
    if(kb->faulted || kb->destroyed)return -EIO;
    int index=locate(kb,key);
    if(index<0)return -ENOENT;
    if(kb->emit(kb->ctx,EV_KEY,key,0)<0 || emit_syn(kb)<0) {
        kb->faulted=1;
        alice_keyboard_release_all(kb);
        return -EIO;
    }
    memmove(&kb->held[index],&kb->held[index+1],
            (kb->held_count-(size_t)index-1)*sizeof(kb->held[0]));
    kb->held_count--;
    if(!kb->held_count)kb->deadline_ms=0;
    return 0;
}
int alice_keyboard_tick(alice_keyboard *kb) {
    if(!kb)return -EINVAL;
    if(kb->held_count && kb->now_ms(kb->ctx)>=kb->deadline_ms)
        return alice_keyboard_release_all(kb);
    return 0;
}
int alice_keyboard_close(alice_keyboard *kb) {
    return alice_keyboard_release_all(kb);
}

/* Runner serializes all device access. No independent process guarantees:
 * if the daemon dies, its uinput FD must be closed by the kernel. */
static void *watchdog_main(void *arg){
    alice_keyboard_runner *r=arg;
    struct timespec delay={.tv_sec=0,.tv_nsec=20000000};
    for(;;){
        nanosleep(&delay,NULL);
        pthread_mutex_lock(&r->mutex);
        if(!r->running){pthread_mutex_unlock(&r->mutex);break;}
        (void)alice_keyboard_tick(r->keyboard);
        pthread_mutex_unlock(&r->mutex);
    }
    return NULL;
}
int alice_keyboard_runner_start(alice_keyboard_runner *r,alice_keyboard *kb){
    if(!r || !kb || !kb->emit || !kb->now_ms)return -EINVAL;
    memset(r,0,sizeof(*r));r->keyboard=kb;
    if(pthread_mutex_init(&r->mutex,NULL))return -EIO;
    r->running=1;
    if(pthread_create(&r->worker,NULL,watchdog_main,r)){
        r->running=0;pthread_mutex_destroy(&r->mutex);return -EIO;
    }
    r->started=1;return 0;
}
int alice_keyboard_runner_down(alice_keyboard_runner *r,unsigned short key){
    if(!r || !r->started)return -EINVAL;
    pthread_mutex_lock(&r->mutex);
    int result=r->running?alice_keyboard_down(r->keyboard,key):-EIO;
    pthread_mutex_unlock(&r->mutex);return result;
}
int alice_keyboard_runner_up(alice_keyboard_runner *r,unsigned short key){
    if(!r || !r->started)return -EINVAL;
    pthread_mutex_lock(&r->mutex);
    int result=r->running?alice_keyboard_up(r->keyboard,key):-EIO;
    pthread_mutex_unlock(&r->mutex);return result;
}
int alice_keyboard_runner_stop(alice_keyboard_runner *r){
    if(!r || !r->started)return -EINVAL;
    pthread_mutex_lock(&r->mutex);r->running=0;pthread_mutex_unlock(&r->mutex);
    pthread_join(r->worker,NULL);
    pthread_mutex_lock(&r->mutex);
    int result=alice_keyboard_close(r->keyboard);
    pthread_mutex_unlock(&r->mutex);
    pthread_mutex_destroy(&r->mutex);r->started=0;
    return result;
}

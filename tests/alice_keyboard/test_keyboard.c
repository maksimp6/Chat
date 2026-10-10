#include "../../alice_mouse/drivers/keyboard/keyboard.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef struct {int64_t now;int down;int up;int syn;int fail_down;int fail_up;int fail_syn;} mock;
static int64_t clock_fn(void *p){return ((mock*)p)->now;}
static int emit(void *p,unsigned short type,unsigned short code,int value){
    mock *m=p;
    if(type==EV_SYN){m->syn++;return m->fail_syn?-1:0;}
    if(type==EV_KEY){
        if(value){m->down++;return m->fail_down?-1:0;}
        m->up++;return m->fail_up?-1:0;
    }
    (void)code;return 0;
}
static void basic(void){
    mock m={0};alice_keyboard k;
    assert(!alice_keyboard_init(&k,&m,emit,clock_fn));
    assert(!alice_keyboard_down(&k,KEY_LEFTCTRL));
    assert(!alice_keyboard_down(&k,KEY_A));
    assert(k.held_count==2);
    assert(alice_keyboard_down(&k,KEY_A)==-EALREADY);
    assert(!alice_keyboard_up(&k,KEY_A));
    assert(!alice_keyboard_up(&k,KEY_LEFTCTRL));
    assert(k.held_count==0 && m.down==2 && m.up==2);
    assert(alice_keyboard_up(&k,KEY_A)==-ENOENT);
    assert(alice_keyboard_down(&k,KEY_POWER)==-EINVAL);
    assert(!alice_keyboard_close(&k));
    puts("basic/chord: PASS");
}
static void lease(void){
    mock m={0};alice_keyboard k;
    assert(!alice_keyboard_init(&k,&m,emit,clock_fn));
    assert(!alice_keyboard_down(&k,KEY_LEFTSHIFT));
    m.now=1499;assert(!alice_keyboard_tick(&k));assert(k.held_count==1);
    m.now=1501;assert(!alice_keyboard_tick(&k));
    assert(k.held_count==0 && m.up==1);
    puts("lease/release: PASS");
}
static void failure(void){
    mock m={0};alice_keyboard k;
    assert(!alice_keyboard_init(&k,&m,emit,clock_fn));
    m.fail_down=1;
    assert(alice_keyboard_down(&k,KEY_A)==-EIO);
    assert(k.faulted && k.held_count==0 && m.up==1);
    assert(alice_keyboard_down(&k,KEY_B)==-EIO);
    puts("failed-down cleanup: PASS");
}
static void stuck(void){
    mock m={0};alice_keyboard k;
    assert(!alice_keyboard_init(&k,&m,emit,clock_fn));
    assert(!alice_keyboard_down(&k,KEY_A));
    m.fail_up=1;
    assert(alice_keyboard_up(&k,KEY_A)==-EIO);
    assert(k.faulted && k.held_count==1);
    m.fail_up=0;
    assert(!alice_keyboard_close(&k));
    assert(k.held_count==0);
    puts("failed-up fail-closed/retry: PASS");
}
static void syn_fault(void){
    mock m={0};alice_keyboard k;
    assert(!alice_keyboard_init(&k,&m,emit,clock_fn));
    m.fail_syn=1;
    assert(alice_keyboard_down(&k,KEY_A)==-EIO);
    assert(k.faulted);
    m.fail_syn=0;
    assert(!alice_keyboard_close(&k));
    puts("failed-SYN recovery: PASS");
}
int main(void){basic();lease();failure();stuck();syn_fault();return 0;}

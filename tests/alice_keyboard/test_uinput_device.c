#include "../../alice_mouse/drivers/keyboard/uinput_device.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef struct {int opens,closes,creates,destroys,keys,setup,events,writes,fail_at,ioctls,partial_write;} mock;
static int open_fn(void *ctx){mock *m=ctx;m->opens++;return 47;}
static int ioctl_fn(void *ctx,int fd,unsigned long req,void *arg){
    mock *m=ctx;assert(fd==47);
    m->ioctls++;
    if(m->fail_at && m->ioctls==m->fail_at)return -1;
    if(req==UI_DEV_CREATE)m->creates++;
    if(req==UI_DEV_DESTROY)m->destroys++;
    if(req==UI_DEV_SETUP){m->setup++;struct uinput_setup *s=arg;assert(!strcmp(s->name,"Alice Virtual Keyboard"));}
    if(req==UI_SET_KEYBIT)m->keys++;
    if(req==UI_SET_EVBIT)m->events++;
    return 0;
}
static ssize_t write_fn(void *ctx,int fd,const void *p,size_t n){
    mock *m=ctx;assert(fd==47);
    assert(n==sizeof(struct input_event));
    const struct input_event *ev=p;
    assert(ev->type==EV_KEY || ev->type==EV_SYN);
    m->writes++;
    return m->partial_write?(ssize_t)n-1:(ssize_t)n;
}
static int close_fn(void *ctx,int fd){mock *m=ctx;assert(fd==47);m->closes++;return 0;}
static alice_uinput_ops ops(mock *m){return (alice_uinput_ops){open_fn,ioctl_fn,write_fn,close_fn,m};}
static int64_t now(void *ctx){(void)ctx;return 0;}
static void lifecycle(void){
    mock m={0};alice_uinput_device d={0};alice_uinput_ops o=ops(&m);
    assert(!alice_uinput_create(&d,&o));
    alice_uinput_emitter emitter={&d,&o};alice_keyboard k;
    assert(!alice_keyboard_init(&k,&emitter,alice_uinput_emit_bound,now));
    alice_keyboard_set_destroy(&k,alice_uinput_destroy_bound);
    assert(!alice_keyboard_down(&k,KEY_A));
    assert(!alice_keyboard_up(&k,KEY_A));
    assert(m.writes==4);
    assert(!alice_uinput_destroy(&d,&o));
    assert(m.destroys==1 && m.closes==1 && !d.created);
    assert(alice_uinput_emit_bound(&emitter,EV_KEY,KEY_A,1)==-EIO);
    puts("mocked-device-lifecycle: PASS");
}
static void failed_create(void){
    mock m={0};alice_uinput_device d={0};alice_uinput_ops o=ops(&m);
    m.fail_at=3;
    assert(alice_uinput_create(&d,&o)==-EIO);
    assert(m.closes==1 && m.destroys==0 && !d.owned);
    puts("failed-create-cleanup: PASS");
}
static void double_destroy(void){
    mock m={0};alice_uinput_device d={0};alice_uinput_ops o=ops(&m);
    assert(!alice_uinput_create(&d,&o));
    assert(!alice_uinput_destroy(&d,&o));
    assert(!alice_uinput_destroy(&d,&o));
    assert(m.destroys==1 && m.closes==1);
    puts("idempotent-destroy: PASS");
}
static void unsafe_direct_events(void){
    mock m={0};alice_uinput_device d={0};alice_uinput_ops o=ops(&m);
    assert(!alice_uinput_create(&d,&o));
    alice_uinput_emitter e={&d,&o};
    assert(alice_uinput_emit_bound(&e,EV_KEY,KEY_POWER,1)==-EINVAL);
    assert(alice_uinput_emit_bound(&e,EV_REL,REL_X,1)==-EINVAL);
    assert(alice_uinput_emit_bound(&e,EV_KEY,KEY_A,2)==-EINVAL);
    assert(m.writes==0);
    assert(!alice_uinput_destroy(&d,&o));
    puts("reject-unsafe-raw-events: PASS");
}
static void partial_write(void){
    mock m={0};alice_uinput_device d={0};alice_uinput_ops o=ops(&m);
    assert(!alice_uinput_create(&d,&o));
    alice_uinput_emitter e={&d,&o};m.partial_write=1;
    assert(alice_uinput_emit_bound(&e,EV_KEY,KEY_A,1)==-EIO);
    assert(!alice_uinput_destroy(&d,&o));
    puts("reject-partial-event-write: PASS");
}
int main(void){lifecycle();failed_create();double_destroy();unsafe_direct_events();partial_write();return 0;}

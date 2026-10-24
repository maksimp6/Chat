#include "../../remote_input/drivers/keyboard/uinput_device.h"
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
    if(req==UI_DEV_SETUP){m->setup++;struct uinput_setup *s=arg;assert(!strcmp(s->name,"Logos Virtual Keyboard"));}
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
static remote_input_uinput_ops ops(mock *m){return (remote_input_uinput_ops){open_fn,ioctl_fn,write_fn,close_fn,m};}
static int64_t now(void *ctx){(void)ctx;return 0;}
static void lifecycle(void){
    mock m={0};remote_input_uinput_device d={0};remote_input_uinput_ops o=ops(&m);
    assert(!remote_input_uinput_create(&d,&o));
    remote_input_uinput_emitter emitter={&d,&o};remote_input_keyboard k;
    assert(!remote_input_keyboard_init(&k,&emitter,remote_input_uinput_emit_bound,now));
    remote_input_keyboard_set_destroy(&k,remote_input_uinput_destroy_bound);
    assert(!remote_input_keyboard_down(&k,KEY_A));
    assert(!remote_input_keyboard_up(&k,KEY_A));
    assert(m.writes==4);
    assert(!remote_input_uinput_destroy(&d,&o));
    assert(m.destroys==1 && m.closes==1 && !d.created);
    assert(remote_input_uinput_emit_bound(&emitter,EV_KEY,KEY_A,1)==-EIO);
    puts("mocked-device-lifecycle: PASS");
}
static void failed_create(void){
    mock m={0};remote_input_uinput_device d={0};remote_input_uinput_ops o=ops(&m);
    m.fail_at=3;
    assert(remote_input_uinput_create(&d,&o)==-EIO);
    assert(m.closes==1 && m.destroys==0 && !d.owned);
    puts("failed-create-cleanup: PASS");
}
static void double_destroy(void){
    mock m={0};remote_input_uinput_device d={0};remote_input_uinput_ops o=ops(&m);
    assert(!remote_input_uinput_create(&d,&o));
    assert(!remote_input_uinput_destroy(&d,&o));
    assert(!remote_input_uinput_destroy(&d,&o));
    assert(m.destroys==1 && m.closes==1);
    puts("idempotent-destroy: PASS");
}
static void unsafe_direct_events(void){
    mock m={0};remote_input_uinput_device d={0};remote_input_uinput_ops o=ops(&m);
    assert(!remote_input_uinput_create(&d,&o));
    remote_input_uinput_emitter e={&d,&o};
    assert(remote_input_uinput_emit_bound(&e,EV_KEY,KEY_POWER,1)==-EINVAL);
    assert(remote_input_uinput_emit_bound(&e,EV_REL,REL_X,1)==-EINVAL);
    assert(remote_input_uinput_emit_bound(&e,EV_KEY,KEY_A,2)==-EINVAL);
    assert(m.writes==0);
    assert(!remote_input_uinput_destroy(&d,&o));
    puts("reject-unsafe-raw-events: PASS");
}
static void partial_write(void){
    mock m={0};remote_input_uinput_device d={0};remote_input_uinput_ops o=ops(&m);
    assert(!remote_input_uinput_create(&d,&o));
    remote_input_uinput_emitter e={&d,&o};m.partial_write=1;
    assert(remote_input_uinput_emit_bound(&e,EV_KEY,KEY_A,1)==-EIO);
    assert(!remote_input_uinput_destroy(&d,&o));
    puts("reject-partial-event-write: PASS");
}
static void failed_key_release_destroys_mock_device(void){
    mock m={0};remote_input_uinput_device d={0};remote_input_uinput_ops o=ops(&m);
    assert(!remote_input_uinput_create(&d,&o));
    remote_input_uinput_emitter e={&d,&o};remote_input_keyboard k;
    assert(!remote_input_keyboard_init(&k,&e,remote_input_uinput_emit_bound,now));
    remote_input_keyboard_set_destroy(&k,remote_input_uinput_destroy_bound);
    assert(!remote_input_keyboard_down(&k,KEY_A));
    /* Emulate a short write on KEY_UP while a key is physically held.
     * The driver must invoke UI_DEV_DESTROY and close, not claim success. */
    m.partial_write=1;
    assert(remote_input_keyboard_up(&k,KEY_A)==-EIO);
    assert(k.faulted && k.destroyed && k.held_count==0);
    assert(m.destroys==1 && m.closes==1);
    assert(d.fd==-1 && !d.owned && !d.created);
    assert(remote_input_keyboard_down(&k,KEY_B)==-EIO);
    assert(!remote_input_uinput_destroy(&d,&o));
    assert(m.destroys==1 && m.closes==1);
    puts("failed-key-release-destroys-mock-device: PASS");
}
int main(void){lifecycle();failed_create();double_destroy();unsafe_direct_events();partial_write();failed_key_release_destroys_mock_device();return 0;}

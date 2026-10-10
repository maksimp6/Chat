/* Privileged-only adapter. No socket, no root escalation, no auto-start. */
#include "uinput_device.h"
#include <errno.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>

int alice_uinput_destroy(alice_uinput_device *d,const alice_uinput_ops *ops){
    if(!d || !ops || !ops->close_device || !ops->ioctl_device)return -EINVAL;
    int failed=0;
    if(d->owned && d->fd>=0){
        if(d->created && ops->ioctl_device(ops->ctx,d->fd,UI_DEV_DESTROY,NULL)<0)failed=1;
        if(ops->close_device(ops->ctx,d->fd)<0)failed=1;
    }
    d->fd=-1;d->owned=0;d->created=0;
    return failed?-EIO:0;
}
int alice_uinput_create(alice_uinput_device *d,const alice_uinput_ops *ops){
    if(!d || !ops || !ops->open_device || !ops->ioctl_device ||
       !ops->write_device || !ops->close_device)return -EINVAL;
    memset(d,0,sizeof(*d));d->fd=-1;
    int fd=ops->open_device(ops->ctx);
    if(fd<0)return -EACCES;
    d->fd=fd;d->owned=1;
    if(ops->ioctl_device(ops->ctx,fd,UI_SET_EVBIT,(void *)(uintptr_t)EV_KEY)<0 ||
       ops->ioctl_device(ops->ctx,fd,UI_SET_EVBIT,(void *)(uintptr_t)EV_SYN)<0)goto fail;
    /* Register exactly the same whitelist used by keyboard_down/up. */
    for(unsigned int k=0;k<KEY_MAX;k++){
        if(alice_keyboard_allowed((unsigned short)k) &&
           ops->ioctl_device(ops->ctx,fd,UI_SET_KEYBIT,(void *)(uintptr_t)k)<0)goto fail;
    }
    struct uinput_setup setup={0};
    setup.id.bustype=BUS_VIRTUAL;
    setup.id.vendor=0x1A11;
    setup.id.product=0x0002;
    strncpy(setup.name,"Alice Virtual Keyboard",sizeof(setup.name)-1);
    if(ops->ioctl_device(ops->ctx,fd,UI_DEV_SETUP,&setup)<0)goto fail;
    if(ops->ioctl_device(ops->ctx,fd,UI_DEV_CREATE,NULL)<0)goto fail;
    d->created=1;
    return 0;
fail:
    (void)alice_uinput_destroy(d,ops);
    return -EIO;
}
int alice_uinput_emit_bound(void *ctx,unsigned short type,unsigned short code,int value){
    alice_uinput_emitter *e=ctx;
    if(!e || !e->device || !e->device->created || !e->ops)return -EIO;
    struct input_event event={0};
    event.type=type;event.code=code;event.value=value;
    ssize_t n=e->ops->write_device(e->ops->ctx,e->device->fd,&event,sizeof(event));
    return n==(ssize_t)sizeof(event)?0:-EIO;
}
int alice_uinput_destroy_bound(void *ctx){
    alice_uinput_emitter *e=ctx;
    if(!e)return -EINVAL;
    return alice_uinput_destroy(e->device,e->ops);
}
int alice_uinput_emit(void *ctx,unsigned short type,unsigned short code,int value){
    return alice_uinput_emit_bound(ctx,type,code,value);
}

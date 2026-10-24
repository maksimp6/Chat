#ifndef ALICE_KEYBOARD_UINPUT_DEVICE_H
#define ALICE_KEYBOARD_UINPUT_DEVICE_H
#include "keyboard.h"
#include <sys/types.h>
typedef struct {
    int fd;
    int created;
    int owned;
} remote_input_uinput_device;
typedef struct {
    int (*open_device)(void *);
    int (*ioctl_device)(void *,int,unsigned long,void *);
    ssize_t (*write_device)(void *,int,const void *,size_t);
    int (*close_device)(void *,int);
    void *ctx;
} remote_input_uinput_ops;
int remote_input_uinput_create(remote_input_uinput_device *,const remote_input_uinput_ops *);
int remote_input_uinput_emit(void *,unsigned short,unsigned short,int);
int remote_input_uinput_destroy(remote_input_uinput_device *,const remote_input_uinput_ops *);
typedef struct {remote_input_uinput_device *device;const remote_input_uinput_ops *ops;} remote_input_uinput_emitter;
int remote_input_uinput_emit_bound(void *,unsigned short,unsigned short,int);
int remote_input_uinput_destroy_bound(void *);
/* Explicit opt-in syscall backend; do not call before root security E2E. */
remote_input_uinput_ops remote_input_uinput_linux_ops(void);
#endif

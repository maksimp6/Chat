#ifndef ALICE_KEYBOARD_UINPUT_DEVICE_H
#define ALICE_KEYBOARD_UINPUT_DEVICE_H
#include "keyboard.h"
#include <sys/types.h>
typedef struct {
    int fd;
    int created;
    int owned;
} alice_uinput_device;
typedef struct {
    int (*open_device)(void *);
    int (*ioctl_device)(void *,int,unsigned long,void *);
    ssize_t (*write_device)(void *,int,const void *,size_t);
    int (*close_device)(void *,int);
    void *ctx;
} alice_uinput_ops;
int alice_uinput_create(alice_uinput_device *,const alice_uinput_ops *);
int alice_uinput_emit(void *,unsigned short,unsigned short,int);
int alice_uinput_destroy(alice_uinput_device *,const alice_uinput_ops *);
typedef struct {alice_uinput_device *device;const alice_uinput_ops *ops;} alice_uinput_emitter;
int alice_uinput_emit_bound(void *,unsigned short,unsigned short,int);
int alice_uinput_destroy_bound(void *);
/* Explicit opt-in syscall backend; do not call before root security E2E. */
alice_uinput_ops alice_uinput_linux_ops(void);
#endif

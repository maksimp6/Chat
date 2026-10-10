/* Root-only Linux syscall adapter. Never starts itself or creates a device. */
#include "uinput_device.h"
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <errno.h>
static int linux_open(void *ctx){(void)ctx;return open("/dev/uinput",O_WRONLY|O_NONBLOCK|O_CLOEXEC);}
static int linux_ioctl(void *ctx,int fd,unsigned long cmd,void *arg){(void)ctx;return ioctl(fd,cmd,arg);}
static ssize_t linux_write(void *ctx,int fd,const void *p,size_t n){(void)ctx;return write(fd,p,n);}
static int linux_close(void *ctx,int fd){(void)ctx;return close(fd);}
alice_uinput_ops alice_uinput_linux_ops(void){
    return (alice_uinput_ops){linux_open,linux_ioctl,linux_write,linux_close,0};
}

/* Isolated syscall-fault harness for Alice Mouse daemon. NO /dev/uinput,
 * no root, no real /data/adb access and no live input events. */
#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/ioctl.h>
#include <linux/uinput.h>
#include <linux/input.h>
#include <sys/file.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#include <sys/wait.h>

enum { FF_LOCK=1, F_UINPUT, F_CONFIG, F_SETUP, F_CREATE,
       F_SOCKET, F_BIND, F_CHMOD, F_LISTEN, F_COUNT };
static int fault, pipe_writer, audit_writer;
static int mock_open(const char *path, int flags, ...) {
    (void)flags;
    if (strstr(path, "shared.lock") && fault == FF_LOCK) { errno=EACCES; return -1; }
    if (strstr(path, "/dev/uinput") && fault == F_UINPUT) { errno=EACCES; return -1; }
    return dup(pipe_writer);
}
static int mock_flock(int fd, int mode) { (void)fd; (void)mode; return 0; }
static int mock_ioctl(int fd, unsigned long cmd, ...) {
    (void)fd;
    if (cmd == UI_DEV_DESTROY) { write(audit_writer, "D", 1); return 0; }
    if (cmd == UI_DEV_CREATE) { write(audit_writer, "C", 1); if (fault==F_CREATE) goto fail; }
    if (cmd == UI_DEV_SETUP && fault==F_SETUP) goto fail;
    if (cmd == UI_SET_EVBIT && fault==F_CONFIG) goto fail;
    return 0;
fail: errno=EIO; return -1;
}
static int mock_socket(int family, int type, int protocol) {
    (void)family; (void)type; (void)protocol;
    if (fault == F_SOCKET) { errno=EIO; return -1; }
    return dup(pipe_writer);
}
static int mock_bind(int fd,const struct sockaddr *addr,socklen_t len) {
    (void)fd; (void)addr; (void)len;
    if(fault==F_BIND){ errno=EIO; return -1; }
    return 0;
}
static int mock_chmod(const char *path,mode_t mode){
    (void)path; (void)mode;
    if(fault==F_CHMOD){ errno=EIO; return -1; }
    return 0;
}
static int mock_listen(int fd,int n){
    (void)fd; (void)n;
    if(fault==F_LISTEN){ errno=EIO; return -1; }
    return 0;
}
static int mock_unlink(const char *path){
    (void)path;
    write(audit_writer,"U",1);return 0;
}

#define open mock_open
#define flock mock_flock
#define ioctl mock_ioctl
#define socket mock_socket
#define bind mock_bind
#define chmod mock_chmod
#define listen mock_listen
#define unlink mock_unlink
#define main alice_candidate_entry
#ifndef ALICE_DAEMON_SOURCE
#define ALICE_DAEMON_SOURCE "alice_mouse_daemon_v8_candidate.c"
#endif
#include ALICE_DAEMON_SOURCE
#undef main
#undef open
#undef flock
#undef ioctl
#undef socket
#undef bind
#undef chmod
#undef listen
#undef unlink

static const char *names[]={"unused","lock","uinput","config","setup",
    "create","socket","bind","chmod","listen"};
static int test_one(int f) {
    int p[2], a[2];
    if(pipe(p)||pipe(a)) return 20;
    pid_t child=fork();
    if(child<0)return 21;
    if(child==0) {
        close(p[0]);close(a[0]); pipe_writer=p[1]; audit_writer=a[1];
        fault=f;
        int code=alice_candidate_entry();
        _exit(code);
    }
    close(p[1]);close(a[1]);
    int status=0;
    if(waitpid(child,&status,0)!=child)return 22;
    char buf[64]={0}; ssize_t n=read(a[0],buf,sizeof(buf)-1);
    close(a[0]);close(p[0]);
    if(n<0)return 23;
    int count_create=0,count_destroy=0,count_unlink=0;
    for(int i=0;i<n;i++) {
        count_create+=(buf[i]=='C');
        count_destroy+=(buf[i]=='D');
        count_unlink+=(buf[i]=='U');
    }
    int expected_code=f==FF_LOCK?5:2;
    int should_destroy=f>=F_SOCKET;
    int pass=WIFEXITED(status) && WEXITSTATUS(status)==expected_code &&
             (count_destroy==should_destroy) &&
             (f<F_BIND || count_create==1) &&
             count_unlink==(f>=F_CHMOD?1:0);
    printf("%s: %s exit=%d create=%d destroy=%d unlink=%d\n",
        names[f],pass?"PASS":"FAIL",WIFEXITED(status)?WEXITSTATUS(status):-1,
        count_create,count_destroy,count_unlink);
    return pass?0:25;
}
int main(void) {
    int failed=0;
    for(int f=FF_LOCK;f<F_COUNT;f++)if(test_one(f))failed++;
    printf("FAULT_SUMMARY failed=%d of %d\n",failed,F_COUNT-1);
    return failed?1:0;
}

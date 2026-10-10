#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <linux/uinput.h>
#include <linux/input.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/file.h>
#include <sys/types.h>
#include <sys/select.h>
#include <time.h>
static int mouse=-1,server=-1,created=0,lockfd=-1,socket_bound=0;
static volatile sig_atomic_t stop_requested=0;
static void request_stop(int sig){(void)sig;stop_requested=1;}
static int held_left=0,held_right=0,unsynced=0;
static int event(int type,int code,int value);static int syn(void);
#ifdef ALICE_FAULT_TEST
static int inject_code=-1;
#endif
static long long lease_until=0;
static long long now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (long long)t.tv_sec*1000+t.tv_nsec/1000000;}
static int release_buttons(void){
 if(mouse<0)return -1;
 int fail=0;
 if(held_left && event(EV_KEY,BTN_LEFT,0))fail=1;
 if(held_right && event(EV_KEY,BTN_RIGHT,0))fail=1;
 if(syn())fail=1;
 if(fail)return -1;
 held_left=held_right=0;lease_until=0;return 0;
}

#ifdef ALICE_FAULT_TEST
static int alice_destroy(int fd){const char marker='D';return write(fd,&marker,1)==1?0:-1;}
#else
static int alice_destroy(int fd){return ioctl(fd,UI_DEV_DESTROY);}
#endif
static const char *sockpath="/data/adb/alice-mouse/control.sock";
static void cleanup(int sig){
 int failed=mouse>=0 && release_buttons()!=0;
 /* Keep the process lock until the kernel device is destroyed. */
 if(mouse>=0){
  if(created && alice_destroy(mouse)!=0)failed=1;
  close(mouse);mouse=-1;
 }
 if(server>=0){close(server);server=-1;}
 /* Never remove a socket path this instance did not successfully bind. */
 if(socket_bound){unlink(sockpath);socket_bound=0;}
 if(lockfd>=0){close(lockfd);lockfd=-1;}
 int exit_code=sig<0?-sig:(sig?128+sig:0);
 if(!exit_code && failed)exit_code=3;
 _exit(exit_code);
}
static int event(int type,int code,int value){
 int *held=NULL;
 if(type==EV_KEY && code==BTN_LEFT)held=&held_left;
 if(type==EV_KEY && code==BTN_RIGHT)held=&held_right;
 /* A failed SYN after DOWN must never leave an untracked pressed button. */
 if(held && value==1){*held=1;lease_until=now_ms()+1500;}
 if(type!=EV_SYN)unsynced=1;
#ifdef ALICE_FAULT_TEST
 if(code==inject_code)return -1;
#endif
 struct input_event e={0};e.type=type;e.code=code;e.value=value;
 if(write(mouse,&e,sizeof(e))!=(ssize_t)sizeof(e))return -1;
 if(held && value==0){*held=0;if(!held_left&&!held_right)lease_until=0;}
 if(type==EV_SYN && code==SYN_REPORT)unsynced=0;
 return 0;
}
static int syn(void){return event(EV_SYN,SYN_REPORT,0);}
static int action(const char *cmd,int a,int b){
 if(a < -500 || a > 500 || b < -500 || b > 500)return -1;
 if(!strcmp(cmd,"move") || !strcmp(cmd,"click")){
  if(!strcmp(cmd,"click") && held_left)return -1;
  if(event(EV_REL,REL_X,a)||event(EV_REL,REL_Y,b)||syn())return -1;
  if(held_left||held_right)lease_until=now_ms()+1500;
  if(!strcmp(cmd,"click")){usleep(50000);if(event(EV_KEY,BTN_LEFT,1)||syn())return -1;usleep(70000);if(event(EV_KEY,BTN_LEFT,0)||syn())return -1;}
  return 0;
 }
 if(!strcmp(cmd,"scroll") && b==0)return event(EV_REL,REL_WHEEL,a)||syn()?-1:0;
 if(!strcmp(cmd,"right") && a==0 && b==0){
  if(held_right)return -1;
  if(event(EV_KEY,BTN_RIGHT,1)||syn())return -1;
  usleep(70000);
  return event(EV_KEY,BTN_RIGHT,0)||syn()?-1:0;
 }
 if(!strcmp(cmd,"down") && b==0 && (a==1||a==2)){
  int *held=a==1?&held_left:&held_right;
  if(*held)return -1;
  if(event(EV_KEY,a==1?BTN_LEFT:BTN_RIGHT,1)||syn())return -1;
  *held=1;lease_until=now_ms()+1500;return 0;
 }
 if(!strcmp(cmd,"up") && b==0 && (a==1||a==2)){
  int *held=a==1?&held_left:&held_right;
  if(!*held)return -1;
  if(event(EV_KEY,a==1?BTN_LEFT:BTN_RIGHT,0)||syn())return -1;
  *held=0;if(!held_left&&!held_right)lease_until=0;return 0;
 }
 return -1;
}
int main(void){
 lockfd=open("/data/adb/alice-mouse/shared.lock",O_CREAT|O_RDWR|O_CLOEXEC,0600);
 if(lockfd<0||flock(lockfd,LOCK_EX|LOCK_NB)<0){perror("lock");return 5;}
 signal(SIGPIPE,SIG_IGN);signal(SIGTERM,request_stop);signal(SIGINT,request_stop);signal(SIGHUP,request_stop);
 mouse=open("/dev/uinput",O_WRONLY|O_NONBLOCK);if(mouse<0){perror("uinput");return 2;}
 if(ioctl(mouse,UI_SET_EVBIT,EV_KEY)<0||ioctl(mouse,UI_SET_KEYBIT,BTN_LEFT)<0||ioctl(mouse,UI_SET_KEYBIT,BTN_RIGHT)<0||ioctl(mouse,UI_SET_EVBIT,EV_REL)<0||ioctl(mouse,UI_SET_RELBIT,REL_X)<0||ioctl(mouse,UI_SET_RELBIT,REL_Y)<0||ioctl(mouse,UI_SET_RELBIT,REL_WHEEL)<0){perror("config");cleanup(-2);}
 struct uinput_setup s={0};s.id.bustype=BUS_USB;s.id.vendor=0x1d6b;s.id.product=0x0104;snprintf(s.name,sizeof(s.name),"Alice RDC Virtual Mouse");
 if(ioctl(mouse,UI_DEV_SETUP,&s)<0||ioctl(mouse,UI_DEV_CREATE)<0){perror("create");cleanup(-2);}created=1;
 server=socket(AF_UNIX,SOCK_STREAM,0);if(server<0){perror("socket");cleanup(-2);}
 struct sockaddr_un addr={0};addr.sun_family=AF_UNIX;snprintf(addr.sun_path,sizeof(addr.sun_path),"%s",sockpath);
 umask(0077);
 if(bind(server,(void*)&addr,sizeof(addr))<0){perror("bind");cleanup(-2);}
 socket_bound=1;
 if(chmod(sockpath,0600)<0){perror("chmod");cleanup(-2);}
 if(listen(server,2)<0){perror("listen");cleanup(-2);}
 puts("READY");fflush(stdout);
 for(;!stop_requested;){
  fd_set rfds;FD_ZERO(&rfds);FD_SET(server,&rfds);
  struct timeval wait={0,100000};int ready=select(server+1,&rfds,NULL,NULL,&wait);
  if((held_left||held_right)&&now_ms()>=lease_until && release_buttons()!=0)cleanup(1);
  if(ready==0|| (ready<0&&errno==EINTR))continue;
  if(ready<0)break;
  int client=accept(server,NULL,NULL);if(client<0){if(errno==EINTR)continue;break;}
  struct ucred peer={0};socklen_t plen=sizeof(peer);
  if(getsockopt(client,SOL_SOCKET,SO_PEERCRED,&peer,&plen)<0||peer.uid!=0){write(client,"DENIED\n",7);close(client);continue;}
  struct timeval tv={0,100000};setsockopt(client,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));
  char buf[96]={0},op[12]={0},extra=0;int a,b;ssize_t n=read(client,buf,sizeof(buf)-1);
  int ok=n>0 && sscanf(buf,"%11s %d %d %c",op,&a,&b,&extra)==3 && action(op,a,b)==0;
  if((!ok || (lease_until && now_ms()>=lease_until)) &&
     (held_left||held_right||unsynced) && release_buttons()!=0)cleanup(1);
  write(client,ok?"OK\n":"ERR\n",ok?3:4);close(client);
 }
 cleanup(0); return 0;
}

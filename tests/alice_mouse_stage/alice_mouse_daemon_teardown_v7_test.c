/* Alice Mouse isolated teardown test includes the exact candidate daemon source.
 * It replaces UI_DEV_DESTROY with a pipe marker and never opens /dev/uinput.
 */
#define ALICE_FAULT_TEST 1
#define main alice_production_entry
#include "alice_mouse_daemon_v7_candidate.c"
#undef main

#include <sys/wait.h>

static int run_case(int inject, int via_signal) {
 int channels[2]={-1,-1};
 if(pipe(channels)!=0)return 20;
 pid_t child=fork();
 if(child<0)return 21;
 if(child==0){
  close(channels[0]);
  mouse=channels[1];
  server=-1;lockfd=-1;
  created=1;held_left=1;held_right=0;
  inject_code=inject;
  if(via_signal){
   signal(SIGTERM,request_stop);
   const char marker='R';
   if(write(channels[1],&marker,1)!=1)_exit(22);
   while(!stop_requested)pause();
  }
  cleanup(SIGTERM);
 }
 close(channels[1]);
 unsigned char data[512]={0};
 size_t length=0;ssize_t n=0;
 if(via_signal){
  if(read(channels[0],data,1)!=1||data[0]!='R')return 30;
  if(kill(child,SIGTERM)!=0)return 31;
 }
 while(length<sizeof(data) && (n=read(channels[0],data+length,sizeof(data)-length))>0)
  length+=(size_t)n;
 close(channels[0]);
 int status=0;
 if(waitpid(child,&status,0)!=child)return 23;
 if(!WIFEXITED(status)||WEXITSTATUS(status)!=128+SIGTERM)return 24;
 if(length<1||data[length-1]!='D')return 25;
 if((length-1)%sizeof(struct input_event)!=0)return 26;
 int has_up=0,has_syn=0;
 for(size_t i=0;i+sizeof(struct input_event)<=length-1;i+=sizeof(struct input_event)){
  struct input_event e={0};
  memcpy(&e,data+i,sizeof(e));
  if(e.type==EV_KEY&&e.code==BTN_LEFT&&e.value==0)has_up=1;
  if(e.type==EV_SYN&&e.code==SYN_REPORT)has_syn=1;
 }
 if(inject==BTN_LEFT && has_up)return 27;
 if(inject==SYN_REPORT && has_syn)return 28;
 if(inject<0 && (!has_up||!has_syn))return 29;
 printf("TEARDOWN_PASS injected=%d signal=%d marker=UI_DEV_DESTROY up=%d syn=%d\n",
        inject,via_signal,has_up,has_syn);
 return 0;
}
int main(void){
 int rc=run_case(-1,0);if(rc)return rc;
 rc=run_case(BTN_LEFT,0);if(rc)return rc;
 rc=run_case(SYN_REPORT,0);if(rc)return rc;
 rc=run_case(-1,1);if(rc)return rc;
 puts("ALL_TEARDOWN_SCENARIOS_PASS");
 return 0;
}

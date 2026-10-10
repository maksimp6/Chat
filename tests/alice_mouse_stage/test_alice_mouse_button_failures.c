/* No root, no device I/O: verifies failed SYN cannot strand virtual buttons. */
#define ALICE_FAULT_TEST 1
#define main unused_daemon_main
#ifndef ALICE_DAEMON_SOURCE
#define ALICE_DAEMON_SOURCE "alice_mouse_daemon_v9_candidate.c"
#endif
#include ALICE_DAEMON_SOURCE
#undef main
#include <sys/wait.h>

static int check_fault(const char *op,int a,int b,int btn){
  int fds[2]; if(pipe(fds))return 20;
  mouse=fds[1];server=-1;created=0;lockfd=-1;socket_bound=0;
  held_left=held_right=0;lease_until=0;
  inject_code=SYN_REPORT; /* button DOWN written, then SYN rejected */
  int rc=action(op,a,b);
  int stuck=(btn==1?held_left:held_right);
  inject_code=-1;
  int release=release_buttons();
  close(fds[1]);mouse=-1;
  unsigned char buf[4096];ssize_t n=read(fds[0],buf,sizeof(buf));
  close(fds[0]);
  int down=0,up=0,syn_count=0;
  for(ssize_t i=0;i+(ssize_t)sizeof(struct input_event)<=n;i+=(ssize_t)sizeof(struct input_event)){
    struct input_event e={0};memcpy(&e,buf+i,sizeof(e));
    if(e.type==EV_KEY && e.code==(btn==1?BTN_LEFT:BTN_RIGHT)) {
      down+=e.value==1; up+=e.value==0;
    }
    if(e.type==EV_SYN && e.code==SYN_REPORT)syn_count++;
  }
  int pass=rc!=0 && stuck && !release && down==1 && up>=1 && syn_count>=1 && !held_left && !held_right;
  printf("%s_SYN_FAILURE: %s down=%d release=%d syn=%d tracked=%d\n",
      op,pass?"PASS":"FAIL",down,up,syn_count,stuck);
  return pass?0:1;
}

int main(void){
  int failures=0;
  failures+=check_fault("right",0,0,2);
  failures+=check_fault("down",1,0,1);
  printf("BUTTON_FAULT_SUMMARY failures=%d\n",failures);
  return failures?1:0;
}

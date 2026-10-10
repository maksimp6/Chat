/* Alice Mouse one-process isolated verifier protocol.
 * No root, /dev/uinput or production sockets. The parent owns the input pipe.
 * Only parent may request a session after independent HTTP authorization.
 */
#define main alice_unused_verifier_main
#include "alice_mouse_c_verifier_v6_candidate.c"
#undef main
#include <inttypes.h>
#include <openssl/rand.h>
#include <unistd.h>
#include <errno.h>

static int nibble(char c){
 if(c>='0'&&c<='9')return c-'0';
 if(c>='a'&&c<='f')return c-'a'+10;
 if(c>='A'&&c<='F')return c-'A'+10;
 return -1;
}
static int decode_grant(const char *hex, Grant *g){
 size_t count=sizeof(*g);
 if(strlen(hex)!=count*2)return 0;
 unsigned char *out=(unsigned char*)g;
 for(size_t i=0;i<count;i++){
  int hi=nibble(hex[2*i]),lo=nibble(hex[2*i+1]);
  if(hi<0||lo<0)return 0;
  out[i]=(unsigned char)((hi<<4)|lo);
 }
 return 1;
}
int main(void){
 Verifier v={0};uint8_t key[32]={0};char line[512];
 const char *fd_env=getenv("ALICE_TEST_KEY_FD");
 if(!fd_env)return 64;
 char *end=NULL;long key_fd=strtol(fd_env,&end,10);
 if(!end||*end||key_fd<3||key_fd>1048576)return 64;
 if(read((int)key_fd,key,32)!=32)return 65;
 close((int)key_fd);
 if(RAND_bytes((unsigned char*)&v.epoch,sizeof(v.epoch))!=1||v.epoch==0)return 66;
 printf("READY %u\n",v.epoch);fflush(stdout);
 while(fgets(line,sizeof(line),stdin)){
  size_t n=strlen(line);
  if(n==0)continue;
  if(line[n-1]=='\n')line[n-1]=0;
  if(!strcmp(line,"ISSUE")){
   uint64_t id=0;
   if(RAND_bytes((unsigned char*)&id,sizeof(id))!=1||id==0){
    puts("DENIED");fflush(stdout);continue;
   }
   /* Single-controller contract: issuing a new session revokes all old ones. */
   memset(v.sessions,0,sizeof(v.sessions));
   if(!provision(&v,id,10000)){
    puts("DENIED");fflush(stdout);continue;
   }
   printf("SESSION %" PRIu64 "\n",id);fflush(stdout);
  }else if(!strncmp(line,"VERIFY ",7)){
   Grant g={0};
   if(!decode_grant(line+7,&g)||!verify(&v,&g,key)){
    puts("DENIED");
   }else{
    printf("EVENT %u %u %d %d\n",g.action,g.button,g.x,g.y);
   }
   fflush(stdout);
  }else if(!strcmp(line,"QUIT"))break;
  else{puts("DENIED");fflush(stdout);}
 }
 OPENSSL_cleanse(key,sizeof(key));
 return 0;
}

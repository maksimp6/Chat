/* Alice Mouse verifier v2: isolated fixed-format signed grant, no uinput. */
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <openssl/rand.h>
#include <openssl/hmac.h>
#include <openssl/crypto.h>
#define MAGIC 0x414d5331u
#define NONCES 128
#define SESSIONS 8
typedef struct __attribute__((packed)) {
 uint32_t magic,epoch,sequence;
 uint64_t session_id;
 uint8_t role,action,button,reserved;
 int16_t x,y;
 uint64_t issued_ms;
 uint8_t nonce[16],mac[32];
} Grant;
typedef struct {uint8_t nonce[16];uint64_t expires_ms;} Entry;
typedef struct {uint64_t id,last_sequence,expires_ms;} Session;
typedef struct {
 uint32_t epoch;
 Session sessions[SESSIONS];
 Entry entries[NONCES];
} Verifier;
static uint64_t monotonic_ms(void){
 struct timespec ts;
 if(clock_gettime(CLOCK_MONOTONIC,&ts)!=0)return 0;
 return (uint64_t)ts.tv_sec*1000+(uint64_t)ts.tv_nsec/1000000;
}
static int provision(Verifier *v,uint64_t id,uint64_t ttl_ms){
 uint64_t now=monotonic_ms();
 if(!now||!id||ttl_ms==0||ttl_ms>30000)return 0;
 for(size_t i=0;i<SESSIONS;i++)if(v->sessions[i].id==id && v->sessions[i].expires_ms>now)return 0;
 for(size_t i=0;i<SESSIONS;i++)if(v->sessions[i].expires_ms<=now){
  v->sessions[i]=(Session){.id=id,.last_sequence=0,.expires_ms=now+ttl_ms};return 1;
 }
 return 0;
}
static int verify(Verifier *v,const Grant *g,const uint8_t key[32]){
 uint64_t now=monotonic_ms();
 unsigned char mac[32];unsigned int len=0;
 if(!now||g->magic!=MAGIC||g->epoch!=v->epoch||g->role!=2||g->reserved)return 0;
 if(g->action<1||g->action>3||g->button<1||g->button>2)return 0;
 if(g->x< -500||g->x>500||g->y< -500||g->y>500)return 0;
 if(!g->session_id)return 0;
 if(g->issued_ms>now||now-g->issued_ms>300)return 0;
 size_t si=SESSIONS;
 for(size_t i=0;i<SESSIONS;i++)if(v->sessions[i].id==g->session_id && v->sessions[i].expires_ms>now){si=i;break;}
 if(si==SESSIONS)return 0; /* No client-created sessions. Trusted issuer must provision. */
 if(g->sequence<=v->sessions[si].last_sequence)return 0;
 if(!HMAC(EVP_sha256(),key,32,(const unsigned char*)g,offsetof(Grant,mac),mac,&len)||len!=32)return 0;
 if(CRYPTO_memcmp(mac,g->mac,32))return 0;
 size_t slot=NONCES;
 for(size_t i=0;i<NONCES;i++){
  if(v->entries[i].expires_ms>now && !memcmp(v->entries[i].nonce,g->nonce,16))return 0;
  if(v->entries[i].expires_ms<=now && slot==NONCES)slot=i;
 }
 if(slot==NONCES)return 0;
 memcpy(v->entries[slot].nonce,g->nonce,16);
 v->entries[slot].expires_ms=now+300;
 v->sessions[si].last_sequence=g->sequence;
 return 1;
}
int main(int argc,char **argv){
 if(argc!=3)return 64;
 uint8_t key[32]={0};Grant g={0};Verifier v={0};
 if(RAND_bytes((unsigned char*)&v.epoch,sizeof(v.epoch))!=1||v.epoch==0)return 69;
 /* Session must be explicitly provisioned by trusted caller; this lab fails closed. */
 (void)provision;
 FILE *f=fopen(argv[1],"rb");if(!f)return 65;
 size_t n=fread(key,1,32,f);fclose(f);if(n!=32)return 66;
 f=fopen(argv[2],"rb");if(!f)return 67;
 n=fread(&g,1,sizeof(g),f);fclose(f);if(n!=sizeof(g))return 68;
 /* Never derive trusted epoch from grant. */
 int ok=verify(&v,&g,key);
 int replay=verify(&v,&g,key);
 printf("VERIFY=%s REPLAY=%s\n",ok?"PASS":"DENIED",replay?"ACCEPTED":"DENIED");
 OPENSSL_cleanse(key,sizeof(key));return ok?0:1;
}

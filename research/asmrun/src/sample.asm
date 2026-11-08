; sample.asm — the next token from the logits. k_sample(rcx=logits f32*, rdx=n)
; -> rax = token id:
;   temperature 0            the greatest logit (k_argmax)
;   otherwise                the g_top_k greatest logits, each divided by the
;                            temperature, softmaxed; the smallest of them dropped
;                            until the kept probability mass is g_top_p of the
;                            whole (top-p); one drawn by a xorshift64* random
;                            number seeded from --seed, or from the TSC
; The settings are the model authors' for Qwen3.5 without thinking (0.7, 0.8,
; 20) unless --temp, --top-p, --top-k set them. The top-k selection is one pass
; over the vocabulary with a sorted insertion (most logits fail the entry test),
; so it costs about what k_argmax does.
;
; %included into asmrun.asm (bits 64, default rel) after kernels.asm.

section .text

SMP_MAX equ 256                 ; the largest --top-k

k_sample:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32] the kept sum, [rsp+40] the draw
  mov  rsi, rcx                ; logits
  mov  rdi, rdx                ; n
  vmovss xmm0, [rel g_temp]
  vxorps xmm1, xmm1, xmm1
  vucomiss xmm0, xmm1
  ja   .sample
  mov  rcx, rsi
  mov  rdx, rdi
  call k_argmax
  jmp  .ret
.sample:
  ; k = min(top_k, n), 1..SMP_MAX
  mov  r12, [rel g_top_k]
  cmp  r12, SMP_MAX
  jbe  .k1
  mov  r12, SMP_MAX
.k1:
  cmp  r12, rdi
  jbe  .k2
  mov  r12, rdi
.k2:
  test r12, r12
  jnz  .k3
  mov  r12d, 1
.k3:
  ; the k greatest: g_smp_v (descending) with their ids in g_smp_id
  lea  r13, [rel g_smp_v]
  lea  r14, [rel g_smp_id]
  xor  r15d, r15d              ; kept so far
  xor  ebx, ebx                ; i
.scan:
  cmp  rbx, rdi
  jae  .scanned
  cmp  r15, r12
  jb   .one                    ; room left: every logit enters
  ; full: eight logits against the smallest kept at once; none above -> skip them
  lea  rax, [rbx + 8]
  cmp  rax, rdi
  ja   .one
  vbroadcastss ymm2, [r13 + r12*4 - 4]
  vmovups ymm1, [rsi + rbx*4]
  vcmpps ymm1, ymm1, ymm2, 14  ; x > threshold (ordered)
  vmovmskps eax, ymm1
  test eax, eax
  jnz  .one
  add  rbx, 8
  jmp  .scan
.one:
  vmovss xmm0, [rsi + rbx*4]
  cmp  r15, r12
  jb   .insert                 ; room left
  vcomiss xmm0, [r13 + r12*4 - 4]
  jbe  .next                   ; not above the smallest kept
.insert:
  ; j = min(kept, k - 1); shift the smaller ones down
  mov  rcx, r15
  cmp  rcx, r12
  jb   .j_ok
  lea  rcx, [r12 - 1]
.j_ok:
.shift:
  test rcx, rcx
  jz   .place
  vmovss xmm1, [r13 + rcx*4 - 4]
  vcomiss xmm1, xmm0
  jae  .place                  ; the one above is not smaller
  vmovss [r13 + rcx*4], xmm1
  mov  eax, [r14 + rcx*4 - 4]
  mov  [r14 + rcx*4], eax
  dec  rcx
  jmp  .shift
.place:
  vmovss [r13 + rcx*4], xmm0
  mov  [r14 + rcx*4], ebx
  cmp  r15, r12
  jae  .next
  inc  r15
.next:
  inc  rbx
  jmp  .scan
.scanned:
  vzeroupper
  ; p[j] = e^((v[j] - v[0]) / T); the sum
  vmovss xmm4, [rel g_temp]
  vmovss xmm5, [r13]           ; the greatest
  vxorps xmm6, xmm6, xmm6      ; sum
  xor  ebx, ebx
.pl:
  cmp  rbx, r15
  jae  .pd
  vmovss xmm0, [r13 + rbx*4]
  vsubss xmm0, xmm0, xmm5
  vdivss xmm0, xmm0, xmm4
  call k_exp                   ; xmm0 = e^x (clobbers xmm1-xmm3)
  vmovss [r13 + rbx*4], xmm0
  vaddss xmm6, xmm6, xmm0
  inc  rbx
  jmp  .pl
.pd:
  ; top-p: keep from the largest until the kept mass reaches top_p * sum
  vmulss xmm7, xmm6, [rel g_top_p]
  vxorps xmm6, xmm6, xmm6      ; the kept mass
  xor  ebx, ebx
.tp:
  vaddss xmm6, xmm6, [r13 + rbx*4]
  inc  rbx
  cmp  rbx, r15
  jae  .tpd
  vcomiss xmm6, xmm7
  jb   .tp
.tpd:
  mov  r15, rbx                ; kept
  vmovss [rsp+32], xmm6
  ; the draw: r in [0, kept mass)
  call smp_rand01              ; xmm0 in [0, 1)
  vmulss xmm0, xmm0, [rsp+32]
  vmovss [rsp+40], xmm0
  vxorps xmm6, xmm6, xmm6
  xor  ebx, ebx
.draw:
  vaddss xmm6, xmm6, [r13 + rbx*4]
  vcomiss xmm6, [rsp+40]
  ja   .drawn
  inc  rbx
  cmp  rbx, r15
  jb   .draw
  dec  rbx                     ; rounding: the last kept
.drawn:
  mov  eax, [r14 + rbx*4]
.ret:
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; smp_rand01() -> xmm0 = a uniform f32 in [0, 1) from xorshift64* (seeded from
; --seed, else from the TSC on the first call). Leaf; clobbers rax, rcx, rdx.
smp_rand01:
  mov  rax, [rel g_smp_rng]
  test rax, rax
  jnz  .have
  mov  rax, [rel g_img_seed]   ; --seed
  test rax, rax
  jnz  .have
  rdtsc
  shl  rdx, 32
  or   rax, rdx
  or   rax, 1
.have:
  mov  rcx, rax
  shr  rcx, 12
  xor  rax, rcx
  mov  rcx, rax
  shl  rcx, 25
  xor  rax, rcx
  mov  rcx, rax
  shr  rcx, 27
  xor  rax, rcx
  mov  [rel g_smp_rng], rax
  mov  rcx, 0x2545F4914F6CDD1D
  imul rax, rcx
  shr  rax, 40                 ; 24 bits
  vcvtsi2ss xmm0, xmm0, rax
  vmulss xmm0, xmm0, [rel smp_2m24]
  ret

section .data
  align 8
g_temp:     dd 0.7              ; --temp (0: the greatest logit)
g_top_p:    dd 0.8              ; --top-p
g_top_k:    dq 20               ; --top-k
g_smp_rng:  dq 0                ; the xorshift64* state (0: not seeded yet)
smp_2m24:   dd 5.9604645e-8     ; 2^-24
  align 32
g_smp_v:    times SMP_MAX dd 0  ; the kept logits, then their probabilities
g_smp_id:   times SMP_MAX dd 0  ; their ids
section .text

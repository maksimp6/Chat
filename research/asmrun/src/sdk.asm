; sdk.asm — the numeric kernels of the image generator (SD-Turbo): many rows at
; once (77 text positions, 4096 latent pixels), unlike kernels.asm's matvec for
; one token. The matrix multiply is compute-bound here, so it is built around
; the N100's caches and registers:
;
;   registers  a 3x4 output tile: 12 ymm accumulators, 3 ymm activation rows,
;              1 ymm weight row = all 16 ymm registers; 12 FMAs per 7 loads
;   L1D 32 KB  a subpanel of 4 weight rows, a slice of SG_KC values each
;              (4 x 1024 x 4 = 16 KB), stays in L1 while 3 activation rows
;              (12 KB) pass over it
;   L2 2 MB    a group of SG_NB = 16 weight rows (64 KB per slice, dequantised
;              once) and the activations; shared by the four cores
;   cores      pool_run: the column groups go round robin over every thread;
;              each weight byte is read from memory once, each activation row
;              once per 16 output columns
;
;   sg_deq_row(rcx=dst f32, rdx=src row, r8=K, r9=ggml type)   q8_0 / f16 / f32
;   sg_matmul(rcx=SgGemm*)       C[m][n] (+)= sum_k A[m][k] * W[n][k] (+ bias[n])
;   sg_layernorm(rcx=out, rdx=in, r8=w, r9=b, [5]=n)             one row
;   sg_gelu(rcx=x, rdx=n)        exact GELU (erf), in place
;   sg_attn(rcx=SgAttn*)         softmax(Q K^T * scale [causal]) V, per head
;
; ISA: AVX2 + FMA + F16C (rt_cpuid_check gates every mode that runs these).
; %included into asmrun.asm (bits 64, default rel) after kernels.asm (EXP8,
; the kc8_* constants, k_softmax).

section .text

SG_KC     equ 1024               ; values of a weight row per L1 slice
SG_NB     equ 16                 ; weight rows (output columns) per group

; C = A W^T (+ bias), A f32 [m][k] (lda bytes per row), W rows of k values in
; its ggml type, C f32 [m][n] (ldc bytes per row). m is any count, n a multiple
; of 4, k a multiple of 32 for q8_0 and of 8 otherwise. With wtype F32 and a
; nonzero .wrow the rows are used in place at that stride (no pack): f32 rows
; inside a wider matrix, such as one head's columns of K.
struc SgGemm
  .a:     resq 1
  .lda:   resq 1
  .m:     resq 1
  .w:     resq 1                 ; weight row n at w + n*wrow
  .wrow:  resq 1                 ; bytes per weight row; 0 = from the type
  .wtype: resq 1
  .n:     resq 1
  .k:     resq 1
  .bias:  resq 1                 ; f32[n] or 0
  .c:     resq 1
  .ldc:   resq 1
  .acc:   resq 1                 ; 1: C += A W^T (+ bias); 0: C = A W^T (+ bias)
endstruc

; Attention over rows of Q, K, V (f32, ld bytes per row, head h at columns
; h*hd..): out[t] = sum_j softmax_j(q_t . k_j * scale) v_j, j over all tk rows,
; or j <= t when causal.
struc SgAttn
  .q:      resq 1
  .k:      resq 1
  .v:      resq 1
  .out:    resq 1
  .tq:     resq 1                ; query rows
  .tk:     resq 1                ; key/value rows
  .heads:  resq 1
  .hd:     resq 1                ; head size, a multiple of 8
  .ldq:    resq 1                ; bytes per row of q and out
  .ldk:    resq 1                ; bytes per row of k and v
  .causal: resq 1
  .scale:  resd 1                ; f32
  .pad:    resd 1
endstruc

; sg_deq_row(rcx=dst f32, rdx=src, r8=K values, r9=ggml type) — one weight row
; to f32. q8_0: blocks of 32 (f16 scale, 32 int8); f16; f32. Leaf; clobbers
; rax, rcx, rdx, r8, ymm0-ymm4.
sg_deq_row:
  cmp  r9d, GGML_F32
  je   .f32
  cmp  r9d, GGML_F16
  je   .f16
  shr  r8, 5
.q:
  movzx eax, word [rdx]
  vmovd xmm0, eax
  vcvtph2ps xmm0, xmm0
  vbroadcastss ymm0, xmm0
  vpmovsxbd ymm1, [rdx+2]
  vpmovsxbd ymm2, [rdx+10]
  vpmovsxbd ymm3, [rdx+18]
  vpmovsxbd ymm4, [rdx+26]
  vcvtdq2ps ymm1, ymm1
  vcvtdq2ps ymm2, ymm2
  vcvtdq2ps ymm3, ymm3
  vcvtdq2ps ymm4, ymm4
  vmulps ymm1, ymm1, ymm0
  vmulps ymm2, ymm2, ymm0
  vmulps ymm3, ymm3, ymm0
  vmulps ymm4, ymm4, ymm0
  vmovups [rcx], ymm1
  vmovups [rcx+32], ymm2
  vmovups [rcx+64], ymm3
  vmovups [rcx+96], ymm4
  add  rdx, 34
  add  rcx, 128
  dec  r8
  jnz  .q
  ret
.f16:
  shr  r8, 3
.h:
  vcvtph2ps ymm0, [rdx]
  vmovups [rcx], ymm0
  add  rdx, 16
  add  rcx, 32
  dec  r8
  jnz  .h
  ret
.f32:
  shr  r8, 3
.c:
  vmovups ymm0, [rdx]
  vmovups [rcx], ymm0
  add  rdx, 32
  add  rcx, 32
  dec  r8
  jnz  .c
  ret

; sg_matmul(rcx=SgGemm*) — fill .wrow from the type when it is 0, make sure
; every thread has a pack buffer for SG_NB rows of k floats, run sg_gemm_job on
; the pool. A type other than q8_0, f16, f32 is rt_fatal. Preserves the
; nonvolatile registers.
sg_matmul:
  push rbx
  push rsi
  WINPROLOGUE
  mov  rbx, rcx
  mov  rax, [rbx + SgGemm.wrow]
  test rax, rax
  jnz  .row_set
  mov  rax, [rbx + SgGemm.k]
  mov  rcx, [rbx + SgGemm.wtype]
  cmp  ecx, GGML_F32
  jne  .nf32
  shl  rax, 2
  jmp  .row
.nf32:
  cmp  ecx, GGML_F16
  jne  .nf16
  shl  rax, 1
  jmp  .row
.nf16:
  cmp  ecx, GGML_Q8_0
  jne  .badtype
  shr  rax, 5
  imul rax, rax, 34
.row:
  mov  [rbx + SgGemm.wrow], rax
.row_set:
  cmp  qword [rbx + SgGemm.wtype], GGML_F32
  je   .packed                 ; f32 rows are used where they are
  mov  rax, [rbx + SgGemm.k]
  cmp  rax, [rel g_sg_pack_k]
  jbe  .packed
  mov  [rel g_sg_pack_k], rax
  xor  esi, esi
.pa:
  cmp  rsi, [rel g_nthreads]
  jae  .packed
  mov  rcx, [rel g_sg_pack_k]
  shl  rcx, 6                  ; SG_NB rows of k f32
  call rt_alloc
  lea  rdx, [rel g_sg_pack]
  mov  [rdx + rsi*8], rax
  inc  rsi
  jmp  .pa
.packed:
  lea  rcx, [rel sg_gemm_job]
  mov  rdx, rbx
  call pool_run
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret
.badtype:
  lea  rcx, [rel sg_err_type]
  call rt_fatal

; SG_RED a,b,c,d — the four row sums of ymm a..d into xmm a: [sum a, sum b, sum c, sum d]
%macro SG_RED 4
  vhaddps ymm%1, ymm%1, ymm%2
  vhaddps ymm%3, ymm%3, ymm%4
  vhaddps ymm%1, ymm%1, ymm%3
  vextractf128 xmm%2, ymm%1, 1
  vaddps xmm%1, xmm%1, xmm%2
%endmacro

; SG_PUT r — xmm r (4 outputs of one row) into C at rdi: the first k slice
; starts from bias (+ C when accumulating), later slices add to C.
%macro SG_PUT 1
  cmp  qword [rsp+184], 0
  jne  %%later
  cmp  qword [r15 + SgGemm.acc], 0
  je   %%noacc
  vaddps xmm%1, xmm%1, [rdi]
%%noacc:
  mov  rax, [r15 + SgGemm.bias]
  test rax, rax
  jz   %%st
  vaddps xmm%1, xmm%1, [rax + rsi*4]
  jmp  %%st
%%later:
  vaddps xmm%1, xmm%1, [rdi]
%%st:
  vmovups [rdi], xmm%1
%endmacro

; SG_SETUP — for the subpanel at column offset [rsp+224] of the group and the
; k slice [rsp+184]: rsi = its first column, rbx = weight row 0 at kc, r9 =
; weight row stride, r12 = weight row 3 at kc, r10 = A[r13][kc], r11 = lda,
; ecx = k steps of 8 in this slice. Clobbers rax.
%macro SG_SETUP 0
  mov  rax, [rsp+224]
  mov  rsi, [rsp+232]
  add  rsi, rax
  mov  rbx, rax
  imul rbx, [rsp+208]
  add  rbx, [rsp+200]
  mov  rax, [rsp+184]
  lea  rbx, [rbx + rax*4]
  mov  r9, [rsp+208]
  lea  r12, [rbx + r9*2]
  add  r12, r9
  mov  r11, [r15 + SgGemm.lda]
  mov  r10, r13
  imul r10, r11
  add  r10, [r15 + SgGemm.a]
  lea  r10, [r10 + rax*4]
  mov  rcx, [r15 + SgGemm.k]
  sub  rcx, rax
  cmp  rcx, SG_KC
  jbe  %%ok
  mov  ecx, SG_KC
%%ok:
  shr  ecx, 3
%endmacro

; sg_gemm_job(rcx=SgGemm*, rdx=tid, r8=nthreads) — pool body. Column groups of
; SG_NB (g = tid, tid+nthreads, ...): dequantise the group's weight rows into
; this thread's pack buffer (f32 rows are used in place), then per SG_KC slice
; run every 3-row tile of A, and the 1 or 2 rows left, over the group's
; subpanels of 4 rows. Saves and restores xmm6-xmm15.
sg_gemm_job:
  push rbx
  push rbp
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  sub  rsp, 248                ; [rsp..160) xmm6-15, [+168] nthreads, [+176] group,
                               ; [+184] kc, [+192] pack, [+200] group base, [+208] row stride,
                               ; [+216] columns in the group, [+224] subpanel offset, [+232] group n0
  vmovups [rsp], xmm6
  vmovups [rsp+16], xmm7
  vmovups [rsp+32], xmm8
  vmovups [rsp+48], xmm9
  vmovups [rsp+64], xmm10
  vmovups [rsp+80], xmm11
  vmovups [rsp+96], xmm12
  vmovups [rsp+112], xmm13
  vmovups [rsp+128], xmm14
  vmovups [rsp+144], xmm15
  mov  r15, rcx
  mov  [rsp+168], r8
  mov  [rsp+176], rdx
  lea  rax, [rel g_sg_pack]
  mov  rax, [rax + rdx*8]
  mov  [rsp+192], rax
.group:
  mov  rax, [rsp+176]
  shl  rax, 4                  ; n0 = group * SG_NB
  cmp  rax, [r15 + SgGemm.n]
  jae  .done
  mov  [rsp+232], rax
  mov  rcx, [r15 + SgGemm.n]
  sub  rcx, rax
  cmp  rcx, SG_NB
  jbe  .cols
  mov  ecx, SG_NB
.cols:
  mov  [rsp+216], rcx
  cmp  qword [r15 + SgGemm.wtype], GGML_F32
  jne  .pack
  imul rax, [r15 + SgGemm.wrow] ; f32: the rows themselves, at their stride
  add  rax, [r15 + SgGemm.w]
  mov  [rsp+200], rax
  mov  rax, [r15 + SgGemm.wrow]
  mov  [rsp+208], rax
  jmp  .kc0
.pack:
  xor  r14d, r14d
.dq:                           ; pack row j = W row n0+j as f32 (k values)
  cmp  r14, [rsp+216]
  jae  .pk
  mov  rcx, [rsp+192]
  mov  rax, [r15 + SgGemm.k]
  imul rax, r14
  lea  rcx, [rcx + rax*4]
  mov  rax, [rsp+232]
  add  rax, r14
  imul rax, [r15 + SgGemm.wrow]
  mov  rdx, [r15 + SgGemm.w]
  add  rdx, rax
  mov  r8, [r15 + SgGemm.k]
  mov  r9, [r15 + SgGemm.wtype]
  call sg_deq_row
  inc  r14
  jmp  .dq
.pk:
  mov  rax, [rsp+192]
  mov  [rsp+200], rax
  mov  rax, [r15 + SgGemm.k]
  shl  rax, 2
  mov  [rsp+208], rax
.kc0:
  mov  qword [rsp+184], 0
.kc:
  mov  rax, [rsp+184]
  cmp  rax, [r15 + SgGemm.k]
  jae  .gnext
  xor  r13d, r13d              ; m
.mt:
  mov  rax, [r15 + SgGemm.m]
  sub  rax, r13
  cmp  rax, 3
  jb   .tail
  mov  qword [rsp+224], 0
.sp3:
  mov  rax, [rsp+224]
  cmp  rax, [rsp+216]
  jae  .mt_next
  SG_SETUP
  vxorps ymm0, ymm0, ymm0
  vxorps ymm1, ymm1, ymm1
  vxorps ymm2, ymm2, ymm2
  vxorps ymm3, ymm3, ymm3
  vxorps ymm4, ymm4, ymm4
  vxorps ymm5, ymm5, ymm5
  vxorps ymm6, ymm6, ymm6
  vxorps ymm7, ymm7, ymm7
  vxorps ymm8, ymm8, ymm8
  vxorps ymm9, ymm9, ymm9
  vxorps ymm10, ymm10, ymm10
  vxorps ymm11, ymm11, ymm11
.k3:
  vmovups ymm12, [r10]
  vmovups ymm13, [r10 + r11]
  vmovups ymm14, [r10 + r11*2]
  vmovups ymm15, [rbx]
  vfmadd231ps ymm0, ymm12, ymm15
  vfmadd231ps ymm4, ymm13, ymm15
  vfmadd231ps ymm8, ymm14, ymm15
  vmovups ymm15, [rbx + r9]
  vfmadd231ps ymm1, ymm12, ymm15
  vfmadd231ps ymm5, ymm13, ymm15
  vfmadd231ps ymm9, ymm14, ymm15
  vmovups ymm15, [rbx + r9*2]
  vfmadd231ps ymm2, ymm12, ymm15
  vfmadd231ps ymm6, ymm13, ymm15
  vfmadd231ps ymm10, ymm14, ymm15
  vmovups ymm15, [r12]
  vfmadd231ps ymm3, ymm12, ymm15
  vfmadd231ps ymm7, ymm13, ymm15
  vfmadd231ps ymm11, ymm14, ymm15
  add  r10, 32
  add  rbx, 32
  add  r12, 32
  dec  ecx
  jnz  .k3
  SG_RED 0, 1, 2, 3
  SG_RED 4, 5, 6, 7
  SG_RED 8, 9, 10, 11
  mov  rdi, r13
  imul rdi, [r15 + SgGemm.ldc]
  add  rdi, [r15 + SgGemm.c]
  lea  rdi, [rdi + rsi*4]      ; C[m][n0]
  mov  rbp, [r15 + SgGemm.ldc]
  SG_PUT 0
  add  rdi, rbp
  SG_PUT 4
  add  rdi, rbp
  SG_PUT 8
  add  qword [rsp+224], 4
  jmp  .sp3
.mt_next:
  add  r13, 3
  jmp  .mt
.tail:                         ; 0, 1 or 2 rows left: one row at a time
  test rax, rax
  jz   .kcnext
.t1:
  cmp  r13, [r15 + SgGemm.m]
  jae  .kcnext
  mov  qword [rsp+224], 0
.sp1:
  mov  rax, [rsp+224]
  cmp  rax, [rsp+216]
  jae  .t1_next
  SG_SETUP
  vxorps ymm0, ymm0, ymm0
  vxorps ymm1, ymm1, ymm1
  vxorps ymm2, ymm2, ymm2
  vxorps ymm3, ymm3, ymm3
.k1:
  vmovups ymm12, [r10]
  vmovups ymm15, [rbx]
  vfmadd231ps ymm0, ymm12, ymm15
  vmovups ymm15, [rbx + r9]
  vfmadd231ps ymm1, ymm12, ymm15
  vmovups ymm15, [rbx + r9*2]
  vfmadd231ps ymm2, ymm12, ymm15
  vmovups ymm15, [r12]
  vfmadd231ps ymm3, ymm12, ymm15
  add  r10, 32
  add  rbx, 32
  add  r12, 32
  dec  ecx
  jnz  .k1
  SG_RED 0, 1, 2, 3
  mov  rdi, r13
  imul rdi, [r15 + SgGemm.ldc]
  add  rdi, [r15 + SgGemm.c]
  lea  rdi, [rdi + rsi*4]
  SG_PUT 0
  add  qword [rsp+224], 4
  jmp  .sp1
.t1_next:
  inc  r13
  jmp  .t1
.kcnext:
  add  qword [rsp+184], SG_KC
  jmp  .kc
.gnext:
  mov  rax, [rsp+168]
  add  [rsp+176], rax
  jmp  .group
.done:
  vmovups xmm6, [rsp]
  vmovups xmm7, [rsp+16]
  vmovups xmm8, [rsp+32]
  vmovups xmm9, [rsp+48]
  vmovups xmm10, [rsp+64]
  vmovups xmm11, [rsp+80]
  vmovups xmm12, [rsp+96]
  vmovups xmm13, [rsp+112]
  vmovups xmm14, [rsp+128]
  vmovups xmm15, [rsp+144]
  vzeroupper
  add  rsp, 248
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbp
  pop  rbx
  ret

; SG_HSUM r — xmm r = the sum of the 8 lanes of ymm r (in lane 0); clobbers xmm5.
%macro SG_HSUM 1
  vextractf128 xmm5, ymm%1, 1
  vaddps xmm%1, xmm%1, xmm5
  vhaddps xmm%1, xmm%1, xmm%1
  vhaddps xmm%1, xmm%1, xmm%1
%endmacro

; sg_layernorm(rcx=out, rdx=in, r8=w f32, r9=b f32, [rsp+40]=n) — one row:
; out = (in - mean) / sqrt(var + 1e-5) * w + b, n a multiple of 8. out may be
; in. Leaf; clobbers rax, r10, ymm0-ymm5.
sg_layernorm:
  mov  r10, [rsp+40]
  vxorps ymm0, ymm0, ymm0
  xor  eax, eax
.s:
  vaddps ymm0, ymm0, [rdx + rax*4]
  add  rax, 8
  cmp  rax, r10
  jb   .s
  SG_HSUM 0
  vcvtsi2ss xmm1, xmm1, r10
  vdivss xmm0, xmm0, xmm1
  vbroadcastss ymm2, xmm0      ; mean
  vxorps ymm0, ymm0, ymm0
  xor  eax, eax
.v:
  vmovups ymm3, [rdx + rax*4]
  vsubps ymm3, ymm3, ymm2
  vfmadd231ps ymm0, ymm3, ymm3
  add  rax, 8
  cmp  rax, r10
  jb   .v
  SG_HSUM 0
  vdivss xmm0, xmm0, xmm1
  vaddss xmm0, xmm0, [rel sg_eps]
  vsqrtss xmm0, xmm0, xmm0
  vmovss xmm3, [rel kc_one]
  vdivss xmm3, xmm3, xmm0
  vbroadcastss ymm3, xmm3      ; 1 / std
  xor  eax, eax
.o:
  vmovups ymm4, [rdx + rax*4]
  vsubps ymm4, ymm4, ymm2
  vmulps ymm4, ymm4, ymm3
  vmulps ymm4, ymm4, [r8 + rax*4]
  vaddps ymm4, ymm4, [r9 + rax*4]
  vmovups [rcx + rax*4], ymm4
  add  rax, 8
  cmp  rax, r10
  jb   .o
  ret

; sg_gelu(rcx=x f32, rdx=n) — x = 0.5 x (1 + erf(x / sqrt 2)) in place, n a
; multiple of 8; erf by Abramowitz-Stegun 7.1.26 (|error| < 1.5e-7) with EXP8.
; Leaf; clobbers rax, ymm0-ymm5.
sg_gelu:
  xor  eax, eax
.l:
  cmp  rax, rdx
  jae  .d
  vmovups ymm4, [rcx + rax*4]           ; x
  vmulps ymm5, ymm4, [rel sg8_rsqrt2]   ; z = x / sqrt 2
  vandps ymm5, ymm5, [rel kc8_absmask]  ; |z|
  vmulps ymm0, ymm5, ymm5
  vxorps ymm0, ymm0, [rel kc8_signmask] ; -z^2
  EXP8                                  ; ymm0 = e^(-z^2); clobbers ymm1-3
  vmulps ymm1, ymm5, [rel sg8_erf_p]
  vaddps ymm1, ymm1, [rel kc8_one]
  vmovaps ymm2, [rel kc8_one]
  vdivps ymm1, ymm2, ymm1               ; t = 1 / (1 + p |z|)
  vmovaps ymm2, [rel sg8_erf_a5]
  vfmadd213ps ymm2, ymm1, [rel sg8_erf_a4]
  vfmadd213ps ymm2, ymm1, [rel sg8_erf_a3]
  vfmadd213ps ymm2, ymm1, [rel sg8_erf_a2]
  vfmadd213ps ymm2, ymm1, [rel sg8_erf_a1]
  vmulps ymm2, ymm2, ymm1
  vmulps ymm2, ymm2, ymm0               ; poly(t) t e^(-z^2)
  vmovaps ymm3, [rel kc8_one]
  vsubps ymm2, ymm3, ymm2               ; erf |z|
  vandps ymm3, ymm4, [rel kc8_signmask]
  vorps ymm2, ymm2, ymm3                ; erf z, the sign of x
  vaddps ymm2, ymm2, [rel kc8_one]
  vmulps ymm2, ymm2, ymm4
  vmulps ymm2, ymm2, [rel sg8_half]
  vmovups [rcx + rax*4], ymm2
  add  rax, 8
  jmp  .l
.d:
  ret

; sg_attn(rcx=SgAttn*) — every head, every query row: scores into g_sg_scores,
; k_softmax, then the weighted sum of V rows into out. Single thread (the text
; encoder's 77 rows; the UNet's thousands of rows go through un_attn, which
; uses sg_matmul). Preserves the nonvolatile registers.
sg_attn:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = head
  mov  r15, rcx
  mov  rax, [r15 + SgAttn.tk]
  cmp  rax, [rel g_sg_scores_n]
  jbe  .have
  mov  [rel g_sg_scores_n], rax
  lea  rcx, [rax*4 + 64]
  call rt_alloc
  mov  [rel g_sg_scores], rax
.have:
  mov  qword [rsp+32], 0
.head:
  mov  rax, [rsp+32]
  cmp  rax, [r15 + SgAttn.heads]
  jae  .done
  imul rax, [r15 + SgAttn.hd]
  shl  rax, 2
  mov  r14, rax                ; byte offset of this head in a row
  xor  r12d, r12d              ; query row t
.row:
  cmp  r12, [r15 + SgAttn.tq]
  jae  .hnext
  mov  r13, [r15 + SgAttn.tk]  ; keys used
  cmp  qword [r15 + SgAttn.causal], 0
  je   .ncaus
  lea  r13, [r12 + 1]
.ncaus:
  mov  rsi, r12
  imul rsi, [r15 + SgAttn.ldq]
  add  rsi, [r15 + SgAttn.q]
  add  rsi, r14                ; q_t (this head)
  xor  ebx, ebx                ; key row j
.sc:
  cmp  rbx, r13
  jae  .soft
  mov  rdi, rbx
  imul rdi, [r15 + SgAttn.ldk]
  add  rdi, [r15 + SgAttn.k]
  add  rdi, r14                ; k_j
  vxorps ymm0, ymm0, ymm0
  xor  eax, eax
  mov  rcx, [r15 + SgAttn.hd]
.dot:
  vmovups ymm1, [rsi + rax*4]
  vfmadd231ps ymm0, ymm1, [rdi + rax*4]
  add  rax, 8
  cmp  rax, rcx
  jb   .dot
  SG_HSUM 0
  vmulss xmm0, xmm0, [r15 + SgAttn.scale]
  mov  rax, [rel g_sg_scores]
  vmovss [rax + rbx*4], xmm0
  inc  rbx
  jmp  .sc
.soft:
  mov  rcx, [rel g_sg_scores]
  mov  rdx, r13
  call k_softmax
  mov  rdi, r12                ; out_t = sum_j p_j v_j
  imul rdi, [r15 + SgAttn.ldq]
  add  rdi, [r15 + SgAttn.out]
  add  rdi, r14
  mov  rcx, [r15 + SgAttn.hd]
  xor  eax, eax
  vxorps ymm0, ymm0, ymm0
.z:
  vmovups [rdi + rax*4], ymm0
  add  rax, 8
  cmp  rax, rcx
  jb   .z
  xor  ebx, ebx
.wv:
  cmp  rbx, r13
  jae  .rnext
  mov  rax, [rel g_sg_scores]
  vbroadcastss ymm1, [rax + rbx*4]
  mov  rdx, rbx
  imul rdx, [r15 + SgAttn.ldk]
  add  rdx, [r15 + SgAttn.v]
  add  rdx, r14
  xor  eax, eax
.acc:
  vmovups ymm2, [rdi + rax*4]
  vfmadd231ps ymm2, ymm1, [rdx + rax*4]
  vmovups [rdi + rax*4], ymm2
  add  rax, 8
  cmp  rax, rcx
  jb   .acc
  inc  rbx
  jmp  .wv
.rnext:
  inc  r12
  jmp  .row
.hnext:
  inc  qword [rsp+32]
  jmp  .head
.done:
  vzeroupper
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

section .data
  align 32
sg8_rsqrt2:  times 8 dd 0.70710678118654752
sg8_half:    times 8 dd 0.5
sg8_erf_p:   times 8 dd 0.3275911
sg8_erf_a1:  times 8 dd 0.254829592
sg8_erf_a2:  times 8 dd -0.284496736
sg8_erf_a3:  times 8 dd 1.421413741
sg8_erf_a4:  times 8 dd -1.453152027
sg8_erf_a5:  times 8 dd 1.061405429
sg_eps:      dd 1.0e-5
  align 8
g_sg_pack:     times MAX_THREADS dq 0 ; per thread: SG_NB weight rows as f32
g_sg_pack_k:   dq 0                   ; the k the pack buffers hold
g_sg_scores:   dq 0                   ; sg_attn's score row
g_sg_scores_n: dq 0
sg_err_type:   db "a weight tensor has a type the image kernels do not read (q8_0, f16, f32)", 0
section .text

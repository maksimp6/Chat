; kernels.asm — the numeric core: matvec over quantised weights, RMSNorm,
; attention (scores + softmax + weighted V), SwiGLU, RoPE, argmax and the sub-
; block sums. %included into asmrun.asm between gguf.asm and model.asm, so this
; is the same `nasm -f bin` translation unit: labels defined here resolve the
; forward references in model.asm, and labels this file uses (g_n_ctx, rt_alloc,
; rt_now_ns, rt_err_*, pool_run, model_matvec, model_fill_sums, Model/Tensor/
; MatvecJob/AttnJob offsets) are defined in the other modules / common.inc.
;
; Nothing here uses `section`/`global`/`extern`/`org`/`bits`/`default`; storage is
; `dd`/`dq 0` initialised bytes (no resb). Windows x64 ABI throughout, also
; between modules: volatile rax rcx rdx r8 r9 r10 r11 xmm0-5; nonvolatile rbx rsi
; rdi rbp r12-r15 xmm6-15; 32-byte shadow + 16-byte stack alignment at every call.
;
; Public surface (called from model.asm):
;   k_rmsnorm(rcx=out f32*, rdx=in f32*, r8=weight f32*, r9=n, xmm0=eps)
;   k_add(rcx=dst f32*, rdx=src f32*, r8=n)                    dst[i]+=src[i]
;   k_silu_mul(rcx=g f32*, rdx=u f32*, r8=n)                   g[i]=silu(g[i])*u[i]
;   k_argmax(rcx=x f32*, rdx=n) -> rax index of max
;   k_sum32(rcx=out f32*, rdx=x f32*, r8=n)                    out[b]=sum x[32b..+32)
;   k_sum16(rcx=out f32*, rdx=x f32*, r8=n)                    out[b]=sum x[16b..+16)
;   k_rope_table(rcx=tbl f32*, rdx=n_ctx, r8=head_dim, xmm0=theta)
;   k_rope(rcx=head f32*, rdx=tbl_row f32*)                    NeoX rotate in place
;   k_matvec_job(rcx=MatvecJob*, rdx=tid, r8=nthreads)         pool body
;   k_attn_job(rcx=AttnJob*, rdx=tid, r8=nthreads)             pool body
;   kernels_cli(rcx=Model*) -> eax                             --kernels micro-bench
; Internal: k_exp, k_softmax, k_dot_f32_row, k_dot_f16_row, k_dot_q4k_row,
;           k_dot_q6k_row.
;
; ISA: AVX2 + FMA + F16C (gated once by rt_cpuid_check in main before any use).
; Transcendentals (exp for silu/softmax, sincos for the rope table) use the x87
; unit: hardware-accurate and one small routine, and not the bottleneck — the
; weights the matvec streams per token dwarf the O(n_ff)/O(seqlen) exp work. x87
; blocks are kept self-contained and stack-balanced, with vzeroupper before them.

KERNELS_BENCH_ITERS equ 24      ; matvecs timed by --kernels

; ---------------------------------------------------------------------------
; f32 constants (initialised bytes in the .flat section; read with [rel ..]).
  align 4
kc_one:     dd 1.0
kc_exp_hi:  dd 88.0
kc_exp_lo:  dd -88.0
  align 32
kc_0f32:    times 32 db 0x0F     ; per-byte low-nibble mask for vpand (AVX2 memory operand)
  align 32
kc_0332:    times 32 db 0x03     ; per-byte 2-bit mask (Q6_K high bits from qh)
  align 16
kc_absmask: times 4 dd 0x7FFFFFFF ; |f32| mask (clear sign bit)
kc_127:     dd 127.0             ; activation quantization scale

; Published by k_rope_table for k_rope (which gets no size argument).
  align 8
kg_rope_dim:  dq 0              ; head_dim
kg_rope_half: dq 0              ; head_dim/2

; --kernels bench strings.
kc_s_head:  db "asmrun: kernels matvec rows=", 0
kc_s_cols:  db " cols=", 0
kc_s_iters: db " iters=", 0
kc_s_ms:    db " ms=", 0
kc_s_gbs:   db " gb_s=", 0
kc_s_nl:    db 10

; ===========================================================================
; k_exp — scalar e^x via x87.  xmm0 (f32) in/out. Clamps x to [-88,88].
; Leaf: uses the caller-provided 32-byte shadow ([rsp+8]) for one temp and the
; x87 stack (left empty). Clobbers xmm0 only; no GPR touched.
; ===========================================================================
k_exp:
  maxss xmm0, [rel kc_exp_lo]
  minss xmm0, [rel kc_exp_hi]
  movss [rsp+8], xmm0
  fld   dword [rsp+8]           ; x
  fldl2e                        ; log2e, x
  fmulp st1, st0                ; y = x*log2e
  fld   st0                     ; y, y
  frndint                       ; n, y
  fsub  st1, st0                ; n, f=y-n
  fxch  st1                     ; f, n
  f2xm1                         ; 2^f-1, n
  fld1                          ; 1, 2^f-1, n
  faddp st1, st0                ; 2^f, n
  fscale                        ; 2^f*2^n = e^x, n
  fstp  st1                     ; e^x
  fstp  dword [rsp+8]
  movss xmm0, [rsp+8]
  ret

; ===========================================================================
; k_sum32 / k_sum16 — per sub-block sums of x (used by the Q4_K/Q6_K dots).
; k_sum32(rcx=out, rdx=x, r8=n): out[b] = sum x[32b .. 32b+32), b in 0..n/32.
; k_sum16(rcx=out, rdx=x, r8=n): out[b] = sum x[16b .. 16b+16), b in 0..n/16.
; n is a multiple of 256 (K-quant row), hence of 32 and 16. Leaf; ymm only.
; ===========================================================================
k_sum32:
  mov  r9, r8
  shr  r9, 5                    ; groups = n/32
  xor  r10, r10                 ; b
.g32:
  cmp  r10, r9
  jae  .done32
  mov  rax, r10
  shl  rax, 5                   ; 32b
  vmovups ymm0, [rdx + rax*4]
  vaddps  ymm0, ymm0, [rdx + rax*4 + 32]
  vaddps  ymm0, ymm0, [rdx + rax*4 + 64]
  vaddps  ymm0, ymm0, [rdx + rax*4 + 96]
  vextractf128 xmm1, ymm0, 1
  vaddps  xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  movss   [rcx + r10*4], xmm0
  inc  r10
  jmp  .g32
.done32:
  vzeroupper
  ret

k_sum16:
  mov  r9, r8
  shr  r9, 4                    ; groups = n/16
  xor  r10, r10
.g16:
  cmp  r10, r9
  jae  .done16
  mov  rax, r10
  shl  rax, 4                   ; 16b
  vmovups ymm0, [rdx + rax*4]
  vaddps  ymm0, ymm0, [rdx + rax*4 + 32]
  vextractf128 xmm1, ymm0, 1
  vaddps  xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  movss   [rcx + r10*4], xmm0
  inc  r10
  jmp  .g16
.done16:
  vzeroupper
  ret

; ===========================================================================
; k_rmsnorm(rcx=out, rdx=in, r8=weight f32*, r9=n, xmm0=eps f32)
;   ss = sum in[i]^2 ; scale = 1/sqrt(ss/n + eps) ; out[i] = in[i]*weight[i]*scale
; Weight tensors (attn_norm, ffn_norm, q/k_norm, output_norm) are stored F32.
; n is a multiple of 8 (n_embd, head_dim). Leaf (no calls): stores eps in the
; caller shadow at [rsp+8]; ymm; vzeroupper before ret.
; ===========================================================================
k_rmsnorm:
  movss [rsp+8], xmm0           ; save eps (xmm0 reused below)
  vxorps ymm0, ymm0, ymm0       ; ss accumulator
  xor  r10, r10                 ; i
.ss_loop:
  cmp  r10, r9
  jae  .ss_done
  vmovups ymm1, [rdx + r10*4]
  vfmadd231ps ymm0, ymm1, ymm1
  add  r10, 8
  jmp  .ss_loop
.ss_done:
  vextractf128 xmm1, ymm0, 1
  vaddps  xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0      ; xmm0[0] = ss
  cvtsi2ss xmm2, r9             ; (float)n
  divss   xmm0, xmm2            ; mean = ss/n
  addss   xmm0, [rsp+8]         ; + eps
  sqrtss  xmm0, xmm0
  movss   xmm2, [rel kc_one]
  divss   xmm2, xmm0            ; scale = 1/sqrt(mean+eps)
  vbroadcastss ymm2, xmm2
  xor  r10, r10
.mul_loop:
  cmp  r10, r9
  jae  .mul_done
  vmovups ymm0, [rdx + r10*4]   ; in
  vmulps  ymm0, ymm0, [r8 + r10*4]   ; * weight
  vmulps  ymm0, ymm0, ymm2      ; * scale
  vmovups [rcx + r10*4], ymm0
  add  r10, 8
  jmp  .mul_loop
.mul_done:
  vzeroupper
  ret

; ===========================================================================
; k_add(rcx=dst, rdx=src, r8=n): dst[i] += src[i]. n multiple of 8. Leaf; ymm.
; ===========================================================================
k_add:
  xor  r10, r10
.l:
  cmp  r10, r8
  jae  .done
  vmovups ymm0, [rcx + r10*4]
  vaddps  ymm0, ymm0, [rdx + r10*4]
  vmovups [rcx + r10*4], ymm0
  add  r10, 8
  jmp  .l
.done:
  vzeroupper
  ret

; ===========================================================================
; k_silu_mul(rcx=g, rdx=u, r8=n): g[i] = (g[i]/(1+e^-g[i])) * u[i], in place.
; Calls k_exp per element, so it is non-leaf: pushes g/u/n in nonvolatiles and
; keeps a 32-byte shadow for k_exp.
; ===========================================================================
k_silu_mul:
  push rbx
  push rsi
  push rdi
  push r12                      ; entry rsp%16==8; +4 pushes -> %16==8
  sub  rsp, 40                  ; 32 shadow + 8 pad -> %16==0 (8+32? see note)
  mov  rbx, rcx                 ; g
  mov  rsi, rdx                 ; u
  mov  rdi, r8                  ; n
  xor  r12, r12                 ; i
.l:
  cmp  r12, rdi
  jae  .done
  movss xmm0, [rbx + r12*4]     ; z
  movss [rsp+36], xmm0          ; save z
  xorps xmm1, xmm1
  subss xmm1, xmm0              ; -z
  movaps xmm0, xmm1
  call k_exp                    ; xmm0 = e^-z
  movss xmm1, [rel kc_one]
  addss xmm0, xmm1              ; 1+e^-z
  movss xmm2, [rel kc_one]
  divss xmm2, xmm0             ; sigmoid = 1/(1+e^-z)
  movss xmm0, [rsp+36]          ; z
  mulss xmm0, xmm2              ; silu = z*sigmoid
  mulss xmm0, [rsi + r12*4]     ; * u[i]
  movss [rbx + r12*4], xmm0
  inc  r12
  jmp  .l
.done:
  add  rsp, 40
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; k_argmax(rcx=x f32*, rdx=n) -> rax = index of the greatest element (first on
; ties). n >= 1. Leaf; scalar (runs once per token over n_vocab).
; ===========================================================================
k_argmax:
  xor  rax, rax                 ; best index
  movss xmm0, [rcx]             ; best value
  mov  r9, 1                    ; i
.l:
  cmp  r9, rdx
  jae  .done
  movss xmm1, [rcx + r9*4]
  comiss xmm1, xmm0
  jbe  .skip
  movss xmm0, xmm1
  mov  rax, r9
.skip:
  inc  r9
  jmp  .l
.done:
  ret

; ===========================================================================
; k_rope_table(rcx=tbl f32*, rdx=n_ctx, r8=head_dim, xmm0=theta f32)
; Fills tbl[p*head_dim + i] = cos(p*theta^(-i/half)) for i in [0,half),
;       tbl[p*head_dim + half + i] = sin(same), half = head_dim/2.
; Publishes head_dim/half for k_rope. Computed once at load with x87 (fsincos,
; fyl2x/f2xm1 for theta^e). Allocates a half-float inv_freq scratch via rt_alloc.
; Non-leaf (calls rt_alloc): saves args in nonvolatiles.
; ===========================================================================
k_rope_table:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15                      ; 7 pushes; entry rsp%16==8 -> after pushes %16==0
  sub  rsp, 48                  ; 32 shadow + 16 locals, multiple of 16 -> stays %16==0
  mov  rbx, rcx                 ; tbl
  mov  rsi, rdx                 ; n_ctx
  mov  r12, r8                  ; head_dim
  mov  [rel kg_rope_dim], r8
  mov  r13, r8
  shr  r13, 1                   ; half
  mov  [rel kg_rope_half], r13
  movss [rsp+36], xmm0          ; theta

  ; inv_freq scratch = f32[half]
  mov  rcx, r13
  shl  rcx, 2
  call rt_alloc
  mov  r14, rax                 ; inv_freq[]

  ; L2T = log2(theta)   (st-based; store to [rsp+32])
  vzeroupper
  fld1
  fld   dword [rsp+36]          ; theta, 1
  fyl2x                         ; log2(theta)
  fstp  dword [rsp+32]          ; L2T

  ; inv_freq[i] = 2^((-i/half) * L2T)
  xor  r15, r15                 ; i
.freq:
  cmp  r15, r13
  jae  .freq_done
  ; e = (-(float)i / (float)half) * L2T
  cvtsi2ss xmm0, r15
  cvtsi2ss xmm1, r13
  divss xmm0, xmm1              ; i/half
  xorps xmm2, xmm2
  subss xmm2, xmm0             ; -(i/half)
  movss [rsp+40], xmm2
  fld   dword [rsp+40]         ; -(i/half)
  fld   dword [rsp+32]         ; L2T, -(i/half)
  fmulp st1, st0               ; e = -(i/half)*L2T
  ; 2^e
  fld   st0                    ; e, e
  frndint                      ; n, e
  fsub  st1, st0               ; n, ef=e-n
  fxch  st1                    ; ef, n
  f2xm1                        ; 2^ef-1, n
  fld1
  faddp st1, st0               ; 2^ef, n
  fscale                       ; 2^e, n
  fstp  st1                    ; 2^e
  fstp  dword [r14 + r15*4]    ; inv_freq[i]
  inc  r15
  jmp  .freq
.freq_done:

  ; tbl[p][i]=cos(p*inv_freq[i]); tbl[p][half+i]=sin(...)
  xor  rdi, rdi                 ; p
.prow:
  cmp  rdi, rsi
  jae  .prow_done
  mov  rax, rdi
  imul rax, r12                 ; p*head_dim
  lea  r8, [rbx + rax*4]        ; row base
  xor  r15, r15                 ; i
.pcol:
  cmp  r15, r13
  jae  .pcol_done
  ; angle = p * inv_freq[i]
  cvtsi2ss xmm0, rdi
  mulss xmm0, [r14 + r15*4]
  movss [rsp+40], xmm0
  fld   dword [rsp+40]          ; angle
  fsincos                       ; cos, sin
  fstp  dword [r8 + r15*4]      ; cos -> [i]
  mov  rax, r15
  add  rax, r13
  fstp  dword [r8 + rax*4]      ; sin -> [half+i]
  inc  r15
  jmp  .pcol
.pcol_done:
  inc  rdi
  jmp  .prow
.prow_done:
  add  rsp, 48
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; k_rope(rcx=head f32* [head_dim values], rdx=tbl_row f32* [this position]).
; NeoX pairing: for i in [0,half):  c=row[i], s=row[half+i], a=head[i],
;   b=head[half+i]; head[i]=a*c-b*s; head[half+i]=a*s+b*c. Leaf; scalar.
; ===========================================================================
k_rope:
  mov  r9, [rel kg_rope_half]
  xor  r10, r10                 ; i
.l:
  cmp  r10, r9
  jae  .done
  mov  r11, r10
  add  r11, r9                  ; half+i
  movss xmm2, [rdx + r10*4]     ; c
  movss xmm3, [rdx + r11*4]     ; s
  movss xmm0, [rcx + r10*4]     ; a
  movss xmm1, [rcx + r11*4]     ; b
  movaps xmm4, xmm0
  mulss xmm4, xmm2              ; a*c
  movaps xmm5, xmm1
  mulss xmm5, xmm3              ; b*s
  subss xmm4, xmm5             ; a*c-b*s
  movss [rcx + r10*4], xmm4
  mulss xmm0, xmm3             ; a*s
  mulss xmm1, xmm2             ; b*c
  addss xmm0, xmm1             ; a*s+b*c
  movss [rcx + r11*4], xmm0
  inc  r10
  jmp  .l
.done:
  ret

; ===========================================================================
; k_softmax(rcx=p f32*, rdx=n): in-place softmax with max subtraction.
; Calls k_exp per element -> non-leaf. Keeps a 32-byte shadow for k_exp and two
; locals (sum, max) above it.
; ===========================================================================
k_softmax:
  push rbx
  push rsi
  push rdi                      ; 3 pushes; entry %16==8 -> %16==0
  sub  rsp, 48                  ; 32 shadow + 16 locals -> %16==0
  mov  rbx, rcx                 ; p
  mov  rsi, rdx                 ; n
  ; max
  movss xmm0, [rbx]
  mov  rdi, 1
.max:
  cmp  rdi, rsi
  jae  .max_done
  movss xmm1, [rbx + rdi*4]
  maxss xmm0, xmm1
  inc  rdi
  jmp  .max
.max_done:
  movss [rsp+40], xmm0          ; max
  xorps xmm0, xmm0
  movss [rsp+32], xmm0          ; sum = 0
  xor  rdi, rdi
.exp:
  cmp  rdi, rsi
  jae  .exp_done
  movss xmm0, [rbx + rdi*4]
  subss xmm0, [rsp+40]          ; s - max
  call k_exp                    ; e^(s-max)
  movss [rbx + rdi*4], xmm0
  movss xmm1, [rsp+32]
  addss xmm1, xmm0
  movss [rsp+32], xmm1          ; sum += e
  inc  rdi
  jmp  .exp
.exp_done:
  movss xmm2, [rel kc_one]
  divss xmm2, [rsp+32]          ; 1/sum
  xor  rdi, rdi
.norm:
  cmp  rdi, rsi
  jae  .norm_done
  movss xmm0, [rbx + rdi*4]
  mulss xmm0, xmm2
  movss [rbx + rdi*4], xmm0
  inc  rdi
  jmp  .norm
.norm_done:
  add  rsp, 48
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; Row dot kernels — out = dot(dequant(row), x). Each takes rcx=row bytes,
; rdx=x f32*, r8=cols, returns the dot in xmm0. Leaf (save nonvolatiles used).
; cols is a multiple of 8 (F32/F16) or 256 (Q4_K/Q6_K).
; ===========================================================================

; k_dot_f32_row(rcx=w f32*, rdx=x, r8=cols)
k_dot_f32_row:
  vxorps ymm0, ymm0, ymm0
  xor  r10, r10
.l:
  cmp  r10, r8
  jae  .done
  vmovups ymm1, [rcx + r10*4]
  vfmadd231ps ymm0, ymm1, [rdx + r10*4]
  add  r10, 8
  jmp  .l
.done:
  vextractf128 xmm1, ymm0, 1
  vaddps  xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  vzeroupper
  ret

; k_dot_f16_row(rcx=w f16*, rdx=x, r8=cols)
k_dot_f16_row:
  vxorps ymm0, ymm0, ymm0
  xor  r10, r10
.l:
  cmp  r10, r8
  jae  .done
  vcvtph2ps ymm1, [rcx + r10*2]      ; 8 halfs -> 8 f32
  vfmadd231ps ymm0, ymm1, [rdx + r10*4]
  add  r10, 8
  jmp  .l
.done:
  vextractf128 xmm1, ymm0, 1
  vaddps  xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  vzeroupper
  ret

; ---------------------------------------------------------------------------
; k_quant_x(rcx=x f32*, rdx=n). Quantize x into the int8 activation buffers for
; the int8 Q4_K matvec: per 256-block, g_d8[b]=amax/127, g_q8[256b+i]=round(x/d8)
; clamped to [-127,127], g_bsums[16b+j]=sum of 16 consecutive g_q8. n % 256 == 0.
; Leaf (no calls); scalar (runs once per matvec over n<=n_ff values, cheap).
; ---------------------------------------------------------------------------
k_quant_x:
  push rbx
  push rsi
  push rdi
  push rbp
  push r12
  push r13
  push r14
  push r15
  mov  rsi, rcx                 ; x
  mov  r13, rdx
  shr  r13, 8                   ; nblocks = n/256
  mov  r12, [rel g_q8]
  mov  r14, [rel g_d8]
  mov  r15, [rel g_bsums]
  xor  rbx, rbx                 ; block b
.qblk:
  cmp  rbx, r13
  jae  .qdone
  mov  rax, rbx
  shl  rax, 8                   ; 256b
  lea  rdi, [rsi + rax*4]       ; &x[256b]
  vxorps xmm2, xmm2, xmm2       ; amax
  xor  rbp, rbp
.qamax:
  cmp  rbp, 256
  jae  .qamax_done
  vmovss xmm0, [rdi + rbp*4]
  vandps xmm0, xmm0, [rel kc_absmask]
  vmaxss xmm2, xmm2, xmm0
  inc  rbp
  jmp  .qamax
.qamax_done:
  vmovss xmm1, [rel kc_127]
  vdivss xmm3, xmm2, xmm1       ; d8 = amax/127
  vmovss [r14 + rbx*4], xmm3
  vxorps xmm4, xmm4, xmm4       ; inv = 0
  vucomiss xmm2, xmm4
  je   .qzero
  vdivss xmm4, xmm1, xmm2       ; inv = 127/amax
.qzero:
  mov  rax, rbx
  shl  rax, 8
  lea  r8, [r12 + rax]          ; &g_q8[256b]
  mov  rax, rbx
  shl  rax, 4                   ; 16b
  lea  r9, [r15 + rax*4]        ; &g_bsums[16b]
  xor  rbp, rbp                 ; j
.qsub:
  cmp  rbp, 16
  jae  .qblk_next
  xor  r10d, r10d               ; per-16 sum
  mov  rax, rbp
  shl  rax, 4                   ; 16j
  xor  r11, r11                 ; i
.qval:
  cmp  r11, 16
  jae  .qsub_done
  mov  rdx, rax
  add  rdx, r11                 ; 16j+i
  vmovss xmm0, [rdi + rdx*4]
  vmulss xmm0, xmm0, xmm4       ; x*inv
  vcvtss2si ecx, xmm0           ; round to nearest
  cmp  ecx, 127
  jle  .qhi
  mov  ecx, 127
.qhi:
  cmp  ecx, -127
  jge  .qlo
  mov  ecx, -127
.qlo:
  mov  [r8 + rdx], cl           ; g_q8[256b+16j+i] (int8)
  add  r10d, ecx
  inc  r11
  jmp  .qval
.qsub_done:
  mov  [r9 + rbp*4], r10d       ; g_bsums[16b+j]
  inc  rbp
  jmp  .qsub
.qblk_next:
  inc  rbx
  jmp  .qblk
.qdone:
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rbp
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ---------------------------------------------------------------------------
; k_dot_q4k_row(rcx=w, r8=cols) -> xmm0.  int8 path: reads g_q8/g_d8/g_bsums (the
; current activation quantized by k_quant_x). dot = d8*(d4*sum_s sc[s]*isum_s -
; dmin*sum_s m[s]*(bsums[2s]+bsums[2s+1])), isum_s = sum_32 q4*q8 via vpdpbusd.
; Q4_K: 144-byte blocks of 256. Per 32-value sub-block s (0..7): dequant value =
; q*d*sc[s] - dmin*m[s]. So dot = d*sum_s sc[s]*(sum_b q*x) - dmin*sum_s m[s]*
; xsum32[blk*8+s]. Scale extraction and nibble layout copied from
; model_dequant_row. sub-block s covers x[32s..32s+32) within the block.
; Saves nonvolatiles; scales/mins kept in a 64-byte stack scratch.
; ---------------------------------------------------------------------------
k_dot_q4k_row:
  push rbx
  push rsi
  push rdi
  push rbp
  push r12
  push r13
  push r14
  push r15                      ; 8 pushes (leaf; no stack-align requirement)
  sub  rsp, 80                  ; [0..32) sc, [32..64) m, [64]d4, [68]dmin4, [72]d8, [76]dot
  mov  rbx, rcx                 ; block ptr (advances)
  mov  r13, r8
  shr  r13, 8                   ; nblocks = cols/256
  mov  r14, [rel g_q8]          ; int8 activations (this x)
  mov  rsi, [rel g_bsums]       ; i32 per-16 sums of g_q8
  mov  rbp, [rel g_d8]          ; f32 per-256 activation scale
  vxorps xmm0, xmm0, xmm0
  vmovss [rsp+76], xmm0         ; dot = 0
  xor  r12, r12                 ; block index
.blk:
  cmp  r12, r13
  jae  .done
  movzx eax, word [rbx]
  vmovd xmm0, eax
  vcvtph2ps xmm0, xmm0
  vmovss [rsp+64], xmm0         ; d4
  movzx eax, word [rbx+2]
  vmovd xmm0, eax
  vcvtph2ps xmm0, xmm0
  vmovss [rsp+68], xmm0         ; dmin4
  vmovss xmm0, [rbp + r12*4]
  vmovss [rsp+72], xmm0         ; d8 = g_d8[blk]
  ; scales[12] at block+4 -> sc[0..7] (int) at [rsp+0..32), m[0..7] at [rsp+32..64)
  lea  r8, [rbx+4]
  xor  rcx, rcx
.sc:
  cmp  rcx, 8
  jae  .sc_done
  cmp  rcx, 4
  jae  .schi
  movzx eax, byte [r8+rcx]       ; j<4: sc=scales[j]&63
  and  eax, 63
  mov  [rsp + rcx*4], eax
  movzx edx, byte [r8+rcx+4]     ;       m=scales[j+4]&63
  and  edx, 63
  mov  [rsp+32 + rcx*4], edx
  jmp  .scn
.schi:
  movzx eax, byte [r8+rcx+4]     ; j>=4: sc=(scales[j+4]&15)|((scales[j-4]>>6)<<4)
  and  eax, 15
  movzx edx, byte [r8+rcx-4]
  shr  edx, 6
  shl  edx, 4
  or   eax, edx
  mov  [rsp + rcx*4], eax
  movzx eax, byte [r8+rcx+4]     ;       m=(scales[j+4]>>4)|((scales[j]>>6)<<4)
  shr  eax, 4
  movzx edx, byte [r8+rcx]
  shr  edx, 6
  shl  edx, 4
  or   eax, edx
  mov  [rsp+32 + rcx*4], eax
.scn:
  inc  rcx
  jmp  .sc
.sc_done:
  lea  r15, [rbx+16]            ; qs (4-bit weights)
  mov  rax, r12
  shl  rax, 8
  lea  r8, [r14 + rax]          ; &g_q8[256*blk]  (this block's int8 activations)
  vpxor  ymm2, ymm2, ymm2       ; acc_int (8 i32) = sum_s sc[s]*isum_s
  xor  rdi, rdi                 ; chunk c
.chunk:
  cmp  rdi, 4
  jae  .chunk_done
  mov  r9, rdi
  shl  r9, 5
  add  r9, r15                  ; &qs[32c]
  mov  r10, rdi
  shl  r10, 6                   ; 64c (q8 offset within this block)
  vmovdqu ymm0, [r9]            ; 32 packed nibble bytes
  vpand  ymm1, ymm0, [rel kc_0f32]   ; low nibbles  (u8 q4, sub-block 2c)
  vpsrlw ymm0, ymm0, 4
  vpand  ymm0, ymm0, [rel kc_0f32]   ; high nibbles (sub-block 2c+1)
  mov  rax, rdi
  shl  rax, 1                   ; 2c
  ; sub-block 2c: isum = sum_32 q4_lo * q8[64c..] ; acc += sc[2c]*isum
  vpxor  ymm3, ymm3, ymm3
  {vex} vpdpbusd ymm3, ymm1, [r8 + r10]
  vpbroadcastd ymm4, [rsp + rax*4]       ; sc[2c]
  vpmulld ymm3, ymm3, ymm4
  vpaddd  ymm2, ymm2, ymm3
  ; sub-block 2c+1: q8[64c+32..] ; acc += sc[2c+1]*isum
  vpxor  ymm3, ymm3, ymm3
  {vex} vpdpbusd ymm3, ymm0, [r8 + r10 + 32]
  vpbroadcastd ymm4, [rsp + rax*4 + 4]   ; sc[2c+1]
  vpmulld ymm3, ymm3, ymm4
  vpaddd  ymm2, ymm2, ymm3
  inc  rdi
  jmp  .chunk
.chunk_done:
  ; term1_int = hsum(acc_int) (8 i32 -> scalar)
  vextracti128 xmm3, ymm2, 1
  vpaddd  xmm2, xmm2, xmm3
  vpshufd xmm3, xmm2, 0x0E
  vpaddd  xmm2, xmm2, xmm3
  vpshufd xmm3, xmm2, 0x01
  vpaddd  xmm2, xmm2, xmm3                ; xmm2[0] = term1_int
  ; term2_int = sum_{s=0..7} m[s]*(bsums[16blk+2s] + bsums[16blk+2s+1])
  xor  r11d, r11d
  mov  r10, r12
  shl  r10, 4                   ; 16*blk
  xor  rax, rax                 ; s
.t2:
  cmp  rax, 8
  jae  .t2_done
  mov  r9, rax
  shl  r9, 1                    ; 2s
  add  r9, r10                  ; 16blk + 2s
  mov  ecx, [rsi + r9*4]
  add  ecx, [rsi + r9*4 + 4]    ; bsums[..2s] + bsums[..2s+1]
  imul ecx, [rsp+32 + rax*4]    ; * m[s]
  add  r11d, ecx
  inc  rax
  jmp  .t2
.t2_done:
  ; dot += d8*(d4*term1_int - dmin4*term2_int)
  vcvtdq2ps xmm2, xmm2          ; (float)term1_int in lane 0
  vcvtsi2ss xmm3, xmm3, r11d    ; (float)term2_int
  vmulss  xmm2, xmm2, [rsp+64]  ; d4*term1
  vmulss  xmm3, xmm3, [rsp+68]  ; dmin4*term2
  vsubss  xmm2, xmm2, xmm3
  vmulss  xmm2, xmm2, [rsp+72]  ; * d8
  vmovss  xmm0, [rsp+76]
  vaddss  xmm0, xmm0, xmm2
  vmovss  [rsp+76], xmm0
  add  rbx, Q4K_BYTES
  inc  r12
  jmp  .blk
.done:
  vmovss xmm0, [rsp+76]
  vzeroupper
  add  rsp, 80
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rbp
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ---------------------------------------------------------------------------
; k_dot_q6k_row(rcx=w, rdx=x, r8=cols, r9=xsum16 f32*) -> xmm0
; Q6_K: 210-byte blocks of 256; sub-block S (0..15) of 16 values, scale sc[S]
; (i8), value = d*sc[S]*(q-32), q the 6-bit code. So dot = d*(sum_S sc[S]*
; (sum q*x) - 32*sum_S sc[S]*xsum16[blk*16+S]). q extraction and the (half,l,4
; group) layout copied from model_dequant_row; sub-block S covers x[16S..16S+16).
; ---------------------------------------------------------------------------
k_dot_q6k_row:
  push rbx
  push rsi
  push rdi
  push rbp
  push r12
  push r13
  push r14
  push r15                      ; 8 pushes; entry %16==8 -> %16==8
  sub  rsp, 72                  ; [0]dot [8]d [16]term1 [32..64) q-code spill; %16->0
  mov  rbx, rcx                 ; block ptr
  mov  rsi, rdx                 ; x base
  mov  r13, r8
  shr  r13, 8                   ; nblocks
  mov  r14, r9                  ; xsum16
  vxorps xmm0, xmm0, xmm0
  vmovss [rsp+0], xmm0          ; dot = 0
  xor  r12, r12                 ; block index
.blk:
  cmp  r12, r13
  jae  .done
  movzx eax, word [rbx+208]     ; d (f16)
  vmovd xmm0, eax
  vcvtph2ps xmm0, xmm0
  vmovss [rsp+8], xmm0          ; d
  vxorps ymm2, ymm2, ymm2       ; 4 term1 accumulators (sc folded in), whole block
  vxorps ymm3, ymm3, ymm3
  vxorps ymm4, ymm4, ymm4
  vxorps ymm5, ymm5, ymm5
  mov  r9, r12
  shl  r9, 8                    ; blk*256 (x value base for this block)
  xor  rbp, rbp                 ; half h in 0..1
.half:
  cmp  rbp, 2
  jae  .half_done
  mov  rax, rbp
  shl  rax, 3
  lea  r15, [rbx + 192 + rax]      ; &sc[h*8] (i8)
  mov  rax, rbp
  shl  rax, 7                      ; h*128
  add  rax, r9                     ; blk*256 + h*128
  mov  r11, rax                    ; x/out value base for this half
  xor  rdi, rdi                    ; group g in 0..3
.grp:
  cmp  rdi, 4
  jae  .grp_done
  ; build 32 six-bit codes (0..63) into ymm0 (reload ql/qh per group so ymm2-5
  ; stay as the block accumulators):  g0:(ql0&0xF)|((qh&3)<<4)
  ; g1:(ql1&0xF)|(((qh>>2)&3)<<4)  g2:(ql0>>4)|(((qh>>4)&3)<<4)  g3:(ql1>>4)|(((qh>>6)&3)<<4)
  mov  rax, rbp
  shl  rax, 6                      ; h*64
  test rdi, 1
  jz   .ql_even
  add  rax, 32                     ; groups 1,3 use ql[h*64+32 ..]
.ql_even:
  vmovdqu ymm0, [rbx + rax]        ; ql slice
  cmp  rdi, 2
  jb   .ql_lo
  vpsrlw  ymm0, ymm0, 4            ; groups 2,3: high nibble
.ql_lo:
  vpand   ymm0, ymm0, [rel kc_0f32]
  mov  rax, rbp
  shl  rax, 5
  vmovdqu ymm1, [rbx + 128 + rax]  ; qh
  cmp  rdi, 1
  je   .qh_s2
  cmp  rdi, 2
  je   .qh_s4
  cmp  rdi, 3
  je   .qh_s6
  jmp  .qh_mask                    ; g0: no shift
.qh_s2:
  vpsrlw  ymm1, ymm1, 2
  jmp  .qh_mask
.qh_s4:
  vpsrlw  ymm1, ymm1, 4
  jmp  .qh_mask
.qh_s6:
  vpsrlw  ymm1, ymm1, 6
.qh_mask:
  vpand   ymm1, ymm1, [rel kc_0332]
  vpsllw  ymm1, ymm1, 4
  vpor    ymm0, ymm0, ymm1         ; 32 codes, 0..63
  vmovdqu [rsp+32], ymm0           ; spill codes
  mov  rax, rdi
  shl  rax, 5                       ; 32g
  add  rax, r11
  lea  rcx, [rsi + rax*4]           ; &x[group]
  mov  rdx, rdi
  shl  rdx, 1                       ; 2g (offset into &sc[h*8])
  ; sub-block S0 (first 16): value = sc[2g]*code; x[0..16) into acc0/acc1
  movsx eax, byte [r15 + rdx]
  vcvtsi2ss xmm1, xmm1, eax
  vbroadcastss ymm1, xmm1
  vpmovzxbd ymm0, qword [rsp+32]
  vcvtdq2ps ymm0, ymm0
  vmulps    ymm0, ymm0, ymm1
  vfmadd231ps ymm2, ymm0, [rcx]
  vpmovzxbd ymm0, qword [rsp+40]
  vcvtdq2ps ymm0, ymm0
  vmulps    ymm0, ymm0, ymm1
  vfmadd231ps ymm3, ymm0, [rcx+32]
  ; sub-block S1 (next 16): value = sc[2g+1]*code; x[16..32) into acc2/acc3
  movsx eax, byte [r15 + rdx + 1]
  vcvtsi2ss xmm1, xmm1, eax
  vbroadcastss ymm1, xmm1
  vpmovzxbd ymm0, qword [rsp+48]
  vcvtdq2ps ymm0, ymm0
  vmulps    ymm0, ymm0, ymm1
  vfmadd231ps ymm4, ymm0, [rcx+64]
  vpmovzxbd ymm0, qword [rsp+56]
  vcvtdq2ps ymm0, ymm0
  vmulps    ymm0, ymm0, ymm1
  vfmadd231ps ymm5, ymm0, [rcx+96]
  inc  rdi
  jmp  .grp
.grp_done:
  inc  rbp
  jmp  .half
.half_done:
  ; term1 = hsum(acc0+acc1+acc2+acc3)  (do this before reusing xmm3/5 below)
  vaddps  ymm2, ymm2, ymm3
  vaddps  ymm4, ymm4, ymm5
  vaddps  ymm2, ymm2, ymm4
  vextractf128 xmm0, ymm2, 1
  vaddps  xmm2, xmm2, xmm0
  vhaddps xmm2, xmm2, xmm2
  vhaddps xmm2, xmm2, xmm2           ; xmm2 = term1
  ; term2 = sum_{S=0..15} sc[S]*xsum16[blk*16+S]
  vxorps  xmm3, xmm3, xmm3
  mov  r10, r12
  shl  r10, 4                       ; blk*16
  lea  r11, [rbx+192]               ; &sc[0]
  xor  rdi, rdi                     ; S
.s2:
  cmp  rdi, 16
  jae  .s2_done
  movsx eax, byte [r11 + rdi]
  vcvtsi2ss xmm0, xmm0, eax
  mov  rax, r10
  add  rax, rdi
  vmulss  xmm0, xmm0, [r14 + rax*4]
  vaddss  xmm3, xmm3, xmm0
  inc  rdi
  jmp  .s2
.s2_done:
  ; dot += d*(term1 - 32*term2)
  mov  eax, 32
  vcvtsi2ss xmm0, xmm0, eax
  vmulss  xmm3, xmm3, xmm0           ; 32*term2
  vsubss  xmm2, xmm2, xmm3           ; term1 - 32*term2
  vmulss  xmm2, xmm2, [rsp+8]        ; * d
  vmovss  xmm0, [rsp+0]
  vaddss  xmm0, xmm0, xmm2
  vmovss  [rsp+0], xmm0
  add  rbx, Q6K_BYTES
  inc  r12
  jmp  .blk
.done:
  vmovss xmm0, [rsp+0]
  vzeroupper
  add  rsp, 72
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rbp
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; k_matvec_job(rcx=MatvecJob*, rdx=tid, r8=nthreads) — pool body.
; out[r] = dot(row r of w, x) for the thread's contiguous row slice
; [tid*rows/nthreads, (tid+1)*rows/nthreads). Dispatches on w.type and passes the
; whole-x xsum32/xsum16 (indexed per sub-block inside each dot).
; ===========================================================================
k_matvec_job:
  push rbx
  push rsi
  push rdi
  push rbp
  push r12
  push r13
  push r14
  push r15                      ; 8 pushes; entry %16==8 -> %16==8
  sub  rsp, 56                  ; 32 shadow + 24 locals; %16: 8-8=0
  mov  r14, rcx                 ; job
  mov  [rsp+32], rdx            ; tid
  mov  [rsp+40], r8             ; nthreads
  mov  r12, [r14 + MatvecJob.x]
  mov  r13, [r14 + MatvecJob.out]
  mov  rbx, [r14 + MatvecJob.w] ; Tensor*
  mov  r15, [r14 + MatvecJob.cols]
  ; contiguous row slice [r0, r1): r0=tid*rows/nthreads, r1=(tid+1)*rows/nthreads
  mov  rax, rdx
  imul rax, [r14 + MatvecJob.rows]
  xor  rdx, rdx
  div  r8                       ; r0
  mov  rsi, rax                 ; r (current row)
  mov  rax, [rsp+32]
  inc  rax
  imul rax, [r14 + MatvecJob.rows]
  xor  rdx, rdx
  div  qword [rsp+40]
  mov  rdi, rax                 ; r1 (slice end)
  ; --- row stride in bytes by type, into rbp ---
  mov  eax, [rbx + Tensor.type]
  cmp  eax, GGML_Q4_K
  je   .q4
  cmp  eax, GGML_Q6_K
  je   .q6
  cmp  eax, GGML_F16
  je   .f16
  ; F32
  mov  rbp, r15
  shl  rbp, 2
  jmp  .run
.f16:
  mov  rbp, r15
  shl  rbp, 1
  jmp  .run
.q4:
  mov  rbp, r15
  shr  rbp, 8
  imul rbp, rbp, Q4K_BYTES
  jmp  .run
.q6:
  mov  rbp, r15
  shr  rbp, 8
  imul rbp, rbp, Q6K_BYTES
.run:
  ; loop r in [rsi, rdi): rowptr = w.data + r*rbp (data base reloaded each row
  ; because the dot helpers clobber the volatile regs).
.rowloop:
  cmp  rsi, rdi
  jae  .done
  mov  rax, rsi
  imul rax, rbp
  add  rax, [rbx + Tensor.data]
  mov  rcx, rax                 ; rowptr
  mov  rdx, r12                 ; x
  mov  r8, r15                  ; cols
  mov  eax, [rbx + Tensor.type]
  cmp  eax, GGML_Q4_K
  je   .d_q4
  cmp  eax, GGML_Q6_K
  je   .d_q6
  cmp  eax, GGML_F16
  je   .d_f16
  call k_dot_f32_row
  jmp  .store
.d_f16:
  call k_dot_f16_row
  jmp  .store
.d_q4:
  mov  r9, [r14 + MatvecJob.xsum32]
  call k_dot_q4k_row
  jmp  .store
.d_q6:
  mov  r9, [r14 + MatvecJob.xsum16]
  call k_dot_q6k_row
.store:
  movss [r13 + rsi*4], xmm0
  ; rowptr base r11, cols r15, stride rbp, w rbx, x r12, out r13, end rdi survive
  inc  rsi
  jmp  .rowloop
.done:
  add  rsp, 56
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rbp
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; k_attn_job(rcx=AttnJob*, rdx=tid, r8=nthreads) — pool body.
; For each query head h in the thread's slice: score[t]=dot(q_h,K_t)/sqrt(hd)
; for t in 0..seqlen-1 (K/V of kv-head h/(n_head/n_head_kv)); softmax; out_h =
; sum_t score[t]*V_t. Per-thread score row at scores + tid*g_n_ctx. head_dim is
; a multiple of 8.
; ===========================================================================
%define A_seqlen   [rsp+32]
%define A_kvdim    [rsp+40]
%define A_group    [rsp+48]
%define A_hend     [rsp+56]
%define A_scale    [rsp+64]
%define A_tid      [rsp+72]
%define A_nthr     [rsp+80]
k_attn_job:
  push rbx
  push rsi
  push rdi
  push rbp
  push r12
  push r13
  push r14
  push r15                      ; 8 pushes; entry %16==8 -> %16==8
  sub  rsp, 88                  ; 32 shadow + 56 locals; %16: 8-8=0 (88 mod16=8)
  mov  r14, rcx                 ; job
  mov  A_tid, rdx
  mov  A_nthr, r8
  mov  r12, [r14 + AttnJob.q]
  mov  r13, [r14 + AttnJob.out]
  mov  r15, [r14 + AttnJob.kbase]
  mov  rbx, [r14 + AttnJob.vbase]
  mov  rbp, [r14 + AttnJob.head_dim]
  mov  rax, [r14 + AttnJob.seqlen]
  mov  A_seqlen, rax
  mov  rax, [r14 + AttnJob.kv_dim]
  mov  A_kvdim, rax
  mov  rax, [r14 + AttnJob.n_head]
  xor  rdx, rdx
  div  qword [r14 + AttnJob.n_head_kv]
  mov  A_group, rax             ; group = n_head/n_head_kv
  ; scores_row = scores + tid*g_n_ctx
  mov  rax, A_tid
  imul rax, [rel g_n_ctx]
  shl  rax, 2
  add  rax, [r14 + AttnJob.scores]
  mov  rsi, rax                 ; scores_row
  ; h0 = tid*n_head/nthreads ; h1 = (tid+1)*n_head/nthreads
  mov  rax, A_tid
  imul rax, [r14 + AttnJob.n_head]
  xor  rdx, rdx
  div  qword A_nthr
  mov  rdi, rax                 ; h0 (current head)
  mov  rax, A_tid
  inc  rax
  imul rax, [r14 + AttnJob.n_head]
  xor  rdx, rdx
  div  qword A_nthr
  mov  A_hend, rax
  ; scale = 1/sqrt(head_dim)
  cvtsi2ss xmm0, rbp
  sqrtss xmm0, xmm0
  movss xmm1, [rel kc_one]
  divss xmm1, xmm0
  movss A_scale, xmm1

.head:
  mov  rax, A_hend
  cmp  rdi, rax
  jae  .done
  ; kv_head = h/group
  mov  rax, rdi
  xor  rdx, rdx
  div  qword A_group
  mov  r8, rax                  ; kv_head
  ; q_h = q + h*head_dim
  mov  rax, rdi
  imul rax, rbp
  lea  r10, [r12 + rax*4]       ; q_h
  ; scores: for t in 0..seqlen
  xor  r9, r9                   ; t
.sc:
  cmp  r9, A_seqlen
  jae  .sc_done
  ; K_t = kbase + (t*kv_dim + kv_head*head_dim)*4
  mov  rax, r9
  imul rax, A_kvdim
  mov  rdx, r8
  imul rdx, rbp
  add  rax, rdx
  lea  r11, [r15 + rax*4]       ; K_t
  vxorps ymm0, ymm0, ymm0
  xor  rax, rax                 ; d
.kd:
  cmp  rax, rbp
  jae  .kd_done
  vmovups ymm1, [r10 + rax*4]
  vfmadd231ps ymm0, ymm1, [r11 + rax*4]
  add  rax, 8
  jmp  .kd
.kd_done:
  vextractf128 xmm1, ymm0, 1
  vaddps  xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  mulss xmm0, A_scale
  movss [rsi + r9*4], xmm0
  inc  r9
  jmp  .sc
.sc_done:
  vzeroupper
  mov  rcx, rsi
  mov  rdx, A_seqlen
  call k_softmax
  ; out_h = out + h*head_dim ; zero it
  mov  rax, rdi
  imul rax, rbp
  lea  r10, [r13 + rax*4]       ; out_h
  vxorps ymm0, ymm0, ymm0
  xor  rax, rax
.z:
  cmp  rax, rbp
  jae  .z_done
  vmovups [r10 + rax*4], ymm0
  add  rax, 8
  jmp  .z
.z_done:
  ; recompute kv_head (r8 clobbered by k_softmax)
  mov  rax, rdi
  xor  rdx, rdx
  div  qword A_group
  mov  r8, rax                  ; kv_head
  ; out_h += score[t]*V_t
  xor  r9, r9                   ; t
.acc:
  cmp  r9, A_seqlen
  jae  .acc_done
  movss xmm2, [rsi + r9*4]
  vbroadcastss ymm2, xmm2
  mov  rax, r9
  imul rax, A_kvdim
  mov  rcx, r8
  imul rcx, rbp
  add  rax, rcx
  lea  r11, [rbx + rax*4]       ; V_t
  xor  rax, rax                 ; d
.vd:
  cmp  rax, rbp
  jae  .vd_done
  vmovups ymm1, [r11 + rax*4]
  vmovups ymm3, [r10 + rax*4]
  vfmadd231ps ymm3, ymm2, ymm1
  vmovups [r10 + rax*4], ymm3
  add  rax, 8
  jmp  .vd
.vd_done:
  inc  r9
  jmp  .acc
.acc_done:
  vzeroupper
  inc  rdi
  jmp  .head
.done:
  add  rsp, 88
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rbp
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; kernels_cli(rcx=Model*) -> eax=0. --kernels micro-benchmark: time
; KERNELS_BENCH_ITERS matvecs of the output projection (the largest weight) and
; report rows, cols, ms and effective GB/s (= weight bytes read / time). Reuses
; the real model_matvec path (thread pool, sums, dequant+dot). The model is
; already loaded, so g_xsum32/g_xsum16/g_mvjob and the pool are ready.
; ===========================================================================
k_dot_bench_x_fill:                ; local: fill r14[0..r12) with 1.0
  xorps xmm0, xmm0
  movss xmm0, [rel kc_one]
  xor  rax, rax
.l:
  cmp  rax, r12
  jae  .done
  movss [r14 + rax*4], xmm0
  inc  rax
  jmp  .l
.done:
  ret

kernels_cli:
  push rbx
  push rsi
  push rdi
  push rbp
  push r12
  push r13
  push r14
  push r15                      ; 8 pushes; entry %16==8 -> %16==8
  sub  rsp, 56                  ; 32 shadow + 24 locals; %16: 8-8=0 (56 mod16=8)
  mov  rbx, rcx                 ; Model*
  mov  rsi, [rbx + Model.output]   ; Tensor* (lm head, largest matrix)
  mov  r12, [rsi + Tensor.ne0]  ; cols
  mov  r13, [rsi + Tensor.ne1]  ; rows
  ; x = rt_alloc(cols*4); out = rt_alloc(rows*4)
  mov  rcx, r12
  shl  rcx, 2
  call rt_alloc
  mov  r14, rax                 ; x
  mov  rcx, r13
  shl  rcx, 2
  call rt_alloc
  mov  r15, rax                 ; out
  call k_dot_bench_x_fill       ; x[*]=1.0  (uses r14,r12)
  ; sums for x
  mov  rcx, r14
  mov  rdx, r12
  call model_fill_sums
  ; warm
  mov  rcx, rsi
  mov  rdx, r14
  mov  r8, r15
  call model_matvec
  ; timed
  call rt_now_ns
  mov  [rsp+32], rax            ; t0
  mov  rbp, KERNELS_BENCH_ITERS
.loop:
  test rbp, rbp
  jz   .loop_done
  mov  rcx, rsi
  mov  rdx, r14
  mov  r8, r15
  call model_matvec
  dec  rbp
  jmp  .loop
.loop_done:
  call rt_now_ns
  sub  rax, [rsp+32]            ; elapsed ns
  mov  [rsp+40], rax            ; ns
  ; bytes = nbytes * iters
  mov  rax, [rsi + Tensor.nbytes]
  imul rax, KERNELS_BENCH_ITERS
  mov  [rsp+48], rax            ; bytes
  ; print: head rows cols iters ms gb_s
  lea  rcx, [rel kc_s_head]
  call rt_err_str
  mov  rcx, r13
  call rt_err_u64
  lea  rcx, [rel kc_s_cols]
  call rt_err_str
  mov  rcx, r12
  call rt_err_u64
  lea  rcx, [rel kc_s_iters]
  call rt_err_str
  mov  rcx, KERNELS_BENCH_ITERS
  call rt_err_u64
  lea  rcx, [rel kc_s_ms]
  call rt_err_str
  mov  rax, [rsp+40]
  xor  rdx, rdx
  mov  rcx, 1000000
  div  rcx                      ; ns/1e6 = ms
  mov  rcx, rax
  call rt_err_u64
  lea  rcx, [rel kc_s_gbs]
  call rt_err_str
  ; gb_s = bytes / ns   (bytes/ns == GB/s with GB=1e9)
  cvtsi2ss xmm0, qword [rsp+48]
  cvtsi2ss xmm1, qword [rsp+40]
  divss xmm0, xmm1
  call rt_err_f32
  lea  rcx, [rel kc_s_nl]
  mov  rdx, 1
  call rt_err
  xor  eax, eax
  add  rsp, 56
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rbp
  pop  rdi
  pop  rsi
  pop  rbx
  ret

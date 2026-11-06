; kernels.asm — the numeric core: matvec over quantised weights, RMSNorm,
; attention (scores + softmax + weighted V), SwiGLU, RoPE, argmax and the sub-
; block sums. %included into asmrun.asm between gguf.asm and model.asm, so this
; is the same `nasm -f bin` translation unit: labels defined here resolve the
; forward references in model.asm, and labels this file uses (g_n_ctx, rt_alloc,
; rt_now_ns, rt_err_*, pool_run, model_matvec, model_fill_sums, Model/Tensor/
; MatvecJob/AttnJob offsets) are defined in the other modules / common.inc.
;
; The constants at the top are in .data (`section .data`, back to .text before the
; code); storage is `dd`/`dq 0` initialised bytes (no resb). Windows x64 ABI throughout, also
; between modules: volatile rax rcx rdx r8 r9 r10 r11 xmm0-5; nonvolatile rbx rsi
; rdi rbp r12-r15 xmm6-15; 32-byte shadow + 16-byte stack alignment at every call.
;
; Public surface (called from model.asm):
;   k_rmsnorm(rcx=out f32*, rdx=in f32*, r8=weight f32*, r9=n, xmm0=eps)
;   k_add(rcx=dst f32*, rdx=src f32*, r8=n)                    dst[i]+=src[i]
;   k_silu_mul(rcx=g f32*, rdx=u f32*, r8=n)                   g[i]=silu(g[i])*u[i]
;   k_argmax(rcx=x f32*, rdx=n) -> rax index of max
;   k_quant_x(rcx=x f32*, rdx=n)                               int8 activation + sums
;   k_sum32 / k_sum16                                          (unused since the int8 path)
;   k_rope_table(rcx=tbl f32*, rdx=n_ctx, r8=head_dim, xmm0=theta)
;   k_rope(rcx=head f32*, rdx=tbl_row f32*)                    NeoX rotate in place
;   k_matvec_job(rcx=MatvecJob*, rdx=tid, r8=nthreads)         pool body
;   k_attn_job(rcx=AttnJob*, rdx=tid, r8=nthreads)             pool body
;   kernels_cli(rcx=Model*) -> eax                             --kernels micro-bench
; Internal: k_exp, k_softmax, k_dot_f32_row, k_dot_f16_row, k_dot_q4k_row,
;           k_dot_q6k_row.
;
; ISA: AVX2 + FMA + F16C (gated once by rt_cpuid_check in main before any use).
; exp (silu/softmax) is a polynomial in SSE/AVX (k_exp / EXP8); only the rope
; table's sincos uses the x87 unit (once at load). x87 blocks are kept
; self-contained and stack-balanced, with vzeroupper before them.

KERNELS_BENCH_ITERS equ 24      ; matvecs timed by --kernels

; ---------------------------------------------------------------------------
; Constants (and the two rope globals) in .data; read with [rel ..].
section .data
  align 4
kc_one:     dd 1.0
  align 32
kc8_one:      times 8 dd 1.0
kc8_exp_hi:   times 8 dd 88.0             ; exp clamp: 2^127 scale, result finite
kc8_exp_lo:   times 8 dd -87.0            ; exp clamp: result stays a normal f32
kc8_log2e:    times 8 dd 1.44269504088896341
kc8_ln2_hi:   times 8 dd 0.693359375      ; ln 2 split (cephes expf): hi + lo
kc8_ln2_lo:   times 8 dd -2.12194440e-4
kc8_p0:       times 8 dd 1.9875691500E-4  ; cephes expf polynomial, highest degree first
kc8_p1:       times 8 dd 1.3981999507E-3
kc8_p2:       times 8 dd 8.3334519073E-3
kc8_p3:       times 8 dd 4.1665795894E-2
kc8_p4:       times 8 dd 1.6666665459E-1
kc8_p5:       times 8 dd 5.0000001201E-1
kc8_127i:     times 8 dd 127              ; f32 exponent bias
kc8_signmask: times 8 dd 0x80000000
kc8_absmask:  times 8 dd 0x7FFFFFFF       ; |f32| mask (clear sign bit)
kc8_127f:     times 8 dd 127.0            ; activation quantisation scale
kc8_ones16:   times 16 dw 1               ; i16 ones: vpmaddwd row sums
kc8_perm_q8:  dd 0, 4, 1, 5, 2, 6, 3, 7   ; vpermd order undoing the vpackssdw/vpacksswb lane interleave
kc_63w:       times 8 dw 63              ; Q4_K scale extraction masks (i16 lanes)
kc_15w:       times 8 dw 15
  align 32
kc_q4_scmask:                            ; vpshufb masks: broadcast i16 lane s of [sc0..7 | sc0..7] to all 16 lanes
%assign s 0
%rep 8
  times 16 db 2*s, 2*s+1
%assign s s+1
%endrep
kc_q6_scmask:                            ; vpshufb masks: [lane 2g x8 | lane 2g+1 x8] of [sc0..7 | sc0..7]
%assign g 0
%rep 4
  times 8 db 4*g, 4*g+1
  times 8 db 4*g+2, 4*g+3
%assign g g+1
%endrep
kc_0f32:    times 32 db 0x0F     ; per-byte low-nibble mask for vpand (AVX2 memory operand)
  align 32
kc_3032:    times 32 db 0x30     ; per-byte bits 4-5 mask (Q6_K high bits, already shifted into place)

; Published by k_rope_table for k_rope (which gets no size argument).
  align 8
kg_rope_dim:  dq 0              ; head_dim
kg_rope_half: dq 0              ; head_dim/2

; --kernels bench strings.
kc_s_head:  db "asmrun: kernels matvec rows=", 0
kc_s_cols:  db " cols=", 0
kc_s_type:  db " type=", 0
kc_s_iters: db " iters=", 0
kc_s_ms:    db " ms=", 0
kc_s_gbs:   db " gb_s=", 0
kc_s_chk:   db "asmrun: kernels check row=", 0
kc_s_kern:  db " kernel=", 0
kc_s_ref:   db " ref_f32=", 0
kc_s_pool:  db "asmrun: pool_run empty job ns=", 0
kc_s_nl:    db 10

section .text

; ===========================================================================
; k_exp — scalar e^x: xmm0 (f32) in/out. Clamps x to [-87,88]. The cephes expf
; scheme: n = round(x*log2e), r = x - n*ln2 (ln2 in two parts), e^r by a degree-6
; polynomial, times 2^n built in the exponent field. ~1 ulp. Leaf; clobbers
; xmm1-xmm3, no GPR. EXP8 below is the same computation on eight lanes.
; ===========================================================================
k_exp:
  vmaxss xmm0, xmm0, [rel kc8_exp_lo]
  vminss xmm0, xmm0, [rel kc8_exp_hi]
  vmulss xmm1, xmm0, [rel kc8_log2e]
  vroundss xmm1, xmm1, xmm1, 8          ; n = nearest integer
  vfnmadd231ss xmm0, xmm1, [rel kc8_ln2_hi]   ; r = x - n*ln2
  vfnmadd231ss xmm0, xmm1, [rel kc8_ln2_lo]
  vmovss xmm2, [rel kc8_p0]
  vfmadd213ss xmm2, xmm0, [rel kc8_p1]  ; p = p*r + c
  vfmadd213ss xmm2, xmm0, [rel kc8_p2]
  vfmadd213ss xmm2, xmm0, [rel kc8_p3]
  vfmadd213ss xmm2, xmm0, [rel kc8_p4]
  vfmadd213ss xmm2, xmm0, [rel kc8_p5]
  vmulss xmm3, xmm0, xmm0               ; r^2
  vfmadd213ss xmm2, xmm3, xmm0          ; p*r^2 + r
  vaddss xmm2, xmm2, [rel kc8_one]      ; e^r
  vcvtps2dq xmm1, xmm1                  ; n (lane 0)
  vpaddd xmm1, xmm1, [rel kc8_127i]
  vpslld xmm1, xmm1, 23                 ; 2^n
  vmulss xmm0, xmm2, xmm1
  ret

%macro EXP8 0                 ; ymm0 = e^ymm0 on 8 lanes; clobbers ymm1-ymm3
  vmaxps  ymm0, ymm0, [rel kc8_exp_lo]
  vminps  ymm0, ymm0, [rel kc8_exp_hi]
  vmulps  ymm1, ymm0, [rel kc8_log2e]
  vroundps ymm1, ymm1, 8
  vfnmadd231ps ymm0, ymm1, [rel kc8_ln2_hi]
  vfnmadd231ps ymm0, ymm1, [rel kc8_ln2_lo]
  vmovaps ymm2, [rel kc8_p0]
  vfmadd213ps ymm2, ymm0, [rel kc8_p1]
  vfmadd213ps ymm2, ymm0, [rel kc8_p2]
  vfmadd213ps ymm2, ymm0, [rel kc8_p3]
  vfmadd213ps ymm2, ymm0, [rel kc8_p4]
  vfmadd213ps ymm2, ymm0, [rel kc8_p5]
  vmulps  ymm3, ymm0, ymm0
  vfmadd213ps ymm2, ymm3, ymm0
  vaddps  ymm2, ymm2, [rel kc8_one]
  vcvtps2dq ymm1, ymm1
  vpaddd  ymm1, ymm1, [rel kc8_127i]
  vpslld  ymm1, ymm1, 23
  vmulps  ymm0, ymm2, ymm1
%endmacro

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
; Eight lanes per step with EXP8 (n is a multiple of 8: n_ff). Leaf; ymm0-4.
; ===========================================================================
k_silu_mul:
  xor  r10, r10
.l:
  cmp  r10, r8
  jae  .done
  vmovups ymm4, [rcx + r10*4]            ; z
  vxorps  ymm0, ymm4, [rel kc8_signmask] ; -z
  EXP8                                   ; e^-z
  vaddps  ymm0, ymm0, [rel kc8_one]      ; 1+e^-z
  vdivps  ymm0, ymm4, ymm0               ; z/(1+e^-z) = silu(z)
  vmulps  ymm0, ymm0, [rdx + r10*4]      ; * u
  vmovups [rcx + r10*4], ymm0
  add  r10, 8
  jmp  .l
.done:
  vzeroupper
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
; k_quant_x(rcx=x f32*, rdx=n). Quantize x into the int8 activation buffers the
; Q4_K/Q6_K matvecs read: per 256-block b, g_d8[b]=amax/127 (f32), g_q8[256b+i]=
; round(x/d8) in [-127,127] (i8), g_bsums[16b+j]=sum of 16 consecutive g_q8 (i16,
; |sum|<=2032), g_bsums32[8b+k]=g_bsums[16b+2k]+g_bsums[16b+2k+1] (i16). n%256==0.
; Leaf (no calls); AVX2 throughout (~1 instruction per value): the block max with
; two accumulators, then per 32 values four vmulps/vcvtps2dq, two vpackssdw to
; i16 (which also feed the sums: vpmaddwd by ones + vphaddd), one vpacksswb to i8
; and one vpermd undoing the packs' lane interleave. Rounding is the MXCSR
; default (nearest even) in both this and the scalar version it replaced, so the
; q8 bytes are identical. Uses rax, rcx, rdx, r8-r11 and ymm0-5 only.
; ---------------------------------------------------------------------------
k_quant_x:
  mov  r9, rdx
  shr  r9, 8                    ; nblocks = n/256 (>= 1)
  mov  r8, [rel g_q8]           ; q8 cursor
  mov  r10, [rel g_d8]          ; d8 cursor
  mov  r11, [rel g_bsums]       ; bsums cursor (i16 per 16)
  mov  rax, [rel g_bsums32]     ; bsums32 cursor (i16 per 32)
  vmovdqa ymm4, [rel kc8_perm_q8]
.blk:
  ; amax = max |x| over the block (two accumulators, 64 bytes per step)
  vmovaps ymm5, [rel kc8_absmask]
  vxorps ymm0, ymm0, ymm0
  vxorps ymm1, ymm1, ymm1
  xor  rdx, rdx
.amax:
  vandps ymm2, ymm5, [rcx + rdx]
  vmaxps ymm0, ymm0, ymm2
  vandps ymm3, ymm5, [rcx + rdx + 32]
  vmaxps ymm1, ymm1, ymm3
  add  rdx, 64
  cmp  rdx, 1024
  jb   .amax
  vmaxps ymm0, ymm0, ymm1
  vextractf128 xmm1, ymm0, 1
  vmaxps xmm0, xmm0, xmm1
  vpermilps xmm1, xmm0, 0x4E
  vmaxps xmm0, xmm0, xmm1
  vpermilps xmm1, xmm0, 0xB1
  vmaxps xmm0, xmm0, xmm1       ; amax in every lane
  vdivss xmm1, xmm0, [rel kc8_127f]
  vmovss [r10], xmm1            ; d8 = amax/127
  vxorps xmm5, xmm5, xmm5       ; inv = 0 when amax == 0 (all-zero block)
  vucomiss xmm0, xmm5
  je   .inv_done
  vmovss xmm5, [rel kc8_127f]
  vdivss xmm5, xmm5, xmm0       ; inv = 127/amax
.inv_done:
  vbroadcastss ymm5, xmm5
  xor  rdx, rdx                 ; byte offset into the block: 128 per 32 values
.q32:
  vmulps ymm0, ymm5, [rcx + rdx]
  vmulps ymm1, ymm5, [rcx + rdx + 32]
  vcvtps2dq ymm0, ymm0
  vcvtps2dq ymm1, ymm1
  vpackssdw ymm0, ymm0, ymm1    ; P: values 0..15 as i16 (lanes interleaved: A0-3 B0-3 | A4-7 B4-7)
  vmulps ymm1, ymm5, [rcx + rdx + 64]
  vmulps ymm2, ymm5, [rcx + rdx + 96]
  vcvtps2dq ymm1, ymm1
  vcvtps2dq ymm2, ymm2
  vpackssdw ymm1, ymm1, ymm2    ; Q: values 16..31 as i16
  ; sums: bsums[2k] = sum P, bsums[2k+1] = sum Q, bsums32[k] = both
  vpmaddwd ymm2, ymm0, [rel kc8_ones16]
  vpmaddwd ymm3, ymm1, [rel kc8_ones16]
  vphaddd ymm2, ymm2, ymm3
  vextracti128 xmm3, ymm2, 1
  vpaddd  xmm2, xmm2, xmm3      ; [sum A, sum B, sum C, sum D]
  vphaddd xmm2, xmm2, xmm2      ; [sum P, sum Q, sum P, sum Q]
  vpackssdw xmm3, xmm2, xmm2    ; as i16
  vmovd   [r11], xmm3           ; bsums[2k], bsums[2k+1]
  vphaddd xmm2, xmm2, xmm2      ; [sum P + sum Q, ...]
  vpackssdw xmm2, xmm2, xmm2
  vpextrw word [rax], xmm2, 0   ; bsums32[k]
  ; q8 bytes, in value order
  vpacksswb ymm0, ymm0, ymm1    ; dwords: A0-3 B0-3 C0-3 D0-3 | A4-7 B4-7 C4-7 D4-7
  vpermd  ymm0, ymm4, ymm0      ; -> A0-3 A4-7 B0-3 B4-7 C0-3 C4-7 D0-3 D4-7
  vmovdqu [r8], ymm0
  add  r8, 32
  add  r11, 4
  add  rax, 2
  add  rdx, 128
  cmp  rdx, 1024
  jb   .q32
  add  rcx, 1024
  add  r10, 4
  dec  r9
  jnz  .blk
  vzeroupper
  ret

; ---------------------------------------------------------------------------
; k_dot_q4k_row(rcx=w row, r8=cols) -> xmm0 = dot(row, x) over the int8 activation
; (g_q8/g_d8/g_bsums32 filled by k_quant_x). Clobbers xmm0-15, rax, rcx, rdx,
; r8-r11 — k_matvec_job saves xmm6-15 once per call, for every row it dots.
;
; Q4_K block (144 B = 256 values): f16 d, f16 dmin, u8 scales[12] (8 six-bit sc
; and 8 six-bit m), u8 qs[128]. Sub-block s (32 values) dequantises to
; d*sc[s]*q - dmin*m[s]; chunk c of qs (32 bytes) holds sub-block 2c in the low
; nibbles and 2c+1 in the high nibbles. With x ~ d8*q8 per block,
;   dot = sum_blk d8 * ( d * sum_s sc[s]*sum_{i in s} q*q8 - dmin * sum_s m[s]*bsums32[s] ).
; The inner sum is exact integer work: vpmaddubsw (u8 q x s8 q8 -> i16 pair
; sums, |.| <= 2*15*127) then vpmaddwd by the sub-block scale (i16 broadcast) into
; eight i32 lanes, summed over the block and converted once; the mins term is one
; vpmaddwd of the eight mins with the eight per-32 activation sums. Float work per
; block: two FMAs. No horizontal reduction until the row ends, no vpmulld, and no
; stack traffic: the 6-bit scales are unpacked in xmm registers (i16 lanes, so
; the shifts cannot leak between bytes) and broadcast per sub-block by vpshufb.
; ---------------------------------------------------------------------------
%macro Q4K_CHUNK 1            ; %1 = chunk c (0..3): qs[32c..32c+32), q8[64c..64c+64), sc[2c], sc[2c+1]
  vmovdqu ymm1, [rbx + 16 + 32*%1]             ; 32 packed nibble bytes (VEX shifts take no memory source)
  vpand   ymm0, ymm1, ymm15                    ; low nibbles  = sub-block 2c   (u8 0..15)
  vpsrlw  ymm1, ymm1, 4
  vpand   ymm1, ymm1, ymm15                    ; high nibbles = sub-block 2c+1
  vpmaddubsw ymm0, ymm0, [r8 + 64*%1]          ; i16 pair sums of q*q8
  vpmaddubsw ymm1, ymm1, [r8 + 64*%1 + 32]
  vpshufb ymm2, ymm9, [rel kc_q4_scmask + 64*%1]      ; sc[2c]   as i16 in every lane
  vpshufb ymm3, ymm9, [rel kc_q4_scmask + 64*%1 + 32] ; sc[2c+1]
  vpmaddwd ymm0, ymm0, ymm2                    ; i32: sc * (pair sums), pairs summed
  vpmaddwd ymm1, ymm1, ymm3
  vpaddd  ymm0, ymm0, ymm1
  vpaddd  ymm8, ymm8, ymm0
%endmacro

k_dot_q4k_row:
  push rbx
  push rsi
  push rbp
  push r13                      ; leaf: no calls, no locals
  mov  rbx, rcx                 ; block cursor
  mov  r13, r8
  shr  r13, 8                   ; nblocks = cols/256 (>= 1)
  mov  r8, [rel g_q8]           ; q8 cursor (+256 per block)
  mov  rsi, [rel g_bsums32]     ; i16 per-32 sums cursor (+16 bytes per block)
  mov  rbp, [rel g_d8]          ; f32 activation scale cursor (+4 per block)
  vmovdqa ymm15, [rel kc_0f32]
  vxorps ymm13, ymm13, ymm13    ; f32 accumulator, 8 lanes (scale terms)
  vxorps xmm12, xmm12, xmm12    ; f32 accumulator, 4 lanes (min terms, subtracted)
.blk:
  ; scales[12] -> sc[0..7] (i16) in both lanes of ymm9, m[0..7] (i16) in xmm11.
  ; ggml get_scale_min_k4: j<4: sc=b[j]&63, m=b[j+4]&63; j>=4: sc=(b[j+4]&15)|
  ; ((b[j-4]>>6)<<4), m=(b[j+4]>>4)|((b[j]>>6)<<4).
  vpmovzxbw xmm0, [rbx+4]       ; b0..b7 as u16
  vpmovzxbw xmm1, [rbx+12]      ; b8..b11 as u16 (lanes 4-7: qs bytes, unused)
  vpand   xmm2, xmm0, [rel kc_63w]   ; [sc0..3, m0..3]
  vpsrlw  xmm0, xmm0, 6
  vpsllw  xmm0, xmm0, 4              ; (b>>6)<<4: the high two bits of [sc4..7, m4..7]
  vpand   xmm3, xmm1, [rel kc_15w]   ; b8..11 & 15: low four bits of sc4..7
  vpsrlw  xmm1, xmm1, 4              ; b8..11 >> 4: low four bits of m4..7
  vpunpcklqdq xmm3, xmm3, xmm1       ; [sc4..7 low, m4..7 low]
  vpor    xmm3, xmm3, xmm0           ; [sc4..7, m4..7]
  vpunpcklqdq xmm9, xmm2, xmm3       ; sc[0..7]
  vpunpckhqdq xmm11, xmm2, xmm3      ; m[0..7]
  vinserti128 ymm9, ymm9, xmm9, 1    ; sc in both lanes for the vpshufb broadcasts
  ; mins: acc4 -= (dmin*d8) * sum_s m[s]*bsums32[s]
  vpmaddwd xmm11, xmm11, [rsi]  ; 4 x i32 pair sums of m*bsums32
  vcvtdq2ps xmm11, xmm11
  vbroadcastss ymm10, [rbp]     ; d8 (this block's activation scale) in 8 lanes
  vpbroadcastw xmm0, [rbx+2]    ; dmin (f16)   (xmm0: ymm9 holds the scales)
  vcvtph2ps xmm0, xmm0
  vmulps  xmm0, xmm0, xmm10     ; dmin*d8
  vfnmadd231ps xmm12, xmm11, xmm0
  ; scales: acc8 = sum_s sc[s] * sum_{i in s} q*q8   (integer, exact)
  vpxor   ymm8, ymm8, ymm8
  Q4K_CHUNK 0
  Q4K_CHUNK 1
  Q4K_CHUNK 2
  Q4K_CHUNK 3
  vcvtdq2ps ymm8, ymm8
  vpbroadcastw xmm0, [rbx]      ; d (f16)
  vcvtph2ps ymm0, xmm0
  vmulps  ymm0, ymm0, ymm10     ; d*d8
  vfmadd231ps ymm13, ymm8, ymm0
  add  rbx, Q4K_BYTES
  add  r8, 256
  add  rsi, 16
  add  rbp, 4
  dec  r13
  jnz  .blk
  ; dot = hsum(acc8) + hsum(acc4)
  vextractf128 xmm1, ymm13, 1
  vaddps  xmm0, xmm13, xmm1
  vaddps  xmm0, xmm0, xmm12
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  vzeroupper
  pop  r13
  pop  rbp
  pop  rsi
  pop  rbx
  ret

; ---------------------------------------------------------------------------
; k_dot_q6k_row(rcx=w row, r8=cols) -> xmm0. Same contract as k_dot_q4k_row
; (reads g_q8/g_d8/g_bsums; clobbers xmm0-15 and the volatile GPRs).
;
; Q6_K block (210 B = 256 values): u8 ql[128] (low 4 bits), u8 qh[64] (high 2
; bits), i8 scales[16], f16 d. Sub-block s (16 values) dequantises to
; d*sc[s]*(q-32). Each 128-value half h uses ql[64h..64h+64), qh[32h..32h+32),
; sc[8h..8h+8): group g (values 32g.. of the half) = (ql slice & 0xF, or >> 4)
; | (two bits of qh << 4), so each 32-byte group vector holds sub-block 8h+2g in
; its low 128 bits and 8h+2g+1 in its high 128 bits. With x ~ d8*q8:
;   dot = sum_blk d8*d * ( sum_s sc[s]*sum_{i in s} q*q8 - 32*sum_s sc[s]*bsums[s] ).
; vpmaddubsw (|.| <= 2*63*127) then vpmaddwd by [sc[2g] x8 | sc[2g+1] x8] into
; eight i32 lanes; the -32 term is one vpmaddwd of the 16 scales with the 16
; per-16 activation sums. The per-group scale pairs are vpshufb broadcasts from
; a register holding the half's eight scales in both lanes (no stack traffic).
; ---------------------------------------------------------------------------
%macro Q6K_HALF 4             ; %1 ql offset, %2 qh offset, %3 q8 offset, %4 ymm with [sc8h..8h+7 | same]
  vmovdqu ymm0, [rbx + %2]                     ; qh
  vpsllw  ymm1, ymm0, 4
  vpand   ymm1, ymm1, ymm14                    ; h0 = (qh & 3) << 4
  vpsllw  ymm2, ymm0, 2
  vpand   ymm2, ymm2, ymm14                    ; h1 = ((qh >> 2) & 3) << 4
  vpand   ymm3, ymm0, ymm14                    ; h2 = ((qh >> 4) & 3) << 4
  vpsrlw  ymm0, ymm0, 2
  vpand   ymm0, ymm0, ymm14                    ; h3 = ((qh >> 6) & 3) << 4
  vmovdqu ymm6, [rbx + %1]                     ; ql[0..31]  (VEX shifts take no memory source)
  vmovdqu ymm7, [rbx + %1 + 32]                ; ql[32..63]
  vpand   ymm4, ymm6, ymm15
  vpor    ymm4, ymm4, ymm1                     ; g0 = (ql[0..31] & 0xF) | h0
  vpand   ymm5, ymm7, ymm15
  vpor    ymm5, ymm5, ymm2                     ; g1 = (ql[32..63] & 0xF) | h1
  vpsrlw  ymm6, ymm6, 4
  vpand   ymm6, ymm6, ymm15
  vpor    ymm6, ymm6, ymm3                     ; g2 = (ql[0..31] >> 4) | h2
  vpsrlw  ymm7, ymm7, 4
  vpand   ymm7, ymm7, ymm15
  vpor    ymm7, ymm7, ymm0                     ; g3 = (ql[32..63] >> 4) | h3
  vpmaddubsw ymm4, ymm4, [r8 + %3]             ; i16 pair sums of q*q8
  vpmaddubsw ymm5, ymm5, [r8 + %3 + 32]
  vpmaddubsw ymm6, ymm6, [r8 + %3 + 64]
  vpmaddubsw ymm7, ymm7, [r8 + %3 + 96]
  vpshufb ymm0, %4, [rel kc_q6_scmask]         ; [sc[8h+0] x8 | sc[8h+1] x8]
  vpshufb ymm1, %4, [rel kc_q6_scmask + 32]    ; [sc2 | sc3]
  vpshufb ymm2, %4, [rel kc_q6_scmask + 64]    ; [sc4 | sc5]
  vpshufb ymm3, %4, [rel kc_q6_scmask + 96]    ; [sc6 | sc7]
  vpmaddwd ymm4, ymm4, ymm0
  vpmaddwd ymm5, ymm5, ymm1
  vpmaddwd ymm6, ymm6, ymm2
  vpmaddwd ymm7, ymm7, ymm3
  vpaddd  ymm4, ymm4, ymm5
  vpaddd  ymm6, ymm6, ymm7
  vpaddd  ymm8, ymm8, ymm4
  vpaddd  ymm8, ymm8, ymm6
%endmacro

k_dot_q6k_row:
  push rbx
  push rsi
  push rbp
  push r13                      ; leaf: no calls, no locals
  mov  rbx, rcx                 ; block cursor
  mov  r13, r8
  shr  r13, 8                   ; nblocks (>= 1)
  mov  r8, [rel g_q8]           ; q8 cursor (+256 per block)
  mov  rsi, [rel g_bsums]       ; i16 per-16 sums cursor (+32 bytes per block)
  mov  rbp, [rel g_d8]          ; f32 activation scale cursor
  vmovdqa ymm15, [rel kc_0f32]
  vmovdqa ymm14, [rel kc_3032]
  vxorps ymm13, ymm13, ymm13    ; f32 accumulator
.blk:
  vpmovsxbw ymm11, [rbx + 192]  ; sc[0..15] as i16
  vinserti128 ymm10, ymm11, xmm11, 1 ; [sc0..7 | sc0..7]   (half 0's vpshufb source)
  vpermq  ymm9, ymm11, 0xEE     ; [sc8..15 | sc8..15] (half 1's)
  vpmaddwd ymm12, ymm11, [rsi]  ; 8 x i32 pair sums of sc*bsums
  vpslld  ymm12, ymm12, 5       ; * 32
  vpxor   ymm8, ymm8, ymm8      ; i32 accumulator
  Q6K_HALF 0, 128, 0, ymm10
  Q6K_HALF 64, 160, 128, ymm9
  vpsubd  ymm8, ymm8, ymm12     ; sum sc*q*q8 - 32*sum sc*bsums
  vcvtdq2ps ymm8, ymm8
  vpbroadcastw xmm0, [rbx + 208] ; d (f16)
  vcvtph2ps ymm0, xmm0
  vbroadcastss ymm1, [rbp]      ; d8
  vmulps  ymm0, ymm0, ymm1
  vfmadd231ps ymm13, ymm8, ymm0
  add  rbx, Q6K_BYTES
  add  r8, 256
  add  rsi, 32
  add  rbp, 4
  dec  r13
  jnz  .blk
  vextractf128 xmm1, ymm13, 1
  vaddps  xmm0, xmm13, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  vzeroupper
  pop  r13
  pop  rbp
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; k_matvec_job(rcx=MatvecJob*, rdx=tid, r8=nthreads) — pool body.
; out[r] = dot(row r of w, x). Rows are handed out dynamically: each thread claims
; job.chunk consecutive rows at a time from job.next (lock xadd) until the rows
; run out, so a thread slowed by an interrupt or a lower clock takes fewer rows
; and the matvec ends when the work does, not when the slowest static slice does.
; Chunks are large (a thread's share / 8) so each stays one long sequential stream
; for the L2 prefetcher.
; Dispatches on w.type; the quantised dots read the int8 activation that
; k_quant_x prepared for this x.
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
  sub  rsp, 216                 ; 32 shadow + 24 locals + 160 xmm6-15 save; %16: 8-8=0
  mov  r14, rcx                 ; job
  vmovdqu [rsp+56], xmm6        ; the quantised dot kernels use ymm0-15 freely; the
  vmovdqu [rsp+72], xmm7        ; low halves of xmm6-15 are callee-saved, so keep
  vmovdqu [rsp+88], xmm8        ; them here once per job instead of once per row
  vmovdqu [rsp+104], xmm9
  vmovdqu [rsp+120], xmm10
  vmovdqu [rsp+136], xmm11
  vmovdqu [rsp+152], xmm12
  vmovdqu [rsp+168], xmm13
  vmovdqu [rsp+184], xmm14
  vmovdqu [rsp+200], xmm15
  mov  [rsp+32], rdx            ; tid
  mov  [rsp+40], r8             ; nthreads
  mov  r12, [r14 + MatvecJob.x]
  mov  r13, [r14 + MatvecJob.out]
  mov  rbx, [r14 + MatvecJob.w] ; Tensor*
  mov  r15, [r14 + MatvecJob.cols]
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
  ; claim the next chunk of rows: [rsi, rdi) = [old next, min(old next + CHUNK, rows))
.grab:
  mov  rax, [r14 + MatvecJob.chunk]
  lock xadd [r14 + MatvecJob.next], rax
  mov  rsi, rax
  cmp  rsi, [r14 + MatvecJob.rows]
  jae  .done                    ; no rows left
  mov  rdi, rsi
  add  rdi, [r14 + MatvecJob.chunk]
  cmp  rdi, [r14 + MatvecJob.rows]
  jbe  .rowloop
  mov  rdi, [r14 + MatvecJob.rows]
  ; loop r in [rsi, rdi): rowptr = w.data + r*rbp (data base reloaded each row
  ; because the dot helpers clobber the volatile regs).
.rowloop:
  cmp  rsi, rdi
  jae  .grab
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
  call k_dot_q4k_row
  jmp  .store
.d_q6:
  call k_dot_q6k_row
.store:
  movss [r13 + rsi*4], xmm0
  ; cols r15, stride rbp, w rbx, x r12, out r13, chunk end rdi survive the dot calls
  inc  rsi
  jmp  .rowloop
.done:
  vmovdqu xmm6, [rsp+56]
  vmovdqu xmm7, [rsp+72]
  vmovdqu xmm8, [rsp+88]
  vmovdqu xmm9, [rsp+104]
  vmovdqu xmm10, [rsp+120]
  vmovdqu xmm11, [rsp+136]
  vmovdqu xmm12, [rsp+152]
  vmovdqu xmm13, [rsp+168]
  vmovdqu xmm14, [rsp+184]
  vmovdqu xmm15, [rsp+200]
  add  rsp, 216
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
; KERNELS_BENCH_ITERS matvecs of the two largest weights — the Q6_K output
; projection and the Q4_K token embedding (same shape; used here only as a big
; Q4_K matrix) — and report rows, cols, type, ms and effective GB/s (= weight
; bytes read / time) for each. Reuses the real model_matvec path (thread pool,
; activation quantisation, dot kernels). The model is already loaded, so the
; activation buffers, g_mvjob and the pool are ready.
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

; kernels_bench(rcx=Tensor*, rdx=iters): one warm matvec, then `iters` timed ones,
; then one report line, then three check lines comparing the kernel's result for
; rows 0, rows/2 and rows-1 with a plain f32 dot of the dequantised row (the
; activation x[*]=1.0 is exactly representable in int8, so they should agree to
; float rounding).
kernels_bench:
  push rbx
  push rsi
  push rdi
  push rbp
  push r12
  push r13
  push r14
  push r15                      ; 8 pushes; entry %16==8 -> %16==8
  sub  rsp, 72                  ; 32 shadow + [32]=iters [40]=t0/ns [48]=bytes [56]=dequant buf [64]=ref; %16==0
  mov  rsi, rcx                 ; Tensor*
  mov  [rsp+32], rdx            ; iters
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
  mov  rcx, r14
  mov  rdx, r12
  call model_fill_sums          ; quantise x
  ; warm
  mov  rcx, rsi
  mov  rdx, r14
  mov  r8, r15
  call model_matvec
  ; timed
  call rt_now_ns
  mov  [rsp+40], rax            ; t0
  mov  rbp, [rsp+32]
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
  sub  rax, [rsp+40]            ; elapsed ns
  mov  [rsp+40], rax            ; ns
  mov  rax, [rsi + Tensor.nbytes]
  imul rax, [rsp+32]
  mov  [rsp+48], rax            ; bytes read = nbytes * iters
  ; print: head rows cols type iters ms gb_s
  lea  rcx, [rel kc_s_head]
  call rt_err_str
  mov  rcx, r13
  call rt_err_u64
  lea  rcx, [rel kc_s_cols]
  call rt_err_str
  mov  rcx, r12
  call rt_err_u64
  lea  rcx, [rel kc_s_type]
  call rt_err_str
  mov  ecx, [rsi + Tensor.type]
  call rt_err_u64
  lea  rcx, [rel kc_s_iters]
  call rt_err_str
  mov  rcx, [rsp+32]
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
  ; check rows 0, rows/2, rows-1 against the f32 dequantised dot
  mov  rcx, r12
  shl  rcx, 2
  call rt_alloc
  mov  [rsp+56], rax            ; f32[cols] dequant scratch
  xor  rdi, rdi                 ; check index 0..2
.chk:
  cmp  rdi, 3
  jae  .chk_done
  xor  rbp, rbp                 ; row 0
  cmp  rdi, 1
  jne  .chk_r2
  mov  rbp, r13
  shr  rbp, 1                   ; rows/2
  jmp  .chk_go
.chk_r2:
  cmp  rdi, 2
  jne  .chk_go
  mov  rbp, r13
  dec  rbp                      ; rows-1
.chk_go:
  mov  rcx, rsi
  mov  rdx, rbp
  mov  r8, [rsp+56]
  call model_dequant_row
  mov  rcx, [rsp+56]
  mov  rdx, r14
  mov  r8, r12
  call k_dot_f32_row            ; xmm0 = reference dot
  movss [rsp+64], xmm0
  lea  rcx, [rel kc_s_chk]
  call rt_err_str
  mov  rcx, rbp
  call rt_err_u64
  lea  rcx, [rel kc_s_kern]
  call rt_err_str
  movss xmm0, [r15 + rbp*4]
  call rt_err_f32
  lea  rcx, [rel kc_s_ref]
  call rt_err_str
  movss xmm0, [rsp+64]
  call rt_err_f32
  lea  rcx, [rel kc_s_nl]
  mov  rdx, 1
  call rt_err
  inc  rdi
  jmp  .chk
.chk_done:
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

k_pool_noop:                       ; empty pool body for the dispatch-cost measurement
  ret

kernels_cli:
  push rbx                      ; entry %16==8 -> 0
  sub  rsp, 48                  ; shadow + [32]=t0; %16==0
  mov  rbx, rcx                 ; Model*
  mov  rcx, [rbx + Model.output]   ; Q6_K lm head (255 MB: streams from DRAM)
  mov  rdx, KERNELS_BENCH_ITERS
  call kernels_bench
  mov  rcx, [rbx + Model.tok_embd] ; Q4_K, same shape (175 MB: DRAM)
  mov  rdx, KERNELS_BENCH_ITERS
  call kernels_bench
  mov  rcx, [rbx + Model.layers]
  mov  rcx, [rcx + Layer.attn_k]   ; Q4_K 1024x2048 = 1.2 MB: stays in L2, so this is the kernel's compute rate
  mov  rdx, KERNELS_BENCH_ITERS*100
  call kernels_bench
  ; pool dispatch cost: time empty jobs
  call rt_now_ns
  mov  [rsp+32], rax
  mov  ebx, KERNELS_BENCH_ITERS*100
.pool:
  lea  rcx, [rel k_pool_noop]
  xor  edx, edx
  call pool_run
  dec  ebx
  jnz  .pool
  call rt_now_ns
  sub  rax, [rsp+32]
  mov  [rsp+32], rax
  lea  rcx, [rel kc_s_pool]
  call rt_err_str
  cvtsi2ss xmm0, qword [rsp+32]
  mov  eax, KERNELS_BENCH_ITERS*100
  cvtsi2ss xmm1, eax
  divss xmm0, xmm1              ; ns per pool_run
  call rt_err_f32
  lea  rcx, [rel kc_s_nl]
  mov  rdx, 1
  call rt_err
  xor  eax, eax
  add  rsp, 48
  pop  rbx
  ret

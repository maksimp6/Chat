; q35k.asm — the kernels Qwen3.5's layers need beyond kernels.asm, for one
; token at a time (decode). Everything is f32, AVX2 + FMA. The equations are
; those of transformers' modeling_qwen3_5.py (torch_recurrent_gated_delta_rule,
; causal_conv1d_update, Qwen3_5RMSNormGated, Qwen3_5Attention) exactly.
;
;   q35_rmsnorm1(rcx=out, rdx=in, r8=w, r9=n, xmm0=eps)   out = in * rstd * (1 + w)
;   q35_conv_step(rcx=UnConvStep*)   the depthwise causal conv (kernel 4) over the
;                                    kept 3 inputs + the new one, SiLU, state shift
;   q35_l2norm(rcx=x, rdx=n)         x /= sqrt(sum x^2 + 1e-6)
;   q35_delta_step(rcx=Q35Delta*)    one head: S *= e^g; d = (v - S^T k) beta;
;                                    S += k d^T; out = S^T q
;   q35_gated_norm(rcx=out, rdx=in, r8=w, r9=z, [5]=n)  out = rmsnorm(in) w silu(z)
;   q35_rope_partial(rcx=head, rdx=cos/sin row, r8=rot)  neox rotation of the first
;                                    rot dims (pairs i, i+rot/2)
;   q35_sigmoid_mul(rcx=x, rdx=gate, r8=n)   x *= sigmoid(gate)
;   q35_softplus(xmm0) -> xmm0       log(1 + e^x), the x87-free way (EXP8 + k_log)
;
; %included into asmrun.asm (bits 64, default rel) after sdk.asm.

section .text

struc Q35Conv
  .state:  resq 1       ; f32 [c][3]: the three previous inputs of every channel
  .x:      resq 1       ; f32 [c]: this token's input (overwritten with the output)
  .w:      resq 1       ; f32 [c][4]: the taps, oldest first (torch conv1d weight [c][1][4])
  .c:      resq 1       ; the end channel (exclusive)
  .c0:     resq 1       ; the first channel: a pool thread's share is [c0, c)
endstruc

struc Q35Delta
  .S:      resq 1       ; f32 [dk][dv] the head's state
  .q:      resq 1       ; f32 [dk]
  .k:      resq 1       ; f32 [dk]
  .v:      resq 1       ; f32 [dv]
  .out:    resq 1       ; f32 [dv]
  .dk:     resq 1
  .dv:     resq 1
  .g:      resd 1       ; f32 log decay (<= 0)
  .beta:   resd 1       ; f32 in (0, 1)
  .mem:    resq 1       ; f32 [dv] scratch: S^T k (the caller's, one per thread)
  .d:      resq 1       ; f32 [dv] scratch: (v - mem) beta
endstruc

; q35_rmsnorm1(rcx=out, rdx=in, r8=w f32, r9=n, xmm0=eps) — Qwen3.5's RMSNorm:
; out = in / sqrt(mean(in^2) + eps) * (1 + w). n a multiple of 8. Leaf; clobbers
; rax, ymm0-ymm5.
q35_rmsnorm1:
  vmovss xmm5, xmm0, xmm0      ; eps
  vxorps ymm0, ymm0, ymm0
  xor  eax, eax
.s:
  vmovups ymm1, [rdx + rax*4]
  vfmadd231ps ymm0, ymm1, ymm1
  add  rax, 8
  cmp  rax, r9
  jb   .s
  vextractf128 xmm1, ymm0, 1
  vaddps xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  vcvtsi2ss xmm1, xmm1, r9
  vdivss xmm0, xmm0, xmm1
  vaddss xmm0, xmm0, xmm5
  vsqrtss xmm0, xmm0, xmm0
  vmovss xmm1, [rel kc_one]
  vdivss xmm0, xmm1, xmm0
  vbroadcastss ymm3, xmm0      ; rstd
  vbroadcastss ymm4, xmm1      ; 1.0
  xor  eax, eax
.o:
  vmovups ymm1, [rdx + rax*4]
  vmulps ymm1, ymm1, ymm3
  vaddps ymm2, ymm4, [r8 + rax*4]
  vmulps ymm1, ymm1, ymm2
  vmovups [rcx + rax*4], ymm1
  add  rax, 8
  cmp  rax, r9
  jb   .o
  ret

; q35_conv_step(rcx=Q35Conv*) — causal_conv1d_update for one token: for every
; channel c, y = silu(w[c][0] s0 + w[c][1] s1 + w[c][2] s2 + w[c][3] x), then
; the state shifts (s0 = s1, s1 = s2, s2 = x) and x = y. The taps and state are
; stored per channel in groups of 4 / 3 floats, so this is a scalar loop over
; the channels [c0, c). Leaf; clobbers rax, rdx, r8-r11, xmm0-xmm5.
q35_conv_step:
  mov  r8, [rcx + Q35Conv.state]
  mov  r9, [rcx + Q35Conv.x]
  mov  r10, [rcx + Q35Conv.w]
  mov  rax, [rcx + Q35Conv.c0]
.c:
  cmp  rax, [rcx + Q35Conv.c]
  jae  .d
  lea  rdx, [rax + rax*2]      ; state index c*3
  mov  r11, rax
  shl  r11, 4
  add  r11, r10                ; the channel's four taps
  vmovss xmm0, [r9 + rax*4]    ; x
  vmovss xmm1, [r11 + 12]
  vmulss xmm1, xmm1, xmm0      ; w3 x
  vmovss xmm2, [r8 + rdx*4]
  vfmadd231ss xmm1, xmm2, [r11]        ; + w0 s0
  vmovss xmm3, [r8 + rdx*4 + 4]
  vfmadd231ss xmm1, xmm3, [r11 + 4]    ; + w1 s1
  vmovss xmm4, [r8 + rdx*4 + 8]
  vfmadd231ss xmm1, xmm4, [r11 + 8]    ; + w2 s2
  vmovss [r8 + rdx*4], xmm3                   ; shift
  vmovss [r8 + rdx*4 + 4], xmm4
  vmovss [r8 + rdx*4 + 8], xmm0
  ; silu: y / (1 + e^-y)   (EXP8 clobbers ymm1-ymm3: y waits in xmm5)
  vmovss xmm5, xmm1, xmm1
  vxorps xmm0, xmm1, [rel kc8_signmask]
  vbroadcastss ymm0, xmm0
  EXP8
  vaddss xmm0, xmm0, [rel kc_one]
  vdivss xmm5, xmm5, xmm0
  vmovss [r9 + rax*4], xmm5
  inc  rax
  jmp  .c
.d:
  ret

; q35_l2norm(rcx=x, rdx=n) — x *= 1 / sqrt(sum x^2 + 1e-6), n a multiple of 8.
; Leaf; clobbers rax, ymm0-ymm2.
q35_l2norm:
  vxorps ymm0, ymm0, ymm0
  xor  eax, eax
.s:
  vmovups ymm1, [rcx + rax*4]
  vfmadd231ps ymm0, ymm1, ymm1
  add  rax, 8
  cmp  rax, rdx
  jb   .s
  vextractf128 xmm1, ymm0, 1
  vaddps xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  vaddss xmm0, xmm0, [rel q35_eps6]
  vsqrtss xmm0, xmm0, xmm0
  vmovss xmm1, [rel kc_one]
  vdivss xmm0, xmm1, xmm0
  vbroadcastss ymm2, xmm0
  xor  eax, eax
.m:
  vmulps ymm1, ymm2, [rcx + rax*4]
  vmovups [rcx + rax*4], ymm1
  add  rax, 8
  cmp  rax, rdx
  jb   .m
  ret

; q35_delta_step(rcx=Q35Delta*) — the gated delta rule for one token and one
; head, S [dk][dv] row-major (S[i][j] at S + (i*dv + j)*4):
;   S *= e^g
;   mem[j]  = sum_i S[i][j] k[i]
;   d[j]    = (v[j] - mem[j]) beta
;   S[i][j] += k[i] d[j]
;   out[j]  = sum_i S[i][j] q[i]
; The decay, the memory read, the update and the output read are fused into one
; pass over S per row i: dv a multiple of 8. mem and d are the caller's scratch
; (Q35Delta.mem / .d: one pair per pool thread).
; Non-leaf frame (none called); clobbers rax, rdx, r8-r11, ymm0-ymm7.
q35_delta_step:
  push rbx
  push rsi
  push rdi
  mov  rbx, rcx
  mov  rsi, [rbx + Q35Delta.S]
  mov  r8, [rbx + Q35Delta.dk]
  mov  r9, [rbx + Q35Delta.dv]
  ; e^g
  vbroadcastss ymm0, [rbx + Q35Delta.g]
  EXP8
  vmovaps ymm7, ymm0           ; decay, all lanes
  ; pass 1: S *= decay; mem = S^T k
  mov  rdi, [rbx + Q35Delta.mem]
  xor  eax, eax
.z:
  vxorps ymm0, ymm0, ymm0
  vmovups [rdi + rax*4], ymm0
  add  rax, 8
  cmp  rax, r9
  jb   .z
  xor  r10d, r10d              ; i
  mov  rdx, rsi                ; row pointer
.p1:
  cmp  r10, r8
  jae  .p1d
  mov  rax, [rbx + Q35Delta.k]
  vbroadcastss ymm6, [rax + r10*4] ; k[i]
  xor  eax, eax
.p1j:
  vmulps ymm0, ymm7, [rdx + rax*4] ; decayed S[i][j..]
  vmovups [rdx + rax*4], ymm0
  vmovups ymm1, [rdi + rax*4]
  vfmadd231ps ymm1, ymm0, ymm6 ; mem += S[i][j] k[i]
  vmovups [rdi + rax*4], ymm1
  add  rax, 8
  cmp  rax, r9
  jb   .p1j
  lea  rdx, [rdx + r9*4]
  inc  r10
  jmp  .p1
.p1d:
  ; d = (v - mem) beta
  vbroadcastss ymm6, [rbx + Q35Delta.beta]
  mov  r11, [rbx + Q35Delta.v]
  mov  r10, [rbx + Q35Delta.d]
  xor  eax, eax
.dd:
  vmovups ymm0, [r11 + rax*4]
  vsubps ymm0, ymm0, [rdi + rax*4]
  vmulps ymm0, ymm0, ymm6
  vmovups [r10 + rax*4], ymm0
  add  rax, 8
  cmp  rax, r9
  jb   .dd
  ; pass 2: S += k d^T; out = S^T q
  mov  rdi, [rbx + Q35Delta.out]
  xor  eax, eax
.z2:
  vxorps ymm0, ymm0, ymm0
  vmovups [rdi + rax*4], ymm0
  add  rax, 8
  cmp  rax, r9
  jb   .z2
  xor  r11d, r11d              ; i
  mov  rdx, rsi
.p2:
  cmp  r11, r8
  jae  .p2d
  mov  rax, [rbx + Q35Delta.k]
  vbroadcastss ymm6, [rax + r11*4] ; k[i]
  mov  rax, [rbx + Q35Delta.q]
  vbroadcastss ymm7, [rax + r11*4] ; q[i]
  xor  eax, eax
.p2j:
  vmovups ymm0, [rdx + rax*4]
  vfmadd231ps ymm0, ymm6, [r10 + rax*4] ; S[i][j] += k[i] d[j]
  vmovups [rdx + rax*4], ymm0
  vmovups ymm1, [rdi + rax*4]
  vfmadd231ps ymm1, ymm0, ymm7 ; out[j] += S[i][j] q[i]
  vmovups [rdi + rax*4], ymm1
  add  rax, 8
  cmp  rax, r9
  jb   .p2j
  lea  rdx, [rdx + r9*4]
  inc  r11
  jmp  .p2
.p2d:
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; q35_gated_norm(rcx=out, rdx=in, r8=w f32, r9=z, [rsp+40]=n) — RMSNormGated:
; out = in rstd w silu(z), eps 1e-6, n a multiple of 8. Leaf; clobbers rax,
; r10, ymm0-ymm6.
q35_gated_norm:
  mov  r10, [rsp+40]
  vxorps ymm0, ymm0, ymm0
  xor  eax, eax
.s:
  vmovups ymm1, [rdx + rax*4]
  vfmadd231ps ymm0, ymm1, ymm1
  add  rax, 8
  cmp  rax, r10
  jb   .s
  vextractf128 xmm1, ymm0, 1
  vaddps xmm0, xmm0, xmm1
  vhaddps xmm0, xmm0, xmm0
  vhaddps xmm0, xmm0, xmm0
  vcvtsi2ss xmm1, xmm1, r10
  vdivss xmm0, xmm0, xmm1
  vaddss xmm0, xmm0, [rel q35_eps6]
  vsqrtss xmm0, xmm0, xmm0
  vmovss xmm1, [rel kc_one]
  vdivss xmm0, xmm1, xmm0
  vbroadcastss ymm6, xmm0      ; rstd
  xor  eax, eax
.o:
  vmovups ymm4, [rdx + rax*4]
  vmulps ymm4, ymm4, ymm6
  vmulps ymm4, ymm4, [r8 + rax*4]
  vmovups ymm5, [r9 + rax*4]   ; z
  vxorps ymm0, ymm5, [rel kc8_signmask]
  EXP8
  vaddps ymm0, ymm0, [rel kc8_one]
  vdivps ymm5, ymm5, ymm0      ; silu(z)
  vmulps ymm4, ymm4, ymm5
  vmovups [rcx + rax*4], ymm4
  add  rax, 8
  cmp  rax, r10
  jb   .o
  ret

; q35_rope_partial(rcx=head f32, rdx=table row f32 [cos[rot/2] | sin[rot/2]],
; r8=rot) — rotate the first rot values of the head, pairs (i, i + rot/2):
; a' = a cos - b sin, b' = b cos + a sin. rot/2 a multiple of 8. Leaf;
; clobbers rax, r9, ymm0-ymm5.
q35_rope_partial:
  mov  r9, r8
  shr  r9, 1                   ; half
  xor  eax, eax
.l:
  cmp  rax, r9
  jae  .d
  vmovups ymm0, [rcx + rax*4]           ; a
  lea  r8, [rcx + r9*4]
  vmovups ymm1, [r8 + rax*4]            ; b
  vmovups ymm2, [rdx + rax*4]           ; cos
  lea  r8, [rdx + r9*4]
  vmovups ymm3, [r8 + rax*4]            ; sin
  vmulps ymm4, ymm0, ymm2
  vfnmadd231ps ymm4, ymm1, ymm3         ; a cos - b sin
  vmulps ymm5, ymm1, ymm2
  vfmadd231ps ymm5, ymm0, ymm3          ; b cos + a sin
  vmovups [rcx + rax*4], ymm4
  lea  r8, [rcx + r9*4]
  vmovups [r8 + rax*4], ymm5
  add  rax, 8
  jmp  .l
.d:
  ret

; q35_sigmoid_mul(rcx=x, rdx=gate, r8=n) — x *= 1 / (1 + e^-gate), n a multiple
; of 8. Leaf; clobbers rax, ymm0-ymm4.
q35_sigmoid_mul:
  xor  eax, eax
.l:
  cmp  rax, r8
  jae  .d
  vmovups ymm0, [rdx + rax*4]
  vxorps ymm0, ymm0, [rel kc8_signmask]
  EXP8
  vaddps ymm0, ymm0, [rel kc8_one]
  vmovups ymm4, [rcx + rax*4]
  vdivps ymm4, ymm4, ymm0
  vmovups [rcx + rax*4], ymm4
  add  rax, 8
  jmp  .l
.d:
  ret

; q35_softplus(xmm0=x) -> xmm0 = log(1 + e^x): for x > 20 it is x; otherwise
; log1p through a 1-lane exp and the x87 fyl2x (once per head per token: 32
; per layer, cheap). Clobbers xmm0-xmm3, the x87 stack is left empty. Keeps
; its temporary on the stack, so pool threads may call it at once.
q35_softplus:
  vmovss xmm3, [rel q35_twenty]
  vcomiss xmm0, xmm3
  ja   .big
  sub  rsp, 8
  vbroadcastss ymm0, xmm0
  EXP8
  vaddss xmm0, xmm0, [rel kc_one]
  vmovss [rsp], xmm0
  vzeroupper
  fld1
  fld  dword [rsp]
  fyl2x                        ; log2(1 + e^x)
  fldln2
  fmulp st1, st0               ; ln
  fstp dword [rsp]
  vmovss xmm0, [rsp]
  add  rsp, 8
.big:
  ret

section .data
  align 32
q35_eps6:   dd 1.0e-6
q35_twenty: dd 20.0
section .text

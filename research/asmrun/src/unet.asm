; unet.asm — SD-Turbo's UNet (the Stable Diffusion 2.x UNet, as one denoising
; step at t = 999), on the SD-Turbo GGUF's weights (sd.asm's index).
;
; Activations are "padded NHWC": a buffer of (h+2) x (w+2) pixels, each pixel
; c f32 channels, the one-pixel border zero. A 3x3 convolution is then nine
; matrix multiplies (one per tap) over all pixels at once: the tap's input is
; the same buffer shifted by a constant number of pixels, and sg_matmul runs
; the rows h*(w+2)-2 at a time (the two junk columns per row land in the border
; and are zeroed afterwards). Weights are gathered into f32 [tap][cout][cin]
; once per convolution (g_un_wbuf); the linear layers read the q8_0/f16 rows
; directly through sg_matmul's pack path.
;
;   un_init()                      scratch buffers, once
;   un_arena_reset()               forget every arena allocation (per picture)
;   un_forward(rcx=latent padded [66][66][8], rdx=context [80][1024], r8=t)
;                                  -> rax = eps, padded [66][66][4]
;   un_conv3 / un_gn / un_lin / un_attn / un_res / un_st / un_down / un_upconv
;   un_aalloc / un_zero_border / un_upsample / un_concat / un_relu / un_silu
;
; %included into asmrun.asm (bits 64, default rel) after clipenc.asm.

section .text

UN_ARENA_BYTES equ 256*1024*1024
UN_WBUF_BYTES  equ 9*2560*1280*4  ; the largest convolution, f32
UN_GROUPS      equ 32
UN_TPAD        equ 80             ; context rows (77 used)
UN_HD          equ 64
UN_LAT         equ 64             ; the latent's side
UN_MAXC        equ 2560

%define ARG5(np) [rbp + 48 + 8*(np)]
%define ARG6(np) [rbp + 56 + 8*(np)]
%define ARG7(np) [rbp + 64 + 8*(np)]

struc UnConv
  .in:     resq 1
  .out:    resq 1
  .cin:    resq 1       ; channels of the in buffer (>= the weight's; 8 for the 4-channel latent)
  .cout:   resq 1       ; channels of the out buffer (>= the weight's; 4 for 3 output channels)
  .h:      resq 1       ; input size
  .w:      resq 1
  .stride: resq 1       ; 1 or 2
  .wt:     resq 1       ; Tensor* [kx ky cin cout]
  .bias:   resq 1       ; f32* or 0
endstruc

struc UnGn
  .in:      resq 1
  .out:     resq 1
  .c:       resq 1
  .h:       resq 1
  .w:       resq 1
  .gamma:   resq 1
  .beta:    resq 1
  .silu:    resq 1
  .compact: resq 1      ; 1: out is [h*w][c] tokens; 0: padded like in
  .eps:     resd 1
  .pad:     resd 1
endstruc

struc UnLin
  .a:    resq 1
  .lda:  resq 1
  .m:    resq 1
  .t:    resq 1         ; Tensor* [k n] (or a 1x1 convolution [1 1 k n])
  .bias: resq 1
  .c:    resq 1
  .ldc:  resq 1
  .acc:  resq 1
endstruc

struc UnAttn
  .q:     resq 1
  .k:     resq 1
  .v:     resq 1
  .out:   resq 1
  .tq:    resq 1
  .tk:    resq 1        ; keys that count
  .tkpad: resq 1        ; keys as stored (a multiple of 8); the rest masked
  .heads: resq 1
  .ldq:   resq 1        ; bytes per row of q and out
  .ldk:   resq 1        ; bytes per row of k and v
endstruc

; ===========================================================================
; Memory
; ===========================================================================

; un_init() — allocate the arena and every reused scratch buffer, once.
un_init:
  WINPROLOGUE
  cmp  qword [rel g_un_ready], 0
  jne  .ret
  mov  rcx, UN_ARENA_BYTES
  call rt_alloc
  mov  [rel g_un_arena], rax
  mov  rcx, UN_WBUF_BYTES
  call rt_alloc
  mov  [rel g_un_wbuf], rax
  mov  ecx, 4096 * 320 * 4
  call rt_alloc
  mov  [rel g_un_T], rax
  mov  ecx, 4096 * 320 * 4
  call rt_alloc
  mov  [rel g_un_T2], rax
  mov  ecx, 4096 * 320 * 4
  call rt_alloc
  mov  [rel g_un_Tn], rax
  mov  ecx, 4096 * 320 * 4
  call rt_alloc
  mov  [rel g_un_q], rax
  mov  ecx, 4096 * 320 * 4
  call rt_alloc
  mov  [rel g_un_k], rax
  mov  ecx, 4096 * 320 * 4
  call rt_alloc
  mov  [rel g_un_v], rax
  mov  ecx, 4096 * 320 * 4
  call rt_alloc
  mov  [rel g_un_ao], rax
  mov  ecx, 4096 * 320 * 4
  call rt_alloc
  mov  [rel g_un_tmp], rax
  mov  ecx, UN_TPAD * 1280 * 4
  call rt_alloc
  mov  [rel g_un_kc], rax
  mov  ecx, UN_TPAD * 1280 * 4
  call rt_alloc
  mov  [rel g_un_vc], rax
  mov  ecx, 4096 * 2560 * 4
  call rt_alloc
  mov  [rel g_un_G], rax
  mov  ecx, 4096 * 1280 * 4
  call rt_alloc
  mov  [rel g_un_F], rax
  mov  ecx, 4096 * 4096 * 4
  call rt_alloc
  mov  [rel g_un_scores], rax
  mov  ecx, UN_HD * 4096 * 4
  call rt_alloc
  mov  [rel g_un_VT], rax
  mov  ecx, 960 * 66 * 66 * 4
  call rt_alloc
  mov  [rel g_un_h1], rax
  mov  ecx, 1280 * 34 * 34 * 4
  call rt_alloc
  mov  [rel g_un_h2], rax
  mov  ecx, 640 * 66 * 66 * 4
  call rt_alloc
  mov  [rel g_un_up], rax
  mov  ecx, 960 * 66 * 66 * 4
  call rt_alloc
  mov  [rel g_un_cat], rax
  mov  ecx, UN_MAXC * 8
  call rt_alloc
  mov  [rel g_un_S], rax
  mov  ecx, UN_MAXC * 8
  call rt_alloc
  mov  [rel g_un_Q], rax
  mov  ecx, UN_MAXC * 4
  call rt_alloc
  mov  [rel g_un_ga], rax
  mov  ecx, UN_MAXC * 4
  call rt_alloc
  mov  [rel g_un_gb], rax
  mov  qword [rel g_un_ready], 1
.ret:
  WINEPILOGUE
  ret

; un_arena_reset() — the arena is empty again.
un_arena_reset:
  mov  qword [rel g_un_arena_used], 0
  ret

; un_aalloc(rcx=bytes) -> rax: zeroed, 64-byte aligned, from the arena.
; rt_fatal when the arena is full. Preserves the nonvolatile registers.
un_aalloc:
  push rbx
  WINPROLOGUE
  lea  rbx, [rcx + 63]
  and  rbx, -64
  mov  rax, [rel g_un_arena_used]
  lea  rcx, [rax + rbx]
  mov  rdx, UN_ARENA_BYTES
  cmp  rcx, rdx
  ja   .full
  mov  [rel g_un_arena_used], rcx
  add  rax, [rel g_un_arena]
  mov  rcx, rax
  xor  edx, edx
  mov  r8, rbx
  call rt_memset                ; rax = dst
  WINEPILOGUE
  pop  rbx
  ret
.full:
  lea  rcx, [rel un_err_arena]
  call rt_fatal

; un_vec(rcx=Tensor*) -> rax = its ne0 values as f32: the data itself when f32,
; else a dequantised copy in the arena. Preserves the nonvolatile registers.
un_vec:
  push rbx
  WINPROLOGUE
  sub  rsp, 16
  mov  rbx, rcx
  mov  rax, [rbx + Tensor.data]
  cmp  dword [rbx + Tensor.type], GGML_F32
  je   .ret
  mov  rcx, [rbx + Tensor.ne0]
  shl  rcx, 2
  call un_aalloc
  mov  [rsp+32], rax
  mov  rcx, rax
  mov  rdx, [rbx + Tensor.data]
  mov  r8, [rbx + Tensor.ne0]
  mov  r9d, [rbx + Tensor.type]
  call sg_deq_row
  mov  rax, [rsp+32]
.ret:
  WINEPILOGUE
  pop  rbx
  ret

; ===========================================================================
; Tensor names
; ===========================================================================

; un_num — append the decimal of rax (if >= 0) and '.' at rdi. Clobbers rax, rcx, rdx.
un_num:
  test rax, rax
  js   .ret
  cmp  rax, 10
  jb   .one
  xor  edx, edx
  mov  ecx, 10
  div  rcx
  add  al, '0'
  stosb
  mov  rax, rdx
.one:
  add  al, '0'
  stosb
  mov  al, '.'
  stosb
.ret:
  ret

; un_set_block(rcx=prefix zstr, rdx=i or -1, r8=j or -1) — g_un_blk =
; prefix [i "."] [j "."], e.g. ("input_blocks.", 1, 0) -> "input_blocks.1.0.".
un_set_block:
  push rsi
  push rdi
  lea  rdi, [rel g_un_blk]
  mov  rsi, rcx
.p:
  lodsb
  test al, al
  jz   .pd
  stosb
  jmp  .p
.pd:
  mov  rax, rdx
  call un_num
  mov  rax, r8
  call un_num
  mov  byte [rdi], 0
  pop  rdi
  pop  rsi
  ret

; un_t(rcx=suffix zstr) -> rax = Tensor* "model.diffusion_model." + g_un_blk +
; suffix; rt_fatal (naming it) when missing. Preserves the nonvolatile registers.
un_t:
  push rsi
  push rdi
  WINPROLOGUE
  lea  rdi, [rel g_un_name]
  lea  rsi, [rel un_prefix]
.a:
  lodsb
  test al, al
  jz   .ad
  stosb
  jmp  .a
.ad:
  lea  rsi, [rel g_un_blk]
.b:
  lodsb
  test al, al
  jz   .bd
  stosb
  jmp  .b
.bd:
  mov  rsi, rcx
.c:
  lodsb
  stosb
  test al, al
  jnz  .c
  lea  rcx, [rel g_un_name]
  call sd_find
  test rax, rax
  jz   .missing
  WINEPILOGUE
  pop  rdi
  pop  rsi
  ret
.missing:
  lea  rcx, [rel un_s_missing]
  call rt_err_str
  lea  rcx, [rel g_un_name]
  call rt_err_str
  lea  rcx, [rel un_s_nl]
  call rt_err_str
  lea  rcx, [rel un_err_missing]
  call rt_fatal

; un_tv(rcx=suffix) -> rax = un_vec(un_t(suffix)).
un_tv:
  WINPROLOGUE
  call un_t
  mov  rcx, rax
  call un_vec
  WINEPILOGUE
  ret

; ===========================================================================
; Elementwise helpers
; ===========================================================================

; un_zero_border(rcx=buf, rdx=c, r8=h, r9=w) — rows 0 and h+1, and columns 0
; and w+1 of every row, to zero. Preserves the nonvolatile registers.
un_zero_border:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx                ; c
  mov  rdi, r8                 ; h
  mov  r12, r9                 ; w
  lea  r13, [r12 + 2]
  imul r13, rsi
  shl  r13, 2                  ; bytes per row
  mov  rcx, rbx
  xor  edx, edx
  mov  r8, r13
  call rt_memset
  lea  rcx, [rdi + 1]
  imul rcx, r13
  add  rcx, rbx
  xor  edx, edx
  mov  r8, r13
  call rt_memset
  mov  r8, 1                   ; rows 1..h
.r:
  cmp  r8, rdi
  ja   .done
  push r8
  push r8
  mov  rcx, r8
  imul rcx, r13
  add  rcx, rbx                ; column 0
  xor  edx, edx
  mov  r8, rsi
  shl  r8, 2
  call rt_memset
  mov  rcx, rax
  lea  rax, [r12 + 1]
  imul rax, rsi
  lea  rcx, [rcx + rax*4]      ; column w+1
  xor  edx, edx
  mov  r8, rsi
  shl  r8, 2
  call rt_memset
  pop  r8
  pop  r8
  inc  r8
  jmp  .r
.done:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_silu(rcx=x, rdx=n) — x = x / (1 + e^-x) in place, n a multiple of 8. Leaf.
un_silu:
  xor  eax, eax
.l:
  cmp  rax, rdx
  jae  .d
  vmovups ymm4, [rcx + rax*4]
  vxorps ymm0, ymm4, [rel kc8_signmask]
  EXP8
  vaddps ymm0, ymm0, [rel kc8_one]
  vdivps ymm4, ymm4, ymm0
  vmovups [rcx + rax*4], ymm4
  add  rax, 8
  jmp  .l
.d:
  ret

; un_relu(rcx=x, rdx=n) — x = max(x, 0) in place, n a multiple of 8. Leaf.
un_relu:
  vxorps ymm1, ymm1, ymm1
  xor  eax, eax
.l:
  cmp  rax, rdx
  jae  .d
  vmaxps ymm0, ymm1, [rcx + rax*4]
  vmovups [rcx + rax*4], ymm0
  add  rax, 8
  jmp  .l
.d:
  ret

; un_softmax_row(rcx=p, rdx=n) — softmax in place, n a multiple of 8. Leaf;
; clobbers rax, ymm0-ymm5.
un_softmax_row:
  vbroadcastss ymm4, [rcx]
  xor  eax, eax
.m:
  vmaxps ymm4, ymm4, [rcx + rax*4]
  add  rax, 8
  cmp  rax, rdx
  jb   .m
  vextractf128 xmm5, ymm4, 1
  vmaxps xmm4, xmm4, xmm5
  vshufps xmm5, xmm4, xmm4, 0x4E
  vmaxps xmm4, xmm4, xmm5
  vshufps xmm5, xmm4, xmm4, 0xB1
  vmaxps xmm4, xmm4, xmm5
  vbroadcastss ymm4, xmm4      ; the row maximum
  vxorps ymm5, ymm5, ymm5      ; the sum
  xor  eax, eax
.e:
  vmovups ymm0, [rcx + rax*4]
  vsubps ymm0, ymm0, ymm4
  EXP8
  vmovups [rcx + rax*4], ymm0
  vaddps ymm5, ymm5, ymm0
  add  rax, 8
  cmp  rax, rdx
  jb   .e
  vextractf128 xmm0, ymm5, 1
  vaddps xmm5, xmm5, xmm0
  vhaddps xmm5, xmm5, xmm5
  vhaddps xmm5, xmm5, xmm5
  vmovss xmm0, [rel kc_one]
  vdivss xmm5, xmm0, xmm5
  vbroadcastss ymm5, xmm5      ; 1 / sum
  xor  eax, eax
.s:
  vmulps ymm0, ymm5, [rcx + rax*4]
  vmovups [rcx + rax*4], ymm0
  add  rax, 8
  cmp  rax, rdx
  jb   .s
  ret

; un_gather(rcx=padded in, rdx=c, r8=h, r9=w, [5]=compact out) — the interior
; pixels as [h*w][c]. Preserves the nonvolatile registers.
un_gather:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  mov  r14, ARG5(6)
  mov  r13, 1                  ; y
.y:
  cmp  r13, rdi
  ja   .done
  mov  r8, 1                   ; x
.x:
  cmp  r8, r12
  ja   .yn
  push r8
  push r8
  mov  rax, r13
  lea  rdx, [r12 + 2]
  imul rax, rdx
  add  rax, r8
  imul rax, rsi
  lea  rdx, [rbx + rax*4]      ; source pixel
  mov  rcx, r14
  mov  r8, rsi
  shl  r8, 2
  call rt_memcpy
  lea  r14, [r14 + rsi*4]
  pop  r8
  pop  r8
  inc  r8
  jmp  .x
.yn:
  inc  r13
  jmp  .y
.done:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_scatter_add(rcx=padded dst, rdx=c, r8=h, r9=w, [5]=compact src) — the
; interior pixels += the [h*w][c] tokens. Preserves the nonvolatile registers.
un_scatter_add:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  mov  r14, ARG5(6)
  mov  r13, 1
.y:
  cmp  r13, rdi
  ja   .done
  mov  r8, 1
.x:
  cmp  r8, r12
  ja   .yn
  push r8
  push r8
  mov  rax, r13
  lea  rdx, [r12 + 2]
  imul rax, rdx
  add  rax, r8
  imul rax, rsi
  lea  rcx, [rbx + rax*4]      ; destination pixel
  mov  rdx, r14
  mov  r8, rsi
  call k_add
  lea  r14, [r14 + rsi*4]
  pop  r8
  pop  r8
  inc  r8
  jmp  .x
.yn:
  inc  r13
  jmp  .y
.done:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_add_rows(rcx=padded dst, rdx=vec f32[c], r8=c, r9=h, [5]=w) — every
; interior pixel += vec. Preserves the nonvolatile registers.
un_add_rows:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  mov  rbx, rcx
  mov  r14, rdx
  mov  rsi, r8
  mov  rdi, r9
  mov  r12, ARG5(6)
  mov  r13, 1
.y:
  cmp  r13, rdi
  ja   .done
  mov  r8, 1
.x:
  cmp  r8, r12
  ja   .yn
  push r8
  push r8
  mov  rax, r13
  lea  rdx, [r12 + 2]
  imul rax, rdx
  add  rax, r8
  imul rax, rsi
  lea  rcx, [rbx + rax*4]
  mov  rdx, r14
  mov  r8, rsi
  call k_add
  pop  r8
  pop  r8
  inc  r8
  jmp  .x
.yn:
  inc  r13
  jmp  .y
.done:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_upsample(rcx=in, rdx=out, r8=c, r9=h, [5]=w) — nearest 2x: out is
; (2h) x (2w) padded. Preserves the nonvolatile registers.
un_upsample:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 16
  mov  rbx, rcx                ; in
  mov  r15, rdx                ; out
  mov  rsi, r8                 ; c
  mov  rdi, r9                 ; h
  mov  r12, ARG5(7)            ; w
  mov  r13, 1                  ; y out, 1..2h
.y:
  lea  rax, [rdi*2]
  cmp  r13, rax
  ja   .border
  mov  r14, 1                  ; x out
.x:
  lea  rax, [r12*2]
  cmp  r14, rax
  ja   .yn
  ; src pixel ((y-1)/2+1, (x-1)/2+1) in the (w+2) layout
  lea  rax, [r13 - 1]
  shr  rax, 1
  inc  rax
  lea  rdx, [r12 + 2]
  imul rax, rdx
  lea  rdx, [r14 - 1]
  shr  rdx, 1
  inc  rdx
  add  rax, rdx
  imul rax, rsi
  lea  rdx, [rbx + rax*4]
  ; dst pixel (y, x) in the (2w+2) layout
  lea  rax, [r12*2 + 2]
  imul rax, r13
  add  rax, r14
  imul rax, rsi
  lea  rcx, [r15 + rax*4]
  mov  r8, rsi
  shl  r8, 2
  call rt_memcpy
  inc  r14
  jmp  .x
.yn:
  inc  r13
  jmp  .y
.border:
  mov  rcx, r15
  mov  rdx, rsi
  lea  r8, [rdi*2]
  lea  r9, [r12*2]
  call un_zero_border
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_concat(rcx=a, rdx=ca, r8=b, r9=cb, [5]=h, [6]=w, [7]=out) — out pixel =
; [a pixel | b pixel], ca+cb channels. Preserves the nonvolatile registers.
un_concat:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32] x, [rsp+40] w+2, [rsp+48] y, [rsp+56] dst, [rsp+64] pixel
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  mov  r13, ARG5(7)            ; h
  mov  r14, ARG6(7)            ; w
  mov  r15, ARG7(7)            ; out
  lea  rax, [r14 + 2]
  mov  [rsp+40], rax
  mov  qword [rsp+48], 1
.y:
  mov  rax, [rsp+48]
  cmp  rax, r13
  ja   .border
  mov  qword [rsp+32], 1
.x:
  mov  rax, [rsp+32]
  cmp  rax, r14
  ja   .yn
  mov  rax, [rsp+48]
  imul rax, [rsp+40]
  add  rax, [rsp+32]           ; pixel index
  mov  [rsp+64], rax
  lea  rcx, [rsi + r12]
  imul rcx, rax
  lea  rcx, [r15 + rcx*4]      ; destination pixel
  mov  [rsp+56], rcx
  mov  rdx, rax
  imul rdx, rsi
  lea  rdx, [rbx + rdx*4]      ; a's pixel
  mov  r8, rsi
  shl  r8, 2
  call rt_memcpy
  mov  rax, [rsp+64]
  imul rax, r12
  lea  rdx, [rdi + rax*4]      ; b's pixel
  mov  rcx, [rsp+56]
  lea  rcx, [rcx + rsi*4]
  mov  r8, r12
  shl  r8, 2
  call rt_memcpy
  inc  qword [rsp+32]
  jmp  .x
.yn:
  inc  qword [rsp+48]
  jmp  .y
.border:
  mov  rcx, r15
  lea  rdx, [rsi + r12]
  mov  r8, r13
  mov  r9, r14
  call un_zero_border
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; The operators
; ===========================================================================

; un_lin(rcx=UnLin*) — C (+)= A W^T (+ bias) through sg_matmul; k and n from
; the tensor (ne0, ne1; or ne2, ne3 for a 1x1 convolution). Preserves the
; nonvolatile registers.
un_lin:
  push rbx
  push rsi
  WINPROLOGUE
  mov  rsi, rcx
  lea  rbx, [rel g_un_gemm]
  mov  rax, [rsi + UnLin.a]
  mov  [rbx + SgGemm.a], rax
  mov  rax, [rsi + UnLin.lda]
  mov  [rbx + SgGemm.lda], rax
  mov  rax, [rsi + UnLin.m]
  mov  [rbx + SgGemm.m], rax
  mov  rcx, [rsi + UnLin.t]
  mov  rax, [rcx + Tensor.ne0]
  mov  rdx, [rcx + Tensor.ne1]
  cmp  dword [rcx + Tensor.ndims], 4
  jne  .kn
  mov  rax, [rcx + Tensor.ne2]
  mov  rdx, [rcx + Tensor.ne3]
.kn:
  mov  [rbx + SgGemm.k], rax
  mov  [rbx + SgGemm.n], rdx
  mov  rax, [rcx + Tensor.data]
  mov  [rbx + SgGemm.w], rax
  mov  eax, [rcx + Tensor.type]
  mov  [rbx + SgGemm.wtype], rax
  mov  qword [rbx + SgGemm.wrow], 0
  mov  rax, [rsi + UnLin.bias]
  mov  [rbx + SgGemm.bias], rax
  mov  rax, [rsi + UnLin.c]
  mov  [rbx + SgGemm.c], rax
  mov  rax, [rsi + UnLin.ldc]
  mov  [rbx + SgGemm.ldc], rax
  mov  rax, [rsi + UnLin.acc]
  mov  [rbx + SgGemm.acc], rax
  mov  rcx, rbx
  call sg_matmul
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; UN_CONV_FIELDS — inside un_conv3 (r15 = UnConv*, rbx = tap, its frame): the
; tap-dependent weight pointer and the fixed fields of g_un_gemm; a, lda, m
; and c are set by the caller. Clobbers rax, rdi.
%macro UN_CONV_FIELDS 0
  lea  rdi, [rel g_un_gemm]
  mov  rax, rbx
  imul rax, [rsp+88]
  add  rax, [rel g_un_wbuf]
  mov  [rdi + SgGemm.w], rax
  mov  rax, [rsp+48]
  mov  [rdi + SgGemm.wrow], rax
  mov  qword [rdi + SgGemm.wtype], GGML_F32
  mov  rax, [r15 + UnConv.cout]
  mov  [rdi + SgGemm.n], rax
  mov  rax, [r15 + UnConv.cin]
  mov  [rdi + SgGemm.k], rax
  mov  rax, [rsp+96]
  mov  [rdi + SgGemm.ldc], rax
  xor  eax, eax
  test ebx, ebx
  jnz  %%nb
  mov  rax, [r15 + UnConv.bias]
%%nb:
  mov  [rdi + SgGemm.bias], rax
  xor  eax, eax
  test ebx, ebx
  setnz al
  mov  [rdi + SgGemm.acc], rax
%endmacro

; un_conv3(rcx=UnConv*) — 3x3 convolution, padding 1, stride 1 or 2: gather the
; weights to f32 [tap][cout][cin] in g_un_wbuf (zero-padded channels when the
; buffers are wider than the weights), then one sg_matmul per tap (stride 1:
; over all pixels; stride 2: per output row), then the output's border is
; zeroed. Preserves the nonvolatile registers.
un_conv3:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 96                 ; [rsp+32] tap, [+40] y, [+48] cin*4, [+56] real cin, [+64] real cout,
                               ; [+72] w+2, [+80] wo+2, [+88] tap stride (cout*cin*4), [+96] cout*4
  mov  r15, rcx
  mov  r12, [r15 + UnConv.wt]
  mov  rax, [r12 + Tensor.ne2]
  mov  [rsp+56], rax
  mov  rax, [r12 + Tensor.ne3]
  mov  [rsp+64], rax
  mov  rax, [r15 + UnConv.cin]
  shl  rax, 2
  mov  [rsp+48], rax
  mov  rax, [r15 + UnConv.cout]
  shl  rax, 2
  mov  [rsp+96], rax
  mov  rax, [r15 + UnConv.cout]
  imul rax, [rsp+48]
  mov  [rsp+88], rax           ; bytes per tap of the gathered weights
  mov  rax, [r15 + UnConv.w]
  add  rax, 2
  mov  [rsp+72], rax
  ; zero the gathered weights when a padded channel exists
  mov  rax, [r15 + UnConv.cin]
  cmp  rax, [rsp+56]
  jne  .zero
  mov  rax, [r15 + UnConv.cout]
  cmp  rax, [rsp+64]
  je   .gather
.zero:
  mov  rcx, [rel g_un_wbuf]
  xor  edx, edx
  mov  r8, [rsp+88]
  imul r8, r8, 9
  call rt_memset
.gather:
  ; dst[(tap*cout + o)*cin + i] = W[o][i][ky][kx], src sequential: o, i, 9 taps
  mov  r13, [r12 + Tensor.data]
  mov  r14, [rsp+88]
  xor  ebx, ebx                ; o
.o:
  cmp  rbx, [rsp+64]
  jae  .taps
  xor  esi, esi                ; i
.i:
  cmp  rsi, [rsp+56]
  jae  .on
  mov  rax, rbx
  imul rax, [r15 + UnConv.cin]
  add  rax, rsi
  shl  rax, 2
  add  rax, [rel g_un_wbuf]
  mov  rdx, rax                ; dst of tap 0
  mov  ecx, 9
.tap:
  cmp  dword [r12 + Tensor.type], GGML_F16
  jne  .f32e
  movzx eax, word [r13]
  vmovd xmm0, eax
  vcvtph2ps xmm0, xmm0
  add  r13, 2
  jmp  .st
.f32e:
  vmovss xmm0, [r13]
  add  r13, 4
.st:
  vmovss [rdx], xmm0
  add  rdx, r14
  dec  ecx
  jnz  .tap
  inc  rsi
  jmp  .i
.on:
  inc  rbx
  jmp  .o
.taps:
  cmp  qword [r15 + UnConv.stride], 2
  je   .s2
  ; stride 1: nine matmuls over h*(w+2)-2 rows
  xor  ebx, ebx                ; tap
.t1:
  cmp  ebx, 9
  jae  .border
  UN_CONV_FIELDS
  mov  eax, ebx
  mov  ecx, 3
  xor  edx, edx
  div  ecx                     ; eax = ky, edx = kx
  imul rax, [rsp+72]
  add  rax, rdx
  imul rax, [rsp+48]
  add  rax, [r15 + UnConv.in]
  lea  rdi, [rel g_un_gemm]
  mov  [rdi + SgGemm.a], rax
  mov  rax, [rsp+48]
  mov  [rdi + SgGemm.lda], rax
  mov  rax, [r15 + UnConv.h]
  imul rax, [rsp+72]
  sub  rax, 2
  mov  [rdi + SgGemm.m], rax
  mov  rax, [rsp+72]
  inc  rax
  imul rax, [rsp+96]
  add  rax, [r15 + UnConv.out]
  mov  [rdi + SgGemm.c], rax
  mov  rcx, rdi
  call sg_matmul
  inc  ebx
  jmp  .t1
.s2:
  ; stride 2: per output row, nine matmuls over wo = w/2 pixels
  mov  rax, [r15 + UnConv.w]
  shr  rax, 1
  add  rax, 2
  mov  [rsp+80], rax           ; wo+2
  mov  qword [rsp+40], 0       ; y
.y2:
  mov  rax, [r15 + UnConv.h]
  shr  rax, 1
  cmp  [rsp+40], rax
  jae  .border
  xor  ebx, ebx
.t2:
  cmp  ebx, 9
  jae  .y2n
  UN_CONV_FIELDS
  mov  eax, ebx
  mov  ecx, 3
  xor  edx, edx
  div  ecx                     ; eax = ky, edx = kx
  mov  rcx, [rsp+40]
  add  rcx, rcx
  add  rax, rcx                ; 2y + ky
  imul rax, [rsp+72]
  add  rax, rdx
  imul rax, [rsp+48]
  add  rax, [r15 + UnConv.in]
  lea  rdi, [rel g_un_gemm]
  mov  [rdi + SgGemm.a], rax
  mov  rax, [rsp+48]
  add  rax, rax
  mov  [rdi + SgGemm.lda], rax ; two input pixels per output pixel
  mov  rax, [r15 + UnConv.w]
  shr  rax, 1
  mov  [rdi + SgGemm.m], rax
  mov  rax, [rsp+40]
  inc  rax
  imul rax, [rsp+80]
  inc  rax
  imul rax, [rsp+96]
  add  rax, [r15 + UnConv.out]
  mov  [rdi + SgGemm.c], rax
  mov  rcx, rdi
  call sg_matmul
  inc  ebx
  jmp  .t2
.y2n:
  inc  qword [rsp+40]
  jmp  .y2
.border:
  mov  rcx, [r15 + UnConv.out]
  mov  rdx, [r15 + UnConv.cout]
  mov  r8, [r15 + UnConv.h]
  mov  r9, [r15 + UnConv.w]
  cmp  qword [r15 + UnConv.stride], 2
  jne  .zb
  shr  r8, 1
  shr  r9, 1
.zb:
  call un_zero_border
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_gn(rcx=UnGn*) — GroupNorm (UN_GROUPS groups) over the interior pixels,
; the statistics in f64, then out = (x - mean) rstd gamma + beta per channel,
; SiLU when asked, into a padded buffer (border zeroed) or compact tokens.
; Preserves the nonvolatile registers.
un_gn:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32] w+2, [+40] y, [+48] x, [+56] N (f64), [+64] eps (f64)
  mov  r15, rcx
  mov  r12, [r15 + UnGn.c]
  mov  r13, [r15 + UnGn.h]
  mov  r14, [r15 + UnGn.w]
  lea  rax, [r14 + 2]
  mov  [rsp+32], rax
  mov  rcx, [rel g_un_S]
  xor  edx, edx
  mov  r8, r12
  shl  r8, 3
  call rt_memset
  mov  rcx, [rel g_un_Q]
  xor  edx, edx
  mov  r8, r12
  shl  r8, 3
  call rt_memset
  ; pass 1: per-channel sums and sums of squares
  mov  qword [rsp+40], 1
.p1y:
  mov  rax, [rsp+40]
  cmp  rax, r13
  ja   .p2
  mov  qword [rsp+48], 1
.p1x:
  mov  rax, [rsp+48]
  cmp  rax, r14
  ja   .p1yn
  mov  rax, [rsp+40]
  imul rax, [rsp+32]
  add  rax, [rsp+48]
  imul rax, r12
  shl  rax, 2
  add  rax, [r15 + UnGn.in]
  mov  rdi, rax
  mov  r8, [rel g_un_S]
  mov  r9, [rel g_un_Q]
  xor  eax, eax
.p1c:
  vmovups ymm0, [rdi + rax*4]
  vcvtps2pd ymm1, xmm0
  vextractf128 xmm2, ymm0, 1
  vcvtps2pd ymm2, xmm2
  vmovupd ymm3, [r8 + rax*8]
  vaddpd ymm3, ymm3, ymm1
  vmovupd [r8 + rax*8], ymm3
  vmovupd ymm3, [r8 + rax*8 + 32]
  vaddpd ymm3, ymm3, ymm2
  vmovupd [r8 + rax*8 + 32], ymm3
  vmulpd ymm1, ymm1, ymm1
  vmulpd ymm2, ymm2, ymm2
  vmovupd ymm3, [r9 + rax*8]
  vaddpd ymm3, ymm3, ymm1
  vmovupd [r9 + rax*8], ymm3
  vmovupd ymm3, [r9 + rax*8 + 32]
  vaddpd ymm3, ymm3, ymm2
  vmovupd [r9 + rax*8 + 32], ymm3
  add  rax, 8
  cmp  rax, r12
  jb   .p1c
  inc  qword [rsp+48]
  jmp  .p1x
.p1yn:
  inc  qword [rsp+40]
  jmp  .p1y
.p2:
  ; pass 2: per group mean and rstd, per channel scale a and shift b
  mov  rbx, r12
  shr  rbx, 5                  ; channels per group
  mov  rax, rbx
  imul rax, r13
  imul rax, r14
  vcvtsi2sd xmm0, xmm0, rax
  vmovsd [rsp+56], xmm0        ; N
  vmovss xmm0, [r15 + UnGn.eps]
  vcvtss2sd xmm0, xmm0, xmm0
  vmovsd [rsp+64], xmm0
  xor  esi, esi                ; group
.g:
  cmp  esi, UN_GROUPS
  jae  .p3
  mov  rdi, rsi
  imul rdi, rbx                ; first channel
  vxorpd xmm0, xmm0, xmm0
  vxorpd xmm1, xmm1, xmm1
  xor  ecx, ecx
.gs:
  cmp  rcx, rbx
  jae  .gd
  lea  rax, [rdi + rcx]
  mov  r8, [rel g_un_S]
  vaddsd xmm0, xmm0, [r8 + rax*8]
  mov  r8, [rel g_un_Q]
  vaddsd xmm1, xmm1, [r8 + rax*8]
  inc  rcx
  jmp  .gs
.gd:
  vdivsd xmm0, xmm0, [rsp+56]  ; mean
  vdivsd xmm1, xmm1, [rsp+56]  ; E[x^2]
  vmulsd xmm2, xmm0, xmm0
  vsubsd xmm1, xmm1, xmm2      ; var
  vxorpd xmm2, xmm2, xmm2
  vmaxsd xmm1, xmm1, xmm2
  vaddsd xmm1, xmm1, [rsp+64]
  vsqrtsd xmm1, xmm1, xmm1
  vmovsd xmm2, [rel un_one64]
  vdivsd xmm1, xmm2, xmm1      ; rstd
  xor  ecx, ecx
.gc:
  cmp  rcx, rbx
  jae  .gn
  lea  rax, [rdi + rcx]
  mov  r8, [r15 + UnGn.gamma]
  vcvtss2sd xmm2, xmm2, dword [r8 + rax*4]
  vmulsd xmm2, xmm2, xmm1      ; a = rstd gamma
  mov  r8, [r15 + UnGn.beta]
  vcvtss2sd xmm3, xmm3, dword [r8 + rax*4]
  vmulsd xmm4, xmm0, xmm2
  vsubsd xmm3, xmm3, xmm4      ; b = beta - mean a
  vcvtsd2ss xmm2, xmm2, xmm2
  mov  r8, [rel g_un_ga]
  vmovss [r8 + rax*4], xmm2
  vcvtsd2ss xmm3, xmm3, xmm3
  mov  r8, [rel g_un_gb]
  vmovss [r8 + rax*4], xmm3
  inc  rcx
  jmp  .gc
.gn:
  inc  esi
  jmp  .g
.p3:
  ; pass 3: out = x a + b (SiLU)
  mov  qword [rsp+40], 1
.p3y:
  mov  rax, [rsp+40]
  cmp  rax, r13
  ja   .p3done
  mov  qword [rsp+48], 1
.p3x:
  mov  rax, [rsp+48]
  cmp  rax, r14
  ja   .p3yn
  mov  rax, [rsp+40]
  imul rax, [rsp+32]
  add  rax, [rsp+48]
  imul rax, r12
  shl  rax, 2
  mov  rdi, rax
  add  rdi, [r15 + UnGn.in]    ; source pixel
  mov  r10, rax
  add  r10, [r15 + UnGn.out]   ; padded destination
  cmp  qword [r15 + UnGn.compact], 0
  je   .dst
  mov  rax, [rsp+40]
  dec  rax
  imul rax, r14
  add  rax, [rsp+48]
  dec  rax
  imul rax, r12
  shl  rax, 2
  mov  r10, rax
  add  r10, [r15 + UnGn.out]   ; compact destination
.dst:
  mov  r8, [rel g_un_ga]
  mov  r9, [rel g_un_gb]
  xor  eax, eax
.p3c:
  vmovups ymm4, [rdi + rax*4]
  vmulps ymm4, ymm4, [r8 + rax*4]
  vaddps ymm4, ymm4, [r9 + rax*4]
  cmp  qword [r15 + UnGn.silu], 0
  je   .nosilu
  vxorps ymm0, ymm4, [rel kc8_signmask]
  EXP8
  vaddps ymm0, ymm0, [rel kc8_one]
  vdivps ymm4, ymm4, ymm0
.nosilu:
  vmovups [r10 + rax*4], ymm4
  add  rax, 8
  cmp  rax, r12
  jb   .p3c
  inc  qword [rsp+48]
  jmp  .p3x
.p3yn:
  inc  qword [rsp+40]
  jmp  .p3y
.p3done:
  cmp  qword [r15 + UnGn.compact], 0
  jne  .done
  mov  rcx, [r15 + UnGn.out]
  mov  rdx, r12
  mov  r8, r13
  mov  r9, r14
  call un_zero_border
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

; un_ln_rows(rcx=out, rdx=in, r8=w, r9=b, [5]=rows, [6]=c) — sg_layernorm on
; every row. Preserves the nonvolatile registers.
un_ln_rows:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  sub  rsp, 16
  mov  rsi, rcx
  mov  rdi, rdx
  mov  r12, r8
  mov  r13, r9
  mov  r14, ARG5(6)
  mov  rbx, ARG6(6)
  mov  qword [rsp+40], 0
.l:
  mov  rax, [rsp+40]
  cmp  rax, r14
  jae  .d
  mov  rcx, rsi
  mov  rdx, rdi
  mov  r8, r12
  mov  r9, r13
  mov  [rsp+32], rbx
  call sg_layernorm
  lea  rsi, [rsi + rbx*4]
  lea  rdi, [rdi + rbx*4]
  inc  qword [rsp+40]
  jmp  .l
.d:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_attn(rcx=UnAttn*) — per head: scores = Q_h K_h^T (sg_matmul, the head's
; columns in place), rows scaled by 1/8, keys beyond tk masked, softmax; V_h
; transposed; out_h = P V_h (sg_matmul). Preserves the nonvolatile registers.
un_attn:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] head
  mov  r15, rcx
  mov  qword [rsp+32], 0
.head:
  mov  rax, [rsp+32]
  cmp  rax, [r15 + UnAttn.heads]
  jae  .done
  shl  rax, 8                  ; head byte offset: h * 64 * 4
  mov  r14, rax
  lea  rbx, [rel g_un_gemm]
  mov  rax, [r15 + UnAttn.q]
  add  rax, r14
  mov  [rbx + SgGemm.a], rax
  mov  rax, [r15 + UnAttn.ldq]
  mov  [rbx + SgGemm.lda], rax
  mov  rax, [r15 + UnAttn.tq]
  mov  [rbx + SgGemm.m], rax
  mov  rax, [r15 + UnAttn.k]
  add  rax, r14
  mov  [rbx + SgGemm.w], rax
  mov  rax, [r15 + UnAttn.ldk]
  mov  [rbx + SgGemm.wrow], rax
  mov  qword [rbx + SgGemm.wtype], GGML_F32
  mov  rax, [r15 + UnAttn.tkpad]
  mov  [rbx + SgGemm.n], rax
  mov  qword [rbx + SgGemm.k], UN_HD
  mov  qword [rbx + SgGemm.bias], 0
  mov  rax, [rel g_un_scores]
  mov  [rbx + SgGemm.c], rax
  mov  rax, [r15 + UnAttn.tkpad]
  shl  rax, 2
  mov  [rbx + SgGemm.ldc], rax
  mov  qword [rbx + SgGemm.acc], 0
  mov  rcx, rbx
  call sg_matmul
  mov  r12, [r15 + UnAttn.tkpad]
  shl  r12, 2                  ; bytes per score row
  mov  rdi, [rel g_un_scores]
  xor  r13d, r13d
.row:
  cmp  r13, [r15 + UnAttn.tq]
  jae  .vt
  vmovaps ymm1, [rel un8_eighth]
  xor  eax, eax
.sc:
  vmulps ymm0, ymm1, [rdi + rax*4]
  vmovups [rdi + rax*4], ymm0
  add  rax, 8
  cmp  rax, [r15 + UnAttn.tkpad]
  jb   .sc
  mov  rax, [r15 + UnAttn.tk]
.mk:
  cmp  rax, [r15 + UnAttn.tkpad]
  jae  .sm
  mov  dword [rdi + rax*4], 0xF149F2CA ; -1e30: a padded key gets no weight
  inc  rax
  jmp  .mk
.sm:
  mov  rcx, rdi
  mov  rdx, [r15 + UnAttn.tkpad]
  call un_softmax_row
  add  rdi, r12
  inc  r13
  jmp  .row
.vt:
  mov  rsi, [rel g_un_VT]
  xor  r13d, r13d              ; key j
.vj:
  cmp  r13, [r15 + UnAttn.tkpad]
  jae  .pv
  mov  rax, r13
  imul rax, [r15 + UnAttn.ldk]
  add  rax, [r15 + UnAttn.v]
  add  rax, r14
  mov  rdx, rax                ; v_j (this head)
  mov  rcx, [r15 + UnAttn.tkpad]
  xor  r8d, r8d
.vd:
  mov  eax, [rdx + r8*4]
  mov  r9, r8
  imul r9, rcx
  add  r9, r13
  mov  [rsi + r9*4], eax       ; VT[d][j]
  inc  r8d
  cmp  r8d, UN_HD
  jb   .vd
  inc  r13
  jmp  .vj
.pv:
  lea  rbx, [rel g_un_gemm]
  mov  rax, [rel g_un_scores]
  mov  [rbx + SgGemm.a], rax
  mov  [rbx + SgGemm.lda], r12
  mov  rax, [r15 + UnAttn.tq]
  mov  [rbx + SgGemm.m], rax
  mov  rax, [rel g_un_VT]
  mov  [rbx + SgGemm.w], rax
  mov  [rbx + SgGemm.wrow], r12
  mov  qword [rbx + SgGemm.wtype], GGML_F32
  mov  qword [rbx + SgGemm.n], UN_HD
  mov  rax, [r15 + UnAttn.tkpad]
  mov  [rbx + SgGemm.k], rax
  mov  qword [rbx + SgGemm.bias], 0
  mov  rax, [r15 + UnAttn.out]
  add  rax, r14
  mov  [rbx + SgGemm.c], rax
  mov  rax, [r15 + UnAttn.ldq]
  mov  [rbx + SgGemm.ldc], rax
  mov  qword [rbx + SgGemm.acc], 0
  mov  rcx, rbx
  call sg_matmul
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

; un_geglu(rcx=G [rows][2*c4], rdx=rows, r8=c4) — g_un_F[r][i] = G[r][i] *
; gelu(G[r][c4+i]). Preserves the nonvolatile registers.
un_geglu:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, [rel g_un_F]
  xor  r13d, r13d
.r:
  cmp  r13, rsi
  jae  .d
  lea  rcx, [rbx + rdi*4]
  mov  rdx, rdi
  call sg_gelu
  lea  rcx, [rbx + rdi*4]
  xor  eax, eax
.i:
  vmovups ymm0, [rbx + rax*4]
  vmulps ymm0, ymm0, [rcx + rax*4]
  vmovups [r12 + rax*4], ymm0
  add  rax, 8
  cmp  rax, rdi
  jb   .i
  lea  rbx, [rbx + rdi*8]
  lea  r12, [r12 + rdi*4]
  inc  r13
  jmp  .r
.d:
  vzeroupper
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; The blocks (tensor names relative to g_un_blk, set by the caller)
; ===========================================================================

; un_res(rcx=x, rdx=cin, r8=cout, r9=h, [5]=w, [6]=out) — a ResBlock:
; out = skip(x) + conv2(silu(gn2(conv1(silu(gn1(x))) + emb_layers(silu(emb))))).
; Preserves the nonvolatile registers.
un_res:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 16
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  mov  r13, ARG5(7)
  mov  r14, ARG6(7)
  ; gn1 + silu -> h1
  lea  r15, [rel g_un_gn]
  mov  [r15 + UnGn.in], rbx
  mov  rax, [rel g_un_h1]
  mov  [r15 + UnGn.out], rax
  mov  [r15 + UnGn.c], rsi
  mov  [r15 + UnGn.h], r12
  mov  [r15 + UnGn.w], r13
  lea  rcx, [rel un_s_in0w]
  call un_tv
  mov  [r15 + UnGn.gamma], rax
  lea  rcx, [rel un_s_in0b]
  call un_tv
  mov  [r15 + UnGn.beta], rax
  mov  qword [r15 + UnGn.silu], 1
  mov  qword [r15 + UnGn.compact], 0
  mov  eax, [rel un_eps5]
  mov  [r15 + UnGn.eps], eax
  mov  rcx, r15
  call un_gn
  ; conv1 -> h2
  lea  r15, [rel g_un_conv]
  mov  rax, [rel g_un_h1]
  mov  [r15 + UnConv.in], rax
  mov  rax, [rel g_un_h2]
  mov  [r15 + UnConv.out], rax
  mov  [r15 + UnConv.cin], rsi
  mov  [r15 + UnConv.cout], rdi
  mov  [r15 + UnConv.h], r12
  mov  [r15 + UnConv.w], r13
  mov  qword [r15 + UnConv.stride], 1
  lea  rcx, [rel un_s_in2w]
  call un_t
  mov  [r15 + UnConv.wt], rax
  lea  rcx, [rel un_s_in2b]
  call un_tv
  mov  [r15 + UnConv.bias], rax
  mov  rcx, r15
  call un_conv3
  ; embo = emb_layers.1(silu(emb))
  lea  r15, [rel g_un_lin]
  lea  rax, [rel g_un_semb]
  mov  [r15 + UnLin.a], rax
  mov  qword [r15 + UnLin.lda], 1280 * 4
  mov  qword [r15 + UnLin.m], 1
  lea  rcx, [rel un_s_embw]
  call un_t
  mov  [r15 + UnLin.t], rax
  lea  rcx, [rel un_s_embb]
  call un_tv
  mov  [r15 + UnLin.bias], rax
  lea  rax, [rel g_un_embo]
  mov  [r15 + UnLin.c], rax
  mov  rax, rdi
  shl  rax, 2
  mov  [r15 + UnLin.ldc], rax
  mov  qword [r15 + UnLin.acc], 0
  mov  rcx, r15
  call un_lin
  mov  rcx, [rel g_un_h2]
  lea  rdx, [rel g_un_embo]
  mov  r8, rdi
  mov  r9, r12
  mov  [rsp+32], r13
  call un_add_rows
  ; gn2 + silu -> h1
  lea  r15, [rel g_un_gn]
  mov  rax, [rel g_un_h2]
  mov  [r15 + UnGn.in], rax
  mov  rax, [rel g_un_h1]
  mov  [r15 + UnGn.out], rax
  mov  [r15 + UnGn.c], rdi
  lea  rcx, [rel un_s_out0w]
  call un_tv
  mov  [r15 + UnGn.gamma], rax
  lea  rcx, [rel un_s_out0b]
  call un_tv
  mov  [r15 + UnGn.beta], rax
  mov  rcx, r15
  call un_gn
  ; conv2 -> out
  lea  r15, [rel g_un_conv]
  mov  rax, [rel g_un_h1]
  mov  [r15 + UnConv.in], rax
  mov  [r15 + UnConv.out], r14
  mov  [r15 + UnConv.cin], rdi
  lea  rcx, [rel un_s_out3w]
  call un_t
  mov  [r15 + UnConv.wt], rax
  lea  rcx, [rel un_s_out3b]
  call un_tv
  mov  [r15 + UnConv.bias], rax
  mov  rcx, r15
  call un_conv3
  ; the skip
  cmp  rsi, rdi
  je   .ident
  lea  r15, [rel g_un_lin]
  lea  rax, [r13 + 3]          ; the interior starts at pixel (w+2)+1
  imul rax, rsi
  shl  rax, 2
  add  rax, rbx
  mov  [r15 + UnLin.a], rax
  mov  rax, rsi
  shl  rax, 2
  mov  [r15 + UnLin.lda], rax
  lea  rax, [r13 + 2]
  imul rax, r12
  sub  rax, 2
  mov  [r15 + UnLin.m], rax
  lea  rcx, [rel un_s_skipw]
  call un_t
  mov  [r15 + UnLin.t], rax
  lea  rcx, [rel un_s_skipb]
  call un_tv
  mov  [r15 + UnLin.bias], rax
  lea  rax, [r13 + 3]
  imul rax, rdi
  shl  rax, 2
  add  rax, r14
  mov  [r15 + UnLin.c], rax
  mov  rax, rdi
  shl  rax, 2
  mov  [r15 + UnLin.ldc], rax
  mov  qword [r15 + UnLin.acc], 1
  mov  rcx, r15
  call un_lin
  mov  rcx, r14
  mov  rdx, rdi
  mov  r8, r12
  mov  r9, r13
  call un_zero_border
  jmp  .done
.ident:
  mov  rcx, r14
  mov  rdx, rbx
  lea  r8, [r12 + 2]
  lea  rax, [r13 + 2]
  imul r8, rax
  imul r8, rdi
  call k_add
.done:
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ST_LIN a, w suffix, b suffix or 0, c, acc, ldc, m, lda — inside un_st: g_un_lin
%macro ST_LIN 8
  lea  r15, [rel g_un_lin]
  mov  rax, %1
  mov  [r15 + UnLin.a], rax
  mov  rax, %8
  mov  [r15 + UnLin.lda], rax
  mov  rax, %7
  mov  [r15 + UnLin.m], rax
  lea  rcx, [rel %2]
  call un_t
  mov  [r15 + UnLin.t], rax
%ifidn %3, 0
  mov  qword [r15 + UnLin.bias], 0
%else
  lea  rcx, [rel %3]
  call un_tv
  mov  [r15 + UnLin.bias], rax
%endif
  mov  rax, %4
  mov  [r15 + UnLin.c], rax
  mov  qword [r15 + UnLin.acc], %5
  mov  rax, %6
  mov  [r15 + UnLin.ldc], rax
  mov  rcx, r15
  call un_lin
%endmacro

; ST_LN w suffix, b suffix — inside un_st: Tn = LayerNorm(T2) over r14 rows of rsi
%macro ST_LN 2
  lea  rcx, [rel %1]
  call un_tv
  mov  [rsp+40], rax
  lea  rcx, [rel %2]
  call un_tv
  mov  r9, rax
  mov  r8, [rsp+40]
  mov  rcx, [rel g_un_Tn]
  mov  rdx, [rel g_un_T2]
  mov  [rsp+32], r14
  mov  [rsp+40], rsi
  call un_ln_rows
%endmacro

; ST_ATTN k, v, tk, tkpad — inside un_st: ao = attention(q, k, v)
%macro ST_ATTN 4
  lea  r15, [rel g_un_attn]
  mov  rax, [rel g_un_q]
  mov  [r15 + UnAttn.q], rax
  mov  rax, %1
  mov  [r15 + UnAttn.k], rax
  mov  rax, %2
  mov  [r15 + UnAttn.v], rax
  mov  rax, [rel g_un_ao]
  mov  [r15 + UnAttn.out], rax
  mov  [r15 + UnAttn.tq], r14
  mov  rax, %3
  mov  [r15 + UnAttn.tk], rax
  mov  rax, %4
  mov  [r15 + UnAttn.tkpad], rax
  mov  rax, [rsp+48]
  mov  [r15 + UnAttn.heads], rax
  mov  [r15 + UnAttn.ldq], rdi
  mov  [r15 + UnAttn.ldk], rdi
  mov  rcx, r15
  call un_attn
%endmacro

; un_st(rcx=x, rdx=c, r8=h, r9=w) — a SpatialTransformer, in place: x +=
; proj_out(block(proj_in(groupnorm(x)))) with self-attention, cross-attention
; to the context and a GEGLU feed-forward. Preserves the nonvolatile registers.
un_st:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32..48) call arguments, [+48] heads, [+56] 8c*4, [+64] 4c*4
  mov  rbx, rcx
  mov  rsi, rdx
  mov  r12, r8
  mov  r13, r9
  mov  r14, r8
  imul r14, r9                 ; tokens
  mov  rdi, rsi
  shl  rdi, 2                  ; bytes per token
  mov  rax, rsi
  shr  rax, 6
  mov  [rsp+48], rax           ; heads of 64
  lea  rax, [rdi*8]
  mov  [rsp+56], rax
  lea  rax, [rdi*4]
  mov  [rsp+64], rax
  ; T = groupnorm(x), compact
  lea  r15, [rel g_un_gn]
  mov  [r15 + UnGn.in], rbx
  mov  rax, [rel g_un_T]
  mov  [r15 + UnGn.out], rax
  mov  [r15 + UnGn.c], rsi
  mov  [r15 + UnGn.h], r12
  mov  [r15 + UnGn.w], r13
  lea  rcx, [rel un_s_normw]
  call un_tv
  mov  [r15 + UnGn.gamma], rax
  lea  rcx, [rel un_s_normb]
  call un_tv
  mov  [r15 + UnGn.beta], rax
  mov  qword [r15 + UnGn.silu], 0
  mov  qword [r15 + UnGn.compact], 1
  mov  eax, [rel un_eps6]
  mov  [r15 + UnGn.eps], eax
  mov  rcx, r15
  call un_gn
  ST_LIN [rel g_un_T], un_s_pinw, un_s_pinb, [rel g_un_T2], 0, rdi, r14, rdi
  ; self-attention
  ST_LN un_s_n1w, un_s_n1b
  ST_LIN [rel g_un_Tn], un_s_a1q, 0, [rel g_un_q], 0, rdi, r14, rdi
  ST_LIN [rel g_un_Tn], un_s_a1k, 0, [rel g_un_k], 0, rdi, r14, rdi
  ST_LIN [rel g_un_Tn], un_s_a1v, 0, [rel g_un_v], 0, rdi, r14, rdi
  ST_ATTN [rel g_un_k], [rel g_un_v], r14, r14
  ST_LIN [rel g_un_ao], un_s_a1ow, un_s_a1ob, [rel g_un_T2], 1, rdi, r14, rdi
  ; cross-attention to the context
  ST_LN un_s_n2w, un_s_n2b
  ST_LIN [rel g_un_Tn], un_s_a2q, 0, [rel g_un_q], 0, rdi, r14, rdi
  ST_LIN [rel g_un_ctx], un_s_a2k, 0, [rel g_un_kc], 0, rdi, UN_TPAD, 1024 * 4
  ST_LIN [rel g_un_ctx], un_s_a2v, 0, [rel g_un_vc], 0, rdi, UN_TPAD, 1024 * 4
  ST_ATTN [rel g_un_kc], [rel g_un_vc], 77, UN_TPAD
  ST_LIN [rel g_un_ao], un_s_a2ow, un_s_a2ob, [rel g_un_T2], 1, rdi, r14, rdi
  ; feed-forward: GEGLU
  ST_LN un_s_n3w, un_s_n3b
  ST_LIN [rel g_un_Tn], un_s_ffpw, un_s_ffpb, [rel g_un_G], 0, [rsp+56], r14, rdi
  mov  rcx, [rel g_un_G]
  mov  rdx, r14
  mov  r8, rsi
  shl  r8, 2
  call un_geglu
  ST_LIN [rel g_un_F], un_s_ff2w, un_s_ff2b, [rel g_un_T2], 1, rdi, r14, [rsp+64]
  ; x += proj_out(T2)
  ST_LIN [rel g_un_T2], un_s_poutw, un_s_poutb, [rel g_un_tmp], 0, rdi, r14, rdi
  mov  rcx, rbx
  mov  rdx, rsi
  mov  r8, r12
  mov  r9, r13
  mov  rax, [rel g_un_tmp]
  mov  [rsp+32], rax
  call un_scatter_add
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_down(rcx=x, rdx=c, r8=h, r9=w, [5]=out) — the "op" convolution, stride 2.
un_down:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  lea  rbx, [rel g_un_conv]
  mov  [rbx + UnConv.in], rcx
  mov  rax, ARG5(5)
  mov  [rbx + UnConv.out], rax
  mov  [rbx + UnConv.cin], rdx
  mov  [rbx + UnConv.cout], rdx
  mov  [rbx + UnConv.h], r8
  mov  [rbx + UnConv.w], r9
  mov  qword [rbx + UnConv.stride], 2
  lea  rcx, [rel un_s_opw]
  call un_t
  mov  [rbx + UnConv.wt], rax
  lea  rcx, [rel un_s_opb]
  call un_tv
  mov  [rbx + UnConv.bias], rax
  mov  rcx, rbx
  call un_conv3
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_upconv(rcx=x, rdx=c, r8=h, r9=w, [5]=out) — nearest 2x, then the "conv".
un_upconv:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 16
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  mov  r13, ARG5(5)
  mov  rcx, rbx
  mov  rdx, [rel g_un_up]
  mov  r8, rsi
  mov  r9, rdi
  mov  [rsp+32], r12
  call un_upsample
  lea  rbx, [rel g_un_conv]
  mov  rax, [rel g_un_up]
  mov  [rbx + UnConv.in], rax
  mov  [rbx + UnConv.out], r13
  mov  [rbx + UnConv.cin], rsi
  mov  [rbx + UnConv.cout], rsi
  lea  rax, [rdi*2]
  mov  [rbx + UnConv.h], rax
  lea  rax, [r12*2]
  mov  [rbx + UnConv.w], rax
  mov  qword [rbx + UnConv.stride], 1
  lea  rcx, [rel un_s_convw]
  call un_t
  mov  [rbx + UnConv.wt], rax
  lea  rcx, [rel un_s_convb]
  call un_tv
  mov  [rbx + UnConv.bias], rax
  mov  rcx, rbx
  call un_conv3
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_temb(rcx=t) — g_un_emb = time_embed(sinusoid(t)), g_un_semb = silu of it.
; The sinusoid: 160 frequencies 10000^(-i/160), cos first then sin (x87).
un_temb:
  push rbx
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32] t, [rsp+40] i
  mov  [rsp+32], rcx
  vzeroupper
  fild qword [rsp+32]          ; t
  xor  ebx, ebx
.l:
  cmp  ebx, 160
  jae  .d
  mov  [rsp+40], ebx
  fild dword [rsp+40]          ; i  t
  fdiv dword [rel un_f40]      ; i/40  t
  fldl2t                       ; log2 10  i/40  t
  fmulp st1, st0               ; (i/40) log2 10  t
  fchs                         ; x = -(i/160) log2 10000  t
  fld  st0                     ; x  x  t
  frndint                      ; n  x  t
  fxch                         ; x  n  t
  fsub st0, st1                ; f  n  t
  f2xm1                        ; 2^f-1  n  t
  fld1
  faddp st1, st0               ; 2^f  n  t
  fscale                       ; 2^x  n  t
  fstp st1                     ; 2^x  t
  fmul st0, st1                ; arg = t 10000^(-i/160)  t
  fsincos                      ; cos  sin  t
  lea  rax, [rel g_un_temb]
  fstp dword [rax + rbx*4]
  fstp dword [rax + 640 + rbx*4]
  inc  ebx
  jmp  .l
.d:
  fstp st0
  lea  rcx, [rel un_empty]
  mov  rdx, -1
  mov  r8, -1
  call un_set_block
  lea  rbx, [rel g_un_lin]
  lea  rax, [rel g_un_temb]
  mov  [rbx + UnLin.a], rax
  mov  qword [rbx + UnLin.lda], 320 * 4
  mov  qword [rbx + UnLin.m], 1
  lea  rcx, [rel un_s_te0w]
  call un_t
  mov  [rbx + UnLin.t], rax
  lea  rcx, [rel un_s_te0b]
  call un_tv
  mov  [rbx + UnLin.bias], rax
  lea  rax, [rel g_un_emb1]
  mov  [rbx + UnLin.c], rax
  mov  qword [rbx + UnLin.ldc], 1280 * 4
  mov  qword [rbx + UnLin.acc], 0
  mov  rcx, rbx
  call un_lin
  lea  rcx, [rel g_un_emb1]
  mov  edx, 1280
  call un_silu
  lea  rax, [rel g_un_emb1]
  mov  [rbx + UnLin.a], rax
  mov  qword [rbx + UnLin.lda], 1280 * 4
  lea  rcx, [rel un_s_te2w]
  call un_t
  mov  [rbx + UnLin.t], rax
  lea  rcx, [rel un_s_te2b]
  call un_tv
  mov  [rbx + UnLin.bias], rax
  lea  rax, [rel g_un_emb]
  mov  [rbx + UnLin.c], rax
  mov  rcx, rbx
  call un_lin
  lea  rcx, [rel g_un_semb]
  lea  rdx, [rel g_un_emb]
  mov  r8d, 1280 * 4
  call rt_memcpy
  lea  rcx, [rel g_un_semb]
  mov  edx, 1280
  call un_silu
  WINEPILOGUE
  pop  rbx
  ret

; un_abuf(rcx=c, rdx=h, r8=w) -> rax = a zeroed padded activation from the arena.
un_abuf:
  WINPROLOGUE
  add  rdx, 2
  add  r8, 2
  imul rdx, r8
  imul rcx, rdx
  shl  rcx, 2
  call un_aalloc
  WINEPILOGUE
  ret

; un_log() — "asmrun: unet <block> ms=<since un_forward began>" to stderr.
un_log:
  WINPROLOGUE
  lea  rcx, [rel un_s_log]
  call rt_err_str
  lea  rcx, [rel g_un_blk]
  call rt_err_str
  lea  rcx, [rel un_s_ms]
  call rt_err_str
  call rt_now_ns
  mov  rdx, rax
  sub  rdx, [rel g_un_t0]
  call model_emit_ms
  lea  rcx, [rel un_s_nl]
  call rt_err_str
  WINEPILOGUE
  ret

; UN_IN_RES block, cin, cout — input block: ResBlock into a new buffer = hs[block]
%macro UN_IN_RES 3
  lea  rcx, [rel un_s_input]
  mov  edx, %1
  xor  r8d, r8d
  call un_set_block
  mov  ecx, %3
  mov  rdx, [rel g_un_h]
  mov  r8, [rel g_un_w]
  call un_abuf
  mov  [rel g_un_hs + 8*%1], rax
  mov  [rsp+40], rax
  mov  rcx, [rel g_un_x]
  mov  edx, %2
  mov  r8d, %3
  mov  r9, [rel g_un_h]
  mov  rax, [rel g_un_w]
  mov  [rsp+32], rax
  call un_res
  mov  rax, [rel g_un_hs + 8*%1]
  mov  [rel g_un_x], rax
  mov  qword [rel g_un_cx], %3
  call un_log
%endmacro

; UN_IN_ST block — input block: SpatialTransformer in place
%macro UN_IN_ST 1
  lea  rcx, [rel un_s_input]
  mov  edx, %1
  mov  r8d, 1
  call un_set_block
  mov  rcx, [rel g_un_x]
  mov  rdx, [rel g_un_cx]
  mov  r8, [rel g_un_h]
  mov  r9, [rel g_un_w]
  call un_st
  call un_log
%endmacro

; UN_IN_DOWN block — input block: the stride-2 convolution, halving h and w
%macro UN_IN_DOWN 1
  lea  rcx, [rel un_s_input]
  mov  edx, %1
  xor  r8d, r8d
  call un_set_block
  mov  rcx, [rel g_un_cx]
  mov  rdx, [rel g_un_h]
  shr  rdx, 1
  mov  r8, [rel g_un_w]
  shr  r8, 1
  call un_abuf
  mov  [rel g_un_hs + 8*%1], rax
  mov  [rsp+32], rax
  mov  rcx, [rel g_un_x]
  mov  rdx, [rel g_un_cx]
  mov  r8, [rel g_un_h]
  mov  r9, [rel g_un_w]
  call un_down
  mov  rax, [rel g_un_hs + 8*%1]
  mov  [rel g_un_x], rax
  shr  qword [rel g_un_h], 1
  shr  qword [rel g_un_w], 1
  call un_log
%endmacro

; UN_MID_RES index — middle block ResBlock (1280 -> 1280) into a new buffer
%macro UN_MID_RES 1
  lea  rcx, [rel un_s_middle]
  mov  edx, %1
  mov  r8, -1
  call un_set_block
  mov  ecx, 1280
  mov  rdx, [rel g_un_h]
  mov  r8, [rel g_un_w]
  call un_abuf
  mov  [rsp+40], rax
  mov  rcx, [rel g_un_x]
  mov  edx, 1280
  mov  r8d, 1280
  mov  r9, [rel g_un_h]
  mov  rax, [rel g_un_w]
  mov  [rsp+32], rax
  call un_res
  mov  rax, [rsp+40]
  mov  [rel g_un_x], rax
  call un_log
%endmacro

; un_forward(rcx=latent padded [66][66][8], rdx=context [80][1024], r8=t) ->
; rax = eps, padded [66][66][4]. The whole UNet once. Preserves the
; nonvolatile registers.
un_forward:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64
  mov  [rel g_un_ctx], rdx
  mov  rbx, rcx
  mov  rsi, r8
  call rt_now_ns
  mov  [rel g_un_t0], rax
  mov  rcx, rsi
  call un_temb
  ; conv_in: input_blocks.0.0
  lea  rcx, [rel un_s_input]
  xor  edx, edx
  xor  r8d, r8d
  call un_set_block
  mov  ecx, 320
  mov  edx, UN_LAT
  mov  r8d, UN_LAT
  call un_abuf
  mov  [rel g_un_hs], rax
  mov  [rel g_un_x], rax
  mov  qword [rel g_un_cx], 320
  mov  qword [rel g_un_h], UN_LAT
  mov  qword [rel g_un_w], UN_LAT
  lea  r15, [rel g_un_conv]
  mov  [r15 + UnConv.in], rbx
  mov  [r15 + UnConv.out], rax
  mov  qword [r15 + UnConv.cin], 8
  mov  qword [r15 + UnConv.cout], 320
  mov  qword [r15 + UnConv.h], UN_LAT
  mov  qword [r15 + UnConv.w], UN_LAT
  mov  qword [r15 + UnConv.stride], 1
  lea  rcx, [rel un_s_w]
  call un_t
  mov  [r15 + UnConv.wt], rax
  lea  rcx, [rel un_s_b]
  call un_tv
  mov  [r15 + UnConv.bias], rax
  mov  rcx, r15
  call un_conv3
  call un_log
  ; the encoder half
  UN_IN_RES 1, 320, 320
  UN_IN_ST 1
  UN_IN_RES 2, 320, 320
  UN_IN_ST 2
  UN_IN_DOWN 3
  UN_IN_RES 4, 320, 640
  UN_IN_ST 4
  UN_IN_RES 5, 640, 640
  UN_IN_ST 5
  UN_IN_DOWN 6
  UN_IN_RES 7, 640, 1280
  UN_IN_ST 7
  UN_IN_RES 8, 1280, 1280
  UN_IN_ST 8
  UN_IN_DOWN 9
  UN_IN_RES 10, 1280, 1280
  UN_IN_RES 11, 1280, 1280
  ; the middle
  UN_MID_RES 0
  lea  rcx, [rel un_s_middle]
  mov  edx, 1
  mov  r8, -1
  call un_set_block
  mov  rcx, [rel g_un_x]
  mov  edx, 1280
  mov  r8, [rel g_un_h]
  mov  r9, [rel g_un_w]
  call un_st
  call un_log
  UN_MID_RES 2
  ; the decoder half
  xor  r12d, r12d              ; output block
.ob:
  cmp  r12d, 12
  jae  .out
  mov  rax, 11
  sub  rax, r12
  lea  rcx, [rel g_un_hs]
  mov  r13, [rcx + rax*8]      ; the skip
  lea  rcx, [rel un_hs_c]
  mov  r14d, [rcx + rax*4]     ; its channels
  mov  rcx, [rel g_un_x]
  mov  rdx, [rel g_un_cx]
  mov  r8, r13
  mov  r9, r14
  mov  rax, [rel g_un_h]
  mov  [rsp+32], rax
  mov  rax, [rel g_un_w]
  mov  [rsp+40], rax
  mov  rax, [rel g_un_cat]
  mov  [rsp+48], rax
  call un_concat
  lea  rcx, [rel un_s_output]
  mov  rdx, r12
  xor  r8d, r8d
  call un_set_block
  lea  rax, [rel un_ob_cout]
  mov  r15d, [rax + r12*4]     ; cout
  mov  rcx, r15
  mov  rdx, [rel g_un_h]
  mov  r8, [rel g_un_w]
  call un_abuf
  mov  rbx, rax
  mov  rcx, [rel g_un_cat]
  mov  rdx, [rel g_un_cx]
  add  rdx, r14
  mov  r8, r15
  mov  r9, [rel g_un_h]
  mov  rax, [rel g_un_w]
  mov  [rsp+32], rax
  mov  [rsp+40], rbx
  call un_res
  mov  [rel g_un_x], rbx
  mov  [rel g_un_cx], r15
  call un_log
  lea  rax, [rel un_ob_st]
  cmp  byte [rax + r12], 0
  je   .nost
  lea  rcx, [rel un_s_output]
  mov  rdx, r12
  mov  r8d, 1
  call un_set_block
  mov  rcx, rbx
  mov  rdx, r15
  mov  r8, [rel g_un_h]
  mov  r9, [rel g_un_w]
  call un_st
  call un_log
.nost:
  lea  rax, [rel un_ob_up]
  movzx r8d, byte [rax + r12]
  test r8d, r8d
  jz   .obn
  lea  rcx, [rel un_s_output]
  mov  rdx, r12
  call un_set_block
  mov  rcx, r15
  mov  rdx, [rel g_un_h]
  shl  rdx, 1
  mov  r8, [rel g_un_w]
  shl  r8, 1
  call un_abuf
  mov  rbx, rax
  mov  rcx, [rel g_un_x]
  mov  rdx, r15
  mov  r8, [rel g_un_h]
  mov  r9, [rel g_un_w]
  mov  [rsp+32], rbx
  call un_upconv
  mov  [rel g_un_x], rbx
  shl  qword [rel g_un_h], 1
  shl  qword [rel g_un_w], 1
  call un_log
.obn:
  inc  r12d
  jmp  .ob
.out:
  ; out: groupnorm + silu, then the 3x3 to 4 channels
  lea  rcx, [rel un_empty]
  mov  rdx, -1
  mov  r8, -1
  call un_set_block
  lea  r15, [rel g_un_gn]
  mov  rax, [rel g_un_x]
  mov  [r15 + UnGn.in], rax
  mov  rax, [rel g_un_h1]
  mov  [r15 + UnGn.out], rax
  mov  qword [r15 + UnGn.c], 320
  mov  qword [r15 + UnGn.h], UN_LAT
  mov  qword [r15 + UnGn.w], UN_LAT
  lea  rcx, [rel un_s_o0w]
  call un_tv
  mov  [r15 + UnGn.gamma], rax
  lea  rcx, [rel un_s_o0b]
  call un_tv
  mov  [r15 + UnGn.beta], rax
  mov  qword [r15 + UnGn.silu], 1
  mov  qword [r15 + UnGn.compact], 0
  mov  eax, [rel un_eps5]
  mov  [r15 + UnGn.eps], eax
  mov  rcx, r15
  call un_gn
  mov  ecx, 4
  mov  edx, UN_LAT
  mov  r8d, UN_LAT
  call un_abuf
  mov  rbx, rax
  lea  r15, [rel g_un_conv]
  mov  rax, [rel g_un_h1]
  mov  [r15 + UnConv.in], rax
  mov  [r15 + UnConv.out], rbx
  mov  qword [r15 + UnConv.cin], 320
  mov  qword [r15 + UnConv.cout], 4
  mov  qword [r15 + UnConv.h], UN_LAT
  mov  qword [r15 + UnConv.w], UN_LAT
  mov  qword [r15 + UnConv.stride], 1
  lea  rcx, [rel un_s_o2w]
  call un_t
  mov  [r15 + UnConv.wt], rax
  lea  rcx, [rel un_s_o2b]
  call un_tv
  mov  [r15 + UnConv.bias], rax
  mov  rcx, r15
  call un_conv3
  lea  rcx, [rel un_s_outname]
  xor  edx, edx
  xor  r8d, r8d
  mov  rdx, -1
  mov  r8, -1
  call un_set_block
  call un_log
  mov  rax, rbx
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
un8_eighth:   times 8 dd 0.125
un_f40:       dd 40.0
un_eps5:      dd 1.0e-5
un_eps6:      dd 1.0e-6
  align 8
un_one64:     dq 1.0
g_un_ready:   dq 0
g_un_arena:   dq 0
g_un_arena_used: dq 0
g_un_wbuf:    dq 0
g_un_T:       dq 0
g_un_T2:      dq 0
g_un_Tn:      dq 0
g_un_q:       dq 0
g_un_k:       dq 0
g_un_v:       dq 0
g_un_ao:      dq 0
g_un_tmp:     dq 0
g_un_kc:      dq 0
g_un_vc:      dq 0
g_un_G:       dq 0
g_un_F:       dq 0
g_un_scores:  dq 0
g_un_VT:      dq 0
g_un_h1:      dq 0
g_un_h2:      dq 0
g_un_up:      dq 0
g_un_cat:     dq 0
g_un_S:       dq 0
g_un_Q:       dq 0
g_un_ga:      dq 0
g_un_gb:      dq 0
g_un_ctx:     dq 0
g_un_x:       dq 0
g_un_cx:      dq 0
g_un_h:       dq 0
g_un_w:       dq 0
g_un_t0:      dq 0
g_un_hs:      times 12 dq 0
g_un_gemm:    times SgGemm_size / 8 dq 0
g_un_conv:    times UnConv_size / 8 dq 0
g_un_gn:      times UnGn_size / 8 dq 0
g_un_lin:     times UnLin_size / 8 dq 0
g_un_attn:    times UnAttn_size / 8 dq 0
  align 32
g_un_temb:    times 320 dd 0
g_un_emb1:    times 1280 dd 0
g_un_emb:     times 1280 dd 0
g_un_semb:    times 1280 dd 0
g_un_embo:    times 1280 dd 0
g_un_blk:     times 64 db 0
g_un_name:    times 192 db 0
  align 4
un_hs_c:      dd 320, 320, 320, 320, 640, 640, 640, 1280, 1280, 1280, 1280, 1280
un_ob_cout:   dd 1280, 1280, 1280, 1280, 1280, 1280, 640, 640, 640, 320, 320, 320
un_ob_st:     db 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1
un_ob_up:     db 0, 0, 1, 0, 0, 2, 0, 0, 2, 0, 0, 0
un_prefix:    db "model.diffusion_model.", 0
un_s_input:   db "input_blocks.", 0
un_s_output:  db "output_blocks.", 0
un_s_middle:  db "middle_block.", 0
un_s_outname: db "out", 0
un_empty:     db 0
un_s_w:       db "weight", 0
un_s_b:       db "bias", 0
un_s_in0w:    db "in_layers.0.weight", 0
un_s_in0b:    db "in_layers.0.bias", 0
un_s_in2w:    db "in_layers.2.weight", 0
un_s_in2b:    db "in_layers.2.bias", 0
un_s_embw:    db "emb_layers.1.weight", 0
un_s_embb:    db "emb_layers.1.bias", 0
un_s_out0w:   db "out_layers.0.weight", 0
un_s_out0b:   db "out_layers.0.bias", 0
un_s_out3w:   db "out_layers.3.weight", 0
un_s_out3b:   db "out_layers.3.bias", 0
un_s_skipw:   db "skip_connection.weight", 0
un_s_skipb:   db "skip_connection.bias", 0
un_s_normw:   db "norm.weight", 0
un_s_normb:   db "norm.bias", 0
un_s_pinw:    db "proj_in.weight", 0
un_s_pinb:    db "proj_in.bias", 0
un_s_poutw:   db "proj_out.weight", 0
un_s_poutb:   db "proj_out.bias", 0
un_s_n1w:     db "transformer_blocks.0.norm1.weight", 0
un_s_n1b:     db "transformer_blocks.0.norm1.bias", 0
un_s_n2w:     db "transformer_blocks.0.norm2.weight", 0
un_s_n2b:     db "transformer_blocks.0.norm2.bias", 0
un_s_n3w:     db "transformer_blocks.0.norm3.weight", 0
un_s_n3b:     db "transformer_blocks.0.norm3.bias", 0
un_s_a1q:     db "transformer_blocks.0.attn1.to_q.weight", 0
un_s_a1k:     db "transformer_blocks.0.attn1.to_k.weight", 0
un_s_a1v:     db "transformer_blocks.0.attn1.to_v.weight", 0
un_s_a1ow:    db "transformer_blocks.0.attn1.to_out.0.weight", 0
un_s_a1ob:    db "transformer_blocks.0.attn1.to_out.0.bias", 0
un_s_a2q:     db "transformer_blocks.0.attn2.to_q.weight", 0
un_s_a2k:     db "transformer_blocks.0.attn2.to_k.weight", 0
un_s_a2v:     db "transformer_blocks.0.attn2.to_v.weight", 0
un_s_a2ow:    db "transformer_blocks.0.attn2.to_out.0.weight", 0
un_s_a2ob:    db "transformer_blocks.0.attn2.to_out.0.bias", 0
un_s_ffpw:    db "transformer_blocks.0.ff.net.0.proj.weight", 0
un_s_ffpb:    db "transformer_blocks.0.ff.net.0.proj.bias", 0
un_s_ff2w:    db "transformer_blocks.0.ff.net.2.weight", 0
un_s_ff2b:    db "transformer_blocks.0.ff.net.2.bias", 0
un_s_opw:     db "op.weight", 0
un_s_opb:     db "op.bias", 0
un_s_convw:   db "conv.weight", 0
un_s_convb:   db "conv.bias", 0
un_s_te0w:    db "time_embed.0.weight", 0
un_s_te0b:    db "time_embed.0.bias", 0
un_s_te2w:    db "time_embed.2.weight", 0
un_s_te2b:    db "time_embed.2.bias", 0
un_s_o0w:     db "out.0.weight", 0
un_s_o0b:     db "out.0.bias", 0
un_s_o2w:     db "out.2.weight", 0
un_s_o2b:     db "out.2.bias", 0
un_s_log:     db "asmrun: unet ", 0
un_s_ms:      db " ms=", 0
un_s_nl:      db 10, 0
un_s_missing: db "asmrun: missing tensor ", 0
un_err_missing: db "the SD-Turbo GGUF lacks a UNet tensor", 0
un_err_arena: db "the image generator's arena is full", 0
section .text

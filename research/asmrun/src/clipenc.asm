; clipenc.asm — SD-Turbo's text encoder: OpenCLIP ViT-H/14's text tower, as
; Stable Diffusion 2.x uses it. 77 token ids in, 77 x 1024 context vectors out
; (what the UNet's cross-attention reads).
;
;   x = token_embedding[id] + position_embedding[t]           t = 0..76
;   23 of the 24 layers (SD 2.x takes the penultimate layer), each pre-norm:
;     x += out_proj( attention( LN1(x) ) )   16 heads of 64, causal, scale 1/8
;     x += fc2( gelu( fc1( LN2(x) ) ) )      1024 -> 4096 -> 1024, exact GELU
;   context = final_layer_norm(x)
;
; Weights come from the SD-Turbo GGUF (sd.asm's index), names
; "cond_stage_model.transformer.text_model.*". The matrices stay in the file
; (q8_0) and sg_matmul dequantises them a strip at a time; the LayerNorm
; weights (q8_0 in this file) and biases are made f32 once at ce_load.
;
; Buffers have CE_ROWS = 80 rows: 77 positions, then 3 zero rows, so the UNet's
; cross-attention can treat the context as 80 keys (a multiple of 4 and 8) and
; mask the last three.
;
;   ce_load()                       after sd_index: find and prepare the weights
;   ce_forward(rcx=u32 ids[77]) -> rax = f32 context [CE_ROWS][1024]
;
; %included into asmrun.asm (bits 64, default rel) after sdk.asm.

section .text

CE_T      equ 77
CE_ROWS   equ 80
CE_D      equ 1024
CE_FF     equ 4096
CE_HEADS  equ 16
CE_LAYERS equ 23                 ; of 24: the penultimate layer's output

; per layer: f32 vectors (LayerNorms, biases) and Tensor* matrices
CEL_LN1W equ 0
CEL_LN1B equ 8
CEL_QW   equ 16
CEL_QB   equ 24
CEL_KW   equ 32
CEL_KB   equ 40
CEL_VW   equ 48
CEL_VB   equ 56
CEL_OW   equ 64
CEL_OB   equ 72
CEL_LN2W equ 80
CEL_LN2B equ 88
CEL_F1W  equ 96
CEL_F1B  equ 104
CEL_F2W  equ 112
CEL_F2B  equ 120
CEL_SIZE equ 128

; ce_name(rcx=suffix zstr, rdx=layer or -1) -> rax = Tensor*: the tensor
; "cond_stage_model.transformer.text_model.[encoder.layers.<l>.]<suffix>",
; rt_fatal when it is missing. Preserves the nonvolatile registers.
ce_name:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rbx, rcx
  lea  rdi, [rel g_ce_name]
  lea  rsi, [rel ce_prefix]
.p:
  lodsb
  test al, al
  jz   .pd
  stosb
  jmp  .p
.pd:
  test rdx, rdx
  js   .suffix
  lea  rsi, [rel ce_layers]
.l:
  lodsb
  test al, al
  jz   .ld
  stosb
  jmp  .l
.ld:
  mov  rax, rdx                ; the layer number, at most two digits
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
.suffix:
  mov  rsi, rbx
.s:
  lodsb
  stosb
  test al, al
  jnz  .s
  lea  rcx, [rel g_ce_name]
  call sd_find
  test rax, rax
  jz   .missing
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.missing:
  lea  rcx, [rel ce_s_missing]
  call rt_err_str
  lea  rcx, [rel g_ce_name]
  call rt_err_str
  lea  rcx, [rel ce_s_nl]
  call rt_err_str
  lea  rcx, [rel ce_err_missing]
  call rt_fatal

; ce_vec(rcx=Tensor*) -> rax = its ne0 values as f32: the data itself when it
; is f32, else a new dequantised copy. Preserves the nonvolatile registers.
ce_vec:
  push rbx
  WINPROLOGUE
  mov  rbx, rcx
  mov  rax, [rbx + Tensor.data]
  cmp  dword [rbx + Tensor.type], GGML_F32
  je   .ret
  mov  rcx, [rbx + Tensor.ne0]
  shl  rcx, 2
  call rt_alloc
  push rax
  sub  rsp, 8
  mov  rcx, rax
  mov  rdx, [rbx + Tensor.data]
  mov  r8, [rbx + Tensor.ne0]
  mov  r9d, [rbx + Tensor.type]
  call sg_deq_row
  add  rsp, 8
  pop  rax
.ret:
  WINEPILOGUE
  pop  rbx
  ret

; CE_W off, suffix — g_ce_layers[l].off = the Tensor* (r12 = layer, rbx = its record)
%macro CE_W 2
  lea  rcx, [rel %2]
  mov  rdx, r12
  call ce_name
  mov  [rbx + %1], rax
%endmacro
; CE_V off, suffix — the same, as an f32 vector
%macro CE_V 2
  lea  rcx, [rel %2]
  mov  rdx, r12
  call ce_name
  mov  rcx, rax
  call ce_vec
  mov  [rbx + %1], rax
%endmacro

; ce_load() — resolve every weight the encoder reads and allocate its
; buffers. Needs sd_index first. Preserves the nonvolatile registers.
ce_load:
  push rbx
  push r12
  WINPROLOGUE
  lea  rbx, [rel g_ce_layers]
  xor  r12d, r12d
.l:
  cmp  r12, CE_LAYERS
  jae  .ld
  CE_V CEL_LN1W, ce_s_ln1w
  CE_V CEL_LN1B, ce_s_ln1b
  CE_W CEL_QW,   ce_s_qw
  CE_V CEL_QB,   ce_s_qb
  CE_W CEL_KW,   ce_s_kw
  CE_V CEL_KB,   ce_s_kb
  CE_W CEL_VW,   ce_s_vw
  CE_V CEL_VB,   ce_s_vb
  CE_W CEL_OW,   ce_s_ow
  CE_V CEL_OB,   ce_s_ob
  CE_V CEL_LN2W, ce_s_ln2w
  CE_V CEL_LN2B, ce_s_ln2b
  CE_W CEL_F1W,  ce_s_f1w
  CE_V CEL_F1B,  ce_s_f1b
  CE_W CEL_F2W,  ce_s_f2w
  CE_V CEL_F2B,  ce_s_f2b
  add  rbx, CEL_SIZE
  inc  r12
  jmp  .l
.ld:
  lea  rcx, [rel ce_s_tok]
  mov  rdx, -1
  call ce_name
  mov  [rel g_ce_tok], rax
  lea  rcx, [rel ce_s_pos]
  mov  rdx, -1
  call ce_name
  mov  [rel g_ce_pos], rax
  mov  rax, [rel g_ce_tok]     ; ce_forward addresses embedding rows as q8_0
  cmp  dword [rax + Tensor.type], GGML_Q8_0
  jne  .badtype
  mov  rax, [rel g_ce_pos]
  cmp  dword [rax + Tensor.type], GGML_Q8_0
  jne  .badtype
  lea  rcx, [rel ce_s_lnfw]
  mov  rdx, -1
  call ce_name
  mov  rcx, rax
  call ce_vec
  mov  [rel g_ce_lnfw], rax
  lea  rcx, [rel ce_s_lnfb]
  mov  rdx, -1
  call ce_name
  mov  rcx, rax
  call ce_vec
  mov  [rel g_ce_lnfb], rax
  mov  ecx, CE_ROWS * CE_D * 4
  call rt_alloc
  mov  [rel g_ce_x], rax
  mov  ecx, CE_ROWS * CE_D * 4
  call rt_alloc
  mov  [rel g_ce_h], rax
  mov  ecx, CE_ROWS * CE_D * 4
  call rt_alloc
  mov  [rel g_ce_q], rax
  mov  ecx, CE_ROWS * CE_D * 4
  call rt_alloc
  mov  [rel g_ce_k], rax
  mov  ecx, CE_ROWS * CE_D * 4
  call rt_alloc
  mov  [rel g_ce_v], rax
  mov  ecx, CE_ROWS * CE_D * 4
  call rt_alloc
  mov  [rel g_ce_att], rax
  mov  ecx, CE_ROWS * CE_FF * 4
  call rt_alloc
  mov  [rel g_ce_ff], rax
  WINEPILOGUE
  pop  r12
  pop  rbx
  ret
.badtype:
  lea  rcx, [rel ce_err_embtype]
  call rt_fatal

; ce_stats(rcx=context) — stderr: mean, standard deviation, min and max over
; the 77 x 1024 context values, and the first four values of rows 0, 1 and 76.
; Preserves the nonvolatile registers.
ce_stats:
  push rbx
  push rsi
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32] sum, [+36] sum of squares, [+40] min, [+44] max (f32)
  mov  rsi, rcx
  vxorps xmm0, xmm0, xmm0
  vmovss [rsp+32], xmm0
  vmovss [rsp+36], xmm0
  vmovss xmm0, [rsi]
  vmovss [rsp+40], xmm0
  vmovss [rsp+44], xmm0
  xor  ebx, ebx
.l:
  cmp  ebx, CE_T * CE_D
  jae  .ld
  vmovss xmm0, [rsi + rbx*4]
  vaddss xmm1, xmm0, [rsp+32]
  vmovss [rsp+32], xmm1
  vmulss xmm1, xmm0, xmm0
  vaddss xmm1, xmm1, [rsp+36]
  vmovss [rsp+36], xmm1
  vminss xmm1, xmm0, [rsp+40]
  vmovss [rsp+40], xmm1
  vmaxss xmm1, xmm0, [rsp+44]
  vmovss [rsp+44], xmm1
  inc  ebx
  jmp  .l
.ld:
  lea  rcx, [rel ce_s_mean]
  call rt_err_str
  mov  eax, CE_T * CE_D
  vcvtsi2ss xmm1, xmm1, eax
  vmovss xmm0, [rsp+32]
  vdivss xmm0, xmm0, xmm1
  vmovss [rsp+48], xmm0        ; mean
  call rt_err_f32
  lea  rcx, [rel ce_s_std]
  call rt_err_str
  mov  eax, CE_T * CE_D
  vcvtsi2ss xmm1, xmm1, eax
  vmovss xmm0, [rsp+36]
  vdivss xmm0, xmm0, xmm1      ; E[x^2]
  vmovss xmm1, [rsp+48]
  vmulss xmm1, xmm1, xmm1
  vsubss xmm0, xmm0, xmm1
  vsqrtss xmm0, xmm0, xmm0
  call rt_err_f32
  lea  rcx, [rel ce_s_min]
  call rt_err_str
  vmovss xmm0, [rsp+40]
  call rt_err_f32
  lea  rcx, [rel ce_s_max]
  call rt_err_str
  vmovss xmm0, [rsp+44]
  call rt_err_f32
  lea  rcx, [rel ce_s_nl]
  call rt_err_str
  xor  ebx, ebx                ; rows 0, 1, 76
.r:
  cmp  ebx, 3
  jae  .done
  lea  rax, [rel ce_stat_rows]
  mov  eax, [rax + rbx*4]
  mov  [rsp+52], eax
  lea  rcx, [rel ce_s_row]
  call rt_err_str
  mov  ecx, [rsp+52]
  call rt_err_u64
  lea  rcx, [rel ce_s_colon]
  call rt_err_str
  mov  eax, [rsp+52]
  shl  eax, 12
  vmovss xmm0, [rsi + rax]
  call rt_err_f32
  lea  rcx, [rel ce_s_sp]
  call rt_err_str
  mov  eax, [rsp+52]
  shl  eax, 12
  vmovss xmm0, [rsi + rax + 4]
  call rt_err_f32
  lea  rcx, [rel ce_s_sp]
  call rt_err_str
  mov  eax, [rsp+52]
  shl  eax, 12
  vmovss xmm0, [rsi + rax + 8]
  call rt_err_f32
  lea  rcx, [rel ce_s_sp]
  call rt_err_str
  mov  eax, [rsp+52]
  shl  eax, 12
  vmovss xmm0, [rsi + rax + 12]
  call rt_err_f32
  lea  rcx, [rel ce_s_nl]
  call rt_err_str
  inc  ebx
  jmp  .r
.done:
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; ce_gemm(rcx=A, rdx=Tensor* W, r8=bias f32, r9=C, [5]=acc) — C (+)= A W^T + bias
; over the CE_T rows; k = W.ne0, n = W.ne1. Preserves the nonvolatile registers.
ce_gemm:
  push rbx
  WINPROLOGUE
  lea  rbx, [rel g_ce_job]
  mov  [rbx + SgGemm.a], rcx
  mov  rax, [rdx + Tensor.ne0]
  mov  [rbx + SgGemm.k], rax
  shl  rax, 2
  mov  [rbx + SgGemm.lda], rax
  mov  rax, [rdx + Tensor.ne1]
  mov  [rbx + SgGemm.n], rax
  shl  rax, 2
  mov  [rbx + SgGemm.ldc], rax
  mov  rax, [rdx + Tensor.data]
  mov  [rbx + SgGemm.w], rax
  mov  eax, [rdx + Tensor.type]
  mov  [rbx + SgGemm.wtype], rax
  mov  qword [rbx + SgGemm.wrow], 0
  mov  qword [rbx + SgGemm.m], CE_T
  mov  [rbx + SgGemm.bias], r8
  mov  [rbx + SgGemm.c], r9
  mov  rax, [rbp + 16 + 8 + 32] ; arg 5: above rbp, the return address and rbx, then the 32-byte shadow
  mov  [rbx + SgGemm.acc], rax
  mov  rcx, rbx
  call sg_matmul
  WINEPILOGUE
  pop  rbx
  ret

; ce_ln_rows(rcx=out, rdx=in, r8=w, r9=b) — sg_layernorm on rows 0..CE_T-1.
; Preserves the nonvolatile registers.
ce_ln_rows:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 16
  mov  rsi, rcx
  mov  rdi, rdx
  mov  r12, r8
  mov  r13, r9
  xor  ebx, ebx
.l:
  cmp  ebx, CE_T
  jae  .d
  mov  rcx, rsi
  mov  rdx, rdi
  mov  r8, r12
  mov  r9, r13
  mov  qword [rsp+32], CE_D
  call sg_layernorm
  add  rsi, CE_D * 4
  add  rdi, CE_D * 4
  inc  ebx
  jmp  .l
.d:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ce_embed(rcx=u32 ids[CE_T]) — g_ce_x[t] = token_embedding[id_t] +
; position_embedding[t]. Preserves the nonvolatile registers.
ce_embed:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  mov  rsi, rcx
  xor  ebx, ebx
.emb:
  cmp  ebx, CE_T
  jae  .embd
  mov  rdi, rbx
  shl  rdi, 12                 ; t * CE_D * 4
  add  rdi, [rel g_ce_x]
  mov  r12, [rel g_ce_tok]
  mov  eax, [rsi + rbx*4]
  imul rax, [r12 + Tensor.ne0]
  shr  rax, 5
  imul rax, rax, 34            ; q8_0 rows: 34 bytes per 32 values
  mov  rdx, [r12 + Tensor.data]
  add  rdx, rax
  mov  rcx, rdi
  mov  r8, CE_D
  mov  r9d, [r12 + Tensor.type]
  call sg_deq_row
  mov  r12, [rel g_ce_pos]
  mov  eax, ebx
  imul rax, [r12 + Tensor.ne0]
  shr  rax, 5
  imul rax, rax, 34
  mov  rdx, [r12 + Tensor.data]
  add  rdx, rax
  mov  rcx, [rel g_ce_h]       ; the position row, then add it
  mov  r8, CE_D
  mov  r9d, [r12 + Tensor.type]
  call sg_deq_row
  mov  rcx, rdi
  mov  rdx, [rel g_ce_h]
  mov  r8, CE_D
  call k_add
  inc  ebx
  jmp  .emb
.embd:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ce_check(rcx=u32 ids[CE_T]) — verify the cache-blocked kernel: the
; embeddings through layer 0's q_proj by sg_matmul, then three outputs
; recomputed as a plain scalar dot of the dequantised weight row and the
; activation row, plus the bias. Both values to stderr per output. Preserves
; the nonvolatile registers.
ce_check:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = ce_gemm's acc argument
  call ce_embed
  lea  rbx, [rel g_ce_layers]  ; layer 0
  mov  rsi, [rbx + CEL_QW]
  cmp  dword [rsi + Tensor.type], GGML_Q8_0
  jne  .skip
  mov  qword [rsp+32], 0
  mov  rcx, [rel g_ce_x]
  mov  rdx, rsi
  mov  r8, [rbx + CEL_QB]
  mov  r9, [rel g_ce_q]
  call ce_gemm
  xor  r12d, r12d
.c:
  cmp  r12d, 3
  jae  .done
  lea  rax, [rel ce_chk_m]
  mov  r13d, [rax + r12*4]     ; m
  lea  rax, [rel ce_chk_n]
  mov  edi, [rax + r12*4]      ; n
  ; the weight row n as f32, in g_ce_h's spare row (row 77)
  mov  rax, rdi
  imul rax, rax, (CE_D / 32) * 34
  mov  rdx, [rsi + Tensor.data]
  add  rdx, rax
  mov  rcx, [rel g_ce_h]
  add  rcx, CE_T * CE_D * 4
  mov  r8, CE_D
  mov  r9d, GGML_Q8_0
  call sg_deq_row
  ; ref = bias[n] + sum_k x[m][k] w[k], one value at a time
  mov  rax, [rbx + CEL_QB]
  vmovss xmm1, [rax + rdi*4]
  mov  rcx, [rel g_ce_h]
  add  rcx, CE_T * CE_D * 4
  mov  rdx, r13
  shl  rdx, 12
  add  rdx, [rel g_ce_x]
  xor  eax, eax
.dot:
  vmovss xmm0, [rdx + rax*4]
  vfmadd231ss xmm1, xmm0, [rcx + rax*4]
  inc  eax
  cmp  eax, CE_D
  jb   .dot
  vmovss [rsp+40], xmm1        ; ref
  lea  rcx, [rel ce_s_chk]
  call rt_err_str
  mov  rcx, r13
  call rt_err_u64
  lea  rcx, [rel ce_s_chk_n]
  call rt_err_str
  mov  rcx, rdi
  call rt_err_u64
  lea  rcx, [rel ce_s_chk_k]
  call rt_err_str
  mov  rax, r13
  shl  rax, 12
  add  rax, [rel g_ce_q]
  vmovss xmm0, [rax + rdi*4]
  call rt_err_f32
  lea  rcx, [rel ce_s_chk_r]
  call rt_err_str
  vmovss xmm0, [rsp+40]
  call rt_err_f32
  lea  rcx, [rel ce_s_nl]
  call rt_err_str
  inc  r12d
  jmp  .c
.skip:
  lea  rcx, [rel ce_s_chk_skip]
  call rt_err_str
.done:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ce_forward(rcx=u32 ids[CE_T]) -> rax = the context, f32 [CE_ROWS][CE_D].
; Preserves the nonvolatile registers.
ce_forward:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  sub  rsp, 16
  call ce_embed
  ; the layers
  lea  rbx, [rel g_ce_layers]
  xor  r12d, r12d
.layer:
  cmp  r12, CE_LAYERS
  jae  .final
  mov  rcx, [rel g_ce_h]
  mov  rdx, [rel g_ce_x]
  mov  r8, [rbx + CEL_LN1W]
  mov  r9, [rbx + CEL_LN1B]
  call ce_ln_rows
  mov  qword [rsp+32], 0
  mov  rcx, [rel g_ce_h]
  mov  rdx, [rbx + CEL_QW]
  mov  r8, [rbx + CEL_QB]
  mov  r9, [rel g_ce_q]
  call ce_gemm
  mov  qword [rsp+32], 0
  mov  rcx, [rel g_ce_h]
  mov  rdx, [rbx + CEL_KW]
  mov  r8, [rbx + CEL_KB]
  mov  r9, [rel g_ce_k]
  call ce_gemm
  mov  qword [rsp+32], 0
  mov  rcx, [rel g_ce_h]
  mov  rdx, [rbx + CEL_VW]
  mov  r8, [rbx + CEL_VB]
  mov  r9, [rel g_ce_v]
  call ce_gemm
  lea  rcx, [rel g_ce_attn]
  mov  rax, [rel g_ce_q]
  mov  [rcx + SgAttn.q], rax
  mov  rax, [rel g_ce_k]
  mov  [rcx + SgAttn.k], rax
  mov  rax, [rel g_ce_v]
  mov  [rcx + SgAttn.v], rax
  mov  rax, [rel g_ce_att]
  mov  [rcx + SgAttn.out], rax
  call sg_attn
  mov  qword [rsp+32], 1       ; x += out_proj(attention)
  mov  rcx, [rel g_ce_att]
  mov  rdx, [rbx + CEL_OW]
  mov  r8, [rbx + CEL_OB]
  mov  r9, [rel g_ce_x]
  call ce_gemm
  mov  rcx, [rel g_ce_h]
  mov  rdx, [rel g_ce_x]
  mov  r8, [rbx + CEL_LN2W]
  mov  r9, [rbx + CEL_LN2B]
  call ce_ln_rows
  mov  qword [rsp+32], 0
  mov  rcx, [rel g_ce_h]
  mov  rdx, [rbx + CEL_F1W]
  mov  r8, [rbx + CEL_F1B]
  mov  r9, [rel g_ce_ff]
  call ce_gemm
  mov  rcx, [rel g_ce_ff]
  mov  edx, CE_T * CE_FF
  call sg_gelu
  mov  qword [rsp+32], 1       ; x += fc2(gelu(fc1))
  mov  rcx, [rel g_ce_ff]
  mov  rdx, [rbx + CEL_F2W]
  mov  r8, [rbx + CEL_F2B]
  mov  r9, [rel g_ce_x]
  call ce_gemm
  add  rbx, CEL_SIZE
  inc  r12
  jmp  .layer
.final:
  mov  rcx, [rel g_ce_x]       ; in place
  mov  rdx, [rel g_ce_x]
  mov  r8, [rel g_ce_lnfw]
  mov  r9, [rel g_ce_lnfb]
  call ce_ln_rows
  mov  rax, [rel g_ce_x]
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

section .data
  align 8
g_ce_layers: times CE_LAYERS * CEL_SIZE / 8 dq 0
g_ce_tok:    dq 0              ; Tensor* token_embedding [49408][1024]
g_ce_pos:    dq 0              ; Tensor* position_embedding [77][1024]
g_ce_lnfw:   dq 0              ; f32 final_layer_norm
g_ce_lnfb:   dq 0
g_ce_x:      dq 0              ; f32 [78][1024] the residual stream, then the context
g_ce_h:      dq 0              ; f32 [78][1024] normalised x
g_ce_q:      dq 0
g_ce_k:      dq 0
g_ce_v:      dq 0
g_ce_att:    dq 0              ; f32 [78][1024] attention output
g_ce_ff:     dq 0              ; f32 [78][4096] fc1 output
g_ce_job:    times SgGemm_size / 8 dq 0
g_ce_attn:                     ; the encoder's attention: 77 rows, 16 heads of 64, causal, 1/8
  dq 0, 0, 0, 0                ; q, k, v, out (set per layer)
  dq CE_T, CE_T, CE_HEADS, CE_D / CE_HEADS
  dq CE_D * 4, CE_D * 4, 1
  dd 0.125, 0
g_ce_name:   times 160 db 0
ce_prefix:   db "cond_stage_model.transformer.text_model.", 0
ce_layers:   db "encoder.layers.", 0
ce_s_ln1w:   db "layer_norm1.weight", 0
ce_s_ln1b:   db "layer_norm1.bias", 0
ce_s_qw:     db "self_attn.q_proj.weight", 0
ce_s_qb:     db "self_attn.q_proj.bias", 0
ce_s_kw:     db "self_attn.k_proj.weight", 0
ce_s_kb:     db "self_attn.k_proj.bias", 0
ce_s_vw:     db "self_attn.v_proj.weight", 0
ce_s_vb:     db "self_attn.v_proj.bias", 0
ce_s_ow:     db "self_attn.out_proj.weight", 0
ce_s_ob:     db "self_attn.out_proj.bias", 0
ce_s_ln2w:   db "layer_norm2.weight", 0
ce_s_ln2b:   db "layer_norm2.bias", 0
ce_s_f1w:    db "mlp.fc1.weight", 0
ce_s_f1b:    db "mlp.fc1.bias", 0
ce_s_f2w:    db "mlp.fc2.weight", 0
ce_s_f2b:    db "mlp.fc2.bias", 0
ce_s_tok:    db "embeddings.token_embedding.weight", 0
ce_s_pos:    db "embeddings.position_embedding.weight", 0
ce_s_lnfw:   db "final_layer_norm.weight", 0
ce_s_lnfb:   db "final_layer_norm.bias", 0
ce_s_missing: db "asmrun: missing tensor ", 0
ce_s_nl:     db 10, 0
ce_err_missing: db "the SD-Turbo GGUF lacks a text encoder tensor", 0
ce_err_embtype: db "the text encoder's embeddings are not q8_0 (this encoder reads q8_0 rows)", 0
  align 4
ce_stat_rows: dd 0, 1, 76
ce_chk_m:    dd 0, 5, 76        ; ce_check: the outputs compared (row, column)
ce_chk_n:    dd 0, 517, 1023
ce_s_chk:    db "asmrun: gemm check m=", 0
ce_s_chk_n:  db " n=", 0
ce_s_chk_k:  db " kernel=", 0
ce_s_chk_r:  db " ref_scalar=", 0
ce_s_chk_skip: db "asmrun: gemm check skipped (layer 0 q_proj is not q8_0)", 10, 0
ce_s_mean:   db "asmrun: context mean=", 0
ce_s_std:    db " std=", 0
ce_s_min:    db " min=", 0
ce_s_max:    db " max=", 0
ce_s_row:    db "asmrun: context row ", 0
ce_s_colon:  db ": ", 0
ce_s_sp:     db " ", 0
section .text

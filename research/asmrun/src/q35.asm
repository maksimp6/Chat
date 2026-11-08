; q35.asm — Qwen3.5 (the 9B and its family): the hybrid of Gated DeltaNet
; linear-attention layers and full-attention layers, one token at a time, on
; the GGUF that gguf_open indexed (general.architecture "qwen35").
;
; A layer is, with x the residual stream (RMSNorm weights are "1 + w" unless
; the file already added the 1; q35_setup looks):
;   linear:  xn = norm(x)
;            qkv = Wqkv xn; z = Wz xn; b = Wb xn; a = Wa xn
;            qkv = silu(causal conv over this and the 3 previous qkv)   (per channel)
;            q, k per key head l2-normalised, q scaled by dk^-1/2
;            per value head h (key head h / (lv/lk)):
;              beta = sigmoid(b_h); g = A_h softplus(a_h + dt_h)        (A = -e^A_log)
;              S_h *= e^g; d = (v_h - S_h^T k) beta; S_h += k d^T; o_h = S_h^T q
;              o_h = rmsnorm(o_h) w_norm silu(z_h)
;            x += Wout o
;   full:    xn = norm(x); [q | gate] = Wq xn (per head); k = Wk xn; v = Wv xn
;            q, k: per-head rmsnorm, rotary on the first hd/4 of hd; k, v cached
;            o = softmax(q K^T / sqrt hd) V (GQA); o *= sigmoid(gate); x += Wo o
;   both:    x += Wdown(silu(Wgate norm(x)) * Wup norm(x))
; then logits = Wout norm(x).
;
;   q35_setup(rcx=Model*, rdx=n_ctx, r8=nthreads) -> rax   called by model_load
;   q35_forward(rcx=Model*, rdx=token, r8=pos) -> rax = logits
;   q35_generate(rcx=Model*, rdx=ids, r8=n, r9=max_new)   continues the conversation
;   q35_encode_chat(rcx=text, rdx=len, r8=ids, r9=max) -> rax = ids
;
; %included into asmrun.asm (bits 64, default rel) after q35k.asm and before model.asm.

section .text

struc Q35L
  .kind:       resq 1   ; 0 linear attention, 1 full attention
  .attn_norm:  resq 1   ; f32*
  .ffn_norm:   resq 1   ; f32*
  .ffn_gate:   resq 1   ; Tensor*
  .ffn_up:     resq 1
  .ffn_down:   resq 1
  .wq:         resq 1   ; Tensor* (full): [embd -> 2 heads hd] fused with the gate, or [embd -> heads hd]
  .wqg:        resq 1   ; Tensor* the gate when it is a tensor of its own, else 0
  .wk:         resq 1
  .wv:         resq 1
  .wo:         resq 1
  .q_norm:     resq 1   ; f32* [hd]
  .k_norm:     resq 1
  .wqkv:       resq 1   ; Tensor* (linear): [embd -> 2 lk dk + lv dv]
  .wz:         resq 1   ; [embd -> lv dv]
  .wb:         resq 1   ; [embd -> lv]
  .wa:         resq 1   ; [embd -> lv]
  .conv_w:     resq 1   ; f32* [conv_dim][4]
  .dt_bias:    resq 1   ; f32* [lv]
  .A:          resq 1   ; f32* [lv], -e^A_log
  .ssm_norm:   resq 1   ; f32* [dv]
  .wout:       resq 1   ; [lv dv -> embd]
  .conv_state: resq 1   ; f32 [conv_dim][3]
  .S:          resq 1   ; f32 [lv][dk][dv]
  .kcache:     resq 1   ; f32 [ctx][kv_dim] (full)
  .vcache:     resq 1
endstruc

; ===========================================================================
; Small helpers
; ===========================================================================

; q35_t(rcx=layer, rdx=suffix zstr) -> rax = Tensor* "blk.<layer>.<suffix>" or 0.
; The name stays in g35_name for a message. Preserves the nonvolatile registers.
q35_t:
  push rbx
  push rsi
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  lea  rcx, [rel g35_name]
  mov  rdx, rbx
  call gguf_blk_prefix         ; rax = length of "blk.N."
  lea  rcx, [rel g35_name]
  add  rcx, rax
.cp:
  mov  dl, [rsi]
  mov  [rcx], dl
  inc  rsi
  inc  rcx
  test dl, dl
  jnz  .cp
  mov  rcx, [rel g35_model]
  lea  rdx, [rel g35_name]
  call gguf_find_tensor
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; q35_vec(rcx=Tensor*) -> rax = its row 0 as f32 (the data when f32, else a
; dequantised copy). Preserves the nonvolatile registers.
q35_vec:
  push rbx
  WINPROLOGUE
  sub  rsp, 16
  mov  rbx, rcx
  mov  rax, [rbx + Tensor.data]
  cmp  dword [rbx + Tensor.type], GGML_F32
  je   .ret
  mov  rcx, [rbx + Tensor.ne0]
  shl  rcx, 2
  call rt_alloc
  mov  [rsp+32], rax
  mov  rcx, rbx
  xor  edx, edx
  mov  r8, rax
  call model_dequant_row
  mov  rax, [rsp+32]
.ret:
  WINEPILOGUE
  pop  rbx
  ret

; q35_tv(rcx=layer, rdx=suffix) -> rax = q35_vec(q35_t(...)) or 0.
q35_tv:
  WINPROLOGUE
  call q35_t
  test rax, rax
  jz   .ret
  mov  rcx, rax
  call q35_vec
.ret:
  WINEPILOGUE
  ret

; q35_missing() — "asmrun: Qwen3.5 tensor missing: <g35_name>" then rt_exit(1).
q35_missing:
  WINPROLOGUE
  lea  rcx, [rel q35_s_missing]
  call rt_err_str
  lea  rcx, [rel g35_name]
  call rt_err_str
  lea  rcx, [rel q35_s_nl]
  call rt_err_str
  mov  ecx, 1
  call rt_exit
  hlt

; Q35_NEED — the last q35_t/q35_tv result in rax must not be 0
%macro Q35_NEED 0
  test rax, rax
  jnz  %%ok
  call q35_missing
%%ok:
%endmacro

; q35_norm(rcx=out, rdx=in, r8=w, r9=n) — the file's RMSNorm convention.
q35_norm:
  vmovss xmm0, [rel g35_eps]
  cmp  qword [rel g35_plus1], 0
  jne  k_rmsnorm               ; the 1 is in the weights already
  jmp  q35_rmsnorm1

; q35_scale(rcx=x, rdx=n, xmm0=s) — x *= s, n a multiple of 8. Leaf.
q35_scale:
  vbroadcastss ymm1, xmm0
  xor  eax, eax
.l:
  vmulps ymm0, ymm1, [rcx + rax*4]
  vmovups [rcx + rax*4], ymm0
  add  rax, 8
  cmp  rax, rdx
  jb   .l
  ret

; q35_sigmoid1(xmm0=x) -> xmm0 = 1 / (1 + e^-x). Clobbers ymm0-ymm3.
q35_sigmoid1:
  vxorps xmm0, xmm0, [rel kc8_signmask]
  vbroadcastss ymm0, xmm0
  EXP8
  vaddss xmm0, xmm0, [rel kc_one]
  vmovss xmm1, [rel kc_one]
  vdivss xmm0, xmm1, xmm0
  ret

; MV tensor, in, out — model_matvec
%macro MV 3
  mov  rcx, %1
  mov  rdx, %2
  mov  r8, %3
  call model_matvec
%endmacro
; TR label — the execution trace of the traced token: one line for the step
; that just finished (no weight bytes)
%macro TR 1
  lea  rcx, [rel %1]
  xor  edx, edx
  call q35_tr
%endmacro
; TRW label, tensor — the same for a step that read the tensor's bytes
%macro TRW 2
  mov  rdx, %2
  mov  rdx, [rdx + Tensor.nbytes]
  lea  rcx, [rel %1]
  call q35_tr
%endmacro
; SUMS buf, n — model_fill_sums (the int8 activation for the quantised matvecs)
%macro SUMS 2
  mov  rcx, %1
  mov  rdx, %2
  call model_fill_sums
%endmacro

; ===========================================================================
; Setup
; ===========================================================================

Q35_POS_MAX equ 65536          ; the dialog's length in positions (16 MB of rope table at 64 rotary dims)

; q35_setup(rcx=Model*, rdx=n_ctx requested, r8=nthreads) -> rax = Model*.
; After gguf_open: the tokenizer, the context, the pool, every layer's tensors
; by name, the states and buffers, the rope table, the weight bytes.
q35_setup:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32] maxcols, [rsp+40] scratch
  mov  r15, rcx
  mov  [rel g35_model], rcx
  mov  r13, rdx
  mov  r14, r8
  mov  rcx, r15
  call tok_init
  ; the context: -c, else 4096 (the trained 262144 would not fit); at most the trained
  mov  rax, r13
  test rax, rax
  jnz  .ctx_nz
  mov  eax, 4096
.ctx_nz:
  cmp  rax, [r15 + Model.n_ctx_train]
  jbe  .ctx_ok
  mov  rax, [r15 + Model.n_ctx_train]
.ctx_ok:
  mov  [r15 + Model.n_ctx], rax
  mov  [rel g_n_ctx], rax
  mov  [rel g35_ctx], rax
  ; the positions the dialog may reach: the attention caches are a ring of
  ; n_ctx slots (the oldest entries are overwritten once full), the rope table
  ; and the position counter run to Q35_POS_MAX or the model's trained length
  mov  rcx, Q35_POS_MAX
  cmp  rcx, [r15 + Model.n_ctx_train]
  jbe  .pm1
  mov  rcx, [r15 + Model.n_ctx_train]
.pm1:
  cmp  rcx, rax
  jae  .pm2
  mov  rcx, rax                ; never below the window itself
.pm2:
  mov  [rel g35_posmax], rcx
  mov  eax, [r15 + Model.rms_eps]
  test eax, eax
  jnz  .eps_ok
  mov  eax, [rel q35_eps6]
.eps_ok:
  mov  [rel g35_eps], eax
  mov  rcx, r14
  call pool_start
  mov  rax, [rel g_nthreads]
  mov  [r15 + Model.n_threads], rax
  ; the dimensions gguf_open read
  mov  rax, [r15 + Model.n_embd]
  mov  [rel g35_embd], rax
  mov  rax, [r15 + Model.n_ff]
  mov  [rel g35_ff], rax
  mov  rax, [r15 + Model.n_head]
  mov  [rel g35_nhead], rax
  mov  rax, [r15 + Model.n_head_kv]
  mov  [rel g35_nkv], rax
  mov  rax, [r15 + Model.head_dim]
  mov  [rel g35_hd], rax
  shr  rax, 2
  mov  [rel g35_rot], rax
  mov  rax, [r15 + Model.kv_dim]
  mov  [rel g35_kvdim], rax
  mov  rax, [r15 + Model.n_vocab]
  mov  [rel g35_vocab], rax
  mov  rax, [r15 + Model.n_layer]
  mov  [rel g35_nlayer], rax
  imul rax, rax, Q35L_size
  mov  rcx, rax
  call rt_alloc
  mov  [rel g35_layers], rax
  mov  rdi, rax
  xor  ebx, ebx                ; layer
.layer:
  cmp  rbx, [rel g35_nlayer]
  jae  .layers_done
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_norm]
  call q35_tv
  Q35_NEED
  mov  [rdi + Q35L.attn_norm], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ffn_norm]
  call q35_tv
  Q35_NEED
  mov  [rdi + Q35L.ffn_norm], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ffn_gate]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.ffn_gate], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ffn_up]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.ffn_up], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ffn_down]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.ffn_down], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_q]
  call q35_t
  test rax, rax
  jz   .linear
  ; --- full attention ---
  mov  qword [rdi + Q35L.kind], 1
  mov  [rdi + Q35L.wq], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_gate]
  call q35_t
  mov  [rdi + Q35L.wqg], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_k]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.wk], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_v]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.wv], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_output]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.wo], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_q_norm]
  call q35_tv
  Q35_NEED
  mov  [rdi + Q35L.q_norm], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_k_norm]
  call q35_tv
  Q35_NEED
  mov  [rdi + Q35L.k_norm], rax
  ; q and its gate: fused rows (2 heads hd) or a gate tensor of its own
  mov  rax, [rdi + Q35L.wq]
  mov  rax, [rax + Tensor.ne1]
  mov  rcx, [rel g35_nhead]
  imul rcx, [rel g35_hd]
  cmp  rax, rcx
  jne  .q_fused_chk
  cmp  qword [rdi + Q35L.wqg], 0
  je   .bad_qgate
  mov  qword [rel g35_qfused], 0
  jmp  .caches
.q_fused_chk:
  add  rcx, rcx
  cmp  rax, rcx
  jne  .bad_qgate
  mov  qword [rel g35_qfused], 1
.caches:
  mov  rcx, [rel g35_ctx]
  imul rcx, [rel g35_kvdim]
  shl  rcx, 2
  call rt_alloc
  mov  [rdi + Q35L.kcache], rax
  mov  rcx, [rel g35_ctx]
  imul rcx, [rel g35_kvdim]
  shl  rcx, 2
  call rt_alloc
  mov  [rdi + Q35L.vcache], rax
  jmp  .layer_next
.linear:
  mov  qword [rdi + Q35L.kind], 0
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_qkv]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.wqkv], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_attn_gate]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.wz], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ssm_beta]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.wb], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ssm_alpha]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.wa], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ssm_conv]
  call q35_t
  Q35_NEED
  cmp  dword [rax + Tensor.type], GGML_F32
  jne  .bad_conv
  cmp  qword [rax + Tensor.ne0], 4
  jne  .bad_conv
  mov  rcx, [rax + Tensor.data]
  mov  [rdi + Q35L.conv_w], rcx
  mov  rcx, [rax + Tensor.ne1]
  mov  [rel g35_convdim], rcx
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ssm_dt]
  call q35_tv
  Q35_NEED
  mov  [rdi + Q35L.dt_bias], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ssm_a]
  call q35_tv
  Q35_NEED
  mov  [rdi + Q35L.A], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ssm_norm]
  call q35_tv
  Q35_NEED
  mov  [rdi + Q35L.ssm_norm], rax
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ssm_out]
  call q35_t
  Q35_NEED
  mov  [rdi + Q35L.wout], rax
  ; the linear dimensions, from the shapes (once)
  cmp  qword [rel g35_lv], 0
  jne  .dims_known
  mov  rax, [rdi + Q35L.wb]
  mov  rax, [rax + Tensor.ne1]
  mov  [rel g35_lv], rax       ; value heads
  mov  rcx, rbx
  lea  rdx, [rel q35_n_ssm_norm]
  call q35_t
  mov  rax, [rax + Tensor.ne0]
  mov  [rel g35_dv], rax       ; value head size
  mov  [rel g35_dk], rax       ; (the key head size: the same in this family)
  imul rax, [rel g35_lv]
  mov  [rel g35_valdim], rax
  mov  rcx, [rel g35_convdim]
  sub  rcx, rax
  shr  rcx, 1
  mov  [rel g35_keydim], rcx
  mov  rax, rcx
  xor  edx, edx
  div  qword [rel g35_dk]
  mov  [rel g35_lk], rax       ; key heads
  imul rax, [rel g35_dk]
  cmp  rax, [rel g35_keydim]
  jne  .bad_dims
  mov  rax, [rel g35_lv]
  xor  edx, edx
  div  qword [rel g35_lk]
  mov  [rel g35_vper], rax     ; value heads per key head
  ; A: the file holds A_log (positive) or -e^A_log (negative)
.dims_known:
  mov  rax, [rdi + Q35L.A]
  vmovss xmm0, [rax]
  vxorps xmm1, xmm1, xmm1
  vcomiss xmm0, xmm1
  jb   .a_ready                ; negative: already -e^A_log
  mov  rcx, [rel g35_lv]
  shl  rcx, 2
  call rt_alloc
  mov  [rsp+40], rax
  xor  esi, esi
.a_conv:
  cmp  rsi, [rel g35_lv]
  jae  .a_done
  mov  rax, [rdi + Q35L.A]
  vmovss xmm0, [rax + rsi*4]
  call k_exp
  vxorps xmm0, xmm0, [rel kc8_signmask]
  mov  rax, [rsp+40]
  vmovss [rax + rsi*4], xmm0
  inc  rsi
  jmp  .a_conv
.a_done:
  mov  rax, [rsp+40]
  mov  [rdi + Q35L.A], rax
.a_ready:
  mov  rcx, [rel g35_convdim]
  imul rcx, rcx, 12            ; 3 f32 per channel
  call rt_alloc
  mov  [rdi + Q35L.conv_state], rax
  mov  rcx, [rel g35_lv]
  imul rcx, [rel g35_dk]
  imul rcx, [rel g35_dv]
  shl  rcx, 2
  call rt_alloc
  mov  [rdi + Q35L.S], rax
.layer_next:
  add  rdi, Q35L_size
  inc  rbx
  jmp  .layer
.layers_done:
  ; the norm convention: weights near 0 mean "1 + w" (the model's own form)
  mov  rax, [rel g35_layers]
  mov  rax, [rax + Q35L.attn_norm]
  vxorps xmm0, xmm0, xmm0
  xor  ecx, ecx
.nw:
  vmovss xmm1, [rax + rcx*4]
  vandps xmm1, xmm1, [rel kc8_absmask]
  vaddss xmm0, xmm0, xmm1
  inc  ecx
  cmp  ecx, 8
  jb   .nw
  vcomiss xmm0, [rel q35_four]
  jb   .plus1_no
  mov  qword [rel g35_plus1], 1
.plus1_no:
  mov  rcx, [r15 + Model.output_norm]
  call q35_vec
  mov  [rel g35_out_norm], rax
  ; buffers
  mov  rcx, [rel g35_embd]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_x], rax
  mov  rcx, [rel g35_embd]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_xn], rax
  mov  rcx, [rel g35_embd]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_t], rax
  mov  rcx, [rel g35_convdim]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_qkv], rax
  mov  rcx, [rel g35_valdim]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_z], rax
  mov  rcx, [rel g35_lv]
  shl  rcx, 2
  add  rcx, 32
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_b], rax
  mov  rcx, [rel g35_lv]
  shl  rcx, 2
  add  rcx, 32
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_a], rax
  mov  rcx, [rel g35_nhead]
  imul rcx, [rel g35_hd]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_q], rax
  mov  rcx, [rel g35_nhead]
  imul rcx, [rel g35_hd]
  shl  rcx, 3
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_qg], rax
  mov  rcx, [rel g35_nhead]
  imul rcx, [rel g35_hd]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_gate], rax
  mov  rcx, [rel g35_kvdim]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_k], rax
  mov  rcx, [rel g35_kvdim]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_v], rax
  mov  rcx, [rel g35_nhead]
  imul rcx, [rel g35_hd]
  cmp  rcx, [rel g35_valdim]
  jae  .attsz
  mov  rcx, [rel g35_valdim]
.attsz:
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_att], rax
  mov  rcx, [rel g35_ff]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_g], rax
  mov  rcx, [rel g35_ff]
  shl  rcx, 2
  imul rcx, rcx, ACT_SLOTS   ; (one per token of a prompt batch)
  call rt_alloc
  mov  [rel g35_u], rax
  mov  rcx, [rel g35_vocab]
  shl  rcx, 2
  call rt_alloc
  mov  [rel g35_logits], rax
  ; the int8 activation buffers the quantised matvecs read (model.asm's globals)
  mov  rax, [rel g35_embd]
  mov  rdx, [rel g35_ff]
  cmp  rax, rdx
  cmovb rax, rdx
  mov  rdx, [rel g35_valdim]
  cmp  rax, rdx
  cmovb rax, rdx
  mov  [rsp+32], rax           ; maxcols (a multiple of 256)
  mov  rcx, rax
  shr  rcx, 5
  shl  rcx, 2
  call rt_alloc
  mov  [rel g_xsum32], rax
  mov  rcx, [rsp+32]
  shr  rcx, 4
  shl  rcx, 2
  call rt_alloc
  mov  [rel g_xsum16], rax
  mov  rcx, [rsp+32]
  call act_slots_init          ; the int8 activation slots (ACT_SLOTS, for a prompt batch)
  mov  rcx, [r15 + Model.n_threads]
  imul rcx, [rel g35_ctx]
  shl  rcx, 2
  call rt_alloc
  mov  [rel g_scores], rax
  mov  rcx, [r15 + Model.n_threads] ; the delta rule's mem/d scratch: 1 KB per thread
  shl  rcx, 10
  call rt_alloc
  mov  [rel g35_scratch], rax
  call tl_setup                ; the file tools' buffers
  ; the rope table for the rotary part of a head
  mov  rcx, [rel g35_posmax]
  imul rcx, [rel g35_rot]
  shl  rcx, 2
  call rt_alloc
  mov  [rel g35_rope], rax
  mov  rcx, rax
  mov  rdx, [rel g35_posmax]
  mov  r8, [rel g35_rot]
  movss xmm0, [r15 + Model.rope_theta]
  vxorps xmm1, xmm1, xmm1
  vcomiss xmm0, xmm1
  ja   .theta_ok
  vmovss xmm0, [rel q35_theta]
.theta_ok:
  call k_rope_table
  ; the tool-call opener is one special token: sampling it is the signal that a
  ; call begins (the API stops streaming the text from there)
  lea  rcx, [rel q35_s_tool_call]
  mov  edx, 11
  call tok_vocab_lookup
  mov  [rel g35_tool_call], rax
  ; the chat ids
  lea  rcx, [rel q35_s_im_start]
  mov  edx, 12
  call tok_vocab_lookup
  mov  [rel g35_im_start], rax
  lea  rcx, [rel q35_s_im_end]
  mov  edx, 10
  call tok_vocab_lookup
  mov  [rel g35_im_end], rax
  lea  rcx, [rel q35_s_think]
  mov  edx, 7
  call tok_vocab_lookup
  cmp  rax, -1
  jne  .think_ok
  xor  eax, eax                ; absent: no thinking block
.think_ok:
  mov  [rel g35_think], rax
  lea  rcx, [rel q35_s_think_end]
  mov  edx, 8
  call tok_vocab_lookup
  mov  [rel g35_think_end], rax
  ; the weight bytes a token reads (every tensor but the embedding table), and a touch of every page
  xor  ebx, ebx
  xor  esi, esi
.touch:
  cmp  rbx, [r15 + Model.n_tensors]
  jae  .touched
  mov  rax, rbx
  imul rax, rax, Tensor_size
  add  rax, [r15 + Model.tensors]
  mov  rcx, [rax + Tensor.data]
  mov  rdx, [rax + Tensor.nbytes]
  add  rsi, rdx
  xor  r8, r8
.tp:
  cmp  r8, rdx
  jae  .tn
  movzx r9d, byte [rcx + r8]
  add  r8, 4096
  jmp  .tp
.tn:
  inc  rbx
  jmp  .touch
.touched:
  mov  rax, [r15 + Model.tok_embd]
  sub  rsi, [rax + Tensor.nbytes]
  mov  [rel g_weight_bytes], rsi
  mov  qword [rel g35_pos], 0
  mov  rax, r15
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.bad_qgate:
  lea  rcx, [rel q35_err_qgate]
  call rt_fatal
.bad_conv:
  lea  rcx, [rel q35_err_conv]
  call rt_fatal
.bad_dims:
  lea  rcx, [rel q35_err_dims]
  call rt_fatal

; ===========================================================================
; Generation (the conversation continues across calls: g35_pos)
; ===========================================================================

; q35_generate(rcx=Model*, rdx=ids, r8=n, r9=max_new) — feed the ids, then up
; to max_new greedy tokens to stdout until a stop token; statistics to stderr.
q35_generate:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32] t0, [+40] t1, [+48] generated, [+56] token, [+64] logits
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  mov  qword [rel g_tl_turnlen], 0
  mov  qword [rel g_tl_rounds], 0
  mov  qword [rel g35_traced], 0
  mov  qword [rel g35_stop_len], 0
  mov  qword [rel g35_gen_total], 0
  mov  qword [rel g_api_mute], 0
  call rt_now_ns
  mov  [rsp+32], rax
  xor  r13d, r13d
.prompt:
  cmp  r13, rdi
  jae  .pdone
  mov  rax, [rel g35_pos]
  cmp  rax, [rel g35_posmax]
  jae  .full
  ; the slice: up to ACT_SLOTS tokens, as many as are left and as the context holds
  mov  r8, rdi
  sub  r8, r13
  cmp  r8, ACT_SLOTS
  jbe  .sl1
  mov  r8d, ACT_SLOTS
.sl1:
  mov  rcx, [rel g35_posmax]
  sub  rcx, rax
  cmp  r8, rcx
  jbe  .sl2
  mov  r8, rcx
.sl2:
  mov  r14, r8
  mov  rcx, rbx
  lea  rdx, [rsi + r13*4]
  mov  r9, rax
  call q35_forward_batch
  mov  [rsp+64], rax
  add  [rel g35_pos], r14
  add  r13, r14
  jmp  .prompt
.pdone:
  test rdi, rdi
  jz   .ret
  call rt_now_ns
  mov  [rsp+40], rax
  mov  [rel g35_last_prompt_n], rdi ; the turn's numbers, for the API's final line
  sub  rax, [rsp+32]
  mov  [rel g35_last_prompt_ns], rax
  mov  qword [rel g35_last_gen_ns], 0
  lea  rcx, [rel s_prompt1]
  call rt_err_str
  mov  rcx, rdi
  call rt_err_u64
  lea  rcx, [rel s_prompt2]
  call rt_err_str
  mov  rdx, [rsp+40]
  sub  rdx, [rsp+32]
  call model_emit_ms
  lea  rcx, [rel s_prompt3]
  call rt_err_str
  mov  rcx, rdi
  mov  rdx, [rsp+40]
  sub  rdx, [rsp+32]
  call q35_rate                ; xmm0 = tokens / s
  call rt_err_f32
  lea  rcx, [rel q35_s_nl]
  call rt_err_str
  mov  rcx, [rsp+64]
  mov  rdx, [rel g35_vocab]
  call k_sample
  mov  [rsp+56], rax
  mov  qword [rsp+48], 0
  call pool_counters_reset     ; the waste metric counts from here
  call rt_now_ns
  mov  [rsp+32], rax
.gen:
  mov  rax, [rsp+48]
  cmp  rax, r12
  jae  .gdone_len
  mov  rcx, [rsp+56]
  call tok_is_stop
  test rax, rax
  jnz  .gdone
  mov  rax, [rsp+56]
  cmp  rax, [rel g35_im_end]   ; the end of the assistant's turn stops too
  je   .gdone
  mov  rax, [rsp+56]
  test rax, rax
  jz   .not_tc
  cmp  rax, [rel g35_tool_call]
  jne  .not_tc
  mov  qword [rel g_api_mute], 1 ; a tool call begins: its markup is not streamed to the client
.not_tc:
  mov  rcx, [rsp+56]
  lea  rdx, [rel g35_decbuf]
  call tok_decode
  mov  r14, rax
  lea  rcx, [rel g35_decbuf]
  mov  rdx, rax
  call rt_out                  ; (teed into dialog.txt's buffer by rt_write_h)
  lea  rcx, [rel g35_decbuf]
  mov  rdx, r14
  call tl_turn_add             ; the turn's text, for the tool-call search
  mov  rax, [rel g35_pos]
  cmp  rax, [rel g35_posmax]
  jae  .full
  cmp  qword [rel g35_traced], 0 ; --trace: the first generated token's forward is traced step by step, once per turn
  jne  .no_tr
  mov  rax, [rel g35_trace]
  mov  [rel g35_trace_exec], rax
  mov  [rel g35_traced], rax
  mov  rax, [rel g35_pos]
.no_tr:
  mov  rcx, rbx
  mov  rdx, [rsp+56]
  mov  r8, rax
  call q35_forward
  inc  qword [rel g35_pos]
  inc  qword [rsp+48]
  inc  qword [rel g35_gen_total] ; all of this turn's generated tokens (the API's eval_count)
  mov  rcx, rax
  mov  rdx, [rel g35_vocab]
  call k_sample
  mov  [rsp+56], rax
  TR   q35_tr_argmax
  cmp  qword [rel g35_trace_exec], 0
  je   .gen
  ; the traced token's printing is not the model's time: the statistics window
  ; starts after it, and it is not counted
  mov  qword [rel g35_trace_exec], 0
  call pool_counters_reset
  call rt_now_ns
  mov  [rsp+32], rax
  mov  qword [rsp+48], 0
  jmp  .gen
.gdone_len:
  mov  qword [rel g35_stop_len], 1 ; -n reached: the answer is cut (the API's done_reason "length")
.gdone:
  ; a tool call in the turn? run it, show the result, feed the tool turn, go on
  cmp  qword [rel g_tools_off], 0
  jne  .no_tool
  mov  rcx, [rel g_tl_turn]
  mov  rdx, [rel g_tl_turnlen]
  call tl_run
  test rax, rax
  jz   .no_tool
  cmp  qword [rel g_tl_rounds], TL_ROUNDS
  jae  .no_tool
  inc  qword [rel g_tl_rounds]
  lea  rcx, [rel tl_s_shown]
  call rt_out_str
  mov  rcx, [rel g_tl_res]
  mov  rdx, [rel g_tl_reslen]
  call rt_out
  lea  rcx, [rel q35_s_nl]
  call rt_out_str
  mov  rcx, [rel g_tl_ids]
  mov  edx, TL_IDS_CAP
  call tl_encode_response
  mov  rsi, [rel g_tl_ids]
  mov  rdi, rax
  mov  qword [rel g_tl_turnlen], 0
  mov  qword [rsp+48], 0       ; -n counts anew for the answer that follows
  xor  r13d, r13d
.tool_feed:
  cmp  r13, rdi
  jae  .tool_fed
  mov  rax, [rel g35_pos]
  cmp  rax, [rel g35_posmax]
  jae  .full
  mov  r8, rdi                 ; the slice, as for the prompt
  sub  r8, r13
  cmp  r8, ACT_SLOTS
  jbe  .tsl1
  mov  r8d, ACT_SLOTS
.tsl1:
  mov  rcx, [rel g35_posmax]
  sub  rcx, rax
  cmp  r8, rcx
  jbe  .tsl2
  mov  r8, rcx
.tsl2:
  mov  r14, r8
  mov  rcx, rbx
  lea  rdx, [rsi + r13*4]
  mov  r9, rax
  call q35_forward_batch
  mov  [rsp+64], rax
  add  [rel g35_pos], r14
  add  r13, r14
  jmp  .tool_feed
.tool_fed:
  mov  rcx, [rsp+64]
  mov  rdx, [rel g35_vocab]
  call k_sample
  mov  [rsp+56], rax
  jmp  .gen
.no_tool:
  call rt_now_ns
  mov  [rsp+40], rax
  sub  rax, [rsp+32]
  mov  [rel g35_last_gen_ns], rax
  lea  rcx, [rel q35_s_nl]
  call rt_out_str
  lea  rcx, [rel s_gen1]
  call rt_err_str
  mov  rcx, [rsp+48]
  call rt_err_u64
  lea  rcx, [rel s_gen2]
  call rt_err_str
  mov  rdx, [rsp+40]
  sub  rdx, [rsp+32]
  call model_emit_ms
  lea  rcx, [rel s_gen3]
  call rt_err_str
  mov  rcx, [rsp+48]
  mov  rdx, [rsp+40]
  sub  rdx, [rsp+32]
  call q35_rate
  vmovss [rsp+56], xmm0        ; tok/s
  call rt_err_f32
  lea  rcx, [rel s_gen4]
  call rt_err_str
  vcvtsi2ss xmm0, xmm0, qword [rel g_weight_bytes]
  vdivss xmm0, xmm0, [rel q35_1e9]
  vmovss [rsp+60], xmm0        ; GB per token
  call rt_err_f32
  lea  rcx, [rel s_gen5]
  call rt_err_str
  vmovss xmm0, [rsp+56]
  vmulss xmm0, xmm0, [rsp+60]
  call rt_err_f32
  lea  rcx, [rel q35_s_nl]
  call rt_err_str
  ; the waste metric for this generation
  mov  rcx, [rsp+48]
  mov  rdx, [rsp+40]
  sub  rdx, [rsp+32]
  call rt_waste_line
  jmp  .ret
.full:
  lea  rcx, [rel q35_s_full]
  call rt_err_str
.ret:
  call rt_log_flush            ; this turn (prompt, answer, statistics) to dialog.txt
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; rt_waste_line(rcx=tokens, rdx=ns of the generation) — the waste metric since
; pool_counters_reset, per generated token, in ms:
;   waste    = total - floor      floor = the weight bytes a token reads / the bus speed
;   serial   = total - the time inside pool_run jobs (one core working)
;   barrier  = the time the caller waited for the slowest worker
;   bus_util = floor / the time inside jobs: how much of the bus the jobs used
; and a second line from the hardware: per core the share of the window's
; TSC ticks spent waiting (for a job, or at a barrier), the total wasted share,
; and, where the fixed counters run (no-OS build), instructions per cycle
; inside jobs. Nothing is printed for 0 tokens. Preserves the nonvolatile
; registers.
rt_waste_line:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32] total, [+36] parallel, [+40] barrier, [+44] floor, [+48] window ticks, [+56] wasted ticks
  test rcx, rcx
  jz   .ret
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rcx, rbx
  mov  rdx, rsi
  call q35_per_token_ms
  vmovss [rsp+32], xmm0
  mov  rcx, rbx
  mov  rdx, [rel g_pool_par_ns]
  call q35_per_token_ms
  vmovss [rsp+36], xmm0
  mov  rcx, rbx
  mov  rdx, [rel g_pool_wait_ns]
  call q35_per_token_ms
  vmovss [rsp+40], xmm0
  vcvtsi2ss xmm0, xmm0, qword [rel g_weight_bytes]
  vdivss xmm0, xmm0, [rel g_bus_gbps]
  vdivss xmm0, xmm0, [rel q35_1e6]
  vmovss [rsp+44], xmm0
  lea  rcx, [rel q35_s_waste]
  call rt_err_str
  vmovss xmm0, [rsp+32]
  vsubss xmm0, xmm0, [rsp+44]
  call rt_err_f32
  lea  rcx, [rel q35_s_w_floor]
  call rt_err_str
  vmovss xmm0, [rsp+44]
  call rt_err_f32
  lea  rcx, [rel q35_s_w_total]
  call rt_err_str
  vmovss xmm0, [rsp+32]
  call rt_err_f32
  lea  rcx, [rel q35_s_w_serial]
  call rt_err_str
  vmovss xmm0, [rsp+32]
  vsubss xmm0, xmm0, [rsp+36]
  call rt_err_f32
  lea  rcx, [rel q35_s_w_barrier]
  call rt_err_str
  vmovss xmm0, [rsp+40]
  call rt_err_f32
  lea  rcx, [rel q35_s_w_bus]
  call rt_err_str
  vmovss xmm0, [rsp+44]
  vdivss xmm0, xmm0, [rsp+36]
  vmulss xmm0, xmm0, [rel q35_100]
  call rt_err_f32
  lea  rcx, [rel q35_s_w_jobs]
  call rt_err_str
  mov  rax, [rel g_pool_jobs]
  xor  edx, edx
  div  rbx
  mov  rcx, rax
  call rt_err_u64
  lea  rcx, [rel q35_s_w_busgiven]
  call rt_err_str
  vmovss xmm0, [rel g_bus_gbps]
  call rt_err_f32
  lea  rcx, [rel q35_s_nl]
  call rt_err_str
  ; the hardware line: ticks
  rdtsc
  shl  rdx, 32
  or   rax, rdx
  sub  rax, [rel g_pool_t0_tick]
  mov  [rsp+48], rax           ; the window, in ticks
  lea  rcx, [rel q35_s_ticks]
  call rt_err_str
  xor  r12d, r12d              ; core
  xor  r13d, r13d              ; wasted ticks, all cores
.core:
  cmp  r12, [rel g_nthreads]
  jae  .cores_done
  lea  rax, [rel g_pool_idle_ticks]
  mov  rax, [rax + r12*8]
  add  r13, rax
  vcvtsi2ss xmm0, xmm0, rax
  vcvtsi2ss xmm1, xmm1, qword [rsp+48]
  vdivss xmm0, xmm0, xmm1
  vmulss xmm0, xmm0, [rel q35_100]
  call rt_err_f32
  lea  rcx, [rel q35_s_sp]
  call rt_err_str
  inc  r12
  jmp  .core
.cores_done:
  lea  rcx, [rel q35_s_wasted_pct]
  call rt_err_str
  vcvtsi2ss xmm0, xmm0, r13
  vcvtsi2ss xmm1, xmm1, qword [rsp+48]
  vcvtsi2ss xmm2, xmm2, qword [rel g_nthreads]
  vmulss xmm1, xmm1, xmm2
  vdivss xmm0, xmm0, xmm1
  vmulss xmm0, xmm0, [rel q35_100]
  call rt_err_f32
  cmp  qword [rel g_pmu_on], 0
  je   .ticks_done
  lea  rcx, [rel q35_s_ipc]
  call rt_err_str
  xor  r12d, r12d
.ipc:
  cmp  r12, [rel g_nthreads]
  jae  .ipc_done
  lea  rax, [rel g_pool_job_instr]
  vcvtsi2ss xmm0, xmm0, qword [rax + r12*8]
  lea  rax, [rel g_pool_job_cycles]
  mov  rax, [rax + r12*8]
  test rax, rax
  jnz  .cyc
  mov  eax, 1
.cyc:
  vcvtsi2ss xmm1, xmm1, rax
  vdivss xmm0, xmm0, xmm1
  call rt_err_f32
  lea  rcx, [rel q35_s_sp]
  call rt_err_str
  inc  r12
  jmp  .ipc
.ipc_done:
  cmp  qword [rel g_pmu_stalls], 0
  je   .ticks_done
  ; per core: the share of the job cycles stalled on a demand load miss, and the DRAM share of it
  lea  rcx, [rel q35_s_memstall]
  call rt_err_str
  xor  r12d, r12d
.ms:
  cmp  r12, [rel g_nthreads]
  jae  .ms_done
  lea  rax, [rel g_pool_job_stall]
  vcvtsi2ss xmm0, xmm0, qword [rax + r12*8]
  lea  rax, [rel g_pool_job_cycles]
  mov  rax, [rax + r12*8]
  test rax, rax
  jnz  .ms_cyc
  mov  eax, 1
.ms_cyc:
  vcvtsi2ss xmm1, xmm1, rax
  vdivss xmm0, xmm0, xmm1
  vmulss xmm0, xmm0, [rel q35_100]
  call rt_err_f32
  lea  rcx, [rel q35_s_sp]
  call rt_err_str
  inc  r12
  jmp  .ms
.ms_done:
  lea  rcx, [rel q35_s_dramstall]
  call rt_err_str
  xor  r12d, r12d
.ds:
  cmp  r12, [rel g_nthreads]
  jae  .ds_done
  lea  rax, [rel g_pool_job_dram]
  vcvtsi2ss xmm0, xmm0, qword [rax + r12*8]
  lea  rax, [rel g_pool_job_cycles]
  mov  rax, [rax + r12*8]
  test rax, rax
  jnz  .ds_cyc
  mov  eax, 1
.ds_cyc:
  vcvtsi2ss xmm1, xmm1, rax
  vdivss xmm0, xmm0, xmm1
  vmulss xmm0, xmm0, [rel q35_100]
  call rt_err_f32
  lea  rcx, [rel q35_s_sp]
  call rt_err_str
  inc  r12
  jmp  .ds
.ds_done:
%ifdef ASMRUN_EFI
  ; per core: the clock the core actually ran at inside the jobs: cycles / (ticks / tsc_hz)
  lea  rcx, [rel q35_s_ghz]
  call rt_err_str
  xor  r12d, r12d
.gh:
  cmp  r12, [rel g_nthreads]
  jae  .gh_done
  lea  rax, [rel g_pool_job_cycles]
  vcvtsi2ss xmm0, xmm0, qword [rax + r12*8]
  lea  rax, [rel g_pool_job_ticks]
  mov  rax, [rax + r12*8]
  test rax, rax
  jnz  .gh_t
  mov  eax, 1
.gh_t:
  vcvtsi2ss xmm1, xmm1, rax
  vdivss xmm0, xmm0, xmm1      ; cycles per TSC tick
  vcvtsi2ss xmm1, xmm1, qword [rel g_tsc_hz]
  vmulss xmm0, xmm0, xmm1      ; Hz
  test r12, r12
  jnz  .gh_nz
  vdivss xmm2, xmm0, [rel q35_1e6]
  vcvttss2si rax, xmm2
  mov  [rel g35_last_ghz_mhz], rax ; core 0's clock, MHz, for the API
.gh_nz:
  vdivss xmm0, xmm0, [rel q35_1e9]
  call rt_err_f32
  lea  rcx, [rel q35_s_sp]
  call rt_err_str
  inc  r12
  jmp  .gh
.gh_done:
  ; the thermal sensors: per core and the package, degrees C
  call rt_temp_read
  cmp  qword [rel g_temp_n], 0
  je   .ticks_done
  lea  rcx, [rel q35_s_temp]
  call rt_err_str
  xor  r12d, r12d
.tc:
  cmp  r12, [rel g_temp_n]
  jae  .tc_done
  lea  rax, [rel g_temp_c]
  mov  rcx, [rax + r12*8]
  call rt_err_u64
  lea  rcx, [rel q35_s_sp]
  call rt_err_str
  inc  r12
  jmp  .tc
.tc_done:
  lea  rcx, [rel q35_s_temp_pkg]
  call rt_err_str
  mov  rcx, [rel g_temp_pkg]
  call rt_err_u64
%endif
.ticks_done:
  lea  rcx, [rel q35_s_nl]
  call rt_err_str
.ret:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; q35_tr(rcx=label zstr, rdx=bytes the step read, or 0) — the execution trace
; (--trace, the first generated token): "asmrun: exec L<layer> <label> us=<n>
; [kb=<bytes/1024> gb_s=<bytes/ns>]" for the step since the last mark. Nothing
; without g35_trace_exec. Preserves the nonvolatile registers.
q35_tr:
  cmp  qword [rel g35_trace_exec], 0
  je   .off
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  call rt_now_ns
  mov  rdi, rax
  sub  rdi, [rel g35_tr_t]     ; ns of the step
  mov  [rel g35_tr_t], rax
  lea  rcx, [rel q35_s_exec]
  call rt_err_str
  mov  rcx, [rel g35_tr_layer]
  call rt_err_u64
  lea  rcx, [rel q35_s_sp]
  call rt_err_str
  mov  rcx, rbx
  call rt_err_str
  lea  rcx, [rel q35_s_us]
  call rt_err_str
  mov  rax, rdi
  xor  edx, edx
  mov  ecx, 1000
  div  rcx
  mov  rcx, rax
  call rt_err_u64
  test rsi, rsi
  jz   .nl
  lea  rcx, [rel q35_s_kb]
  call rt_err_str
  mov  rcx, rsi
  shr  rcx, 10
  call rt_err_u64
  lea  rcx, [rel q35_s_gbs]
  call rt_err_str
  vcvtsi2ss xmm0, xmm0, rsi
  test rdi, rdi
  jnz  .nz
  mov  edi, 1
.nz:
  vcvtsi2ss xmm1, xmm1, rdi
  vdivss xmm0, xmm0, xmm1      ; bytes / ns = GB/s
  call rt_err_f32
.nl:
  lea  rcx, [rel q35_s_nl]
  call rt_err_str
  call rt_now_ns               ; the next step is measured from after this line was
  mov  [rel g35_tr_t], rax     ; printed (the firmware console takes ~76 ms per line)
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
.off:
  ret

; q35_trace_vec(rcx=label number, rdx=f32*, r8=n) — "asmrun: trace <label>
; mean=<mean> absmax=<max |x|> argmax=<index> nan=<count>" to stderr.
q35_trace_vec:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32] sum, [+36] absmax, [+40] argmax, [+48] nan count
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  vxorps xmm0, xmm0, xmm0
  vmovss [rsp+32], xmm0
  vmovss [rsp+36], xmm0
  mov  qword [rsp+40], 0
  mov  qword [rsp+48], 0
  vmovss xmm3, [rsi]           ; the best so far
  xor  r12d, r12d
.l:
  cmp  r12, rdi
  jae  .d
  vmovss xmm0, [rsi + r12*4]
  vucomiss xmm0, xmm0
  jnp  .num
  inc  qword [rsp+48]          ; NaN
  jmp  .n
.num:
  vaddss xmm1, xmm0, [rsp+32]
  vmovss [rsp+32], xmm1
  vandps xmm1, xmm0, [rel kc8_absmask]
  vmaxss xmm1, xmm1, [rsp+36]
  vmovss [rsp+36], xmm1
  vcomiss xmm0, xmm3
  jbe  .n
  vmovss xmm3, xmm0, xmm0
  mov  [rsp+40], r12
.n:
  inc  r12
  jmp  .l
.d:
  lea  rcx, [rel q35_s_trace]
  call rt_err_str
  mov  rcx, rbx
  call rt_err_u64
  lea  rcx, [rel q35_s_tr_mean]
  call rt_err_str
  vmovss xmm0, [rsp+32]
  vcvtsi2ss xmm1, xmm1, rdi
  vdivss xmm0, xmm0, xmm1
  call rt_err_f32
  lea  rcx, [rel q35_s_tr_max]
  call rt_err_str
  vmovss xmm0, [rsp+36]
  call rt_err_f32
  lea  rcx, [rel q35_s_tr_arg]
  call rt_err_str
  mov  rcx, [rsp+40]
  call rt_err_u64
  lea  rcx, [rel q35_s_tr_nan]
  call rt_err_str
  mov  rcx, [rsp+48]
  call rt_err_u64
  lea  rcx, [rel q35_s_nl]
  call rt_err_str
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; q35_per_token_ms(rcx=tokens, rdx=ns) -> xmm0 = ns / tokens / 1e6.
q35_per_token_ms:
  vcvtsi2ss xmm0, xmm0, rdx
  vcvtsi2ss xmm1, xmm1, rcx
  vdivss xmm0, xmm0, xmm1
  vdivss xmm0, xmm0, [rel q35_1e6]
  ret

; q35_parse_f32w(rcx=utf16 "12.34") -> xmm0 (digits, one '.', digits).
q35_parse_f32w:
  vxorps xmm0, xmm0, xmm0
  vmovss xmm2, [rel q35_ten]
.i:
  movzx eax, word [rcx]
  sub  eax, '0'
  cmp  eax, 9
  ja   .dot
  vcvtsi2ss xmm1, xmm1, eax
  vmulss xmm0, xmm0, xmm2
  vaddss xmm0, xmm0, xmm1
  add  rcx, 2
  jmp  .i
.dot:
  cmp  word [rcx], '.'
  jne  .ret
  add  rcx, 2
  vmovss xmm3, [rel kc_one]    ; the place value
.f:
  movzx eax, word [rcx]
  sub  eax, '0'
  cmp  eax, 9
  ja   .ret
  vdivss xmm3, xmm3, xmm2
  vcvtsi2ss xmm1, xmm1, eax
  vmulss xmm1, xmm1, xmm3
  vaddss xmm0, xmm0, xmm1
  add  rcx, 2
  jmp  .f
.ret:
  ret

; q35_rate(rcx=count, rdx=ns) -> xmm0 = count * 1e9 / ns (f32).
q35_rate:
  vcvtsi2ss xmm0, xmm0, rcx
  vmulss xmm0, xmm0, [rel q35_1e9]
  test rdx, rdx
  jnz  .nz
  mov  edx, 1
.nz:
  vcvtsi2ss xmm1, xmm1, rdx
  vdivss xmm0, xmm0, xmm1
  ret

; q35_encode_chat(rcx=text, rdx=len, r8=ids, r9=max) -> rax = ids: one user
; turn in ChatML — after the first turn, the assistant's turn is closed first:
;   [<|im_end|>\n] <|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n{g35_asst_tail}
; Every text span goes through tok_encode (which preserves rbx, rsi, rdi, r12-r15).
q35_encode_chat:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  mov  rbx, r8                 ; ids
  mov  r13, r9                 ; capacity
  mov  rdi, rcx
  mov  r12, rdx
  xor  esi, esi
  cmp  qword [rel g35_pos], 0
  je   .first
  mov  rax, [rel g35_im_end]
  call q35_chat_id
  lea  rcx, [rel q35_s_lf]
  mov  edx, 1
  call q35_chat_text
  jmp  .user
.first:
  ; the system turn opens the conversation: the tools block (the template's
  ; wording), then --system's text when there is one
  mov  rax, [rel g35_im_start]
  call q35_chat_id
  lea  rcx, [rel s_chat_system]
  mov  edx, S_CHAT_SYSTEM_LEN
  call q35_chat_text
  cmp  qword [rel g_tools_off], 0
  jne  .sys_text               ; --no-tools: the system turn is --system's text alone
  lea  rcx, [rel tl_sys_text]
  mov  edx, TL_SYS_LEN
  call q35_chat_text
  cmp  qword [rel g_system_len], 0
  je   .sys_end
  lea  rcx, [rel q35_s_lflf]
  mov  edx, 2
  call q35_chat_text
.sys_text:
  cmp  qword [rel g_system_len], 0
  je   .sys_end
  mov  rcx, [rel g_system_text]
  mov  rdx, [rel g_system_len]
  call q35_chat_text
.sys_end:
  mov  rax, [rel g35_im_end]
  call q35_chat_id
  lea  rcx, [rel q35_s_lf]
  mov  edx, 1
  call q35_chat_text
.user:
  mov  rax, [rel g35_im_start]
  call q35_chat_id
  lea  rcx, [rel q35_s_user]
  mov  edx, 5
  call q35_chat_text
  mov  rcx, rdi
  mov  rdx, r12
  call q35_chat_text
  mov  rax, [rel g35_im_end]
  call q35_chat_id
  lea  rcx, [rel q35_s_lf]
  mov  edx, 1
  call q35_chat_text
  mov  rax, [rel g35_im_start]
  call q35_chat_id
  lea  rcx, [rel q35_s_asst]
  mov  edx, 10
  call q35_chat_text
  ; the empty thinking block (thinking off): <think>, </think> are special ids
  mov  rax, [rel g35_think]
  test rax, rax
  jz   .done
  call q35_chat_id
  lea  rcx, [rel q35_s_lflf]
  mov  edx, 2
  call q35_chat_text
  mov  rax, [rel g35_think_end]
  call q35_chat_id
  lea  rcx, [rel q35_s_lflf]
  mov  edx, 2
  call q35_chat_text
.done:
  mov  rax, rsi
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; q35_reset() — the conversation starts over: every layer's recurrent state
; (conv_state, S) and attention cache zeroed, the position 0. The weights and
; buffers stay. Preserves the nonvolatile registers.
q35_reset:
  push rbx
  push rsi
  WINPROLOGUE
  mov  rbx, [rel g35_layers]
  mov  rsi, [rel g35_nlayer]
.layer:
  test rsi, rsi
  jz   .done
  mov  rcx, [rbx + Q35L.conv_state]
  test rcx, rcx
  jz   .no_conv
  mov  r8, [rel g35_convdim]
  imul r8, r8, 12
  xor  edx, edx
  call rt_memset
.no_conv:
  mov  rcx, [rbx + Q35L.S]
  test rcx, rcx
  jz   .no_s
  mov  r8, [rel g35_lv]
  imul r8, [rel g35_dk]
  imul r8, [rel g35_dv]
  shl  r8, 2
  xor  edx, edx
  call rt_memset
.no_s:
  mov  rcx, [rbx + Q35L.kcache]
  test rcx, rcx
  jz   .no_k
  mov  r8, [rel g35_ctx]
  imul r8, [rel g35_kvdim]
  shl  r8, 2
  xor  edx, edx
  call rt_memset
  mov  rcx, [rbx + Q35L.vcache]
  mov  r8, [rel g35_ctx]
  imul r8, [rel g35_kvdim]
  shl  r8, 2
  xor  edx, edx
  call rt_memset
.no_k:
  add  rbx, Q35L_size
  dec  rsi
  jmp  .layer
.done:
  mov  qword [rel g35_pos], 0
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; q35_chat_id(rax=id) — append one id at the cursor rsi into rbx (capacity r13).
q35_chat_id:
  cmp  rsi, r13
  jae  .ret
  mov  [rbx + rsi*4], eax
  inc  rsi
.ret:
  ret

; q35_chat_text(rcx=text, rdx=len) — tok_encode at the cursor rsi into rbx.
q35_chat_text:
  WINPROLOGUE
  lea  r8, [rbx + rsi*4]
  mov  r9, r13
  sub  r9, rsi
  call tok_encode
  add  rsi, rax
  cmp  rsi, r13
  jbe  .ok
  mov  rsi, r13
.ok:
  WINEPILOGUE
  ret

section .data
  align 8
g35_model:    dq 0
g35_layers:   dq 0
g35_nlayer:   dq 0
g35_embd:     dq 0
g35_ff:       dq 0
g35_nhead:    dq 0
g35_nkv:      dq 0
g35_hd:       dq 0
g35_rot:      dq 0
g35_kvdim:    dq 0
g35_vocab:    dq 0
g35_ctx:      dq 0
g35_lv:       dq 0           ; linear attention: value heads
g35_lk:       dq 0           ; key heads
g35_dv:       dq 0           ; value head size
g35_dk:       dq 0           ; key head size
g35_vper:     dq 0           ; value heads per key head
g35_valdim:   dq 0
g35_keydim:   dq 0
g35_convdim:  dq 0
g35_qfused:   dq 0
g35_plus1:    dq 0
g35_pos:      dq 0
g35_posmax:   dq 0           ; positions the dialog may reach (rope table rows); the caches wrap at g35_ctx
g35_im_start: dq 0
g35_im_end:   dq 0
g35_out_norm: dq 0
g35_x:        dq 0
g35_xn:       dq 0
g35_t:        dq 0
g35_qkv:      dq 0
g35_z:        dq 0
g35_b:        dq 0
g35_a:        dq 0
g35_q:        dq 0
g35_qg:       dq 0
g35_gate:     dq 0
g35_k:        dq 0
g35_v:        dq 0
g35_att:      dq 0
g35_g:        dq 0
g35_u:        dq 0
g35_logits:   dq 0
g35_rope:     dq 0
g35_scratch:  dq 0           ; q35_lin_job: per thread 128 f32 mem + 128 f32 d (1 KB each)
g35_eps:      dd 0
q35_four:     dd 4.0
q35_theta:    dd 10000000.0
q35_1e9:      dd 1.0e9
q35_1e6:      dd 1.0e6
q35_100:      dd 100.0
q35_ten:      dd 10.0
g_bus_gbps:   dd 22.9          ; --bus: the machine's memory read bandwidth in GB/s (asmrun --bw measures it)
q35_s_waste:     db "asmrun: waste_ms_per_token=", 0
q35_s_w_floor:   db " floor=", 0
q35_s_w_total:   db " total=", 0
q35_s_w_serial:  db " serial=", 0
q35_s_w_barrier: db " barrier=", 0
q35_s_w_bus:     db " bus_util_pct=", 0
q35_s_w_jobs:    db " jobs_per_token=", 0
q35_s_w_busgiven: db " bus_gbps=", 0
g35_trace:        dq 0        ; --trace
g35_trace_exec:   dq 0        ; 1 while the traced token's forward runs (q35_tr prints)
g35_traced:       dq 0        ; 1 once a token of this turn was traced
g35_stop_len:     dq 0        ; 1 when the last turn stopped at -n, not at a stop token
g35_last_prompt_n:  dq 0      ; the last turn's prompt tokens, prompt time and generation time (ns)
g35_last_prompt_ns: dq 0
g35_last_gen_ns:    dq 0
g35_last_ghz_mhz:   dq 0      ; core 0's clock inside the jobs, MHz (no-OS build)
g35_gen_total:    dq 0        ; tokens generated in the last turn, tool rounds included
g35_tr_t:         dq 0        ; q35_tr: the time of the last mark
g35_tr_layer:     dq 0        ; q35_tr: the layer (99: after the layers)
q35_s_exec:       db "asmrun: exec L", 0
q35_s_sp:         db " ", 0
q35_s_us:         db " us=", 0
q35_s_kb:         db " kb=", 0
q35_s_gbs:        db " gb_s=", 0
q35_tr_embd:      db "embed", 0
q35_tr_norm:      db "norm+sums", 0
q35_tr_proj4:     db "proj qkv+z+beta+alpha", 0
q35_tr_conv:      db "conv+l2norm (pool)", 0
q35_tr_delta:     db "delta+gated_norm (pool)", 0
q35_tr_sums:      db "sums", 0
q35_tr_out:       db "ssm_out", 0
q35_tr_projqkv:   db "proj q+gate+k+v", 0
q35_tr_qknorm:    db "qk_norm+rope", 0
q35_tr_attn:      db "kv_cache+attention (pool)", 0
q35_tr_gate:      db "sigmoid_gate+sums", 0
q35_tr_wo:        db "attn_output", 0
q35_tr_ffnnorm:   db "add+ffn_norm+sums", 0
q35_tr_gateup:    db "ffn_gate+up", 0
q35_tr_silu:      db "silu+sums", 0
q35_tr_down:      db "ffn_down", 0
q35_tr_add:       db "add", 0
q35_tr_outnorm:   db "output_norm+sums", 0
q35_tr_lmhead:    db "lm_head", 0
q35_tr_argmax:    db "sample", 0
q35_tr_stats:     db "logit_stats (trace only)", 0
q35_s_trace:      db "asmrun: trace ", 0
q35_s_tr_mean:    db " mean=", 0
q35_s_tr_max:     db " absmax=", 0
q35_s_tr_arg:     db " argmax=", 0
q35_s_tr_nan:     db " nan=", 0
q35_s_ticks:      db "asmrun: ticks: idle_pct_per_core=", 0
q35_s_wasted_pct: db " wasted_ticks_pct=", 0
q35_s_ipc:        db " ipc_in_jobs_per_core=", 0
q35_s_memstall:   db " mem_stall_pct_per_core=", 0
q35_s_dramstall:  db " dram_stall_pct_per_core=", 0
q35_s_ghz:        db " ghz_in_jobs_per_core=", 0
q35_s_temp:       db " temp_c_per_core=", 0
q35_s_temp_pkg:   db " pkg_temp_c=", 0
q35_s_sp2:        db " ", 0
g35_tool_call: dq 0          ; <tool_call> id (0: the vocabulary has none)
g35_think:     dq 0          ; <think> id (0: the vocabulary has none, no thinking block is appended)
g35_think_end: dq 0          ; </think> id
q35_s_think:   db "<think>"
q35_s_think_end: db "</think>"
q35_s_lflf:    db 10, 10
               times 64 - 17 db 0
g35_name:     times 96 db 0
g35_decbuf:   times 64 db 0
q35_n_attn_norm:   db "attn_norm.weight", 0
q35_n_ffn_norm:    db "post_attention_norm.weight", 0
q35_n_ffn_gate:    db "ffn_gate.weight", 0
q35_n_ffn_up:      db "ffn_up.weight", 0
q35_n_ffn_down:    db "ffn_down.weight", 0
q35_n_attn_q:      db "attn_q.weight", 0
q35_n_attn_gate:   db "attn_gate.weight", 0
q35_n_attn_k:      db "attn_k.weight", 0
q35_n_attn_v:      db "attn_v.weight", 0
q35_n_attn_output: db "attn_output.weight", 0
q35_n_attn_q_norm: db "attn_q_norm.weight", 0
q35_n_attn_k_norm: db "attn_k_norm.weight", 0
q35_n_attn_qkv:    db "attn_qkv.weight", 0
q35_n_ssm_beta:    db "ssm_beta.weight", 0
q35_n_ssm_alpha:   db "ssm_alpha.weight", 0
q35_n_ssm_conv:    db "ssm_conv1d.weight", 0
q35_n_ssm_dt:      db "ssm_dt.bias", 0
q35_n_ssm_a:       db "ssm_a", 0
q35_n_ssm_norm:    db "ssm_norm.weight", 0
q35_n_ssm_out:     db "ssm_out.weight", 0
q35_s_im_start:    db "<|im_start|>"
q35_s_im_end:      db "<|im_end|>"
q35_s_tool_call:   db "<tool_call>"
q35_s_user:        db "user", 10
q35_s_asst:        db "assistant", 10
q35_s_lf:          db 10
q35_s_nl:          db 10, 0
q35_s_missing:     db "asmrun: Qwen3.5 tensor missing: ", 0
q35_s_full:        db "asmrun: the context (-c) is full; start a new conversation", 10, 0
q35_err_qgate:     db "the attention gate is neither fused into attn_q nor an attn_gate tensor", 0
q35_err_conv:      db "ssm_conv1d is not an f32 [4][channels] tensor", 0
q35_err_dims:      db "the linear-attention shapes do not fit the Gated DeltaNet layout", 0
section .text

; q3f.asm — the Qwen3 path's forward pass for a batch of tokens: T tokens
; (T <= ACT_SLOTS) at positions pos0 .. pos0+T-1 in one pass, the weights read
; once per layer for all of them (owner: "big scenarios are better because
; batch is cheaper"). The same arithmetic as model_forward, token by token
; where the order matters (norms, rope, the cache writes, attention), one
; fused matvec over T activation slots where it does not. Used by the
; embedder (every text is prompt and no generation) and available for the
; Qwen3-family chat models' prompts.
;
; The scratch buffers of model.asm hold ACT_SLOTS vectors each; token t's
; vector is base + t*dim*4, which is also the layout model_matvec_run expects
; for g_mv_nx slots.

section .text

%macro Q3T 3                   ; %1 = token r15's vector: %2 (base global) + r15 * [rbx + Model.%3] * 4
  mov  %1, r15
  imul %1, [rbx + Model.%3]
  shl  %1, 2
  add  %1, [rel %2]
%endmacro
%macro Q3SLOT 1                ; %1 = &g_act_slots[r15]
  mov  %1, r15
  imul %1, %1, ActSlot_size
  lea  rax, [rel g_act_slots]
  add  %1, rax
%endmacro

; model_forward_batch(rcx=Model*, rdx=ids u32*, r8=T, r9=pos0) -> rax = the
; last token's normed final state (while g_embed_only) or its logits.
model_forward_batch:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32] pos0, [rsp+40] q dim (n_head*head_dim), [rsp+48] scratch
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  [rsp+32], r9
  mov  rax, [rbx + Model.n_head]
  imul rax, [rbx + Model.head_dim]
  mov  [rsp+40], rax
  mov  [rel g_mv_nx], rdi
  ; x[t] = dequant(token_embd[ids[t]])
  xor  r15d, r15d
.emb:
  cmp  r15, rdi
  jae  .embd
  mov  rcx, [rbx + Model.tok_embd]
  mov  edx, [rsi + r15*4]
  Q3T  r8, g_x, n_embd
  call model_dequant_row
  inc  r15
  jmp  .emb
.embd:
  xor  r13d, r13d              ; layer
.layer:
  cmp  r13, [rbx + Model.n_layer]
  jae  .layers_done
  mov  r14, [rbx + Model.layers]
  mov  rax, r13
  imul rax, rax, Layer_size
  add  r14, rax                ; Layer*
  ; per token: xn = rmsnorm(x) * attn_norm, quantised into slot t
  xor  r15d, r15d
.n1:
  cmp  r15, rdi
  jae  .n1d
  Q3T  rcx, g_xn, n_embd
  Q3T  rdx, g_x, n_embd
  mov  r8, [r14 + Layer.attn_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.n_embd]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  Q3SLOT rcx
  Q3T  rdx, g_xn, n_embd
  mov  r8, [rbx + Model.n_embd]
  call k_quant_slot
  inc  r15
  jmp  .n1
.n1d:
  ; q, k, v for all T tokens: one fused job over the slots
  lea  rcx, [rel g_mv_ws]
  mov  rax, [r14 + Layer.attn_q]
  mov  [rcx], rax
  mov  rax, [r14 + Layer.attn_k]
  mov  [rcx+8], rax
  mov  rax, [r14 + Layer.attn_v]
  mov  [rcx+16], rax
  lea  rdx, [rel g_mv_outs]
  mov  rax, [rel g_q]
  mov  [rdx], rax
  mov  rax, [rel g_k]
  mov  [rdx+8], rax
  mov  rax, [rel g_v]
  mov  [rdx+16], rax
  mov  r8d, 3
  mov  r9, [rel g_xn]
  call model_matvec_multi
  ; per token: q/k head norms, rope at its position, k and v into the caches,
  ; attention over the positions up to its own
  xor  r15d, r15d
.at:
  cmp  r15, rdi
  jae  .atd
  mov  rax, [rsp+32]
  add  rax, r15                ; pos
  mov  [rsp+48], rax
  imul rax, [rbx + Model.head_dim]
  shl  rax, 2
  add  rax, [rbx + Model.rope_table]
  mov  [rsp+56], rax           ; the rope row
  ; q heads
  xor  r12d, r12d
.qh:
  cmp  r12, [rbx + Model.n_head]
  jae  .qhd
  mov  rax, r12
  imul rax, [rbx + Model.head_dim]
  shl  rax, 2
  mov  rcx, r15
  imul rcx, [rsp+40]
  shl  rcx, 2
  add  rax, rcx
  add  rax, [rel g_q]          ; head r12 of token r15
  mov  rcx, rax
  mov  rdx, rax
  mov  r8, [r14 + Layer.attn_q_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.head_dim]
  movss xmm0, [rbx + Model.rms_eps]
  push rax
  push rax
  call k_rmsnorm
  pop  rax
  pop  rax
  mov  rcx, rax
  mov  rdx, [rsp+56]
  call k_rope
  inc  r12
  jmp  .qh
.qhd:
  ; k heads
  xor  r12d, r12d
.kh:
  cmp  r12, [rbx + Model.n_head_kv]
  jae  .khd
  mov  rax, r12
  imul rax, [rbx + Model.head_dim]
  shl  rax, 2
  mov  rcx, r15
  imul rcx, [rbx + Model.kv_dim]
  shl  rcx, 2
  add  rax, rcx
  add  rax, [rel g_k]
  mov  rcx, rax
  mov  rdx, rax
  mov  r8, [r14 + Layer.attn_k_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.head_dim]
  movss xmm0, [rbx + Model.rms_eps]
  push rax
  push rax
  call k_rmsnorm
  pop  rax
  pop  rax
  mov  rcx, rax
  mov  rdx, [rsp+56]
  call k_rope
  inc  r12
  jmp  .kh
.khd:
  ; caches [layer][pos]
  mov  rax, r13
  imul rax, [rbx + Model.n_ctx]
  add  rax, [rsp+48]
  imul rax, [rbx + Model.kv_dim]
  shl  rax, 2
  mov  r12, rax                ; byte offset in both caches
  mov  rcx, r12
  add  rcx, [rbx + Model.kcache]
  Q3T  rdx, g_k, kv_dim
  mov  r8, [rbx + Model.kv_dim]
  shl  r8, 2
  call rt_memcpy
  mov  rcx, r12
  add  rcx, [rbx + Model.vcache]
  Q3T  rdx, g_v, kv_dim
  mov  r8, [rbx + Model.kv_dim]
  shl  r8, 2
  call rt_memcpy
  ; attention for this token
  lea  r9, [rel g_attnjob]
  mov  rax, r15
  imul rax, [rsp+40]
  shl  rax, 2
  mov  rcx, rax
  add  rcx, [rel g_q]
  mov  [r9 + AttnJob.q], rcx
  add  rax, [rel g_att_out]
  mov  [r9 + AttnJob.out], rax
  mov  rax, r13
  imul rax, [rbx + Model.n_ctx]
  imul rax, [rbx + Model.kv_dim]
  shl  rax, 2
  mov  rcx, rax
  add  rcx, [rbx + Model.kcache]
  mov  [r9 + AttnJob.kbase], rcx
  add  rax, [rbx + Model.vcache]
  mov  [r9 + AttnJob.vbase], rax
  mov  rax, [rel g_scores]
  mov  [r9 + AttnJob.scores], rax
  mov  rax, [rbx + Model.n_ctx]
  mov  [r9 + AttnJob.stride], rax
  mov  rax, [rsp+48]
  inc  rax
  mov  [r9 + AttnJob.seqlen], rax
  mov  rax, [rbx + Model.n_head]
  mov  [r9 + AttnJob.n_head], rax
  mov  rax, [rbx + Model.n_head_kv]
  mov  [r9 + AttnJob.n_head_kv], rax
  mov  rax, [rbx + Model.head_dim]
  mov  [r9 + AttnJob.head_dim], rax
  mov  rax, [rbx + Model.kv_dim]
  mov  [r9 + AttnJob.kv_dim], rax
  lea  rcx, [rel k_attn_job]
  lea  rdx, [rel g_attnjob]
  call pool_run
  ; the attention output quantised into slot t
  Q3SLOT rcx
  mov  rdx, r15
  imul rdx, [rsp+40]
  shl  rdx, 2
  add  rdx, [rel g_att_out]
  mov  r8, [rsp+40]
  call k_quant_slot
  inc  r15
  jmp  .at
.atd:
  ; x += Wo.att_out, all tokens
  mov  rcx, [r14 + Layer.attn_output]
  mov  rdx, [rel g_att_out]
  mov  r8, [rel g_xn]
  call model_matvec
  xor  r15d, r15d
.add1:
  cmp  r15, rdi
  jae  .add1d
  Q3T  rcx, g_x, n_embd
  Q3T  rdx, g_xn, n_embd
  mov  r8, [rbx + Model.n_embd]
  call k_add
  ; xn = rmsnorm(x) * ffn_norm, quantised into slot t
  Q3T  rcx, g_xn, n_embd
  Q3T  rdx, g_x, n_embd
  mov  r8, [r14 + Layer.ffn_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.n_embd]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  Q3SLOT rcx
  Q3T  rdx, g_xn, n_embd
  mov  r8, [rbx + Model.n_embd]
  call k_quant_slot
  inc  r15
  jmp  .add1
.add1d:
  ; gate and up for all tokens, silu per token, down for all
  lea  rcx, [rel g_mv_ws]
  mov  rax, [r14 + Layer.ffn_gate]
  mov  [rcx], rax
  mov  rax, [r14 + Layer.ffn_up]
  mov  [rcx+8], rax
  lea  rdx, [rel g_mv_outs]
  mov  rax, [rel g_g]
  mov  [rdx], rax
  mov  rax, [rel g_u]
  mov  [rdx+8], rax
  mov  r8d, 2
  mov  r9, [rel g_xn]
  call model_matvec_multi
  xor  r15d, r15d
.ff:
  cmp  r15, rdi
  jae  .ffd
  Q3T  rcx, g_g, n_ff
  Q3T  rdx, g_u, n_ff
  mov  r8, [rbx + Model.n_ff]
  call k_silu_mul
  Q3SLOT rcx
  Q3T  rdx, g_g, n_ff
  mov  r8, [rbx + Model.n_ff]
  call k_quant_slot
  inc  r15
  jmp  .ff
.ffd:
  mov  rcx, [r14 + Layer.ffn_down]
  mov  rdx, [rel g_g]
  mov  r8, [rel g_xn]
  call model_matvec
  xor  r15d, r15d
.add2:
  cmp  r15, rdi
  jae  .add2d
  Q3T  rcx, g_x, n_embd
  Q3T  rdx, g_xn, n_embd
  mov  r8, [rbx + Model.n_embd]
  call k_add
  inc  r15
  jmp  .add2
.add2d:
  inc  r13
  jmp  .layer
.layers_done:
  ; the last token: xf = rmsnorm(x) * output_norm into slot 0's vector
  mov  qword [rel g_mv_nx], 1
  lea  r15, [rdi - 1]
  mov  rcx, [rel g_xn]
  Q3T  rdx, g_x, n_embd
  mov  r8, [rbx + Model.output_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.n_embd]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  cmp  qword [rel g_embed_only], 0
  je   .lm
  mov  rax, [rel g_xn]
  jmp  .ret
.lm:
  mov  rcx, [rel g_xn]
  mov  rdx, [rbx + Model.n_embd]
  call model_fill_sums
  mov  rcx, [rbx + Model.output]
  mov  rdx, [rel g_xn]
  mov  r8, [rel g_logits]
  call model_matvec
  mov  rax, [rel g_logits]
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

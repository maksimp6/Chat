; q35f.asm — Qwen3.5's forward pass for a batch of tokens (a prompt in slices
; of up to ACT_SLOTS, or one token when generating), and the pool jobs it runs.
; Every weight is read once per batch: the matvecs dot each row with all T
; activation slots (k_matvec_job, g_mv_nx = T); the parts that depend on the
; order of tokens — the causal conv, the delta rule, the attention — loop over
; the tokens inside the heads' jobs. Buffers hold T tokens: token t's vector of
; size D starts at base + t*D*4 (BT below), which is also where the matvec job
; puts slot t's output (outs + t*rows*4).
;
;   q35_forward_batch(rcx=Model*, rdx=ids u32*, r8=T, r9=pos0) -> rax = the last
;                     token's logits f32*  (positions pos0 .. pos0+T-1)
;   q35_forward(rcx=Model*, rdx=token, r8=pos) -> rax = logits   (T = 1)
;   q35_conv_job, q35_lin_job   pool bodies (rcx=Q35L*, rdx=tid, r8=nthreads)
;
; %included into asmrun.asm after q35.asm (bits 64, default rel).

section .text

%macro BT 3                    ; %1 = token t's vector: %2 (base global) + r15 * %3 (dim global) * 4
  mov  %1, r15
  imul %1, [rel %3]
  shl  %1, 2
  add  %1, [rel %2]
%endmacro
%macro SLOT 1                  ; %1 = &g_act_slots[r15]
  mov  %1, r15
  imul %1, %1, ActSlot_size
  lea  rax, [rel g_act_slots]
  add  %1, rax
%endmacro

; q35_forward(rcx=Model*, rdx=token, r8=pos) -> rax = logits: one token.
q35_forward:
  WINPROLOGUE
  sub  rsp, 16
  mov  [rsp+32], edx
  lea  rdx, [rsp+32]
  mov  r9, r8
  mov  r8d, 1
  call q35_forward_batch
  WINEPILOGUE
  ret

; q35_forward_batch — see above. rbx = Model*, r12 = ids, r13 = layer,
; r14 = Q35L*, r15 = token t.
q35_forward_batch:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32..48) arg 5; [rsp+48] scratch
  mov  rbx, rcx
  mov  r12, rdx
  mov  [rel g35_bt], r8
  mov  [rel g35_pos0], r9
  mov  [rel g_mv_nx], r8
  cmp  qword [rel g35_trace_exec], 0
  je   .no_tr0
  lea  rcx, [rel q35_s_nl]
  call rt_out_str              ; the trace on lines of its own
  call rt_now_ns               ; the trace's clock starts with the forward
  mov  [rel g35_tr_t], rax
  mov  qword [rel g35_tr_layer], 0
.no_tr0:
  ; x_t = embedding[ids[t]]
  xor  r15d, r15d
.emb:
  cmp  r15, [rel g35_bt]
  jae  .embd
  mov  rcx, [rbx + Model.tok_embd]
  mov  edx, [r12 + r15*4]
  BT   r8, g35_x, g35_embd
  call model_dequant_row
  inc  r15
  jmp  .emb
.embd:
  TR   q35_tr_embd
  xor  r13d, r13d
.layer:
  cmp  r13, [rel g35_nlayer]
  jae  .final
  mov  rax, r13
  imul rax, rax, Q35L_size
  add  rax, [rel g35_layers]
  mov  r14, rax
  mov  [rel g35_tr_layer], r13
  ; xn_t = norm(x_t), quantised into slot t
  xor  r15d, r15d
.n1:
  cmp  r15, [rel g35_bt]
  jae  .n1d
  BT   rcx, g35_xn, g35_embd
  BT   rdx, g35_x, g35_embd
  mov  r8, [r14 + Q35L.attn_norm]
  mov  r9, [rel g35_embd]
  call q35_norm
  SLOT rcx
  BT   rdx, g35_xn, g35_embd
  mov  r8, [rel g35_embd]
  call k_quant_slot
  inc  r15
  jmp  .n1
.n1d:
  TR   q35_tr_norm
  cmp  qword [r14 + Q35L.kind], 0
  jne  .full
  ; ---------------- linear attention ----------------
  ; the four projections (qkv, z, beta, alpha: one row space) as one pool job
  lea  rcx, [rel g_mv_ws]
  mov  rax, [r14 + Q35L.wqkv]
  mov  [rcx], rax
  mov  rax, [r14 + Q35L.wz]
  mov  [rcx + 8], rax
  mov  rax, [r14 + Q35L.wb]
  mov  [rcx + 16], rax
  mov  rax, [r14 + Q35L.wa]
  mov  [rcx + 24], rax
  lea  rdx, [rel g_mv_outs]
  mov  rax, [rel g35_qkv]
  mov  [rdx], rax
  mov  rax, [rel g35_z]
  mov  [rdx + 8], rax
  mov  rax, [rel g35_b]
  mov  [rdx + 16], rax
  mov  rax, [rel g35_a]
  mov  [rdx + 24], rax
  mov  r8d, 4
  mov  r9, [rel g35_xn]
  call model_matvec_multi
  cmp  qword [rel g35_trace_exec], 0
  je   .no_tr1
  mov  rax, [r14 + Q35L.wqkv]
  mov  rdx, [rax + Tensor.nbytes]
  mov  rax, [r14 + Q35L.wz]
  add  rdx, [rax + Tensor.nbytes]
  mov  rax, [r14 + Q35L.wb]
  add  rdx, [rax + Tensor.nbytes]
  mov  rax, [r14 + Q35L.wa]
  add  rdx, [rax + Tensor.nbytes]
  lea  rcx, [rel q35_tr_proj4]
  call q35_tr
.no_tr1:
  ; the conv with the q/k norms, then the delta rule with the gated norm: every
  ; core takes a share of the channels, then of the value heads; every token in order
  lea  rcx, [rel q35_conv_job]
  mov  rdx, r14
  call pool_run
  TR   q35_tr_conv
  lea  rcx, [rel q35_lin_job]
  mov  rdx, r14
  call pool_run
  TR   q35_tr_delta
  xor  r15d, r15d
.ls:
  cmp  r15, [rel g35_bt]
  jae  .lsd
  SLOT rcx
  BT   rdx, g35_att, g35_valdim
  mov  r8, [rel g35_valdim]
  call k_quant_slot
  inc  r15
  jmp  .ls
.lsd:
  TR   q35_tr_sums
  MV [r14 + Q35L.wout], [rel g35_att], [rel g35_t]
  TRW  q35_tr_out, [r14 + Q35L.wout]
  jmp  .residual
.full:
  ; ---------------- full attention ----------------
  ; q(+gate), k, v as one pool job
  lea  rcx, [rel g_mv_ws]
  lea  rdx, [rel g_mv_outs]
  mov  rax, [r14 + Q35L.wq]
  mov  [rcx], rax
  mov  rax, [rel g35_qg]
  mov  [rdx], rax
  mov  r8d, 1
  cmp  qword [r14 + Q35L.wqg], 0
  je   .nogate_mv
  mov  rax, [r14 + Q35L.wqg]
  mov  [rcx + 8], rax
  mov  rax, [rel g35_gate]
  mov  [rdx + 8], rax
  inc  r8d
.nogate_mv:
  mov  rax, [r14 + Q35L.wk]
  mov  [rcx + r8*8], rax
  mov  rax, [rel g35_k]
  mov  [rdx + r8*8], rax
  mov  rax, [r14 + Q35L.wv]
  mov  [rcx + r8*8 + 8], rax
  mov  rax, [rel g35_v]
  mov  [rdx + r8*8 + 8], rax
  add  r8d, 2
  mov  r9, [rel g35_xn]
  call model_matvec_multi
  cmp  qword [rel g35_trace_exec], 0
  je   .no_tr2
  mov  rax, [r14 + Q35L.wq]
  mov  rdx, [rax + Tensor.nbytes]
  mov  rax, [r14 + Q35L.wk]
  add  rdx, [rax + Tensor.nbytes]
  mov  rax, [r14 + Q35L.wv]
  add  rdx, [rax + Tensor.nbytes]
  mov  rax, [r14 + Q35L.wqg]
  test rax, rax
  jz   .tr2_nog
  add  rdx, [rax + Tensor.nbytes]
.tr2_nog:
  lea  rcx, [rel q35_tr_projqkv]
  call q35_tr
.no_tr2:
  ; per token: q heads normed and roped (gate half left where it is), k heads
  ; normed and roped, k and v into the caches at pos0 + t
  xor  r15d, r15d
.at:
  cmp  r15, [rel g35_bt]
  jae  .atd
  mov  rax, [rel g35_pos0]
  add  rax, r15
  imul rax, [rel g35_rot]
  shl  rax, 2
  add  rax, [rel g35_rope]
  mov  [rsp+48], rax           ; this token's rope row
  xor  esi, esi                ; head
.qh:
  cmp  rsi, [rel g35_nhead]
  jae  .qh_done
  mov  rax, rsi
  imul rax, [rel g35_hd]
  shl  rax, 2                  ; head offset in q
  mov  rdx, rax
  cmp  qword [rel g35_qfused], 0
  je   .qsrc
  add  rdx, rdx                ; fused: head h at 2 h hd
.qsrc:
  mov  rcx, r15                ; token t's qg row: qg + t * (rows of wq) * 4
  mov  r8, [r14 + Q35L.wq]
  imul rcx, [r8 + Tensor.ne1]
  shl  rcx, 2
  add  rdx, rcx
  add  rdx, [rel g35_qg]
  mov  rcx, r15                ; q_t
  imul rcx, [rel g35_nhead]
  imul rcx, [rel g35_hd]
  shl  rcx, 2
  add  rcx, rax
  add  rcx, [rel g35_q]
  mov  rdi, rcx                ; the head in q_t
  mov  r8, [r14 + Q35L.q_norm]
  mov  r9, [rel g35_hd]
  call q35_norm
  mov  rcx, rdi
  mov  rdx, [rsp+48]
  mov  r8, [rel g35_rot]
  call q35_rope_partial
  inc  rsi
  jmp  .qh
.qh_done:
  xor  esi, esi
.kh:
  cmp  rsi, [rel g35_nkv]
  jae  .kh_done
  mov  rax, rsi
  imul rax, [rel g35_hd]
  shl  rax, 2
  BT   rcx, g35_k, g35_kvdim
  add  rcx, rax
  mov  rdi, rcx
  mov  rdx, rcx
  mov  r8, [r14 + Q35L.k_norm]
  mov  r9, [rel g35_hd]
  call q35_norm
  mov  rcx, rdi
  mov  rdx, [rsp+48]
  mov  r8, [rel g35_rot]
  call q35_rope_partial
  inc  rsi
  jmp  .kh
.kh_done:
  ; the caches are a ring of g35_ctx slots: position p lives in slot p mod ctx,
  ; so once the context is full the oldest attention entries are overwritten
  ; and the dialog goes on (the recurrent layers keep what came before)
  mov  rax, [rel g35_pos0]
  add  rax, r15
  xor  edx, edx
  div  qword [rel g35_ctx]
  mov  rax, rdx                ; the slot
  imul rax, [rel g35_kvdim]
  shl  rax, 2
  mov  [rsp+56], rax           ; the slot's byte offset
  mov  rcx, [r14 + Q35L.kcache]
  add  rcx, rax
  BT   rdx, g35_k, g35_kvdim
  mov  r8, [rel g35_kvdim]
  shl  r8, 2
  call rt_memcpy
  mov  rax, [rsp+56]
  mov  rcx, [r14 + Q35L.vcache]
  add  rcx, rax
  BT   rdx, g35_v, g35_kvdim
  mov  r8, [rel g35_kvdim]
  shl  r8, 2
  call rt_memcpy
  ; this token's attention right away, over the slots written so far: the
  ; later tokens of the batch have not overwritten anything it may see
  lea  r9, [rel g_attnjob]
  mov  rax, r15
  imul rax, [rel g35_nhead]
  imul rax, [rel g35_hd]
  shl  rax, 2
  mov  rcx, rax
  add  rcx, [rel g35_q]
  mov  [r9 + AttnJob.q], rcx
  add  rax, [rel g35_att]
  mov  [r9 + AttnJob.out], rax
  mov  rax, [r14 + Q35L.kcache]
  mov  [r9 + AttnJob.kbase], rax
  mov  rax, [r14 + Q35L.vcache]
  mov  [r9 + AttnJob.vbase], rax
  mov  rax, [rel g_scores]
  mov  [r9 + AttnJob.scores], rax
  mov  rax, [rel g35_ctx]
  mov  [r9 + AttnJob.stride], rax
  mov  rax, [rel g35_pos0]
  add  rax, r15
  inc  rax
  cmp  rax, [rel g35_ctx]
  jbe  .seq_ok
  mov  rax, [rel g35_ctx]      ; the ring is full: every slot is in the window
.seq_ok:
  mov  [r9 + AttnJob.seqlen], rax
  mov  rax, [rel g35_nhead]
  mov  [r9 + AttnJob.n_head], rax
  mov  rax, [rel g35_nkv]
  mov  [r9 + AttnJob.n_head_kv], rax
  mov  rax, [rel g35_hd]
  mov  [r9 + AttnJob.head_dim], rax
  mov  rax, [rel g35_kvdim]
  mov  [r9 + AttnJob.kv_dim], rax
  lea  rcx, [rel k_attn_job]
  lea  rdx, [rel g_attnjob]
  call pool_run
  inc  r15
  jmp  .at
.atd:
  TR   q35_tr_attn
  ; per token: o *= sigmoid(gate) per head, then quantised into slot t
  xor  r15d, r15d
.ag:
  cmp  r15, [rel g35_bt]
  jae  .agd
  xor  esi, esi
.gh:
  cmp  rsi, [rel g35_nhead]
  jae  .gh_done
  mov  rax, rsi
  imul rax, [rel g35_hd]
  shl  rax, 2                  ; the head's offset
  mov  rcx, r15
  imul rcx, [rel g35_nhead]
  imul rcx, [rel g35_hd]
  shl  rcx, 2
  add  rcx, rax
  add  rcx, [rel g35_att]      ; the head in att_t
  cmp  qword [rel g35_qfused], 0
  je   .gsep
  mov  rdx, rax
  add  rdx, rdx
  mov  r8, [rel g35_hd]
  shl  r8, 2
  add  rdx, r8                 ; the gate half of head h
  mov  r8, r15
  mov  r9, [r14 + Q35L.wq]
  imul r8, [r9 + Tensor.ne1]
  shl  r8, 2
  add  rdx, r8
  add  rdx, [rel g35_qg]
  jmp  .gmul
.gsep:
  mov  rdx, r15
  imul rdx, [rel g35_nhead]
  imul rdx, [rel g35_hd]
  shl  rdx, 2
  add  rdx, rax
  add  rdx, [rel g35_gate]
.gmul:
  mov  r8, [rel g35_hd]
  call q35_sigmoid_mul
  inc  rsi
  jmp  .gh
.gh_done:
  SLOT rcx
  mov  rdx, r15
  imul rdx, [rel g35_nhead]
  imul rdx, [rel g35_hd]
  shl  rdx, 2
  add  rdx, [rel g35_att]
  mov  r8, [rel g35_nhead]
  imul r8, [rel g35_hd]
  call k_quant_slot
  inc  r15
  jmp  .ag
.agd:
  TR   q35_tr_gate
  MV [r14 + Q35L.wo], [rel g35_att], [rel g35_t]
  TRW  q35_tr_wo, [r14 + Q35L.wo]
.residual:
  ; per token: x_t += t_t; xn_t = ffn_norm(x_t), quantised into slot t
  xor  r15d, r15d
.rs:
  cmp  r15, [rel g35_bt]
  jae  .rsd
  BT   rcx, g35_x, g35_embd
  BT   rdx, g35_t, g35_embd
  mov  r8, [rel g35_embd]
  call k_add
  BT   rcx, g35_xn, g35_embd
  BT   rdx, g35_x, g35_embd
  mov  r8, [r14 + Q35L.ffn_norm]
  mov  r9, [rel g35_embd]
  call q35_norm
  SLOT rcx
  BT   rdx, g35_xn, g35_embd
  mov  r8, [rel g35_embd]
  call k_quant_slot
  inc  r15
  jmp  .rs
.rsd:
  TR   q35_tr_ffnnorm
  ; ---------------- feed-forward ----------------
  lea  rcx, [rel g_mv_ws]
  mov  rax, [r14 + Q35L.ffn_gate]
  mov  [rcx], rax
  mov  rax, [r14 + Q35L.ffn_up]
  mov  [rcx + 8], rax
  lea  rdx, [rel g_mv_outs]
  mov  rax, [rel g35_g]
  mov  [rdx], rax
  mov  rax, [rel g35_u]
  mov  [rdx + 8], rax
  mov  r8d, 2
  mov  r9, [rel g35_xn]
  call model_matvec_multi
  cmp  qword [rel g35_trace_exec], 0
  je   .no_tr3
  mov  rax, [r14 + Q35L.ffn_gate]
  mov  rdx, [rax + Tensor.nbytes]
  mov  rax, [r14 + Q35L.ffn_up]
  add  rdx, [rax + Tensor.nbytes]
  lea  rcx, [rel q35_tr_gateup]
  call q35_tr
.no_tr3:
  xor  r15d, r15d
.fs:
  cmp  r15, [rel g35_bt]
  jae  .fsd
  BT   rcx, g35_g, g35_ff
  BT   rdx, g35_u, g35_ff
  mov  r8, [rel g35_ff]
  call k_silu_mul
  SLOT rcx
  BT   rdx, g35_g, g35_ff
  mov  r8, [rel g35_ff]
  call k_quant_slot
  inc  r15
  jmp  .fs
.fsd:
  TR   q35_tr_silu
  MV [r14 + Q35L.ffn_down], [rel g35_g], [rel g35_t]
  TRW  q35_tr_down, [r14 + Q35L.ffn_down]
  xor  r15d, r15d
.fa:
  cmp  r15, [rel g35_bt]
  jae  .fad
  BT   rcx, g35_x, g35_embd
  BT   rdx, g35_t, g35_embd
  mov  r8, [rel g35_embd]
  call k_add
  inc  r15
  jmp  .fa
.fad:
  TR   q35_tr_add
  cmp  qword [rel g35_trace_exec], 0
  je   .next_layer
  mov  rcx, r13                ; --trace: this layer's residual statistics (token 0)
  mov  rdx, [rel g35_x]
  mov  r8, [rel g35_embd]
  call q35_trace_vec
.next_layer:
  inc  r13
  jmp  .layer
.final:
  ; the last token's logits: xn_0 = norm(x_last), one activation vector
  mov  qword [rel g35_tr_layer], 99
  mov  r15, [rel g35_bt]
  dec  r15
  BT   rdx, g35_x, g35_embd
  mov  rcx, [rel g35_xn]
  mov  r8, [rel g35_out_norm]
  mov  r9, [rel g35_embd]
  call q35_norm
  xor  r15d, r15d
  SLOT rcx
  mov  rdx, [rel g35_xn]
  mov  r8, [rel g35_embd]
  call k_quant_slot
  TR   q35_tr_outnorm
  mov  qword [rel g_mv_nx], 1
  MV [rbx + Model.output], [rel g35_xn], [rel g35_logits]
  TRW  q35_tr_lmhead, [rbx + Model.output]
  cmp  qword [rel g35_trace_exec], 0
  je   .fwd_ret
  mov  rcx, 1000               ; --trace: the logits' statistics and the best id
  mov  rdx, [rel g35_logits]
  mov  r8, [rel g35_vocab]
  call q35_trace_vec
  TR   q35_tr_stats            ; (the statistics' own cost, apart from the sampler's)
.fwd_ret:
  mov  rax, [rel g35_logits]
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; q35_conv_job(rcx=Q35L*, rdx=tid, r8=nthreads) — pool body: for every token
; in order, the causal conv over this thread's share of the channels (whole
; dk-blocks; the state advances per token), then the l2norm of the q and k
; heads inside that share (q also scaled by dk^-1/2).
q35_conv_job:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32..80) Q35Conv; [rsp+80]: (unused)
  mov  rbx, rcx                ; layer
  mov  r12, rdx                ; tid
  mov  r13, r8                 ; threads
  ; blocks of dk: b0 = tid nblk / n, b1 = (tid + 1) nblk / n
  mov  rax, [rel g35_convdim]
  xor  edx, edx
  div  qword [rel g35_dk]
  mov  rsi, rax                ; nblk
  mov  rax, r12
  imul rax, rsi
  xor  edx, edx
  div  r13
  mov  r14, rax                ; b0
  lea  rax, [r12 + 1]
  imul rax, rsi
  xor  edx, edx
  div  r13
  mov  rsi, rax                ; b1
  cmp  r14, rsi
  jae  .done
  xor  r15d, r15d              ; token t
.tok:
  cmp  r15, [rel g35_bt]
  jae  .done
  lea  rcx, [rsp+32]
  mov  rax, [rbx + Q35L.conv_state]
  mov  [rcx + Q35Conv.state], rax
  BT   rax, g35_qkv, g35_convdim
  mov  [rcx + Q35Conv.x], rax
  mov  rax, [rbx + Q35L.conv_w]
  mov  [rcx + Q35Conv.w], rax
  mov  rax, r14
  imul rax, [rel g35_dk]
  mov  [rcx + Q35Conv.c0], rax
  mov  rax, rsi
  imul rax, [rel g35_dk]
  mov  [rcx + Q35Conv.c], rax
  call q35_conv_step
  ; the q heads (blocks below lk) and the k heads (below 2 lk) in the share
  mov  rdi, r14
.blk:
  cmp  rdi, rsi
  jae  .tok_next
  mov  rax, [rel g35_lk]
  add  rax, rax
  cmp  rdi, rax
  jae  .tok_next               ; a v block: nothing more to do
  mov  rax, rdi
  imul rax, [rel g35_dk]
  shl  rax, 2
  BT   rcx, g35_qkv, g35_convdim
  add  rcx, rax
  mov  r12, rcx                ; the head
  mov  rdx, [rel g35_dk]
  call q35_l2norm
  cmp  rdi, [rel g35_lk]
  jae  .next_blk
  mov  rcx, r12
  mov  rdx, [rel g35_dk]
  vcvtsi2ss xmm0, xmm0, rdx
  vsqrtss xmm0, xmm0, xmm0
  vmovss xmm1, [rel kc_one]
  vdivss xmm0, xmm1, xmm0
  call q35_scale
.next_blk:
  inc  rdi
  jmp  .blk
.tok_next:
  inc  r15
  jmp  .tok
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

; q35_lin_job(rcx=Q35L*, rdx=tid, r8=nthreads) — pool body: for this thread's
; share of the value heads, token by token: g and beta, the delta rule and
; the gated norm, with the thread's own mem/d scratch (g35_scratch + tid*1024).
; rbx = h1, r12 = tid, r14 = layer, r15 = head, rdi = Q35Delta, rsi = token.
q35_lin_job:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 112                ; [rsp+32] arg 5; [rsp+40..120) Q35Delta; [rsp+120] h0
  mov  r14, rcx                ; layer
  mov  r12, rdx                ; tid
  mov  r13, r8                 ; threads
  mov  rax, r12
  imul rax, [rel g35_lv]
  xor  edx, edx
  div  r13
  mov  [rsp+120], rax          ; h0
  lea  rax, [r12 + 1]
  imul rax, [rel g35_lv]
  xor  edx, edx
  div  r13
  mov  rbx, rax                ; h1
  lea  rdi, [rsp+40]           ; this thread's Q35Delta
  mov  rax, r12
  shl  rax, 10
  add  rax, [rel g35_scratch]
  mov  [rdi + Q35Delta.mem], rax
  add  rax, 512
  mov  [rdi + Q35Delta.d], rax
  mov  r15, [rsp+120]
.vh:
  cmp  r15, rbx
  jae  .vh_done
  ; the head's state and sizes (the same for every token)
  mov  rax, r15
  imul rax, [rel g35_dk]
  imul rax, [rel g35_dv]
  shl  rax, 2
  add  rax, [r14 + Q35L.S]
  mov  [rdi + Q35Delta.S], rax
  mov  rax, [rel g35_dk]
  mov  [rdi + Q35Delta.dk], rax
  mov  rax, [rel g35_dv]
  mov  [rdi + Q35Delta.dv], rax
  xor  esi, esi                ; token
.tok:
  cmp  rsi, [rel g35_bt]
  jae  .tok_done
  ; q, k of the head's key head (v mod key heads: the GGUF's value heads are
  ; tiled over the key heads), v of the head, in token t's qkv
  mov  rax, rsi
  imul rax, [rel g35_convdim]
  shl  rax, 2
  add  rax, [rel g35_qkv]
  mov  r13, rax                ; qkv_t
  mov  rax, r15
  xor  edx, edx
  div  qword [rel g35_lk]
  mov  rax, rdx
  imul rax, [rel g35_dk]
  shl  rax, 2
  lea  rcx, [r13 + rax]
  mov  [rdi + Q35Delta.q], rcx
  mov  rcx, [rel g35_keydim]
  shl  rcx, 2
  add  rcx, r13
  add  rcx, rax
  mov  [rdi + Q35Delta.k], rcx
  mov  rax, [rel g35_keydim]
  add  rax, rax
  mov  rcx, r15
  imul rcx, [rel g35_dv]
  add  rax, rcx
  shl  rax, 2
  add  rax, r13
  mov  [rdi + Q35Delta.v], rax
  ; out: the head in att_t (stride valdim)
  mov  rax, rsi
  imul rax, [rel g35_valdim]
  mov  rcx, r15
  imul rcx, [rel g35_dv]
  add  rax, rcx
  shl  rax, 2
  add  rax, [rel g35_att]
  mov  [rdi + Q35Delta.out], rax
  ; g = A softplus(a + dt_bias); beta = sigmoid(b)   (a_t, b_t: stride lv)
  mov  rax, rsi
  imul rax, [rel g35_lv]
  add  rax, r15
  mov  rcx, [rel g35_a]
  vmovss xmm0, [rcx + rax*4]
  mov  rcx, [r14 + Q35L.dt_bias]
  vaddss xmm0, xmm0, [rcx + r15*4]
  call q35_softplus
  mov  rcx, [r14 + Q35L.A]
  vmulss xmm0, xmm0, [rcx + r15*4]
  vmovss [rdi + Q35Delta.g], xmm0
  mov  rax, rsi
  imul rax, [rel g35_lv]
  add  rax, r15
  mov  rcx, [rel g35_b]
  vmovss xmm0, [rcx + rax*4]
  call q35_sigmoid1
  vmovss [rdi + Q35Delta.beta], xmm0
  mov  rcx, rdi
  call q35_delta_step
  ; o_h = rmsnorm(o_h) w silu(z_h)   (z_t: stride valdim)
  mov  rcx, [rdi + Q35Delta.out]
  mov  rdx, rcx
  mov  r8, [r14 + Q35L.ssm_norm]
  mov  r9, rsi
  imul r9, [rel g35_valdim]
  mov  rax, r15
  imul rax, [rel g35_dv]
  add  r9, rax
  shl  r9, 2
  add  r9, [rel g35_z]
  mov  rax, [rel g35_dv]
  mov  [rsp+32], rax
  call q35_gated_norm
  inc  rsi
  jmp  .tok
.tok_done:
  inc  r15
  jmp  .vh
.vh_done:
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
  align 8
g35_bt:       dq 1             ; tokens in the batch being processed
g35_pos0:     dq 0             ; the first token's position
section .text

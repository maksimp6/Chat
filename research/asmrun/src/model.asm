; model.asm — the Qwen3 forward pass, KV cache, generation loop, and CLI.
;
; %included into asmrun.asm (bits 64, default rel already set). Nothing here uses
; `section`/`global`/`extern`/`org`/`bits`, and no storage uses `resb`: mutable
; globals are zero-initialised `dq 0` bytes at the end of this file, and every big
; buffer comes from rt_alloc at run time and is kept in a module global pointer.
;
; Public surface (Windows x64 everywhere, also between modules):
;   model_load(rcx=path utf16, rdx=n_ctx, r8=nthreads) -> rax Model*
;   model_forward(rcx=Model*, rdx=token, r8=pos)        -> rax logits f32*
;   model_generate(rcx=Model*, rdx=ids u32*, r8=n, r9=max_new)
;   main(rcx=argc, rdx=argv)                            -> eax exit code
;
; Internal: model_dequant_row, model_matvec, model_fill_sums, model_emit_ms,
;           model_emit_rate, main_print_summary.
;
; Calls into runtime: rt_alloc, rt_map_file, rt_read_file, rt_read_stdin, rt_out,
;   rt_err, rt_err_str, rt_err_u64, rt_err_f32, rt_fatal, rt_now_ns, rt_cpu_count,
;   rt_cpuid_check, rt_bench_bandwidth, pool_start, pool_run, rt_memcpy,
;   rt_watoi, rt_wcmp_ascii.
;   Into gguf:      gguf_open, gguf_cli.
;   Into tokenizer: tok_init, tok_encode, tok_decode, tok_is_stop, tok_cli.
;   Into kernels:   k_rmsnorm, k_add, k_silu_mul, k_argmax, k_sum32, k_sum16,
;   k_rope, k_rope_table, k_matvec_job, k_attn_job, kernels_cli.
;
; Coordination with kernels.asm: the attention scores scratch is f32[n_threads]
; [n_ctx] and the per-thread row stride is n_ctx. AttnJob has no n_ctx field, so
; model_load publishes it in the module global g_n_ctx; k_attn_job reads g_n_ctx
; and places thread tid's score row at AttnJob.scores + tid*g_n_ctx.

; CLI mode selector values held in r14d while main parses the argument vector.
MODE_DEFAULT  equ 0
MODE_BW       equ 1
MODE_CPUID    equ 2
MODE_GGUF     equ 3
MODE_TOKENIZE equ 4
MODE_KERNELS  equ 5

; f32 bit patterns used when formatting rates (no .rodata float loads needed).
F32_1E6 equ 0x49742400          ; 1000000.0f  (nanoseconds -> milliseconds)
F32_1E9 equ 0x4E6E6B28          ; 1000000000.0f (seconds <-> nanoseconds, GB=1e9)

; ===========================================================================
; model_dequant_row — dequantise one row of a tensor into f32[ne0]
; ===========================================================================

; model_dequant_row(rcx=Tensor*, rdx=row, r8=out f32*). Writes ne0 f32 values of
; row `row` of the tensor to `out`, handling F32, F16, Q4_K and Q6_K exactly as
; ggml stores them. Used for the token embedding (a Q4_K row); the other type
; paths exist for completeness. Leaf (no external calls); saves every nonvolatile
; it uses and a 128-byte stack scratch for the Q4_K/Q6_K sub-block scales.
model_dequant_row:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  push rbp
  mov  rbp, rsp
  sub  rsp, 128                 ; scratch: [rsp+0..64) sc[8]/m[8], [+64]=d, [+68]=dmin,
                                ; [+72]=half, [+80]=ql, [+88]=qh, [+104]=out half base
  mov  rsi, rcx                 ; Tensor*
  mov  rdi, r8                  ; out f32*
  mov  r9, rdx                  ; row
  mov  r10d, [rsi + Tensor.type]
  mov  r11, [rsi + Tensor.ne0]  ; cols
  mov  r12, [rsi + Tensor.data] ; data base
  cmp  r10d, GGML_F32
  je   .f32
  cmp  r10d, GGML_F16
  je   .f16
  cmp  r10d, GGML_Q4_K
  je   .q4k
  cmp  r10d, GGML_Q6_K
  je   .q6k
  jmp  .done                    ; unknown type: leave out as-is (gguf already refused it)

.f32:
  mov  rax, r9
  imul rax, r11
  shl  rax, 2
  add  rax, r12                 ; src = data + row*cols*4
  xor  rcx, rcx
.f32l:
  cmp  rcx, r11
  jae  .done
  mov  edx, [rax + rcx*4]
  mov  [rdi + rcx*4], edx
  inc  rcx
  jmp  .f32l

.f16:
  mov  rax, r9
  imul rax, r11
  shl  rax, 1
  add  rax, r12                 ; src = data + row*cols*2
  xor  rcx, rcx
.f16l:
  cmp  rcx, r11
  jae  .done
  movzx edx, word [rax + rcx*2]
  movd xmm0, edx
  vcvtph2ps xmm0, xmm0          ; half -> f32 (low lane)
  movss [rdi + rcx*4], xmm0
  inc  rcx
  jmp  .f16l

.q4k:
  mov  rax, r11
  shr  rax, 8                   ; blocks = cols / QK_K
  mov  r13, rax
  mov  rax, r9
  imul rax, r13                 ; row*blocks
  imul rax, rax, Q4K_BYTES
  add  rax, r12                 ; first block pointer
  mov  rsi, rax                 ; rsi = current block
  xor  r14, r14                 ; block index
  xor  r15, r15                 ; out element base (block*256)
.q4blk:
  cmp  r14, r13
  jae  .done
  movzx eax, word [rsi]         ; d (f16)
  movd xmm0, eax
  vcvtph2ps xmm0, xmm0
  movss [rsp+64], xmm0
  movzx eax, word [rsi+2]       ; dmin (f16)
  movd xmm0, eax
  vcvtph2ps xmm0, xmm0
  movss [rsp+68], xmm0
  lea  r8, [rsi+4]              ; scales[12]
  xor  rcx, rcx                 ; j
.scloop:
  cmp  rcx, 8
  jae  .scdone
  cmp  rcx, 4
  jae  .schi
  movzx eax, byte [r8+rcx]      ; j<4: sc = scales[j] & 63
  and  eax, 63
  mov  [rsp + rcx*4], eax
  movzx edx, byte [r8+rcx+4]    ;        m = scales[j+4] & 63
  and  edx, 63
  mov  [rsp+32 + rcx*4], edx
  jmp  .scnext
.schi:
  movzx eax, byte [r8+rcx+4]    ; j>=4: sc = (scales[j+4]&15) | ((scales[j-4]>>6)<<4)
  and  eax, 15
  movzx edx, byte [r8+rcx-4]
  shr  edx, 6
  shl  edx, 4
  or   eax, edx
  mov  [rsp + rcx*4], eax
  movzx eax, byte [r8+rcx+4]    ;        m  = (scales[j+4]>>4) | ((scales[j]>>6)<<4)
  shr  eax, 4
  movzx edx, byte [r8+rcx]
  shr  edx, 6
  shl  edx, 4
  or   eax, edx
  mov  [rsp+32 + rcx*4], eax
.scnext:
  inc  rcx
  jmp  .scloop
.scdone:
  lea  r9, [rsi+16]             ; qs[128]
  xor  rcx, rcx                 ; chunk c in 0..3
.q4chunk:
  cmp  rcx, 4
  jae  .q4blk_next
  mov  rax, rcx
  shl  rax, 1                   ; 2c (low sub-block)
  mov  edx, [rsp + rax*4]       ; sc[2c]
  cvtsi2ss xmm0, edx
  mulss xmm0, [rsp+64]          ; dd_lo = d*sc[2c]
  mov  edx, [rsp+32 + rax*4]    ; m[2c]
  cvtsi2ss xmm1, edx
  mulss xmm1, [rsp+68]          ; mm_lo = dmin*m[2c]
  mov  edx, [rsp + rax*4 + 4]   ; sc[2c+1]
  cvtsi2ss xmm2, edx
  mulss xmm2, [rsp+64]          ; dd_hi
  mov  edx, [rsp+32 + rax*4 + 4]; m[2c+1]
  cvtsi2ss xmm3, edx
  mulss xmm3, [rsp+68]          ; mm_hi
  mov  r8, rcx
  shl  r8, 5
  add  r8, r9                   ; &qs[32c]
  mov  r11, rcx
  shl  r11, 6
  add  r11, r15                 ; out element base = block_base + 64c
  xor  rbx, rbx                 ; b in 0..31
.q4b:
  cmp  rbx, 32
  jae  .q4chunk_next
  movzx eax, byte [r8+rbx]
  mov  edx, eax
  and  edx, 15                  ; low nibble -> value 64c+b, sub-block 2c
  shr  eax, 4                   ; high nibble -> value 64c+32+b, sub-block 2c+1
  cvtsi2ss xmm4, edx
  mulss xmm4, xmm0
  subss xmm4, xmm1
  mov  r10, r11
  add  r10, rbx
  movss [rdi + r10*4], xmm4
  cvtsi2ss xmm5, eax
  mulss xmm5, xmm2
  subss xmm5, xmm3
  mov  r10, r11
  add  r10, rbx
  add  r10, 32
  movss [rdi + r10*4], xmm5
  inc  rbx
  jmp  .q4b
.q4chunk_next:
  inc  rcx
  jmp  .q4chunk
.q4blk_next:
  add  rsi, Q4K_BYTES
  add  r15, 256
  inc  r14
  jmp  .q4blk

.q6k:
  mov  rax, r11
  shr  rax, 8                   ; blocks
  mov  r13, rax
  mov  rax, r9
  imul rax, r13
  imul rax, rax, Q6K_BYTES
  add  rax, r12
  mov  rsi, rax                 ; current block
  xor  r14, r14                 ; block index
  xor  r15, r15                 ; out element base
.q6blk:
  cmp  r14, r13
  jae  .done
  movzx eax, word [rsi+208]     ; d (f16) at ql[128]+qh[64]+scales[16]
  movd xmm0, eax
  vcvtph2ps xmm0, xmm0
  movss [rsp+64], xmm0
  xor  rcx, rcx                 ; half in 0..1
.q6half:
  cmp  rcx, 2
  jae  .q6blk_next
  mov  rax, rcx
  shl  rax, 6
  lea  rax, [rsi+rax]           ; ql base = block + half*64
  mov  [rsp+80], rax
  mov  rax, rcx
  shl  rax, 5
  lea  rax, [rsi+128+rax]       ; qh base = block+128 + half*32
  mov  [rsp+88], rax
  mov  rax, rcx
  shl  rax, 3
  lea  rbx, [rsi+192+rax]       ; sc base (i8) = block+192 + half*8
  mov  rax, rcx
  shl  rax, 7
  add  rax, r15                 ; out element base for this half = block_base + half*128
  mov  [rsp+104], rax
  mov  [rsp+72], rcx            ; save half
  xor  rcx, rcx                 ; l in 0..31
.q6l:
  cmp  rcx, 32
  jae  .q6half_next
  xor  r11d, r11d               ; is = (l >= 16) ? 1 : 0
  cmp  rcx, 16
  jb   .q6is0
  mov  r11d, 1
.q6is0:
  mov  rax, [rsp+88]
  movzx r8d, byte [rax+rcx]     ; hb = qh[l]
  mov  rax, [rsp+80]
  movzx r9d, byte [rax+rcx]     ; ql[l]
  movzx r10d, byte [rax+rcx+32] ; ql[l+32]
  ; q1 -> index l, scale sc[is]
  mov  eax, r9d
  and  eax, 15
  mov  edx, r8d
  and  edx, 3
  shl  edx, 4
  or   eax, edx
  sub  eax, 32
  cvtsi2ss xmm0, eax
  movsx edx, byte [rbx + r11]
  cvtsi2ss xmm1, edx
  mulss xmm0, xmm1
  mulss xmm0, [rsp+64]
  mov  rax, [rsp+104]
  add  rax, rcx
  movss [rdi + rax*4], xmm0
  ; q2 -> index l+32, scale sc[is+2]
  mov  eax, r10d
  and  eax, 15
  mov  edx, r8d
  shr  edx, 2
  and  edx, 3
  shl  edx, 4
  or   eax, edx
  sub  eax, 32
  cvtsi2ss xmm0, eax
  mov  edx, r11d
  add  edx, 2
  movsx edx, byte [rbx + rdx]
  cvtsi2ss xmm1, edx
  mulss xmm0, xmm1
  mulss xmm0, [rsp+64]
  mov  rax, [rsp+104]
  add  rax, rcx
  add  rax, 32
  movss [rdi + rax*4], xmm0
  ; q3 -> index l+64, scale sc[is+4]
  mov  eax, r9d
  shr  eax, 4
  mov  edx, r8d
  shr  edx, 4
  and  edx, 3
  shl  edx, 4
  or   eax, edx
  sub  eax, 32
  cvtsi2ss xmm0, eax
  mov  edx, r11d
  add  edx, 4
  movsx edx, byte [rbx + rdx]
  cvtsi2ss xmm1, edx
  mulss xmm0, xmm1
  mulss xmm0, [rsp+64]
  mov  rax, [rsp+104]
  add  rax, rcx
  add  rax, 64
  movss [rdi + rax*4], xmm0
  ; q4 -> index l+96, scale sc[is+6]
  mov  eax, r10d
  shr  eax, 4
  mov  edx, r8d
  shr  edx, 6
  and  edx, 3
  shl  edx, 4
  or   eax, edx
  sub  eax, 32
  cvtsi2ss xmm0, eax
  mov  edx, r11d
  add  edx, 6
  movsx edx, byte [rbx + rdx]
  cvtsi2ss xmm1, edx
  mulss xmm0, xmm1
  mulss xmm0, [rsp+64]
  mov  rax, [rsp+104]
  add  rax, rcx
  add  rax, 96
  movss [rdi + rax*4], xmm0
  inc  rcx
  jmp  .q6l
.q6half_next:
  mov  rcx, [rsp+72]
  inc  rcx
  jmp  .q6half
.q6blk_next:
  add  rsi, Q6K_BYTES
  add  r15, 256
  inc  r14
  jmp  .q6blk

.done:
  mov  rsp, rbp
  pop  rbp
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; model_matvec / model_fill_sums — matvec and the x sub-block sums it needs
; ===========================================================================

; model_matvec(rcx=Tensor* w, rdx=x f32*, r8=out f32*). Runs out[r]=dot(row r,x)
; over the thread pool: fills the module MatvecJob g_mvjob (rows=w.ne1, cols=w.ne0,
; xsum32/xsum16 pointing at the shared sum buffers) and pool_run's k_matvec_job.
; The matching k_sum32/k_sum16 must already describe x. Clobbers volatiles only.
model_matvec:
  WINPROLOGUE
  lea  r9, [rel g_mvjob]
  mov  [r9 + MatvecJob.w], rcx
  mov  [r9 + MatvecJob.x], rdx
  mov  [r9 + MatvecJob.out], r8
  mov  rax, [rel g_xsum32]
  mov  [r9 + MatvecJob.xsum32], rax
  mov  rax, [rel g_xsum16]
  mov  [r9 + MatvecJob.xsum16], rax
  mov  rax, [rcx + Tensor.ne1]  ; rows
  mov  [r9 + MatvecJob.rows], rax
  mov  rax, [rcx + Tensor.ne0]  ; cols
  mov  [r9 + MatvecJob.cols], rax
  lea  rcx, [rel k_matvec_job]
  lea  rdx, [rel g_mvjob]
  call pool_run
  WINEPILOGUE
  ret

; model_fill_sums(rcx=x f32*, rdx=n). Fills g_xsum32 (sums per 32 values) and
; g_xsum16 (sums per 16 values) for x, so the following matvecs on this x have
; the Q4_K min / Q6_K offset terms ready. Clobbers volatiles only.
model_fill_sums:
  WINPROLOGUE
  sub  rsp, 48                  ; [rsp+32]=x, [rsp+40]=n
  mov  [rsp+32], rcx
  mov  [rsp+40], rdx
  mov  rcx, [rel g_xsum32]
  mov  rdx, [rsp+32]
  mov  r8, [rsp+40]
  call k_sum32
  mov  rcx, [rel g_xsum16]
  mov  rdx, [rsp+32]
  mov  r8, [rsp+40]
  call k_sum16
  WINEPILOGUE
  ret

; ===========================================================================
; model_load
; ===========================================================================

; model_load(rcx=path utf16, rdx=n_ctx, r8=nthreads) -> rax Model*. Allocates a
; Model, maps the GGUF, parses it, builds the tokenizer, clamps n_ctx to the
; trained context, starts the thread pool, allocates the KV caches, the rope
; table and every forward-pass scratch buffer, pre-faults every weight page (and
; totals the weight bytes for the bandwidth statistic), and returns the Model*.
model_load:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 48                  ; [rsp+32] = file size (and &size for rt_map_file)
  mov  r12, rcx                 ; path
  mov  r13, rdx                 ; requested n_ctx
  mov  r14, r8                  ; nthreads

  mov  rcx, Model_size
  call rt_alloc
  mov  r15, rax                 ; Model*

  mov  rcx, r12
  lea  rdx, [rsp+32]
  call rt_map_file              ; rax = view base, [rsp+32] = size
  mov  [r15 + Model.file_base], rax
  mov  rcx, [rsp+32]
  mov  [r15 + Model.file_size], rcx

  mov  rcx, r15
  call gguf_open                ; fills the Model from the mapped header

  mov  rcx, r15
  call tok_init                 ; vocab + merge tables, byte maps

  ; n_ctx = min(requested, n_ctx_train); 0 means "use the trained context".
  mov  rax, r13
  test rax, rax
  jnz  .ctx_nz
  mov  rax, [r15 + Model.n_ctx_train]
.ctx_nz:
  cmp  rax, [r15 + Model.n_ctx_train]
  jbe  .ctx_ok
  mov  rax, [r15 + Model.n_ctx_train]
.ctx_ok:
  mov  [r15 + Model.n_ctx], rax
  mov  [rel g_n_ctx], rax       ; published for k_attn_job's per-thread score stride
  mov  [r15 + Model.n_threads], r14

  mov  rcx, r14
  call pool_start

  ; KV caches: f32[n_layer][n_ctx][kv_dim] each.
  mov  rax, [r15 + Model.n_layer]
  imul rax, [r15 + Model.n_ctx]
  imul rax, [r15 + Model.kv_dim]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [r15 + Model.kcache], rax
  mov  rax, [r15 + Model.n_layer]
  imul rax, [r15 + Model.n_ctx]
  imul rax, [r15 + Model.kv_dim]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [r15 + Model.vcache], rax

  ; rope table: f32[n_ctx][head_dim], cos in [0,d/2), sin in [d/2,d).
  mov  rax, [r15 + Model.n_ctx]
  imul rax, [r15 + Model.head_dim]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [r15 + Model.rope_table], rax
  mov  rcx, rax
  mov  rdx, [r15 + Model.n_ctx]
  mov  r8, [r15 + Model.head_dim]
  movss xmm0, [r15 + Model.rope_theta]
  call k_rope_table

  ; forward-pass scratch (module globals holding the allocated pointers).
  mov  rax, [r15 + Model.n_embd]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_x], rax
  mov  rax, [r15 + Model.n_embd]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_xn], rax
  mov  rax, [r15 + Model.n_head]
  imul rax, [r15 + Model.head_dim]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_q], rax
  mov  rax, [r15 + Model.n_head]
  imul rax, [r15 + Model.head_dim]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_att_out], rax
  mov  rax, [r15 + Model.kv_dim]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_k], rax
  mov  rax, [r15 + Model.kv_dim]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_v], rax
  mov  rax, [r15 + Model.n_ff]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_g], rax
  mov  rax, [r15 + Model.n_ff]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_u], rax
  mov  rax, [r15 + Model.n_vocab]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_logits], rax

  ; maxcols = max(n_embd, n_ff); sum buffers sized to it.
  mov  rax, [r15 + Model.n_embd]
  mov  rdx, [r15 + Model.n_ff]
  cmp  rax, rdx
  cmovb rax, rdx
  mov  rbx, rax                 ; maxcols
  mov  rcx, rbx
  shr  rcx, 5
  shl  rcx, 2
  call rt_alloc
  mov  [rel g_xsum32], rax
  mov  rcx, rbx
  shr  rcx, 4
  shl  rcx, 2
  call rt_alloc
  mov  [rel g_xsum16], rax

  ; attention scores: f32[n_threads][n_ctx].
  mov  rax, [r15 + Model.n_threads]
  imul rax, [r15 + Model.n_ctx]
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_scores], rax

  ; Pre-fault every weight page (one byte per 4 KiB) so page-in is counted in
  ; load time, and total all tensor bytes for weights_gb_per_token / effective_gb_s.
  xor  rbx, rbx                 ; tensor index
  xor  rsi, rsi                 ; total weight bytes
.touch_loop:
  cmp  rbx, [r15 + Model.n_tensors]
  jae  .touch_done
  mov  rax, rbx
  imul rax, rax, Tensor_size
  add  rax, [r15 + Model.tensors]
  mov  rcx, [rax + Tensor.data]
  mov  rdx, [rax + Tensor.nbytes]
  add  rsi, rdx
  xor  r8, r8
.touch_page:
  cmp  r8, rdx
  jae  .touch_last
  movzx r9d, byte [rcx + r8]
  add  r8, 4096
  jmp  .touch_page
.touch_last:
  test rdx, rdx
  jz   .touch_next
  movzx r9d, byte [rcx + rdx - 1]
.touch_next:
  inc  rbx
  jmp  .touch_loop
.touch_done:
  mov  [rel g_weight_bytes], rsi

  mov  rax, r15                 ; return Model*
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
; model_forward
; ===========================================================================

; model_forward(rcx=Model*, rdx=token, r8=pos) -> rax = logits f32*. Runs the
; Qwen3 forward pass for one token at position `pos`, updating the KV caches at
; [layer][pos] and leaving the final logits in g_logits (returned). Registers:
;   rbx=Model*, rsi=pos, r12=token then the rope table row for `pos`,
;   r13=layer index, r14=current Layer*, r15=per-head loop counter.
model_forward:
  push rbx
  push rsi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  mov  rbx, rcx
  mov  r12, rdx                 ; token
  mov  rsi, r8                  ; pos

  ; x = dequant(token_embd[token])
  mov  rcx, [rbx + Model.tok_embd]
  mov  rdx, r12
  mov  r8, [rel g_x]
  call model_dequant_row

  ; rope table row for this position (constant across layers)
  mov  rax, rsi
  imul rax, [rbx + Model.head_dim]
  shl  rax, 2
  add  rax, [rbx + Model.rope_table]
  mov  r12, rax                 ; token no longer needed; reuse for the table row

  xor  r13, r13                 ; layer index
.layer:
  cmp  r13, [rbx + Model.n_layer]
  jae  .layers_done
  mov  r14, [rbx + Model.layers]
  mov  rax, r13
  imul rax, rax, Layer_size
  add  r14, rax                 ; Layer*

  ; xn = rmsnorm(x) * attn_norm
  mov  rcx, [rel g_xn]
  mov  rdx, [rel g_x]
  mov  r8, [r14 + Layer.attn_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.n_embd]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  ; sums of xn for the Q/K/V matvecs
  mov  rcx, [rel g_xn]
  mov  rdx, [rbx + Model.n_embd]
  call model_fill_sums
  ; q = Wq.xn ; k = Wk.xn ; v = Wv.xn
  mov  rcx, [r14 + Layer.attn_q]
  mov  rdx, [rel g_xn]
  mov  r8, [rel g_q]
  call model_matvec
  mov  rcx, [r14 + Layer.attn_k]
  mov  rdx, [rel g_xn]
  mov  r8, [rel g_k]
  call model_matvec
  mov  rcx, [r14 + Layer.attn_v]
  mov  rdx, [rel g_xn]
  mov  r8, [rel g_v]
  call model_matvec

  ; per-head q_norm (over head_dim), in place on each of n_head heads
  xor  r15, r15
.qn:
  cmp  r15, [rbx + Model.n_head]
  jae  .qn_done
  mov  rax, r15
  imul rax, [rbx + Model.head_dim]
  shl  rax, 2
  add  rax, [rel g_q]
  mov  rcx, rax
  mov  rdx, rax
  mov  r8, [r14 + Layer.attn_q_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.head_dim]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  inc  r15
  jmp  .qn
.qn_done:
  ; per-head k_norm on each of n_head_kv heads
  xor  r15, r15
.kn:
  cmp  r15, [rbx + Model.n_head_kv]
  jae  .kn_done
  mov  rax, r15
  imul rax, [rbx + Model.head_dim]
  shl  rax, 2
  add  rax, [rel g_k]
  mov  rcx, rax
  mov  rdx, rax
  mov  r8, [r14 + Layer.attn_k_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.head_dim]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  inc  r15
  jmp  .kn
.kn_done:
  ; rope each q head then each k head at this position
  xor  r15, r15
.rq:
  cmp  r15, [rbx + Model.n_head]
  jae  .rq_done
  mov  rax, r15
  imul rax, [rbx + Model.head_dim]
  shl  rax, 2
  add  rax, [rel g_q]
  mov  rcx, rax
  mov  rdx, r12
  call k_rope
  inc  r15
  jmp  .rq
.rq_done:
  xor  r15, r15
.rk:
  cmp  r15, [rbx + Model.n_head_kv]
  jae  .rk_done
  mov  rax, r15
  imul rax, [rbx + Model.head_dim]
  shl  rax, 2
  add  rax, [rel g_k]
  mov  rcx, rax
  mov  rdx, r12
  call k_rope
  inc  r15
  jmp  .rk
.rk_done:
  ; store k, v into the caches at [layer][pos]
  mov  rax, r13
  imul rax, [rbx + Model.n_ctx]
  add  rax, rsi
  imul rax, [rbx + Model.kv_dim]
  shl  rax, 2
  add  rax, [rbx + Model.kcache]
  mov  rcx, rax
  mov  rdx, [rel g_k]
  mov  r8, [rbx + Model.kv_dim]
  shl  r8, 2
  call rt_memcpy
  mov  rax, r13
  imul rax, [rbx + Model.n_ctx]
  add  rax, rsi
  imul rax, [rbx + Model.kv_dim]
  shl  rax, 2
  add  rax, [rbx + Model.vcache]
  mov  rcx, rax
  mov  rdx, [rel g_v]
  mov  r8, [rbx + Model.kv_dim]
  shl  r8, 2
  call rt_memcpy

  ; attention -> g_att_out, over seqlen = pos+1
  lea  r9, [rel g_attnjob]
  mov  rax, [rel g_q]
  mov  [r9 + AttnJob.q], rax
  mov  rax, r13
  imul rax, [rbx + Model.n_ctx]
  imul rax, [rbx + Model.kv_dim]
  shl  rax, 2
  add  rax, [rbx + Model.kcache]
  mov  [r9 + AttnJob.kbase], rax
  mov  rax, r13
  imul rax, [rbx + Model.n_ctx]
  imul rax, [rbx + Model.kv_dim]
  shl  rax, 2
  add  rax, [rbx + Model.vcache]
  mov  [r9 + AttnJob.vbase], rax
  mov  rax, [rel g_att_out]
  mov  [r9 + AttnJob.out], rax
  mov  rax, [rel g_scores]
  mov  [r9 + AttnJob.scores], rax
  mov  rax, rsi
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

  ; x += Wo.att_out
  mov  rcx, [rel g_att_out]
  mov  rdx, [rbx + Model.n_head]
  imul rdx, [rbx + Model.head_dim]
  call model_fill_sums
  mov  rcx, [r14 + Layer.attn_output]
  mov  rdx, [rel g_att_out]
  mov  r8, [rel g_xn]
  call model_matvec
  mov  rcx, [rel g_x]
  mov  rdx, [rel g_xn]
  mov  r8, [rbx + Model.n_embd]
  call k_add

  ; xn = rmsnorm(x) * ffn_norm
  mov  rcx, [rel g_xn]
  mov  rdx, [rel g_x]
  mov  r8, [r14 + Layer.ffn_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.n_embd]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  mov  rcx, [rel g_xn]
  mov  rdx, [rbx + Model.n_embd]
  call model_fill_sums
  ; g = Wgate.xn ; u = Wup.xn ; g = silu(g)*u
  mov  rcx, [r14 + Layer.ffn_gate]
  mov  rdx, [rel g_xn]
  mov  r8, [rel g_g]
  call model_matvec
  mov  rcx, [r14 + Layer.ffn_up]
  mov  rdx, [rel g_xn]
  mov  r8, [rel g_u]
  call model_matvec
  mov  rcx, [rel g_g]
  mov  rdx, [rel g_u]
  mov  r8, [rbx + Model.n_ff]
  call k_silu_mul
  ; x += Wdown.g
  mov  rcx, [rel g_g]
  mov  rdx, [rbx + Model.n_ff]
  call model_fill_sums
  mov  rcx, [r14 + Layer.ffn_down]
  mov  rdx, [rel g_g]
  mov  r8, [rel g_xn]
  call model_matvec
  mov  rcx, [rel g_x]
  mov  rdx, [rel g_xn]
  mov  r8, [rbx + Model.n_embd]
  call k_add

  inc  r13
  jmp  .layer
.layers_done:
  ; xf = rmsnorm(x) * output_norm ; logits = Wout.xf
  mov  rcx, [rel g_xn]
  mov  rdx, [rel g_x]
  mov  r8, [rbx + Model.output_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.n_embd]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  mov  rcx, [rel g_xn]
  mov  rdx, [rbx + Model.n_embd]
  call model_fill_sums
  mov  rcx, [rbx + Model.output]
  mov  rdx, [rel g_xn]
  mov  r8, [rel g_logits]
  call model_matvec

  mov  rax, [rel g_logits]
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; model_emit_ms / model_emit_rate — formatted stderr numbers
; ===========================================================================

; model_emit_ms(rdx=ns). Writes ns/1e6 (milliseconds) as f32 to stderr. ns is
; clamped to >= 1 to avoid a divide-by-zero on an impossibly fast phase.
model_emit_ms:
  WINPROLOGUE
  test rdx, rdx
  jnz  .ok
  mov  rdx, 1
.ok:
  cvtsi2ss xmm0, rdx
  mov  eax, F32_1E6
  movd xmm1, eax
  divss xmm0, xmm1
  call rt_err_f32
  WINEPILOGUE
  ret

; model_emit_rate(rcx=count, rdx=ns). Writes count*1e9/ns (events per second) as
; f32 to stderr. ns clamped to >= 1.
model_emit_rate:
  WINPROLOGUE
  test rdx, rdx
  jnz  .ok
  mov  rdx, 1
.ok:
  cvtsi2ss xmm0, rcx
  mov  eax, F32_1E9
  movd xmm1, eax
  mulss xmm0, xmm1
  cvtsi2ss xmm1, rdx
  divss xmm0, xmm1
  call rt_err_f32
  WINEPILOGUE
  ret

; ===========================================================================
; model_generate
; ===========================================================================

; model_generate(rcx=Model*, rdx=ids u32*, r8=n, r9=max_new). Feeds the n prompt
; tokens at positions 0..n-1 (timed as the prompt phase), then greedily decodes
; up to max_new tokens (argmax -> emit bytes -> forward at the next position),
; stopping at a stop token or n_ctx, and prints the prompt and generation
; statistics to stderr. Registers: rbx=Model*, rsi=ids, r12=n, r13=max_new,
; r14=pos, r15=gen count, rdi=current logits.
model_generate:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 80                  ; [rsp+32]=decode scratch, [+40]=t0/prompt_ns,
                                ; [+48]=t1/gen_ns, [+56]=next, [+64]=tend
  mov  rbx, rcx
  mov  rsi, rdx
  mov  r12, r8                  ; n
  mov  r13, r9                  ; max_new

  mov  rcx, 1024
  call rt_alloc
  mov  [rsp+32], rax            ; decode scratch (one token's bytes)

  call rt_now_ns
  mov  [rsp+40], rax            ; t0

  xor  r14, r14                 ; pos
.ploop:
  cmp  r14, r12
  jae  .pdone
  mov  rcx, rbx
  mov  edx, [rsi + r14*4]       ; prompt token (u32, zero-extended)
  mov  r8, r14
  call model_forward
  mov  rdi, rax                 ; keep the last prompt logits
  inc  r14
  jmp  .ploop
.pdone:
  call rt_now_ns
  mov  [rsp+48], rax            ; t1

  xor  r15, r15                 ; generated count
  test r12, r12
  jz   .gdone                   ; empty prompt: nothing to decode from
.gloop:
  cmp  r15, r13
  jae  .gdone                   ; produced max_new
  mov  rax, r14
  cmp  rax, [rbx + Model.n_ctx]
  jae  .gdone                   ; context full
  mov  rcx, rdi
  mov  rdx, [rbx + Model.n_vocab]
  call k_argmax
  mov  [rsp+56], rax            ; next token
  mov  rcx, rax
  call tok_is_stop
  test rax, rax
  jnz  .gdone
  mov  rcx, [rsp+56]
  mov  rdx, [rsp+32]
  call tok_decode               ; rax = bytes (0 for a control token)
  test rax, rax
  jz   .noemit
  mov  rcx, [rsp+32]
  mov  rdx, rax
  call rt_out
.noemit:
  inc  r15
  cmp  r15, r13
  jae  .gdone                   ; enough tokens: skip the final forward
  mov  rcx, rbx
  mov  rdx, [rsp+56]
  mov  r8, r14
  call model_forward
  mov  rdi, rax
  inc  r14
  jmp  .gloop
.gdone:
  call rt_now_ns
  mov  [rsp+64], rax            ; tend

  ; convert the two phase timestamps into elapsed nanoseconds in place
  mov  rax, [rsp+48]
  sub  rax, [rsp+40]
  mov  [rsp+40], rax            ; prompt_ns
  mov  rax, [rsp+64]
  sub  rax, [rsp+48]
  mov  [rsp+48], rax            ; gen_ns

  ; prompt line
  lea  rcx, [rel s_prompt1]
  call rt_err_str
  mov  rcx, r12
  call rt_err_u64
  lea  rcx, [rel s_prompt2]
  call rt_err_str
  mov  rdx, [rsp+40]
  call model_emit_ms
  lea  rcx, [rel s_prompt3]
  call rt_err_str
  mov  rcx, r12
  mov  rdx, [rsp+40]
  call model_emit_rate
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err

  ; generation line
  lea  rcx, [rel s_gen1]
  call rt_err_str
  mov  rcx, r15
  call rt_err_u64
  lea  rcx, [rel s_gen2]
  call rt_err_str
  mov  rdx, [rsp+48]
  call model_emit_ms
  lea  rcx, [rel s_gen3]
  call rt_err_str
  mov  rcx, r15
  mov  rdx, [rsp+48]
  call model_emit_rate
  lea  rcx, [rel s_gen4]
  call rt_err_str
  mov  rax, [rel g_weight_bytes]
  cvtsi2ss xmm0, rax
  mov  eax, F32_1E9
  movd xmm1, eax
  divss xmm0, xmm1              ; weights_gb_per_token = bytes / 1e9
  call rt_err_f32
  lea  rcx, [rel s_gen5]
  call rt_err_str
  mov  rax, [rel g_weight_bytes]
  imul rax, r15                 ; weight bytes * gen_tokens
  cvtsi2ss xmm0, rax
  mov  rdx, [rsp+48]            ; gen_ns
  test rdx, rdx
  jnz  .eff_ok
  mov  rdx, 1
.eff_ok:
  cvtsi2ss xmm1, rdx
  divss xmm0, xmm1             ; bytes*tokens / ns == GB/s (GB=1e9)
  call rt_err_f32
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err

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
; main_print_summary
; ===========================================================================

; main_print_summary(rcx=Model*). Writes the one-line model summary to stderr.
main_print_summary:
  push rbx
  WINPROLOGUE
  mov  rbx, rcx
  lea  rcx, [rel s_sum1]
  call rt_err_str
  mov  rcx, [rbx + Model.n_layer]
  call rt_err_u64
  lea  rcx, [rel s_sum_embd]
  call rt_err_str
  mov  rcx, [rbx + Model.n_embd]
  call rt_err_u64
  lea  rcx, [rel s_sum_ff]
  call rt_err_str
  mov  rcx, [rbx + Model.n_ff]
  call rt_err_u64
  lea  rcx, [rel s_sum_heads]
  call rt_err_str
  mov  rcx, [rbx + Model.n_head]
  call rt_err_u64
  lea  rcx, [rel s_slash]
  call rt_err_str
  mov  rcx, [rbx + Model.n_head_kv]
  call rt_err_u64
  lea  rcx, [rel s_sum_vocab]
  call rt_err_str
  mov  rcx, [rbx + Model.n_vocab]
  call rt_err_u64
  lea  rcx, [rel s_sum_ctx]
  call rt_err_str
  mov  rcx, [rbx + Model.n_ctx]
  call rt_err_u64
  lea  rcx, [rel s_sum_threads]
  call rt_err_str
  mov  rcx, [rbx + Model.n_threads]
  call rt_err_u64
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err
  WINEPILOGUE
  pop  rbx
  ret

; ===========================================================================
; main — argument parsing and mode dispatch
; ===========================================================================

; main(rcx=argc, rdx=argv) -> eax. argv is an array of UTF-16 wide-string pointers
; (argv[0] is the program name). Parses flags (-n -c -t -p and the --modes), then
; dispatches: --bw / --cpuid without a model; --gguf / --tokenize / --kernels after
; model_load; the default path loads, reads the prompt, encodes and generates.
; Registers: r12=argc, rbx=argv, r13=arg index, rsi=gguf path, rdi=prompt file,
; r14d=mode, r15=current argv[i].
main:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 112                 ; [rsp+32]=max_new [+40]=n_ctx [+48]=threads
                                ; [+56]=model* [+64]=text [+72]=len [+80]=ids
                                ; [+88]=count [+96]=load_t0 [+104]=load_t1
  mov  r12, rcx                 ; argc
  mov  rbx, rdx                 ; argv
  mov  qword [rsp+32], 64       ; -n default
  mov  qword [rsp+40], 1024     ; -c default
  call rt_cpu_count
  mov  [rsp+48], rax            ; -t default
  xor  esi, esi                 ; gguf path
  xor  edi, edi                 ; prompt file
  xor  r14d, r14d               ; mode
  mov  r13, 1                   ; skip argv[0]
.argloop:
  cmp  r13, r12
  jae  .argdone
  mov  r15, [rbx + r13*8]       ; argv[i]
  mov  rcx, r15
  lea  rdx, [rel f_n]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_n
  mov  rcx, r15
  lea  rdx, [rel f_c]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_c
  mov  rcx, r15
  lea  rdx, [rel f_t]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_t
  mov  rcx, r15
  lea  rdx, [rel f_p]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_p
  mov  rcx, r15
  lea  rdx, [rel f_bw]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_bw
  mov  rcx, r15
  lea  rdx, [rel f_cpuid]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_cpuid
  mov  rcx, r15
  lea  rdx, [rel f_gguf]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_gguf
  mov  rcx, r15
  lea  rdx, [rel f_tok]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_tok
  mov  rcx, r15
  lea  rdx, [rel f_kern]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_kern
  ; positional argument: the first one is the GGUF path
  test rsi, rsi
  jnz  .arg_next
  mov  rsi, r15
  jmp  .arg_next
.opt_n:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call rt_watoi
  mov  [rsp+32], rax
  jmp  .arg_next
.opt_c:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call rt_watoi
  mov  [rsp+40], rax
  jmp  .arg_next
.opt_t:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call rt_watoi
  mov  [rsp+48], rax
  jmp  .arg_next
.opt_p:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rdi, [rbx + r13*8]
  jmp  .arg_next
.opt_bw:
  mov  r14d, MODE_BW
  jmp  .arg_next
.opt_cpuid:
  mov  r14d, MODE_CPUID
  jmp  .arg_next
.opt_gguf:
  mov  r14d, MODE_GGUF
  jmp  .arg_next
.opt_tok:
  mov  r14d, MODE_TOKENIZE
  jmp  .arg_next
.opt_kern:
  mov  r14d, MODE_KERNELS
  jmp  .arg_next
.arg_next:
  inc  r13
  jmp  .argloop
.argdone:

  ; --bw: ISA gate then the bandwidth probe (no model needed)
  cmp  r14d, MODE_BW
  jne  .not_bw
  call rt_cpuid_check
  call rt_bench_bandwidth
  jmp  .ret0
.not_bw:
  ; --cpuid: ISA gate then print the detected features
  cmp  r14d, MODE_CPUID
  jne  .not_cpuid
  call rt_cpuid_check
  lea  rcx, [rel s_feat]
  call rt_err_str
  mov  rcx, [rel g_has_vnni]
  call rt_err_u64
  lea  rcx, [rel s_feat_cores]
  call rt_err_str
  call rt_cpu_count
  mov  rcx, rax
  call rt_err_u64
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err
  jmp  .ret0
.not_cpuid:
  ; every remaining mode needs the GGUF path
  test rsi, rsi
  jnz  .have_path
  lea  rcx, [rel m_usage]
  call rt_fatal
.have_path:
  call rt_cpuid_check
  call rt_now_ns
  mov  [rsp+96], rax
  mov  rcx, rsi
  mov  rdx, [rsp+40]
  mov  r8, [rsp+48]
  call model_load
  mov  [rsp+56], rax
  call rt_now_ns
  mov  [rsp+104], rax

  cmp  r14d, MODE_GGUF
  jne  .not_gguf
  mov  rcx, [rsp+56]
  call gguf_cli
  jmp  .ret0
.not_gguf:
  cmp  r14d, MODE_TOKENIZE
  jne  .not_tok
  mov  rcx, [rsp+56]
  call tok_cli
  jmp  .ret0
.not_tok:
  cmp  r14d, MODE_KERNELS
  jne  .not_kern
  mov  rcx, [rsp+56]
  call kernels_cli
  jmp  .ret0
.not_kern:
  ; default: summary + load time, read the prompt, encode, generate
  mov  rcx, [rsp+56]
  call main_print_summary
  lea  rcx, [rel s_loadms]
  call rt_err_str
  mov  rdx, [rsp+104]
  sub  rdx, [rsp+96]
  call model_emit_ms
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err

  test rdi, rdi
  jz   .stdin
  mov  rcx, rdi
  lea  rdx, [rsp+72]
  call rt_read_file             ; prompt file bytes (UTF-8, as-is)
  mov  [rsp+64], rax
  jmp  .have_prompt
.stdin:
  lea  rcx, [rsp+72]
  call rt_read_stdin
  mov  [rsp+64], rax
  ; strip one trailing CR/LF pair: "\n", "\r\n" or a lone "\r"
  mov  rax, [rsp+64]
  mov  rdx, [rsp+72]
  test rdx, rdx
  jz   .have_prompt
  mov  cl, [rax + rdx - 1]
  cmp  cl, 0x0A
  jne  .chkcr
  dec  rdx
  test rdx, rdx
  jz   .setlen
  mov  cl, [rax + rdx - 1]
  cmp  cl, 0x0D
  jne  .setlen
  dec  rdx
  jmp  .setlen
.chkcr:
  cmp  cl, 0x0D
  jne  .have_prompt
  dec  rdx
.setlen:
  mov  [rsp+72], rdx
.have_prompt:
  ; ids buffer = (len+1) u32 (token count never exceeds byte length)
  mov  rcx, [rsp+72]
  inc  rcx
  shl  rcx, 2
  call rt_alloc
  mov  [rsp+80], rax
  mov  rcx, [rsp+64]
  mov  rdx, [rsp+72]
  mov  r8, [rsp+80]
  mov  r9, [rsp+72]
  inc  r9
  call tok_encode
  mov  [rsp+88], rax
  mov  rcx, [rsp+56]
  mov  rdx, [rsp+80]
  mov  r8, [rsp+88]
  mov  r9, [rsp+32]
  call model_generate
.ret0:
  xor  eax, eax
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
; model.asm mutable globals and read-only text (zero-initialised bytes; no resb)
; ===========================================================================

g_x:            dq 0           ; f32[n_embd]  residual stream
g_xn:           dq 0           ; f32[n_embd]  normalised / matvec-output scratch
g_q:            dq 0           ; f32[n_head*head_dim]  query heads
g_k:            dq 0           ; f32[kv_dim]  key for this position
g_v:            dq 0           ; f32[kv_dim]  value for this position
g_att_out:      dq 0           ; f32[n_head*head_dim]  attention output
g_g:            dq 0           ; f32[n_ff]  ffn gate
g_u:            dq 0           ; f32[n_ff]  ffn up
g_logits:       dq 0           ; f32[n_vocab]
g_xsum32:       dq 0           ; f32[maxcols/32]  per-32 sums of the current x
g_xsum16:       dq 0           ; f32[maxcols/16]  per-16 sums of the current x
g_scores:       dq 0           ; f32[n_threads*n_ctx]  attention score scratch
g_n_ctx:        dq 0           ; per-thread score-row stride read by k_attn_job
g_weight_bytes: dq 0           ; total tensor bytes, for the bandwidth statistic

  align 8
g_mvjob:        times (MatvecJob_size / 8) dq 0
g_attnjob:      times (AttnJob_size / 8) dq 0

f_n:        db "-n", 0
f_c:        db "-c", 0
f_t:        db "-t", 0
f_p:        db "-p", 0
f_bw:       db "--bw", 0
f_cpuid:    db "--cpuid", 0
f_gguf:     db "--gguf", 0
f_tok:      db "--tokenize", 0
f_kern:     db "--kernels", 0

m_usage:    db "usage: asmrun <model.gguf> [-n N] [-c CTX] [-t T] [-p FILE] [--bw|--cpuid|--gguf|--tokenize|--kernels]", 0

s_feat:        db "asmrun: avx2=1 fma=1 f16c=1 avx_vnni=", 0
s_feat_cores:  db " cores=", 0
s_loadms:      db "asmrun: load_ms=", 0

s_sum1:        db "asmrun: model=qwen3 layers=", 0
s_sum_embd:    db " embd=", 0
s_sum_ff:      db " ff=", 0
s_sum_heads:   db " heads=", 0
s_slash:       db "/", 0
s_sum_vocab:   db " vocab=", 0
s_sum_ctx:     db " ctx=", 0
s_sum_threads: db " threads=", 0

s_prompt1:     db "asmrun: prompt_tokens=", 0
s_prompt2:     db " prompt_ms=", 0
s_prompt3:     db " prompt_tok_s=", 0
s_gen1:        db "asmrun: gen_tokens=", 0
s_gen2:        db " gen_ms=", 0
s_gen3:        db " gen_tok_s=", 0
s_gen4:        db " weights_gb_per_token=", 0
s_gen5:        db " effective_gb_s=", 0

s_nl:          db 10

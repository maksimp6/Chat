; model.asm — the Qwen3 forward pass, KV cache, generation loop, and CLI.
;
; %included into asmrun.asm (bits 64, default rel already set). Code is in .text;
; the globals and strings at the end switch to .data (`section .data`), and no
; storage uses `resb`: mutable globals are zero-initialised `dq 0` bytes, and
; every big buffer comes from rt_alloc at run time and is kept in a module global.
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

section .text

; CLI mode selector values held in r14d while main parses the argument vector.
MODE_DEFAULT  equ 0
MODE_BW       equ 1
MODE_CPUID    equ 2
MODE_GGUF     equ 3
MODE_TOKENIZE equ 4
MODE_KERNELS  equ 5
MODE_VIEW     equ 6             ; --view FILE.bmp: show the picture (view.asm, rt_view_show)
MODE_TESTCARD equ 7             ; --testcard: show view.asm's test card
MODE_IMAGE    equ 8             ; --image: type a prompt, get a picture (image.asm, rt_image_ui)
MODE_SDINFO   equ 9             ; --sdinfo FILE.gguf: list an SD-Turbo GGUF's tensors (sd.asm)
MODE_CLIPTOK  equ 10            ; --cliptok --vocab V --merges M: the prompt's CLIP token ids (cliptok.asm)
MODE_CLIPENC  equ 11            ; --clipenc SD.gguf --vocab V --merges M: run the text encoder (clipenc.asm)
MODE_GEN      equ 12            ; --gen --sd S --taesd T --vocab V --merges M -p FILE -o out.bmp: one picture to a file
MODE_KV       equ 13            ; --kv FILE.gguf: every metadata key and value (sd.asm)
MODE_TENSOR   equ 14            ; --tensor NAME FILE.gguf: a tensor's shape and first values (sd.asm)

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
  cmp  dword [rcx + Tensor.type], GGML_Q8_0
  jne  .not_q8
  mov  rax, [rcx + Tensor.ne0]
  mov  r9, rax
  shr  rax, 5
  imul rax, rax, Q80_BYTES
  imul rax, rdx
  add  rax, [rcx + Tensor.data]
  mov  rdx, rax                ; the row
  mov  rcx, r8                 ; out
  mov  r8, r9                  ; values
  mov  r9d, GGML_Q8_0
  jmp  sg_deq_row
.not_q8:
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
  cmp  r10d, GGML_Q5_K
  je   .q5k
  cmp  r10d, GGML_Q6_K
  je   .q6k
  jmp  .done                    ; unknown type: leave out as-is (gguf already refused it)

.q5k:
  ; Q5_K: Q4_K's block with qh[32] between the scales and qs; the value gains
  ; 16 when its qh bit is set (chunk c: bit 2c for the low nibbles, 2c+1 for the high)
  mov  rax, r11
  shr  rax, 8                   ; blocks = cols / QK_K
  mov  r13, rax
  mov  rax, r9
  imul rax, r13
  imul rax, rax, Q5K_BYTES
  add  rax, r12
  mov  rsi, rax                 ; the block
  xor  r14, r14
  xor  r15, r15
.q5blk:
  cmp  r14, r13
  jae  .done
  movzx eax, word [rsi]         ; d
  movd xmm0, eax
  vcvtph2ps xmm0, xmm0
  movss [rsp+64], xmm0
  movzx eax, word [rsi+2]       ; dmin
  movd xmm0, eax
  vcvtph2ps xmm0, xmm0
  movss [rsp+68], xmm0
  lea  r8, [rsi+4]              ; scales[12]: as Q4_K
  xor  rcx, rcx
.q5scloop:
  cmp  rcx, 8
  jae  .q5scdone
  cmp  rcx, 4
  jae  .q5schi
  movzx eax, byte [r8+rcx]
  and  eax, 63
  mov  [rsp + rcx*4], eax
  movzx edx, byte [r8+rcx+4]
  and  edx, 63
  mov  [rsp+32 + rcx*4], edx
  jmp  .q5scnext
.q5schi:
  movzx eax, byte [r8+rcx+4]
  and  eax, 15
  movzx edx, byte [r8+rcx-4]
  shr  edx, 6
  shl  edx, 4
  or   eax, edx
  mov  [rsp + rcx*4], eax
  movzx eax, byte [r8+rcx+4]
  shr  eax, 4
  movzx edx, byte [r8+rcx]
  shr  edx, 6
  shl  edx, 4
  or   eax, edx
  mov  [rsp+32 + rcx*4], eax
.q5scnext:
  inc  rcx
  jmp  .q5scloop
.q5scdone:
  lea  r9, [rsi+48]             ; qs[128]
  xor  rcx, rcx                 ; chunk c
.q5chunk:
  cmp  rcx, 4
  jae  .q5blk_next
  mov  rax, rcx
  shl  rax, 1
  mov  edx, [rsp + rax*4]
  cvtsi2ss xmm0, edx
  mulss xmm0, [rsp+64]          ; d*sc[2c]
  mov  edx, [rsp+32 + rax*4]
  cvtsi2ss xmm1, edx
  mulss xmm1, [rsp+68]          ; dmin*m[2c]
  mov  edx, [rsp + rax*4 + 4]
  cvtsi2ss xmm2, edx
  mulss xmm2, [rsp+64]          ; d*sc[2c+1]
  mov  edx, [rsp+32 + rax*4 + 4]
  cvtsi2ss xmm3, edx
  mulss xmm3, [rsp+68]          ; dmin*m[2c+1]
  mov  eax, 1
  shl  eax, cl
  shl  eax, cl
  mov  [rsp+72], eax            ; u1 = 1 << 2c
  add  eax, eax
  mov  [rsp+76], eax            ; u2 = 2 << 2c
  mov  r8, rcx
  shl  r8, 5
  add  r8, r9                   ; &qs[32c]
  mov  r11, rcx
  shl  r11, 6
  add  r11, r15                 ; out base = block base + 64c
  xor  rbx, rbx
.q5b:
  cmp  rbx, 32
  jae  .q5chunk_next
  movzx eax, byte [r8+rbx]
  movzx r10d, byte [rsi+16+rbx] ; qh[b]
  mov  edx, eax
  and  edx, 15
  test r10d, dword [rsp+72]
  jz   .q5lo
  add  edx, 16
.q5lo:
  shr  eax, 4
  test r10d, dword [rsp+76]
  jz   .q5hi
  add  eax, 16
.q5hi:
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
  jmp  .q5b
.q5chunk_next:
  inc  rcx
  jmp  .q5chunk
.q5blk_next:
  add  rsi, Q5K_BYTES
  add  r15, 256
  inc  r14
  jmp  .q5blk

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
  mov  qword [r9 + MatvecJob.nseg], 1
  mov  [r9 + MatvecJob.ws], rcx
  mov  [r9 + MatvecJob.outs], r8
  mov  rax, [rcx + Tensor.ne1]
  mov  [r9 + MatvecJob.cum], rax
  mov  rax, [rel g_xsum32]
  mov  [r9 + MatvecJob.xsum32], rax
  mov  rax, [rel g_xsum16]
  mov  [r9 + MatvecJob.xsum16], rax
  mov  rax, [rcx + Tensor.ne1]  ; rows
  mov  [r9 + MatvecJob.rows], rax
  mov  rax, [rcx + Tensor.ne0]  ; cols
  mov  [r9 + MatvecJob.cols], rax
  jmp  model_matvec_run

; model_matvec_multi(rcx=&Tensor*[n], rdx=&f32*[n], r8=n, r9=x) — up to
; MATVEC_SEGS matrices with the same number of columns and the same x, as one
; pool job: the threads claim rows from the concatenated row space, so the
; pool synchronises once. Clobbers volatiles only.
model_matvec_multi:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  lea  rbx, [rel g_mvjob]
  mov  [rbx + MatvecJob.x], r9
  mov  [rbx + MatvecJob.nseg], r8
  xor  esi, esi                 ; segment
  xor  edi, edi                 ; rows so far
.s:
  cmp  rsi, r8
  jae  .filled
  mov  rax, [rcx + rsi*8]
  mov  [rbx + MatvecJob.ws + rsi*8], rax
  add  rdi, [rax + Tensor.ne1]
  mov  [rbx + MatvecJob.cum + rsi*8], rdi
  mov  rax, [rdx + rsi*8]
  mov  [rbx + MatvecJob.outs + rsi*8], rax
  inc  rsi
  jmp  .s
.filled:
  mov  rax, [rbx + MatvecJob.ws]
  mov  [rbx + MatvecJob.w], rax
  mov  rax, [rbx + MatvecJob.outs]
  mov  [rbx + MatvecJob.out], rax
  mov  [rbx + MatvecJob.rows], rdi
  mov  rax, [rbx + MatvecJob.ws]
  mov  rax, [rax + Tensor.ne0]
  mov  [rbx + MatvecJob.cols], rax
  mov  rax, [rel g_xsum32]
  mov  [rbx + MatvecJob.xsum32], rax
  mov  rax, [rel g_xsum16]
  mov  [rbx + MatvecJob.xsum16], rax
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  WINPROLOGUE
  lea  r9, [rel g_mvjob]
  jmp  model_matvec_run

; model_matvec_run — r9 = g_mvjob filled but for .next and .chunk; inside a
; WINPROLOGUE frame. Sizes the chunks and runs the job on the pool.
model_matvec_run:
  mov  qword [r9 + MatvecJob.next], 0 ; row counter the threads claim chunks from
  mov  rax, [r9 + MatvecJob.rows]
  mov  rcx, [rel g_nthreads]
  imul rcx, rcx, MATVEC_CHUNKS_PER_THREAD
  xor  rdx, rdx
  div  rcx                      ; rax = rows / (nthreads * chunks per thread)
  cmp  rax, MATVEC_CHUNK_MIN
  jae  .chunk_ok
  mov  rax, MATVEC_CHUNK_MIN
.chunk_ok:
  mov  [r9 + MatvecJob.chunk], rax
  ; the activation vectors: g_mv_nx slots; slot t's f32 values are x + t*cols*4
  mov  rax, [rel g_mv_nx]
  mov  [r9 + MatvecJob.nx], rax
  mov  r8, [r9 + MatvecJob.x]
  mov  r10, [r9 + MatvecJob.cols]
  shl  r10, 2
  lea  r11, [rel g_act_slots]
  xor  ecx, ecx
.sx:
  cmp  rcx, rax
  jae  .sx_done
  mov  [r11 + ActSlot.x], r8
  add  r8, r10
  add  r11, ActSlot_size
  inc  rcx
  jmp  .sx
.sx_done:
  lea  rcx, [rel k_matvec_job]
  lea  rdx, [rel g_mvjob]
  call pool_run
  WINEPILOGUE
  ret

; act_slots_init(rcx=maxcols, a multiple of 256) — ACT_SLOTS activation slots:
; per slot q8[maxcols] i8, d8[maxcols/256] f32, bsums[maxcols/16] i16 and
; bsums32[maxcols/32] i16 (each sum buffer with a spare 32 bytes: the dot
; kernels read a whole block's sums at once). Slot 0's buffers are also
; g_q8/g_d8/g_bsums/g_bsums32. Clobbers the volatile registers.
act_slots_init:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rbx, rcx                 ; maxcols
  xor  esi, esi                 ; slot
.s:
  cmp  rsi, ACT_SLOTS
  jae  .done
  mov  rdi, rsi
  imul rdi, rdi, ActSlot_size
  lea  rax, [rel g_act_slots]
  add  rdi, rax                 ; the slot
  mov  rcx, rbx
  call rt_alloc
  mov  [rdi + ActSlot.q8], rax
  mov  rcx, rbx
  shr  rcx, 8
  add  rcx, 1
  shl  rcx, 2
  call rt_alloc
  mov  [rdi + ActSlot.d8], rax
  mov  rcx, rbx
  shr  rcx, 4
  shl  rcx, 1
  add  rcx, 32
  call rt_alloc
  mov  [rdi + ActSlot.bsums], rax
  mov  rcx, rbx
  shr  rcx, 5
  shl  rcx, 1
  add  rcx, 32
  call rt_alloc
  mov  [rdi + ActSlot.bsums32], rax
  inc  rsi
  jmp  .s
.done:
  lea  rdi, [rel g_act_slots]
  mov  rax, [rdi + ActSlot.q8]
  mov  [rel g_q8], rax
  mov  rax, [rdi + ActSlot.d8]
  mov  [rel g_d8], rax
  mov  rax, [rdi + ActSlot.bsums]
  mov  [rel g_bsums], rax
  mov  rax, [rdi + ActSlot.bsums32]
  mov  [rel g_bsums32], rax
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; model_fill_sums(rcx=x f32*, rdx=n). Fills g_xsum32 (sums per 32 values) and
; g_xsum16 (sums per 16 values) for x, so the following matvecs on this x have
; the Q4_K min / Q6_K offset terms ready. Clobbers volatiles only.
model_fill_sums:
  WINPROLOGUE
  sub  rsp, 48                  ; [rsp+32]=x, [rsp+40]=n
  mov  [rsp+32], rcx
  mov  [rsp+40], rdx
  ; quantize x to int8 (g_q8/g_d8/g_bsums) for the int8 Q4_K and Q6_K matvecs
  mov  rcx, [rsp+32]
  mov  rdx, [rsp+40]
  call k_quant_x
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
  call rt_model_open            ; rax = the header's bytes (Windows: the whole file mapped), [rsp+32] = size
  mov  [r15 + Model.file_base], rax
  mov  rcx, [rsp+32]
  mov  [r15 + Model.file_size], rcx

  mov  rcx, r15
  call gguf_open                ; fills the Model from the header; rt_tensor_data gives every tensor's bytes
  call rt_model_done            ; (no OS: the read rate; the file is closed)
  cmp  qword [r15 + Model.arch], ARCH_QWEN35
  jne  .qwen3
  mov  rcx, r15                 ; Qwen3.5: its own layers, states and buffers
  mov  rdx, r13
  mov  r8, r14
  call q35_setup
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
.qwen3:

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

  cmp  qword [rel g_nthreads], 0
  jne  .pool_up                 ; a second model (the embedder) shares the pool the first one started
  mov  rcx, r14
  call pool_start               ; may lower g_nthreads (UEFI: enabled processors, a processor the firmware would not start, no MP protocol)
.pool_up:
  mov  rax, [rel g_nthreads]    ; the pool that exists, for the summary line and the score scratch
  mov  [r15 + Model.n_threads], rax

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

  ; forward-pass scratch (module globals holding the allocated pointers), each
  ; ACT_SLOTS vectors deep: model_forward_batch (q3f.asm) keeps token t at
  ; base + t*dim*4, model_forward uses vector 0
  mov  rax, [r15 + Model.n_embd]
  imul rax, rax, ACT_SLOTS
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_x], rax
  mov  rax, [r15 + Model.n_embd]
  imul rax, rax, ACT_SLOTS
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_xn], rax
  mov  rax, [r15 + Model.n_head]
  imul rax, [r15 + Model.head_dim]
  imul rax, rax, ACT_SLOTS
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_q], rax
  mov  rax, [r15 + Model.n_head]
  imul rax, [r15 + Model.head_dim]
  imul rax, rax, ACT_SLOTS
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_att_out], rax
  mov  rax, [r15 + Model.kv_dim]
  imul rax, rax, ACT_SLOTS
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_k], rax
  mov  rax, [r15 + Model.kv_dim]
  imul rax, rax, ACT_SLOTS
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_v], rax
  mov  rax, [r15 + Model.n_ff]
  imul rax, rax, ACT_SLOTS
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_g], rax
  mov  rax, [r15 + Model.n_ff]
  imul rax, rax, ACT_SLOTS
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
  ; the int8 activation slots (ACT_SLOTS of them, for a prompt batch); a second
  ; model (the embedder) uses the first one's, sized for the larger model
  cmp  qword [rel g_act_slots], 0
  jne  .slots_up
  mov  rcx, rbx
  call act_slots_init
.slots_up:

  ; attention scores: f32[n_threads][n_ctx]; a second model keeps the first
  ; one's buffer when that is large enough (each job carries its own stride)
  mov  rax, [r15 + Model.n_threads]
  imul rax, [r15 + Model.n_ctx]
  cmp  rax, [rel g_scores_n]
  jbe  .scores_up
  mov  [rel g_scores_n], rax
  shl  rax, 2
  mov  rcx, rax
  call rt_alloc
  mov  [rel g_scores], rax
.scores_up:

  ; Pre-fault every weight page (one byte per 4 KiB) so page-in is counted in
  ; load time, and total the tensor bytes a token reads (every tensor except the
  ; token embedding table) for weights_gb_per_token / effective_gb_s.
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
  mov  rax, [r15 + Model.tok_embd]
  sub  rsi, [rax + Tensor.nbytes] ; a token reads one embedding row, not the table
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
; Phase profile: PROF_BEGIN / PROF_END slot bracket a step of the forward pass
; and accumulate its nanoseconds in g_prof[slot]; model_generate prints the
; totals. rt_now_ns clobbers the volatile registers, so the brackets sit outside
; each step's argument setup. Slots: 0 quant (model_fill_sums), 1 Q/K/V matvecs,
; 2 attention (q/k norms, rope, cache store, k_attn_job), 3 attn_output matvec,
; 4 gate+up matvecs, 5 silu, 6 ffn_down matvec, 7 output matvec, 8 rmsnorm and
; residual adds, 9 argmax.
; ===========================================================================
%macro PROF_BEGIN 0
  call rt_now_ns
  mov  [rel g_prof_t0], rax
%endmacro
%macro PROF_END 1
  call rt_now_ns
  sub  rax, [rel g_prof_t0]
  add  [rel g_prof + 8*%1], rax
%endmacro
%macro PROF_PRINT 2           ; %1 label string, %2 slot
  lea  rcx, [rel %1]
  call rt_err_str
  mov  rdx, [rel g_prof + 8*%2]
  call model_emit_ms
%endmacro

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
  PROF_BEGIN
  mov  rcx, [rel g_xn]
  mov  rdx, [rel g_x]
  mov  r8, [r14 + Layer.attn_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.n_embd]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  PROF_END 8
  ; quantise xn for the Q/K/V matvecs
  PROF_BEGIN
  mov  rcx, [rel g_xn]
  mov  rdx, [rbx + Model.n_embd]
  call model_fill_sums
  PROF_END 0
  ; q = Wq.xn ; k = Wk.xn ; v = Wv.xn, as one pool job
  PROF_BEGIN
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
  PROF_END 1
  PROF_BEGIN

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
  mov  rax, [rbx + Model.n_ctx]
  mov  [r9 + AttnJob.stride], rax
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
  PROF_END 2

  ; x += Wo.att_out
  PROF_BEGIN
  mov  rcx, [rel g_att_out]
  mov  rdx, [rbx + Model.n_head]
  imul rdx, [rbx + Model.head_dim]
  call model_fill_sums
  PROF_END 0
  PROF_BEGIN
  mov  rcx, [r14 + Layer.attn_output]
  mov  rdx, [rel g_att_out]
  mov  r8, [rel g_xn]
  call model_matvec
  PROF_END 3
  PROF_BEGIN
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
  PROF_END 8
  PROF_BEGIN
  mov  rcx, [rel g_xn]
  mov  rdx, [rbx + Model.n_embd]
  call model_fill_sums
  PROF_END 0
  ; g = Wgate.xn ; u = Wup.xn (one pool job) ; g = silu(g)*u
  PROF_BEGIN
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
  PROF_END 4
  PROF_BEGIN
  mov  rcx, [rel g_g]
  mov  rdx, [rel g_u]
  mov  r8, [rbx + Model.n_ff]
  call k_silu_mul
  PROF_END 5
  ; x += Wdown.g
  PROF_BEGIN
  mov  rcx, [rel g_g]
  mov  rdx, [rbx + Model.n_ff]
  call model_fill_sums
  PROF_END 0
  PROF_BEGIN
  mov  rcx, [r14 + Layer.ffn_down]
  mov  rdx, [rel g_g]
  mov  r8, [rel g_xn]
  call model_matvec
  PROF_END 6
  PROF_BEGIN
  mov  rcx, [rel g_x]
  mov  rdx, [rel g_xn]
  mov  r8, [rbx + Model.n_embd]
  call k_add
  PROF_END 8

  inc  r13
  jmp  .layer
.layers_done:
  ; xf = rmsnorm(x) * output_norm ; logits = Wout.xf
  PROF_BEGIN
  mov  rcx, [rel g_xn]
  mov  rdx, [rel g_x]
  mov  r8, [rbx + Model.output_norm]
  mov  r8, [r8 + Tensor.data]
  mov  r9, [rbx + Model.n_embd]
  movss xmm0, [rbx + Model.rms_eps]
  call k_rmsnorm
  PROF_END 8
  cmp  qword [rel g_embed_only], 0
  je   .lm_head
  mov  rax, [rel g_xn]          ; an embedding: the normed state of this token, no word scores
  jmp  .fwd_ret
.lm_head:
  PROF_BEGIN
  mov  rcx, [rel g_xn]
  mov  rdx, [rbx + Model.n_embd]
  call model_fill_sums
  PROF_END 0
  PROF_BEGIN
  mov  rcx, [rbx + Model.output]
  mov  rdx, [rel g_xn]
  mov  r8, [rel g_logits]
  call model_matvec
  PROF_END 7

  mov  rax, [rel g_logits]
.fwd_ret:
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; model_embed_load(rcx=path utf16, rdx=n_ctx, r8=chat Model*) — the embedding
; model (--embed FILE: Qwen3-Embedding, a Qwen3-family GGUF) as a second model
; beside a Qwen3.5 chat model. It takes the Qwen3 path's scratch buffers for
; itself, shares the pool and the activation slots, keeps its own tokenizer
; tables (swapped in around every embedding by api.asm), and leaves the chat
; model's weight-bytes statistic alone. g_embed_model = 0 when it cannot be
; loaded beside this chat model.
; ===========================================================================
model_embed_load:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  cmp  qword [r8 + Model.arch], ARCH_QWEN35
  jne  .not_beside              ; a Qwen3-family chat model owns the Qwen3 path's buffers
  lea  rcx, [rel g_tok_main]    ; the chat model's tokenizer tables
  lea  rdx, [rel g_tok_ready]
  mov  r8d, TOK_STATE_BYTES
  call rt_memcpy
  mov  rdi, [rel g_weight_bytes]
  mov  qword [rel g_tok_ready], 0 ; tok_init builds the embedder's own tables (it returns early on built ones)
  mov  rcx, rbx
  mov  edx, EMBED_CTX           ; its own short context: a text to embed is one chunk, not a dialog
  mov  r8, [rel g_nthreads]
  call model_load
  mov  [rel g_embed_model], rax
  mov  rcx, [rel g_weight_bytes]
  mov  [rel g_embed_bytes], rcx
  mov  [rel g_weight_bytes], rdi
  lea  rcx, [rel g_tok_embed]   ; the embedder's tokenizer tables
  lea  rdx, [rel g_tok_ready]
  mov  r8d, TOK_STATE_BYTES
  call rt_memcpy
  lea  rcx, [rel g_tok_ready]   ; and the chat model's back
  lea  rdx, [rel g_tok_main]
  mov  r8d, TOK_STATE_BYTES
  call rt_memcpy
  lea  rcx, [rel s_embed_ok1]
  call rt_err_str
  mov  rax, [rel g_embed_model]
  mov  rcx, [rax + Model.n_embd]
  call rt_err_u64
  lea  rcx, [rel s_embed_ok2]
  call rt_err_str
  mov  rcx, [rel g_embed_bytes]
  shr  rcx, 20
  call rt_err_u64
  lea  rcx, [rel s_embed_ok3]
  call rt_err_str
  jmp  .ret
.not_beside:
  lea  rcx, [rel s_embed_no]
  call rt_err_str
.ret:
  WINEPILOGUE
  pop  rdi
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
  cmp  qword [rcx + Model.arch], ARCH_QWEN35
  jne  .qwen3
  jmp  q35_generate             ; the same arguments; the conversation continues across calls
.qwen3:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 96                  ; [rsp+32]=decode scratch, [+40]=t0/prompt_ns,
                                ; [+48]=t1/gen_ns, [+56]=next, [+64]=tend,
                                ; [+72..96) the pool counters at the start of generation
  mov  rbx, rcx
  mov  rsi, rdx
  mov  r12, r8                  ; n
  mov  r13, r9                  ; max_new

  cmp  r12, [rbx + Model.n_ctx]
  jbe  .fits
  lea  rcx, [rel s_err_ctx]     ; a prompt beyond n_ctx would write K/V, scores and rope rows past their buffers
  call rt_fatal
.fits:
  lea  rcx, [rel g_prof]        ; the phase profile is per call (the -i loop calls again)
  xor  edx, edx
  mov  r8d, 80
  call rt_memset

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
  call pool_counters_reset      ; the waste metric counts from here

  xor  r15, r15                 ; generated count
  test r12, r12
  jz   .gdone                   ; empty prompt: nothing to decode from
.gloop:
  cmp  r15, r13
  jae  .gdone                   ; produced max_new
  mov  rax, r14
  cmp  rax, [rbx + Model.n_ctx]
  jae  .gdone                   ; context full
  PROF_BEGIN
  mov  rcx, rdi
  mov  rdx, [rbx + Model.n_vocab]
  call k_sample
  mov  [rsp+56], rax            ; next token
  PROF_END 9                    ; (clobbers rax: the token is reloaded)
  mov  rcx, [rsp+56]
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
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_out                   ; end the generated text's line before the statistics

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

  ; phase profile (totals over the prompt and generation forwards)
  PROF_PRINT s_prof0, 0
  PROF_PRINT s_prof1, 1
  PROF_PRINT s_prof2, 2
  PROF_PRINT s_prof3, 3
  PROF_PRINT s_prof4, 4
  PROF_PRINT s_prof5, 5
  PROF_PRINT s_prof6, 6
  PROF_PRINT s_prof7, 7
  PROF_PRINT s_prof8, 8
  PROF_PRINT s_prof9, 9
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err

  ; the waste metric for the generation phase
  mov  rcx, r15
  mov  rdx, [rsp+48]
  call rt_waste_line
  call rt_log_flush             ; this turn (prompt, answer, statistics) to dialog.txt

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
  cmp  qword [rbx + Model.arch], ARCH_QWEN35
  jne  .sum_arch
  lea  rcx, [rel s_sum1_35]
.sum_arch:
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
  sub  rsp, 128                 ; [rsp+32]=max_new [+40]=n_ctx [+48]=threads
                                ; [+56]=model* [+64]=text [+72]=len [+80]=ids
                                ; [+88]=count [+96]=load_t0 [+104]=load_t1
                                ; [+112]=interactive (-i)
  mov  r12, rcx                 ; argc
  mov  rbx, rdx                 ; argv
  mov  qword [rsp+32], 64       ; -n default
  mov  qword [rsp+40], 1024     ; -c default
  mov  qword [rsp+112], 0       ; -i off
  mov  qword [rsp+120], 0       ; --chat off (wrap each prompt as a Qwen3 chat turn)
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
  mov  rcx, r15
  lea  rdx, [rel f_i]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_i
  mov  rcx, r15
  lea  rdx, [rel f_chat]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_chat
  mov  rcx, r15
  lea  rdx, [rel f_view]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_view
  mov  rcx, r15
  lea  rdx, [rel f_testcard]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_testcard
  mov  rcx, r15
  lea  rdx, [rel f_image]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_image
  mov  rcx, r15
  lea  rdx, [rel f_sdinfo]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_sdinfo
  mov  rcx, r15
  lea  rdx, [rel f_cliptok]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_cliptok
  mov  rcx, r15
  lea  rdx, [rel f_clipenc]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_clipenc
  mov  rcx, r15
  lea  rdx, [rel f_vocab]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_vocab
  mov  rcx, r15
  lea  rdx, [rel f_merges]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_merges
  mov  rcx, r15
  lea  rdx, [rel f_sd]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_sd
  mov  rcx, r15
  lea  rdx, [rel f_taesd]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_taesd
  mov  rcx, r15
  lea  rdx, [rel f_seed]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_seed
  mov  rcx, r15
  lea  rdx, [rel f_vpred]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_vpred
  mov  rcx, r15
  lea  rdx, [rel f_o]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_o
  mov  rcx, r15
  lea  rdx, [rel f_gen]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_gen
  mov  rcx, r15
  lea  rdx, [rel f_kv]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_kv
  mov  rcx, r15
  lea  rdx, [rel f_tensor]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_tensor
  mov  rcx, r15
  lea  rdx, [rel f_bus]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_bus
  mov  rcx, r15
  lea  rdx, [rel f_trace]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_trace
  mov  rcx, r15
  lea  rdx, [rel f_temp]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_temp
  mov  rcx, r15
  lea  rdx, [rel f_top_p]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_top_p
  mov  rcx, r15
  lea  rdx, [rel f_top_k]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_top_k
  mov  rcx, r15
  lea  rdx, [rel f_system]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_system
  mov  rcx, r15
  lea  rdx, [rel f_http]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_http
  mov  rcx, r15
  lea  rdx, [rel f_ip]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_ip
  mov  rcx, r15
  lea  rdx, [rel f_mask]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_mask
  mov  rcx, r15
  lea  rdx, [rel f_gw]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_gw
  mov  rcx, r15
  lea  rdx, [rel f_embed]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_embed
  mov  rcx, r15
  lea  rdx, [rel f_auto]
  call rt_wcmp_ascii
  test rax, rax
  jz   .opt_auto
  ; positional argument: the first one is the GGUF path (with --view, the picture)
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
.opt_i:
  mov  qword [rsp+112], 1
  mov  qword [rel g_stdin_line], 1 ; rt_read_stdin returns at the end of a line
  jmp  .arg_next
.opt_chat:
  mov  qword [rsp+120], 1
  jmp  .arg_next
.opt_view:
  mov  r14d, MODE_VIEW
  jmp  .arg_next
.opt_testcard:
  mov  r14d, MODE_TESTCARD
  jmp  .arg_next
.opt_image:
  mov  r14d, MODE_IMAGE
  jmp  .arg_next
.opt_sdinfo:
  mov  r14d, MODE_SDINFO
  jmp  .arg_next
.opt_cliptok:
  mov  r14d, MODE_CLIPTOK
  jmp  .arg_next
.opt_clipenc:
  mov  r14d, MODE_CLIPENC
  jmp  .arg_next
.opt_vocab:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rax, [rbx + r13*8]
  mov  [rel g_ct_vocab_path], rax
  jmp  .arg_next
.opt_merges:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rax, [rbx + r13*8]
  mov  [rel g_ct_merges_path], rax
  jmp  .arg_next
.opt_sd:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rax, [rbx + r13*8]
  mov  [rel g_img_sd_path], rax
  jmp  .arg_next
.opt_taesd:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rax, [rbx + r13*8]
  mov  [rel g_img_taesd_path], rax
  jmp  .arg_next
.opt_seed:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call rt_watoi
  mov  [rel g_img_seed], rax
  jmp  .arg_next
.opt_vpred:
  mov  qword [rel g_img_vpred], 1
  jmp  .arg_next
.opt_o:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rax, [rbx + r13*8]
  mov  [rel g_img_out_path], rax
  jmp  .arg_next
.opt_gen:
  mov  r14d, MODE_GEN
  jmp  .arg_next
.opt_kv:
  mov  r14d, MODE_KV
  jmp  .arg_next
.opt_trace:
  mov  qword [rel g35_trace], 1
  jmp  .arg_next
.opt_auto:                     ; --auto: the runner never stops for a human (owner: "i want it to always non stop work")
  mov  qword [rel g_auto], 1
  jmp  .arg_next
.opt_http:                     ; --http PORT: prompts from the network; the loop goes on like -i
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call rt_watoi
  mov  [rel g_http_port], rax
  mov  qword [rsp+112], 1
  jmp  .arg_next
.opt_ip:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call http_wparse_ip
  mov  [rel g_http_ip], eax
  jmp  .arg_next
.opt_mask:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call http_wparse_ip
  mov  [rel g_http_mask], eax
  jmp  .arg_next
.opt_gw:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call http_wparse_ip
  mov  [rel g_http_gw], eax
  jmp  .arg_next
.opt_embed:                    ; --embed FILE: the embedding model, loaded after the chat model
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  mov  [rel g_embed_path], rcx
  jmp  .arg_next
.opt_temp:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call q35_parse_f32w
  vmovss [rel g_temp], xmm0
  jmp  .arg_next
.opt_top_p:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call q35_parse_f32w
  vmovss [rel g_top_p], xmm0
  jmp  .arg_next
.opt_top_k:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call rt_watoi
  mov  [rel g_top_k], rax
  jmp  .arg_next
.opt_system:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  lea  rdx, [rel g_system_len]
  call rt_read_file            ; the system prompt's text (UTF-8); a missing file is fatal
  mov  [rel g_system_text], rax
  ; without its trailing line ends
.sys_trim:
  mov  rcx, [rel g_system_len]
  test rcx, rcx
  jz   .arg_next
  mov  dl, [rax + rcx - 1]
  cmp  dl, 10
  je   .sys_cut
  cmp  dl, 13
  jne  .arg_next
.sys_cut:
  dec  qword [rel g_system_len]
  jmp  .sys_trim
.opt_bus:
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rcx, [rbx + r13*8]
  call q35_parse_f32w
  vmovss [rel g_bus_gbps], xmm0
  jmp  .arg_next
.opt_tensor:
  mov  r14d, MODE_TENSOR
  inc  r13
  cmp  r13, r12
  jae  .argdone
  mov  rax, [rbx + r13*8]
  mov  [rel g_tensor_name_w], rax ; the name, UTF-16; made UTF-8 at dispatch
  jmp  .arg_next
.arg_next:
  inc  r13
  jmp  .argloop
.argdone:

  ; --bw: ISA gate, start the pool with -t threads, then the bandwidth probe (no model needed)
  cmp  r14d, MODE_BW
  jne  .not_bw
  call rt_cpuid_check
  mov  rcx, [rsp+48]
  call pool_start
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
  ; --image: the prompt box (Windows) or prompt line (no OS), a picture per prompt
  cmp  r14d, MODE_IMAGE
  jne  .not_image
  call rt_cpuid_check
  mov  rcx, [rsp+48]
  call pool_start
  call rt_image_ui
  jmp  .ret0
.not_image:
  ; --gen: one picture of the prompt (-p FILE or stdin) into -o FILE (BMP)
  cmp  r14d, MODE_GEN
  jne  .not_gen
  cmp  qword [rel g_img_out_path], 0
  je   .gen_usage
  call rt_cpuid_check
  mov  rcx, [rsp+48]
  call pool_start
  test rdi, rdi
  jz   .gen_stdin
  mov  rcx, rdi
  lea  rdx, [rsp+72]
  call rt_read_file
  jmp  .gen_text
.gen_stdin:
  lea  rcx, [rsp+72]
  call rt_read_stdin
.gen_text:
  mov  rcx, rax
  mov  rdx, [rsp+72]
  call img_generate
  test rax, rax
  jz   .gen_none
  mov  rcx, [rel g_img_out_path]
  mov  rdx, rax
  mov  r8d, IMG_SIDE
  mov  r9d, IMG_SIDE
  call img_write               ; PNG or BMP by the extension
  test rax, rax
  jz   .gen_nowrite
  lea  rcx, [rel s_gen_wrote]
  call rt_err_str
  jmp  .ret0
.gen_none:
  mov  eax, 1
  jmp  .retcode
.gen_nowrite:
  lea  rcx, [rel s_gen_nowrite]
  call rt_fatal
.gen_usage:
  lea  rcx, [rel m_usage]
  call rt_fatal
.not_gen:
  ; --testcard: the viewer with view.asm's test card (no model, no file)
  cmp  r14d, MODE_TESTCARD
  jne  .not_card
  call view_testcard
  mov  rcx, rax
  mov  edx, VIEW_CARD
  mov  r8d, VIEW_CARD
  call rt_view_show
  jmp  .ret0
.not_card:
  ; --view FILE.bmp: the viewer with a picture from a file
  cmp  r14d, MODE_VIEW
  jne  .not_view
  test rsi, rsi
  jnz  .view_file
  lea  rcx, [rel m_usage]
  call rt_fatal
.view_file:
  mov  rcx, rsi
  call view_load_bmp           ; rax = pixels, rdx = w, r8 = h
  mov  rcx, rax
  call rt_view_show
  jmp  .ret0
.not_view:
  ; --sdinfo FILE.gguf: the SD-Turbo weights' tensor table
  cmp  r14d, MODE_SDINFO
  jne  .not_sdinfo
  test rsi, rsi
  jnz  .sdinfo_file
  lea  rcx, [rel m_usage]
  call rt_fatal
.sdinfo_file:
  mov  rcx, rsi
  call sd_index
  call sd_list
  jmp  .ret0
.not_sdinfo:
  ; --kv FILE.gguf / --tensor NAME FILE.gguf: the metadata, or one tensor's values
  cmp  r14d, MODE_KV
  je   .kvmode
  cmp  r14d, MODE_TENSOR
  jne  .not_kvt
.kvmode:
  test rsi, rsi
  jnz  .kvt_file
  lea  rcx, [rel m_usage]
  call rt_fatal
.kvt_file:
  mov  rcx, rsi
  call sd_index
  cmp  r14d, MODE_KV
  jne  .tensormode
  call sd_kv
  jmp  .ret0
.tensormode:
  mov  rcx, [rel g_tensor_name_w]
  test rcx, rcx
  jz   .kvmode_usage
  lea  rdx, [rel g_tensor_name]
  mov  r8d, 255
  call rt_utf16_to_utf8
  lea  rcx, [rel g_tensor_name]
  mov  byte [rcx + rax], 0
  call sd_tensor_dump
  jmp  .ret0
.kvmode_usage:
  lea  rcx, [rel m_usage]
  call rt_fatal
.not_kvt:
  ; --cliptok --vocab V --merges M [-p FILE]: the prompt (FILE or stdin) as CLIP ids
  cmp  r14d, MODE_CLIPENC
  je   .clipenc
  cmp  r14d, MODE_CLIPTOK
  jne  .not_cliptok
  cmp  qword [rel g_ct_vocab_path], 0
  je   .cliptok_usage
  cmp  qword [rel g_ct_merges_path], 0
  je   .cliptok_usage
  mov  rcx, [rel g_ct_vocab_path]
  mov  rdx, [rel g_ct_merges_path]
  call ct_load
  test rdi, rdi
  jz   .ct_stdin
  mov  rcx, rdi
  lea  rdx, [rsp+72]
  call rt_read_file
  jmp  .ct_text
.ct_stdin:
  lea  rcx, [rsp+72]
  call rt_read_stdin
.ct_text:
  mov  rcx, rax
  mov  rdx, [rsp+72]
  lea  r8, [rel g_ct_ids]
  call ct_encode
  mov  [rsp+88], rax           ; ids before the padding
  lea  rcx, [rel s_ct_count]
  call rt_out_str
  mov  rcx, [rsp+88]
  call rt_out_u64
  lea  rcx, [rel s_ct_ids]
  call rt_out_str
  mov  qword [rsp+80], 0
.ct_ids:
  mov  rax, [rsp+80]
  cmp  rax, [rsp+88]
  jae  .ct_end
  lea  rcx, [rel g_ct_ids]
  mov  ecx, [rcx + rax*4]
  call rt_out_u64
  lea  rcx, [rel s_ct_sp]
  call rt_out_str
  inc  qword [rsp+80]
  jmp  .ct_ids
.ct_end:
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_out
  jmp  .ret0
.cliptok_usage:
  lea  rcx, [rel m_usage]
  call rt_fatal
.clipenc:
  ; --clipenc SD.gguf --vocab V --merges M [-p FILE]: tokens, then the 23 layers
  ; on every core; the time and the context's statistics to stderr
  test rsi, rsi
  jz   .cliptok_usage
  cmp  qword [rel g_ct_vocab_path], 0
  je   .cliptok_usage
  cmp  qword [rel g_ct_merges_path], 0
  je   .cliptok_usage
  call rt_cpuid_check
  mov  rcx, [rsp+48]
  call pool_start
  mov  rcx, rsi
  call sd_index
  mov  rcx, [rel g_ct_vocab_path]
  mov  rdx, [rel g_ct_merges_path]
  call ct_load
  test rdi, rdi
  jz   .ce_stdin
  mov  rcx, rdi
  lea  rdx, [rsp+72]
  call rt_read_file
  jmp  .ce_text
.ce_stdin:
  lea  rcx, [rsp+72]
  call rt_read_stdin
.ce_text:
  mov  rcx, rax
  mov  rdx, [rsp+72]
  lea  r8, [rel g_ct_ids]
  call ct_encode
  mov  [rsp+88], rax           ; rt_err_str clobbers rax
  lea  rcx, [rel s_ct_count]
  call rt_err_str
  mov  rcx, [rsp+88]
  call rt_err_u64
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err
  call ce_load
  lea  rcx, [rel g_ct_ids]
  call ce_check                ; the matmul kernel against a scalar reference
  call rt_now_ns
  mov  [rsp+96], rax
  lea  rcx, [rel g_ct_ids]
  call ce_forward
  mov  [rsp+56], rax
  call rt_now_ns
  mov  [rsp+104], rax
  lea  rcx, [rel s_ce_ms]
  call rt_err_str
  mov  rdx, [rsp+104]
  sub  rdx, [rsp+96]
  call model_emit_ms
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err
  mov  rcx, [rsp+56]
  call ce_stats
  jmp  .ret0
.not_cliptok:
  ; every remaining mode needs the GGUF path; without one the USB build offers
  ; the .gguf files on the boot volume as a numbered list (Windows: usage)
  test rsi, rsi
  jnz  .have_path
  call rt_pick_model
  mov  rsi, rax
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
  mov  rcx, [rel g_embed_path]
  test rcx, rcx
  jz   .no_embed
  mov  rdx, [rsp+40]            ; the same context as the chat model (k_attn_job's score stride is shared)
  mov  r8, rax
  call model_embed_load
.no_embed:
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
  mov  qword [rel g_log_tee], 1 ; from here the screen is copied into dialog.txt (every model)
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

.prompt_again:
  test rdi, rdi
  jz   .stdin
  mov  rcx, rdi
  lea  rdx, [rsp+72]
  call rt_read_file             ; prompt file bytes (UTF-8, as-is)
  mov  [rsp+64], rax
  jmp  .have_prompt
.stdin:
  cmp  qword [rel g_http_port], 0
  je   .console
  lea  rcx, [rsp+72]
  call http_wait_prompt         ; --http: the previous answer goes out, the next POST's body comes in
  test rax, rax
  jnz  .have_prompt_http
  cmp  qword [rel g_http_key], 0
  je   .stdin                   ; the server could not start: g_http_port is 0 now, the console it is
  mov  qword [rel g_http_key], 0 ; --auto: a key was pressed while idle; this one prompt comes from the console
  jmp  .console
.have_prompt_http:
  mov  [rsp+64], rax
  jmp  .have_prompt
.console:
  cmp  qword [rsp+112], 0
  je   .readin
  lea  rcx, [rel s_prompt_mark] ; -i: a prompt marker before reading the line
  call rt_out_str
.readin:
  mov  qword [rel g_log_tee], 0 ; the console's echo of the typed line is not teed
  lea  rcx, [rsp+72]
  call rt_read_stdin
  mov  [rsp+64], rax
  mov  qword [rel g_log_tee], 1
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
  cmp  qword [rsp+72], 0
  jne  .nonempty
  cmp  qword [rsp+112], 0
  jne  .ret0                    ; -i: an empty line (or Esc) ends the session
.nonempty:
  ; the prompt's text to dialog.txt (the "> " mark, the answer and the statistics
  ; reach it from the screen output; the typed line is not teed, see .readin)
  mov  rcx, [rsp+64]
  mov  rdx, [rsp+72]
  call rt_log_add
  lea  rcx, [rel s_nl]
  mov  edx, 1
  call rt_log_add
  ; an Ollama chat request (api.asm) brings its own ids: the rendered dialog's
  ; new tail, after a state reset when the dialog is a different one
  cmp  qword [rel g_api_nids], 0
  je   .own_ids
  cmp  qword [rel g_api_reset], 0
  je   .api_ids
  call q35_reset
.api_ids:
  mov  rax, [rel g_api_ids]
  mov  [rsp+80], rax
  mov  rax, [rel g_api_nids]
  jmp  .enc_done
.own_ids:
  ; ids buffer = (len + system len + 64) u32 (text tokens <= byte length; chat adds a few control ids)
  mov  rcx, [rsp+72]
  add  rcx, [rel g_system_len]
  add  rcx, 64
  shl  rcx, 2
  call rt_alloc
  mov  [rsp+80], rax
  mov  rcx, [rsp+64]
  mov  rdx, [rsp+72]
  mov  r8, [rsp+80]
  mov  r9, [rsp+72]
  add  r9, [rel g_system_len]
  add  r9, 64                   ; the capacity: the same as the allocation
  cmp  qword [rsp+120], 0
  je   .enc_raw
  mov  rax, [rsp+56]
  cmp  qword [rax + Model.arch], ARCH_QWEN35
  jne  .enc_q3
  call q35_encode_chat          ; --chat: a ChatML turn with this file's ids, continuing the conversation
  jmp  .enc_done
.enc_q3:
  call tok_encode_chat          ; --chat: wrap as a Qwen3 chat turn
  jmp  .enc_done
.enc_raw:
  call tok_encode
.enc_done:
  mov  [rsp+88], rax
  ; -i from the console: a prompt longer than the context is refused with a
  ; message and the loop goes on (model_generate would stop the program)
  mov  rcx, [rsp+56]
  cmp  rax, [rcx + Model.n_ctx]
  jbe  .fits
  cmp  qword [rsp+112], 0
  je   .fits
  test rdi, rdi
  jnz  .fits
  lea  rcx, [rel s_too_long1]
  call rt_err_str
  mov  rcx, [rsp+88]
  call rt_err_u64
  lea  rcx, [rel s_too_long2]
  call rt_err_str
  mov  rax, [rsp+56]
  mov  rcx, [rax + Model.n_ctx]
  call rt_err_u64
  lea  rcx, [rel s_nl]
  mov  rdx, 1
  call rt_err
  jmp  .prompt_again
.fits:
  mov  rcx, [rsp+56]
  mov  rdx, [rsp+80]
  mov  r8, [rsp+88]
  mov  r9, [rsp+32]
  call model_generate
  cmp  qword [rsp+112], 0
  je   .ret0
  test rdi, rdi                 ; -i: the next prompt from the console (a -p file is used once)
  jnz  .ret0
  jmp  .prompt_again
.ret0:
  xor  eax, eax
.retcode:
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
section .data
  align 8

g_x:            dq 0           ; f32[n_embd]  residual stream
g_xn:           dq 0           ; f32[n_embd]  normalised / matvec-output scratch
g_q:            dq 0           ; f32[n_head*head_dim]  query heads
g_k:            dq 0           ; f32[kv_dim]  key for this position
g_v:            dq 0           ; f32[kv_dim]  value for this position
g_att_out:      dq 0           ; f32[n_head*head_dim]  attention output
g_g:            dq 0           ; f32[n_ff]  ffn gate
g_u:            dq 0           ; f32[n_ff]  ffn up
g_logits:       dq 0           ; f32[n_vocab]
g_xsum32:       dq 0           ; f32[maxcols/32]  per-32 sums of the current x (unused since int8 Q4_K)
g_xsum16:       dq 0           ; f32[maxcols/16]  per-16 sums of the current x (Q6_K fold-sc)
g_q8:           dq 0           ; int8[maxcols]      activations quantized for the int8 Q4_K/Q6_K matvecs
g_d8:           dq 0           ; f32[maxcols/256]   per-256-block activation scale
g_bsums:        dq 0           ; i16[maxcols/16]    per-16 sums of g_q8 (Q6_K -32 offset term)
g_bsums32:      dq 0           ; i16[maxcols/32]    per-32 sums of g_q8 (Q4_K mins term)
g_scores:       dq 0           ; f32[n_threads*n_ctx]  attention score scratch
g_scores_n:     dq 0           ; floats it holds (the largest model's n_threads*n_ctx)
g_n_ctx:        dq 0           ; per-thread score-row stride read by k_attn_job
g_weight_bytes: dq 0           ; tensor bytes read per token, for the bandwidth statistic
g_prof_t0:      dq 0           ; PROF_BEGIN timestamp
g_prof:         times 10 dq 0  ; PROF_END accumulators (ns per phase slot)

  align 8
g_mvjob:        times (MatvecJob_size / 8) dq 0
g_mv_nx:        dq 1                    ; activation vectors per matvec (a prompt batch sets it; 1 otherwise)
g_act_slots:    times (ACT_SLOTS * ActSlot_size / 8) dq 0 ; the activation slots (act_slots_init)
g_mv_ws:        times MATVEC_SEGS dq 0  ; model_matvec_multi's argument arrays
g_mv_outs:      times MATVEC_SEGS dq 0
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
f_i:        db "-i", 0
f_chat:     db "--chat", 0
f_view:     db "--view", 0
f_testcard: db "--testcard", 0
f_image:    db "--image", 0
f_sdinfo:   db "--sdinfo", 0
f_cliptok:  db "--cliptok", 0
f_clipenc:  db "--clipenc", 0
f_sd:       db "--sd", 0
f_taesd:    db "--taesd", 0
f_seed:     db "--seed", 0
f_vpred:    db "--vpred", 0
f_o:        db "-o", 0
f_gen:      db "--gen", 0
f_kv:       db "--kv", 0
f_bus:      db "--bus", 0
f_trace:    db "--trace", 0
f_temp:     db "--temp", 0
f_top_p:    db "--top-p", 0
f_top_k:    db "--top-k", 0
f_system:   db "--system", 0
f_http:     db "--http", 0
f_ip:       db "--ip", 0
f_mask:     db "--mask", 0
f_gw:       db "--gw", 0
f_embed:    db "--embed", 0
f_auto:     db "--auto", 0
  align 8
g_auto:     dq 0               ; --auto: no key waits, a fatal error reboots, the context resets when full, the idle loop serves the stick's task queue
s_embed_ok1: db "asmrun: embeddings: ", 0
s_embed_ok2: db " dims, ", 0
s_embed_ok3: db " MB of weights (POST /api/embed)", 10, 0
s_embed_no:  db "asmrun: embeddings: --embed needs a Qwen3.5 chat model beside it (a Qwen3-family one owns the same buffers); none loaded", 10, 0
TOK_STATE_BYTES equ 80         ; g_tok_ready .. g_merge_slots in tokenizer.asm: ten qwords
EMBED_CTX equ 1024             ; the embedder's positions: Logos's 2000-character chunks are under this; 28 layers x 224 KB per position
  align 8
g_embed_path:   dq 0           ; --embed FILE (UTF-16), or 0
g_embed_model:  dq 0           ; the embedding model's Model*, or 0
g_embed_bytes:  dq 0           ; its weight bytes
g_embed_only:   dq 0           ; 1 while model_forward returns the normed state instead of word scores
g_tok_main:     times 80 db 0  ; the chat model's tokenizer tables (a copy of the globals)
g_tok_embed:    times 80 db 0  ; the embedder's
  align 8
g_system_text: dq 0           ; --system FILE: the text (UTF-8), or 0
g_system_len:  dq 0
s_chat_system: db "system", 10 ; the ChatML role line of the system turn
S_CHAT_SYSTEM_LEN equ $ - s_chat_system
f_tensor:   db "--tensor", 0
g_tensor_name_w: dq 0
g_tensor_name:   times 256 db 0
s_gen_wrote: db "asmrun: the picture was written (-o)", 10, 0
s_gen_nowrite: db "the picture could not be written to the -o file", 0
s_ce_ms:    db "asmrun: text encoder 23 layers ms=", 0
f_vocab:    db "--vocab", 0
f_merges:   db "--merges", 0
s_ct_count: db "clip_tokens=", 0
s_ct_ids:   db " ids: ", 0
s_ct_sp:    db " ", 0

m_usage:    db "usage: asmrun <model.gguf> [-n N] [-c CTX] [-t T] [-p FILE] [-i] [--chat] [--bw|--cpuid|--gguf|--tokenize|--kernels] | asmrun --image --sd <sd_turbo.gguf> --taesd <taesd.safetensors> --vocab <vocab.json> --merges <merges.txt> [--seed N] [--vpred] | asmrun --gen (the same files) -p prompt.txt -o out.bmp | asmrun --sdinfo <sd.gguf> | asmrun --cliptok --vocab <vocab.json> --merges <merges.txt> [-p FILE] | asmrun --view <picture.bmp> | asmrun --testcard", 0
s_prompt_mark: db 10, "> ", 0
s_err_ctx:     db "the prompt has more tokens than the context (-c); it would not fit the KV cache", 0
s_too_long1:   db "asmrun: the prompt has ", 0
s_too_long2:   db " tokens, more than the context of ", 0

s_feat:        db "asmrun: avx2=1 fma=1 f16c=1 avx_vnni=", 0
s_feat_cores:  db " cores=", 0
s_loadms:      db "asmrun: load_ms=", 0

s_sum1:        db "asmrun: model=qwen3 layers=", 0
s_sum1_35:     db "asmrun: model=qwen3.5 layers=", 0
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
s_prof0:       db "asmrun: prof_ms quant=", 0
s_prof1:       db " qkv=", 0
s_prof2:       db " attn=", 0
s_prof3:       db " wo=", 0
s_prof4:       db " gateup=", 0
s_prof5:       db " silu=", 0
s_prof6:       db " down=", 0
s_prof7:       db " out=", 0
s_prof8:       db " norm_add=", 0
s_prof9:       db " argmax=", 0

s_nl:          db 10

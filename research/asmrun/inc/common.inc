; common.inc — structs, constants and macros shared by every asmrun module.
;
; Included once (from asmrun.asm) before any module. Emits no bytes: only
; `struc` definitions (which define `.field` offsets and `Name_size`), `equ`
; constants and `%define` macros. All modules rely on these offsets exactly.

%ifndef COMMON_INC
%define COMMON_INC

; ---------------------------------------------------------------------------
; GGML tensor types this runner understands. Any other type is fatal at load.
GGML_F32   equ 0
GGML_F16   equ 1
GGML_Q8_0  equ 8
GGML_Q4_K  equ 12
GGML_Q5_K  equ 13
GGML_Q6_K  equ 14
Q80_BYTES  equ 34          ; f16 d; i8 qs[32]

; GGUF scalar value types (used by the parser).
GGUF_U8 equ 0
GGUF_I8 equ 1
GGUF_U16 equ 2
GGUF_I16 equ 3
GGUF_U32 equ 4
GGUF_I32 equ 5
GGUF_F32 equ 6
GGUF_BOOL equ 7
GGUF_STRING equ 8
GGUF_ARRAY equ 9
GGUF_U64 equ 10
GGUF_I64 equ 11
GGUF_F64 equ 12

; general.architecture values gguf_open accepts
ARCH_QWEN3  equ 0
ARCH_QWEN35 equ 1

; Quantized block geometry (llama.cpp ggml-common.h).
QK_K        equ 256        ; values per super-block (Q4_K, Q6_K)
Q4K_BYTES   equ 144        ; f16 d; f16 dmin; u8 scales[12]; u8 qs[128]
Q5K_BYTES   equ 176        ; f16 d; f16 dmin; u8 scales[12]; u8 qh[32]; u8 qs[128]
Q6K_BYTES   equ 210        ; u8 ql[128]; u8 qh[64]; i8 scales[16]; f16 d

; ---------------------------------------------------------------------------
; One tensor from the GGUF table. `.data` is the absolute address of its first
; byte (data_base + offset). Dims are GGUF order: a matrix is `.ne1` rows of
; `.ne0` values, and matvec computes out[r] = dot(row r, x), r in 0..ne1-1.
struc Tensor
  .data:     resq 1
  .type:     resd 1
  .ndims:    resd 1
  .ne0:      resq 1
  .ne1:      resq 1
  .ne2:      resq 1
  .ne3:      resq 1
  .nbytes:   resq 1
  .name:     resq 1        ; address of the name bytes inside the mapped header
  .namelen:  resq 1
endstruc

; Addresses of the Tensor records of one transformer block.
struc Layer
  .attn_norm:   resq 1
  .attn_q:      resq 1
  .attn_k:      resq 1
  .attn_v:      resq 1
  .attn_output: resq 1
  .attn_q_norm: resq 1
  .attn_k_norm: resq 1
  .ffn_norm:    resq 1
  .ffn_gate:    resq 1
  .ffn_up:      resq 1
  .ffn_down:    resq 1
endstruc

; The parsed model. Scalars are read from GGUF metadata; the pointers are
; resolved tensors and allocated caches.
struc Model
  .file_base:     resq 1
  .file_size:     resq 1
  .data_base:     resq 1
  .n_tensors:     resq 1
  .tensors:       resq 1   ; -> Tensor[ n_tensors ]
  .n_layer:       resq 1
  .n_embd:        resq 1
  .n_ff:          resq 1
  .n_head:        resq 1
  .n_head_kv:     resq 1
  .head_dim:      resq 1
  .n_vocab:       resq 1
  .n_ctx_train:   resq 1
  .n_ctx:         resq 1   ; chosen at load (<= n_ctx_train)
  .rope_theta:    resd 1   ; f32
  .rms_eps:       resd 1   ; f32
  .eos_id:        resq 1
  .bos_id:        resq 1
  .pad_id:        resq 1
  .tok_embd:      resq 1   ; -> Tensor
  .output:        resq 1   ; -> Tensor
  .output_norm:   resq 1   ; -> Tensor
  .layers:        resq 1   ; -> Layer[ n_layer ]
  .tokens_arr:    resq 1   ; -> first token's u64 length field (packed strings)
  .n_tokens:      resq 1
  .merges_arr:    resq 1   ; -> first merge's u64 length field (packed strings)
  .n_merges:      resq 1
  .token_type_arr: resq 1  ; -> i32[ n_tokens ] or 0
  .n_threads:     resq 1
  .rope_table:    resq 1   ; -> f32[ n_ctx ][ head_dim ]  (cos in [0..d/2), sin in [d/2..d))
  .kv_dim:        resq 1   ; n_head_kv * head_dim
  .kcache:        resq 1   ; -> f32[ n_layer ][ n_ctx ][ kv_dim ]
  .vcache:        resq 1   ; -> f32[ n_layer ][ n_ctx ][ kv_dim ]
  .arch:          resq 1   ; ARCH_QWEN3 (the Layer records above) or ARCH_QWEN35 (q35.asm resolves its own)
endstruc

; One matrix-vector multiply, split across threads by row range.
;   out[r] = dot(row r of w, x)      for r in 0..rows-1
; xsum32 holds sum(x) over each 32-value sub-block (needed by Q4_K mins);
; xsum16 holds sum(x) over each 16-value sub-block (needed by Q6_K offsets).
; A caller fills xsum32/xsum16 once per x with k_sum32 / k_sum16 (or 0 if the
; weight type does not need them).
; One job may hold up to MATVEC_SEGS matrices that share x (q, k, v of a layer;
; gate and up): their rows form one row space the threads claim from, so the
; pool synchronises once instead of once per matrix. Segment s holds rows
; [cum[s-1], cum[s]) of that space (cum[-1] = 0), its output at out[s].
MATVEC_SEGS equ 4
struc MatvecJob
  .w:       resq 1   ; -> Tensor (segment 0)
  .x:       resq 1   ; -> f32[ cols ]
  .out:     resq 1   ; -> f32[ rows ] (segment 0)
  .xsum32:  resq 1   ; -> f32[ cols/32 ] or 0
  .xsum16:  resq 1   ; -> f32[ cols/16 ] or 0
  .rows:    resq 1   ; all segments
  .cols:    resq 1
  .next:    resq 1   ; next unclaimed row (threads take .chunk rows at a time with lock xadd); reset to 0 per matvec
  .chunk:   resq 1   ; rows per claim: rows / (nthreads * MATVEC_CHUNKS_PER_THREAD), at least MATVEC_CHUNK_MIN
  .nx:      resq 1   ; activation vectors (prompt batch): slot t of g_act_slots, out + t*rows*4 per segment
  .nseg:    resq 1   ; segments in use (1..MATVEC_SEGS)
  .ws:      resq MATVEC_SEGS   ; -> Tensor per segment (ws[0] = w)
  .outs:    resq MATVEC_SEGS   ; -> f32 output per segment
  .cum:     resq MATVEC_SEGS   ; the row space's end of each segment
endstruc
; One activation vector for the dot kernels: its f32 values and its int8 form
; (k_quant_slot). g_act_slots holds ACT_SLOTS of them: a prompt batch quantises
; token t into slot t and the matvec job dots every row with every slot.
struc ActSlot
  .q8:      resq 1   ; -> i8[ cols ]
  .d8:      resq 1   ; -> f32[ cols/256 ]
  .bsums:   resq 1   ; -> i16[ cols/16 ]
  .bsums32: resq 1   ; -> i16[ cols/32 ]
  .x:       resq 1   ; -> f32[ cols ] (the f32/f16 kernels read this)
endstruc
ACT_SLOTS equ 32     ; the largest prompt batch

MATVEC_CHUNKS_PER_THREAD equ 8   ; claims per thread per matvec: the tail imbalance is at most 1/8 of a thread's share
MATVEC_CHUNK_MIN         equ 8

; Pool size cap: the per-thread tables (runtime.asm, win.asm, efi.asm) have this
; many slots; pool_start clamps -t to it.
MAX_THREADS equ 64

; One attention step for the current position. Each thread handles a slice of
; the query heads. `scores` is a scratch base; thread t uses scores + t*n_ctx.
;   for query head h (kv head h / (n_head/n_head_kv)):
;     s_t = dot(q_h, K[t]) / sqrt(head_dim)   for t in 0..seqlen-1
;     p = softmax(s);  out_h = sum_t p_t * V[t]
struc AttnJob
  .q:        resq 1   ; -> f32[ n_head * head_dim ]   (already roped)
  .kbase:    resq 1   ; -> f32[ seqlen ][ kv_dim ]    (this layer's K cache)
  .vbase:    resq 1   ; -> f32[ seqlen ][ kv_dim ]    (this layer's V cache)
  .out:      resq 1   ; -> f32[ n_head * head_dim ]
  .scores:   resq 1   ; -> f32[ n_threads ][ stride ]  (per-thread scratch)
  .stride:   resq 1   ; floats per thread's score row (the model's own n_ctx)
  .seqlen:   resq 1   ; number of cached positions (pos + 1)
  .n_head:   resq 1
  .n_head_kv: resq 1
  .head_dim: resq 1
  .kv_dim:   resq 1
endstruc

; ---------------------------------------------------------------------------
; Reserve 32 bytes of shadow space, align the stack, and the reverse. Use at
; the top/bottom of any function that calls a Win32 import. `%1` extra locals
; (bytes, kept 16-aligned by the caller) may be added.
%macro WINPROLOGUE 0
  push rbp
  mov  rbp, rsp
  sub  rsp, 32
  and  rsp, -16
%endmacro
%macro WINEPILOGUE 0
  mov  rsp, rbp
  pop  rbp
%endmacro

%endif ; COMMON_INC

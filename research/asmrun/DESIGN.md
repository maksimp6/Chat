# asmrun: an Ollama model runner in x86-64 assembly

Research question: how efficient can hand-written assembly be at running a
language model from the Ollama store on this machine's CPU, measured against
Ollama itself (llama.cpp)? This file is the design every part of the runner is
written against. `README.md` reports what was measured.

## The machine

| Fact | Value (measured 2026-11-05) |
| --- | --- |
| CPU | Intel N100: 4 Gracemont cores, no SMT, 2 MB L2, 6 MB L3 |
| ISA | x86-64 with AVX2, FMA, F16C, BMI1/2, AVX-VNNI; no AVX-512 |
| Memory | 7.7 GB total, about 2.7 GB free with the desktop running |
| OS | Windows 11 Pro 10.0.26300 |
| Toolchain | NASM only (installed for this work); no linker, libc, Python or WSL |
| Baseline | Ollama 0.40.14 (llama.cpp, 100% CPU, 4 threads): qwen3:1.7b decodes at 12.5–14.5 tok/s, prompt eval 38–76 tok/s |

Reading all weights once per generated token is 1.353 GB, so at 15 tok/s
llama.cpp already moves about 20 GB/s. Decoding is memory-bound on this CPU;
the runner's own memory-bandwidth probe (`--bw`) gives the roofline.

## The model: qwen3:1.7b (Ollama blob sha256-3d0b7905…)

GGUF v3, 311 tensors, data at file offset 5951136 (alignment 32), 1,359,279,776 bytes.

| Key | Value |
| --- | --- |
| architecture | qwen3 |
| block_count | 28 |
| embedding_length | 2048 |
| feed_forward_length | 6144 |
| head_count / head_count_kv | 16 / 8 (GQA, 2 query heads per KV head) |
| key_length = value_length | 128 |
| rope.freq_base | 1000000 (NeoX rotation: pairs (i, i+64)) |
| layer_norm_rms_epsilon | 1e-6 |
| context_length | 40960 |
| tokenizer | gpt2 byte-level BPE, pre-tokenizer `qwen2`, 151936 tokens, 151387 merges |
| eos / bos / pad | 151645 `<|im_end|>` / 151643 `<|endoftext|>` / 151643; add_bos false |
| other control ids | 151644 `<|im_start|>`, 151667 `<think>`, 151668 `</think>` |

Tensors (GGUF dims are `ne0 x ne1`; a matrix is `ne1` rows of `ne0` values, out = W·x):

| Tensor | Type | Dims | Bytes |
| --- | --- | --- | --- |
| token_embd.weight | Q4_K | 2048 x 151936 | 175,030,272 |
| output.weight (not tied) | Q6_K | 2048 x 151936 | 255,252,480 |
| output_norm.weight | F32 | 2048 | |
| blk.N.attn_norm / ffn_norm | F32 | 2048 | |
| blk.N.attn_q_norm / attn_k_norm | F32 | 128 | per-head RMSNorm weights |
| blk.N.attn_q.weight | Q4_K | 2048 x 2048 | |
| blk.N.attn_k.weight | Q4_K | 2048 x 1024 | |
| blk.N.attn_v.weight | F16 | 2048 x 1024 | |
| blk.N.attn_output.weight | Q4_K | 2048 x 2048 | |
| blk.N.ffn_gate / ffn_up.weight | Q4_K | 2048 x 6144 | |
| blk.N.ffn_down.weight | Q6_K in layers 0,1,2,5,8,11,14,17,20,23,24,25,26,27; Q4_K elsewhere | 6144 x 2048 | |

Totals: Q4_K 155 tensors / 835.6 MB, Q6_K 15 / 399.8 MB, F16 28 / 117.4 MB, F32 113 / 0.5 MB.
The runner must handle exactly F32, F16, Q4_K and Q6_K and refuse anything else by name.

### Quantized block formats (llama.cpp `ggml-common.h`)

`Q4_K`, 256 values in 144 bytes: `f16 d; f16 dmin; u8 scales[12]; u8 qs[128]`.
Eight sub-blocks of 32 values, each with a 6-bit scale `sc` and 6-bit min `m`:
for j < 4: `sc = scales[j] & 63`, `m = scales[j+4] & 63`; for j ≥ 4:
`sc = (scales[j+4] & 15) | ((scales[j-4] >> 6) << 4)`,
`m = (scales[j+4] >> 4) | ((scales[j] >> 6) << 4)`.
`qs` holds 4-bit values: for chunk c in 0..3, bytes `qs[32c .. 32c+31]`: low nibbles
are values `64c + 0..31` (sub-block 2c), high nibbles are values `64c + 32..63`
(sub-block 2c+1). Value = `d*sc*q - dmin*m`. Hence
`dot(row, x) = Σ_j d*sc_j*Σ(q·x)_j − Σ_j dmin*m_j*Σx_j` with `Σx_j` the sum of x over
sub-block j: precompute the 32-wide sums of x once per matvec.

`Q6_K`, 256 values in 210 bytes: `u8 ql[128]; u8 qh[64]; i8 scales[16]; f16 d`.
Two halves of 128 values; per half (`ql += 64, qh += 32, sc += 8`), for l in 0..31
with `is = l/16`:
`q1 = (ql[l] & 15) | ((qh[l] & 3) << 4)` → value index l, scale sc[is];
`q2 = (ql[l+32] & 15) | (((qh[l] >> 2) & 3) << 4)` → index l+32, scale sc[is+2];
`q3 = (ql[l] >> 4) | (((qh[l] >> 4) & 3) << 4)` → index l+64, scale sc[is+4];
`q4 = (ql[l+32] >> 4) | (((qh[l] >> 6) & 3) << 4)` → index l+96, scale sc[is+6];
value = `d * sc * (q − 32)`. Hence `dot = d*Σ_j sc_j*(Σ(q·x)_j − 32*Σx_j)` over the 16
sub-blocks of 16 values: precompute the 16-wide sums of x once per matvec.

`F16`: IEEE half per value (`vcvtph2ps`). `F32`: float.

### Forward pass (Qwen3, one token at position `pos`)

```
x = dequant(token_embd[token])                      # f32[2048]
for layer in 0..27:
    xn = rmsnorm(x) * attn_norm                      # eps 1e-6, scale = 1/sqrt(mean(x²)+eps)
    q = Wq·xn (2048) ; k = Wk·xn (1024) ; v = Wv·xn (1024)
    for each of 16 q heads: q_h = rmsnorm(q_h) * attn_q_norm   # over the 128 values
    for each of 8 kv heads:  k_h = rmsnorm(k_h) * attn_k_norm
    rope(q_h, pos), rope(k_h, pos): for i in 0..63 with θ_i = 1e6^(-i/64), a = pos*θ_i:
        (u, w) = (h[i], h[i+64]); h[i] = u*cos a − w*sin a; h[i+64] = u*sin a + w*cos a
    K[layer][pos] = k ; V[layer][pos] = v
    for head h (kv head h/2): s_t = q_h·K[layer][t]_{kv} / sqrt(128) for t ≤ pos
        p = softmax(s); attn_h = Σ_t p_t V[layer][t]_{kv}
    x += Wo·attn                                      # 2048
    xn = rmsnorm(x) * ffn_norm
    g = Wgate·xn ; u = Wup·xn                         # 6144 each
    h = silu(g) * u                                   # silu(z) = z / (1 + e^−z)
    x += Wdown·h                                      # 2048
xf = rmsnorm(x) * output_norm
logits = Wout·xf                                      # 151936
next = argmax(logits)                                 # greedy, as Ollama with temperature 0
```

The prompt is processed one token at a time (positions 0..n−1); the first generated
token is the argmax after the last prompt token. Generation stops at `<|im_end|>`,
`<|endoftext|>` or the token limit. No chat template: the prompt is raw text, which is
what Ollama's `/api/generate` with `raw: true` sees, so outputs are comparable.

### Tokenizer (gpt2 byte-level BPE, pre-tokenizer qwen2)

Byte-to-unicode map (GPT-2 `bytes_to_unicode`): bytes 33–126, 161–172 and 174–255 map
to the code point of the same number; the other 68 bytes (0–32, 127–160, 173) map, in
increasing order, to code points 256–323 (so a space is U+0120 "Ġ", a newline U+010A "Ċ").
A token string in the GGUF is the UTF-8 of those code points; decoding reverses the map.

Pre-tokenizer, the `qwen2` regular expression
`(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+`,
implemented for bytes with these rules at each position, first match wins:

1. the contractions `'s 't 're 've 'm 'll 'd` (case-insensitive);
2. one optional byte that is not a letter, digit, CR or LF, then one or more letters;
   letters are ASCII letters and every byte ≥ 0x80 (an approximation of `\p{L}` that is
   exact for ASCII and keeps non-ASCII words whole);
3. a single ASCII digit;
4. an optional space, then one or more bytes that are neither whitespace, letters nor
   digits, then any run of CR/LF;
5. whitespace: a run that contains CR or LF ends after its last CR/LF; a run of n ≥ 2
   other whitespace bytes followed by a non-space yields the first n−1 bytes (the last
   space belongs to the next word); otherwise the whole run.

BPE per chunk: start from the byte tokens, repeatedly merge the adjacent pair whose
merge rank (`tokenizer.ggml.merges`, "A B" → index) is lowest, until none applies;
look every piece up in the vocabulary. Merges and vocabulary live in two open-addressing
hash tables (FNV-1a over the bytes; the merge key is `A`, a space, `B`) of 2^19 slots.

A check without a reference tokenizer: Ollama reports `prompt_eval_count` for a raw
prompt, which must equal the runner's token count for the same text (the known
prompt "The capital of France is" is 5 tokens: 785 "The", then " capital", " of",
" France", " is"; 576 is " The", 220 " ", 198 "\n").

## Program layout

```
research/asmrun/
  asmrun.asm          top level: %includes in order, PE header first
  inc/pe.inc          PE64 header, section table, import directory (kernel32 only)
  inc/win.inc         import names and IAT labels, Win32 constants
  inc/common.inc      struc definitions, constants, macros shared by every module
  src/runtime.asm     process start, argv, stdout/stderr, memory, file mapping, timing,
                      cpuid check, thread pool, memory-bandwidth probe
  src/gguf.asm        GGUF parsing into the Model struct
  src/kernels.asm     matvec per type, rmsnorm, softmax, silu, exp, rope, attention
  src/tokenizer.asm   BPE encode, byte-level decode
  src/model.asm       forward pass, KV cache, generation loop, statistics
  build.ps1           nasm -f bin -O2 -o build/asmrun.exe asmrun.asm (build/ is ignored)
  bench.ps1           Ollama baseline and asmrun runs on the same prompts → results
  DESIGN.md           this file
  README.md           how to build and run, measurements, what was learned
```

One NASM `-f bin` output, no linker: the file starts with the DOS and PE headers and has
one section (code, data and imports), characteristics code | initialized data | execute |
read | write. ImageBase 0x140000000, SectionAlignment 0x1000, FileAlignment 0x200, the
section at file offset 0x200 = RVA 0x1000 (`org 0x140000000 + 0x1000 - 0x200`, so a
label's virtual address equals its address and header RVAs are `label - IMAGE_BASE`).
No relocations and no DYNAMIC_BASE flag, so the image loads at its preferred base.
Subsystem console; stack reserve 8 MB, commit 1 MB; imports from kernel32.dll through
the IAT (`call [rel __imp_Name]`). Every big buffer comes from VirtualAlloc; the model
file is mapped read-only (CreateFileW, CreateFileMappingW, MapViewOfFile) and touched
once at load so page-in time is counted in `load_ms`, not in the token rates.

## Conventions

- NASM syntax, `bits 64`, `default rel`. Global labels `module_function`; local
  labels `.name`. Constants `UPPER_CASE`. One blank line and a comment before each function
  saying what it computes and its register contract.
- Calling convention: Windows x64 everywhere, also between modules. Arguments in
  rcx, rdx, r8, r9, then the stack; floats in xmm0–3; return in rax or xmm0. Callee-saved:
  rbx, rbp, rsi, rdi, r12–r15, xmm6–xmm15. The caller reserves the 32-byte shadow space;
  rsp is 16-byte aligned at every `call`. Functions that call kernel32 must keep this
  exactly; hot kernels are leaf functions and may use any register they save.
- Errors: `rt_fatal(rcx = zero-terminated message)` writes `asmrun: <message>` to
  stderr and exits with code 1. Nothing is silently skipped.
- All floats in activations, norms, KV cache and logits are f32. Weights stay in their
  GGUF type and are dequantized inside the dot kernels; nothing is dequantized ahead.
- Text in and out is UTF-8 bytes. The prompt comes from `-p <file>` (bytes as they are)
  or stdin (one trailing CR/LF pair stripped). Generated text goes to stdout as it is
  produced; statistics go to stderr.

## Shared structures (`inc/common.inc`)

```
struc Tensor
  .data     resq 1   ; address of the first byte
  .type     resd 1   ; ggml type: 0 F32, 1 F16, 12 Q4_K, 14 Q6_K
  .ndims    resd 1
  .ne0      resq 1   ; values per row (columns)
  .ne1      resq 1   ; rows (1 for a vector)
  .nbytes   resq 1
  .name     resq 1   ; address of the name bytes in the GGUF header
  .namelen  resq 1
endstruc

struc Layer           ; addresses of Tensor records
  .attn_norm, .attn_q, .attn_k, .attn_v, .attn_output, .attn_q_norm, .attn_k_norm,
  .ffn_norm, .ffn_gate, .ffn_up, .ffn_down   (resq 1 each)
endstruc

struc Model
  .file_base, .file_size, .data_base       ; mapping
  .n_tensors, .tensors (Tensor array)      ; parsed table
  .n_layer, .n_embd, .n_ff, .n_head, .n_head_kv, .head_dim, .n_vocab, .n_ctx_train (resq)
  .rope_theta, .rms_eps (resd)
  .eos_id, .bos_id (resq)
  .tok_embd, .output, .output_norm (Tensor addresses)
  .layers (address of Layer array, n_layer entries)
  .tokens_arr, .n_tokens, .merges_arr, .n_merges, .token_type_arr
             ; tokens_arr / merges_arr point at the first string's u64 length field
endstruc

struc MatvecJob
  .w        resq 1   ; Tensor address
  .x        resq 1   ; f32[cols]
  .out      resq 1   ; f32[rows]
  .xsum32   resq 1   ; f32[cols/32], sums of x per 32 values (Q4_K mins)
  .xsum16   resq 1   ; f32[cols/16], sums of x per 16 values (Q6_K offsets)
  .rows     resq 1
  .cols     resq 1
endstruc
```

## Module interfaces

### runtime (`src/runtime.asm`)

| Function | Contract |
| --- | --- |
| `_entry` | PE entry (AddressOfEntryPoint): parses `GetCommandLineW` into UTF-16 argv, calls `main`, `ExitProcess(eax)` |
| `rt_out(rcx=ptr, rdx=len)` / `rt_err(...)` | write bytes to stdout / stderr |
| `rt_out_str(rcx=zstr)`, `rt_err_str` | zero-terminated text |
| `rt_out_u64(rcx)`, `rt_err_u64`, `rt_err_f32(xmm0)` (3 decimals), `rt_err_i64` | numbers |
| `rt_fatal(rcx=zstr)` | message, exit 1 |
| `rt_alloc(rcx=bytes) -> rax` | VirtualAlloc, zeroed, fatal on failure |
| `rt_map_file(rcx=utf16 path, rdx=&size) -> rax base` | read-only mapping of a file |
| `rt_read_stdin(rcx=&len) -> rax ptr` | whole stdin |
| `rt_read_file(rcx=utf16 path, rdx=&len) -> rax ptr` | whole file into memory |
| `rt_now_ns() -> rax` | QueryPerformanceCounter in nanoseconds |
| `rt_cpu_count() -> rax` | processors |
| `rt_cpuid_check()` | fatal without AVX2+FMA+F16C; sets `g_has_vnni` |
| `pool_start(rcx=nthreads)` | creates nthreads−1 worker threads that spin for jobs |
| `pool_run(rcx=func, rdx=arg)` | runs `func(rcx=arg, rdx=tid, r8=nthreads)` on all threads, tid 0 on the caller; returns when all are done |
| `rt_bench_bandwidth()` | `--bw`: all threads read a 512 MB buffer three times; prints GB/s |
| `rt_utf16_to_utf8`, `rt_parse_u64`, `rt_streq`, `rt_memcmp`, `rt_memcpy`, `rt_memset` | small helpers |

Workers spin on a generation counter with `pause`; `pool_run` publishes `func`/`arg`,
increments the generation, runs tid 0 itself and waits for the done counter. Each job
splits its own work by `tid`/`nthreads` (rows of a matvec, heads of attention).

### gguf (`src/gguf.asm`)

`gguf_open(rcx=Model*)`: `file_base`/`file_size` set by the caller; parses the header
(magic `GGUF`, version 3, counts), every key (scalar values into the Model fields;
arrays: tokens, merges, token_type addresses and counts), the tensor table (name, type,
dims, offset → `Tensor.data = data_base + offset`), computes `data_base` from
`general.alignment` (default 32), resolves the named tensors into the Model and Layer
records, and fatals on: wrong magic, unknown architecture, a tensor type outside
{F32, F16, Q4_K, Q6_K}, a missing tensor. `gguf_cli(Model*)` (`--gguf`) prints the
scalar metadata, the per-type counts and the first tensors, as the PowerShell dump did.

### kernels (`src/kernels.asm`)

| Function | Computes |
| --- | --- |
| `k_rmsnorm(rcx=out, rdx=x, r8=w, r9=n, xmm0=eps)` | out = x · (1/sqrt(mean(x²)+eps)) · w (ymm FMA) |
| `k_add(rcx=dst, rdx=src, r8=n)` | dst += src (ymm) |
| `k_silu_mul(rcx=g, rdx=u, r8=n)` | g = silu(g) · u, silu(z)=z/(1+e^−z) via `k_exp` |
| `k_softmax(rcx=x, rdx=n)` | in place, max-subtracted, `k_exp` per element |
| `k_exp(xmm0=x f32) -> xmm0` | e^x via x87 (`fldl2e`/`frndint`/`f2xm1`/`fscale`); clamped to ±88. Hardware-accurate, not a polynomial; it is never the bottleneck (see efficiency notes) |
| `k_dot_f32_row(rcx=row, rdx=x, r8=cols) -> xmm0` | one F32 row (ymm FMA) |
| `k_dot_f16_row(rcx=row, rdx=x, r8=cols) -> xmm0` | one F16 row (`vcvtph2ps` 8 at a time) |
| `k_argmax(rcx=x, rdx=n) -> rax` | first index of the maximum |
| `k_sum32(rcx=out, rdx=x, r8=n)`, `k_sum16` | sums per 32 / 16 values of x (feed the quant dots) |
| `k_dot_q4k_row(rcx=row, rdx=x, r8=cols, r9=xsum32) -> xmm0` | one Q4_K row |
| `k_dot_q6k_row(rcx=row, rdx=x, r8=cols, r9=xsum16) -> xmm0` | one Q6_K row |
| `k_matvec_job(rcx=MatvecJob*, rdx=tid, r8=nthreads)` | `pool_run` body: contiguous row slice [tid·rows/nthreads, (tid+1)·rows/nthreads), dispatch by type to the row dots. (The per-row-range work `k_matvec_rows` is folded directly into this loop.) |
| `k_rope_table(rcx=out, rdx=n_ctx, r8=head_dim, xmm0=theta)` | out[pos][0..half) = cos, [half..d) = sin, via x87 `fsincos`; publishes head_dim/half for `k_rope` |
| `k_rope(rcx=head f32*, rdx=table_row)` | NeoX rotation in place (half from the published global) |
| `k_attn_job(rcx=AttnJob*, rdx=tid, r8=nthreads)` | `pool_run` body: splits the query heads by tid; scores (÷√head_dim), softmax, weighted V sum per head. Per-thread score row at `scores + tid·g_n_ctx` |
| `kernels_cli(rcx=Model*)` (`--kernels`) | micro-benchmark: times `KERNELS_BENCH_ITERS` matvecs of the output projection (largest weight) through the real `model_matvec` path and prints rows, cols, iters, ms and effective GB/s (= weight bytes read ÷ time) |

Quant dots fuse dequant and the dot: for each 256-value block they reproduce the
exact bit-extraction of `model_dequant_row` and accumulate q·x per sub-block
instead of storing. Q4_K: each 32-byte `qs` chunk splits into a low sub-block
(nibble `& 0x0F`) over x[64c..+32) and a high sub-block (nibble `>> 4`) over
x[64c+32..+64); per block, dot += d·Σ sc[s]·(Σ q·x) − dmin·Σ m[s]·xsum32[blk·8+s].
Q6_K: the four 32-value groups per half are built from `ql`/`qh` with shifts/masks;
dot += d·(Σ sc[S]·(Σ q·x) − 32·Σ sc[S]·xsum16[blk·16+S]). The `xsum32`/`xsum16`
buffers (filled by `k_sum32`/`k_sum16` once per x) hoist the constant min/offset
terms out of the inner loop. The f16 `d`/`dmin` convert with `vcvtph2ps` from a
scalar load. The q·x accumulation is scalar (the nibble/6-bit unpack is inherently
scalar and the kernel is memory-bandwidth bound); F16/F32 rows and the attention
q·K / p·V use 8-wide ymm FMA. An int8 x path (`vpmaddubsw`/`vpdpbusd`) is a possible
second version if the dot ever proves compute-bound rather than bandwidth-bound.

### tokenizer (`src/tokenizer.asm`)

| Function | Contract |
| --- | --- |
| `tok_init(rcx=Model*)` | vocabulary index (address, length per id), the two hash tables, byte maps |
| `tok_encode(rcx=text, rdx=len, r8=out_ids (u32), r9=max) -> rax count` | pre-tokenize + BPE |
| `tok_decode(rcx=id, rdx=out_buf) -> rax bytes` | token text as bytes; 0 for control tokens |
| `tok_is_stop(rcx=id) -> rax` | eos, bos/pad |
| `tok_cli(Model*)` (`--tokenize`) | stdin → ids, count, round-trip text |

### model (`src/model.asm`)

| Function | Contract |
| --- | --- |
| `model_load(rcx=utf16 path, rdx=n_ctx, r8=nthreads) -> rax Model*` | map, parse, resolve, allocate, build rope table, touch every weight page |
| `model_forward(rcx=Model*, rdx=token, r8=pos) -> rax logits` | one token |
| `model_generate(rcx=Model*, rdx=ids, r8=n, r9=max_new)` | the loop, text to stdout, stats to stderr |
| `main(rcx=argc, rdx=argv) -> eax` | `asmrun.exe <gguf> [-n N] [-t T] [-c CTX] [-p file] [--bw] [--cpuid] [--gguf] [--tokenize] [--kernels]` |

Statistics printed to stderr at the end:

```
asmrun: model=qwen3 layers=28 embd=2048 ff=6144 heads=16/8 vocab=151936 ctx=1024 threads=4
asmrun: load_ms=<map+parse+tokenizer+page-in>
asmrun: prompt_tokens=<n> prompt_ms=<t> prompt_tok_s=<r>
asmrun: gen_tokens=<m> gen_ms=<t> gen_tok_s=<r> weights_gb_per_token=1.353 effective_gb_s=<r>
```

`effective_gb_s` = 1.353 GB × gen_tokens / gen seconds: how much of the roofline the
runner reaches. Timing uses QueryPerformanceCounter around the forward passes only.

## Verification and measurement rules

- The product is checked by running it. Component inspection modes (`--gguf`,
  `--tokenize`, `--kernels`, `--bw`, `--cpuid`) are features of the research tool.
  No test files, fixtures or test workflows are added to the repository (AGENTS.md).
- Numeric references come from ad-hoc PowerShell `Add-Type` C# snippets kept in the
  session scratchpad, never committed: they read the same GGUF, dequantize a row in
  double precision and compute the same dot as `--kernels`; agreement within 1e-3
  relative is required.
- Tokenizer check: the token count of a raw prompt equals Ollama's `prompt_eval_count`
  for the same text, and `--tokenize` round-trips the text.
- End-to-end check: greedy generation for "The capital of France is" against Ollama
  (`raw: true`, temperature 0), which answers " Paris. The capital of the United States
  is Washington, D.C. The capital of the United Kingdom is London. …". The first tokens
  must match; a later divergence from float rounding is reported, not hidden.
- Measurements run one at a time on an idle machine with Ollama's model unloaded
  (`ollama stop qwen3:1.7b`) before an asmrun measurement and vice versa; each number
  is the median of three runs of 64 generated tokens on the two prompts of `bench.ps1`.
- No model file is ever committed; `build/` is ignored.

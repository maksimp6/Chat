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
  asmrun.asm          top level: org, the two sections, %includes in order, PE header first
  inc/pe.inc          PE32+ header and section table; kernel32 import tables (Windows) or the
                      no-op relocation block (UEFI)
  inc/win.inc         import names and IAT labels, Win32 constants
  inc/efi.inc         UEFI table/protocol offsets, status codes
  inc/common.inc      struc definitions, constants, macros shared by every module
  src/runtime.asm     shared: argv, stdout/stderr formatting, fatal, cpuid check, thread pool
                      (pool_worker/pool_run), memory-bandwidth probe, string helpers
  src/win.asm         Windows half: entry, WriteFile, VirtualAlloc, file mapping, QPC, CreateThread
  src/efi.asm         UEFI half: entry, ConOut/ConIn, AllocatePages, file reads, TSC clock,
                      EFI_MP_SERVICES workers, return to the firmware
  src/gguf.asm        GGUF parsing into the Model struct
  src/kernels.asm     matvec per type, rmsnorm, softmax, silu, exp, rope, attention
  src/tokenizer.asm   BPE encode, byte-level decode
  src/model.asm       forward pass, KV cache, generation loop, statistics, CLI (-i loop)
  build.ps1           nasm -f bin for both images, EVEX scan, PE header dump (build/ is ignored)
  make-usb.ps1        BOOTX64.EFI + model.gguf + asmrun.cfg onto a FAT32 USB stick
  bench.ps1           Ollama baseline and asmrun runs on the same prompts → results
  DESIGN.md           this file
  README.md           how to build and run, measurements, what was learned
```

One NASM `-f bin` output, no linker, two images from the same sources: `asmrun.exe`
(Windows console) and, with `-D ASMRUN_EFI`, `BOOTX64.EFI` (UEFI application). The file
starts with the DOS and PE headers; then `.text` (import tables, code, read-only
constants; execute | read) from RVA 0x1000 and `.data` (every module's mutable globals
and strings; read | write) from the next page. SectionAlignment = FileAlignment = 0x1000,
so a byte's file offset equals its RVA; with `org 0x140000000` a label's assembled
address equals its link-time virtual address and header RVAs are `label - IMAGE_BASE`.
In NASM's bin format the modules switch between `section .text` and `section .data`
(declared once in asmrun.asm with `align=4096 follows=.text`), and the assembler lays
the data section out after the code, page aligned, with file offset = RVA.

The code is position-independent: RIP-relative addressing only, no absolute address in
any data (the one table that held string addresses, `blk_tab`, holds offsets from
itself). The Windows image says RELOCS_STRIPPED and loads at its base; the UEFI image
clears that bit so the firmware may load it anywhere and advertises a 10-byte base-
relocation block with one ABSOLUTE (no-op) entry, which the EDK2 loader walks without
changing a byte. Subsystem 3 or 10; stack reserve 8 MB, commit 1 MB (Windows). Windows
imports from kernel32.dll go through the IAT (`call [rel __imp_Name]`); the UEFI half
calls through the system table's function pointers (`call [rax + BS_AllocatePages]`).
Every big buffer comes from `rt_alloc` (VirtualAlloc / AllocatePages); on Windows the
model file is mapped read-only (CreateFileW, CreateFileMappingW, MapViewOfFile) and
touched once at load so page-in time is counted in `load_ms`, not in the token rates;
on UEFI it is read from the stick into pages at load.

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
  .xsum32   resq 1   ; (unused since the int8 path; kept for layout)
  .xsum16   resq 1   ; (unused)
  .rows     resq 1
  .cols     resq 1
  .next     resq 1   ; next unclaimed row; threads take .chunk rows at a time with lock xadd
  .chunk    resq 1   ; rows / (nthreads · 8), at least 8
endstruc
```

## Module interfaces

### runtime (`src/runtime.asm`, shared) and its platform halves (`src/win.asm`, `src/efi.asm`)

The shared file implements everything that does not touch the platform and defines the
ten names each platform half must provide. The modules call only these names.

| Function | Contract |
| --- | --- |
| `rt_parse_cmdline(rcx=utf16) -> rax argc` | splits a UTF-16 command line into `g_argv` in place (quotes honoured) |
| `rt_out(rcx=ptr, rdx=len)` / `rt_err(...)` | write bytes to stdout / stderr (`rt_write_h` with `g_stdout`/`g_stderr`) |
| `rt_out_str(rcx=zstr)`, `rt_err_str` | zero-terminated text |
| `rt_out_u64(rcx)`, `rt_err_u64`, `rt_err_f32(xmm0)` (3 decimals), `rt_err_i64`, `rt_err_hex64` | numbers |
| `rt_fatal(rcx=zstr)` | message, `rt_exit(1)` |
| `rt_cpuid_check()` | fatal without AVX2+FMA+F16C and OS-enabled YMM state; sets `g_has_vnni` |
| `pool_worker(rcx=tid)` | spins on the generation counter, runs each job, returns when `g_pool_quit` is set |
| `pool_run(rcx=func, rdx=arg)` | runs `func(rcx=arg, rdx=tid, r8=nthreads)` on all threads, tid 0 on the caller; returns when all are done; fatal after 2^31 idle spins (a worker that never finishes is a bug, and with no OS it would be a silent hang) |
| `rt_bench_bandwidth()` | `--bw`: all threads read a 512 MB buffer three times; prints GB/s |
| `rt_utf16_to_utf8`, `rt_watoi`, `rt_wcmp_ascii`, `rt_parse_u64`, `rt_streq`, `rt_memcmp`, `rt_memcpy`, `rt_memset` | small helpers |

| Platform name | Windows (`win.asm`) | UEFI (`efi.asm`) |
| --- | --- | --- |
| `_entry` | PE entry: console handles, QPC frequency, `GetCommandLineW` → argv, `main`, `rt_exit` | `efi_main(ImageHandle, SystemTable)`: save the firmware's registers, CR4, XCR0 and MXCSR, enable AVX state (after CPUID confirms XSAVE, AVX and the XCR0 bits), disarm the 5-minute boot watchdog, measure the TSC against `Stall`, request the highest clock (`efi_cpu_setup`), open the boot volume, `\asmrun.cfg` → argv (default `model.gguf -i`), `main`, `rt_exit` |
| `rt_write_h(rcx=handle, rdx=ptr, r8=len)` | `WriteFile` | UTF-8 → UCS-2 with CR LF, `ConOut->OutputString`; decoder state kept across calls (a token may end mid-character) |
| `rt_exit(rcx=code)` | `ExitProcess` | set `g_pool_quit`, wait for every AP `pool_start` started to return to the firmware, print the code, wait for a key, restore XCR0/CR4 and the firmware's registers, return from `_entry`'s frame (`EFI_SUCCESS` / `EFI_ABORTED`); if an AP has not returned within 3 s (stuck in a job) say so and reset the machine after the key instead of returning |
| `rt_alloc(rcx=bytes) -> rax` | `VirtualAlloc`, zeroed by the OS | `AllocatePages(EfiLoaderData)`, zeroed here |
| `rt_map_file(rcx=utf16 path, rdx=&size) -> rax` | read-only mapping | open on the boot volume, `GetInfo` size, pages, `Read` in 8 MB pieces with a progress line |
| `rt_read_file` | same | same |
| `rt_read_stdin(rcx=&len) -> rax` | all of stdin to EOF; with `-i` (`g_stdin_line`) to the end of the line | one line from `ConIn` with echo, Backspace, Enter; Esc gives an empty line; keys queued before the first prompt are dropped once |
| `rt_now_ns() -> rax` | `QueryPerformanceCounter` scaled | `rdtsc` scaled by the measured TSC Hz |
| `rt_cpu_count() -> rax` | `GetSystemInfo` | `EFI_MP_SERVICES_PROTOCOL.GetNumberOfProcessors` (enabled), 1 without the protocol |
| `pool_start(rcx=nthreads)` | `CreateThread` × (nthreads−1) running `pool_worker`; nthreads clamped to 1..`MAX_THREADS` | `StartupThisAP` × (nthreads−1), non-blocking with a wait event and no timeout, running `pool_ap_entry(tid)` on each application processor; clamped to 1..`MAX_THREADS` and the enabled processors; a processor the firmware will not start is reported and skipped; `g_ap_started` counts the accepted ones for `rt_exit` |

Workers spin on a generation counter with `pause`; `pool_run` publishes `func`/`arg`,
increments the generation, runs tid 0 itself and waits for the done counter. Each job
splits its own work by `tid`/`nthreads` (rows of a matvec, heads of attention).

## No operating system (the UEFI build)

`BOOTX64.EFI` is the same runner with `efi.asm` as its platform half. The firmware's
boot manager loads it from `\EFI\BOOT\BOOTX64.EFI` on a FAT32 stick and calls `_entry`
with the Microsoft x64 calling convention, which is what every function in asmrun already
uses, so nothing above the runtime changes. Boot services stay active for the whole run
(`ExitBootServices` is never called): the firmware is the only other code on the machine,
and it is used at the edges only.

- **Processors.** `EFI_MP_SERVICES_PROTOCOL` (PI specification; present in EDK2-derived
  firmware such as AMI Aptio) starts a procedure on an application processor.
  `pool_start` starts `pool_ap_entry(tid)` on nthreads−1 of them in non-blocking mode
  with a wait event and no timeout; the procedure saves the core's CR4, XCR0 and MXCSR,
  enables AVX state (firmware leaves XCR0 at x87|SSE, and the first VEX instruction would
  fault), asks the core for its highest clock, moves to its own 1 MiB stack, checks in and
  runs `pool_worker` until `g_pool_quit`, then restores what it saved and returns to the
  firmware on the firmware's stack so the processor is the firmware's again when we leave.
  Interrupts on the APs are as the firmware leaves them for an AP procedure (disabled in
  EDK2); the boot processor keeps the firmware's timer tick, USB polling and MP status
  check, whose handlers save and restore only the FXSAVE state (xmm, not the upper ymm
  halves): the ymm registers survive because firmware code is non-VEX (legacy SSE leaves
  the upper halves alone). Without the protocol the pool is the boot processor alone (a
  message says so).
- **Clock.** Without an operating system nothing sets the P-state after the firmware,
  which commonly leaves the cores at their base ratio (800 MHz on the N100, whose burst
  is 3.4 GHz). Every processor runs `efi_cpu_perf`: if CPUID.06H reports Hardware
  P-states it enables them (`IA32_PM_ENABLE`, sticky until reset) when the firmware has
  not, and writes `IA32_HWP_REQUEST` with minimum = maximum = the highest level of
  `IA32_HWP_CAPABILITIES` (desired 0, EPP 0 = performance); `IA32_ENERGY_PERF_BIAS` goes
  to 0 when CPUID says it exists. The hardware still enforces its power and thermal
  limits. The boot processor measures its clock with `IA32_APERF`/`IA32_MPERF` against the
  TSC over 50 ms before and after, and prints both with the levels. Every MSR is touched
  only behind its CPUID bit: a wrong MSR is a #GP, and the firmware's handler stops the
  machine with a register dump.
- **Memory.** `AllocatePages(EfiLoaderData)` for everything; the firmware's page tables
  identity-map all RAM with large pages, so the 1.36 GB model lives in a handful of TLB
  entries instead of Windows's 4 KB pages. The model is read from the stick into pages
  (the firmware's FAT and USB drivers do the I/O; a progress line reports MB/s), not
  mapped. Nothing is freed: the firmware reclaims loader memory when something else boots.
- **Console.** stdout and stderr are both `ConOut`; UTF-8 is converted to UCS-2 with
  CR LF line ends, characters outside the BMP print as `?`. The console font is only
  required to have Latin glyphs (plus box-drawing shapes), so text in other scripts may
  show as blanks; the firmware does not report it. Input is `ConIn`: one line with echo
  and Backspace; the keys the firmware queued before the first prompt (pressed during the
  load) are dropped once, after that typing ahead works. `-i` (the default without an
  `asmrun.cfg`) loops: prompt, generation, statistics, next prompt; an empty line or Esc
  ends the session; a prompt longer than the context is refused with a message.
- **Time.** `rdtsc` scaled by the TSC frequency measured over a 100 ms `Stall`
  (CPUID.15H's figure is printed beside it when the leaf reports a crystal, and replaces
  a measurement outside 100 MHz..20 GHz; with neither the run stops with a message rather
  than divide by a wrong value later).
- **Arguments.** `\asmrun.cfg` holds the Windows command-line arguments (`model.gguf -p
  prompt.txt -n 64 -t 4`, `model.gguf --bw`, `model.gguf --kernels`, ...) and is turned
  into the same UTF-16 argv `main` parses on Windows; file names are relative to the
  stick's root, `/` and `\` both work.
- **Leaving.** The 5-minute watchdog the boot manager arms is disarmed at entry.
  `rt_exit` (normal end or `rt_fatal`) hands the processors back, prints the exit code,
  waits for a key, restores XCR0 and CR4 and returns to the boot manager, which
  continues with the next boot option. The boot manager frees an application's pages the
  moment its entry point returns, so the return happens only when every processor
  `pool_start` started has come back; a worker stuck inside a job (the case the pool's
  hang detector reports) cannot be given back, and after the key the machine is reset
  instead. Every step before the model loads prints one line and every firmware error
  prints its EFI status, so a boot that stops shows where.
- **Requirements and assumptions.** FAT32 stick (the firmware has no exFAT/NTFS
  driver); Secure Boot off (the image is unsigned); x86-64 UEFI with XSAVE, AVX and the
  ISA the kernels need. The image carries separate code (read-only) and data (read-write)
  sections, executes nothing from data, pages or a stack, and says so with NX_COMPAT, so
  a firmware that protects images keeps its strict mode and one that does not runs it as
  is. The HWP request is skipped, with a message, on a CPU without Hardware P-states.

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
| `k_silu_mul(rcx=g, rdx=u, r8=n)` | g = silu(g) · u, silu(z)=z/(1+e^−z); eight lanes per step with the `EXP8` macro |
| `k_softmax(rcx=x, rdx=n)` | in place, max-subtracted, `k_exp` per element |
| `k_exp(xmm0=x f32) -> xmm0`, `EXP8` (ymm0) | e^x by the cephes expf scheme: n = round(x·log2e), r = x − n·ln2 (two-part ln2), degree-6 polynomial for e^r, times 2^n built in the exponent field; clamped to [−87, 88]; ~1 ulp. (The x87 version this replaced cost ~150 cycles per element, 8 ms per token in SiLU alone.) |
| `k_dot_f32_row(rcx=row, rdx=x, r8=cols) -> xmm0` | one F32 row (ymm FMA) |
| `k_dot_f16_row(rcx=row, rdx=x, r8=cols) -> xmm0` | one F16 row (`vcvtph2ps` 8 at a time) |
| `k_argmax(rcx=x, rdx=n) -> rax` | first index of the maximum |
| `k_quant_x(rcx=x, rdx=n)` | activation quantisation, once per matvec input: per 256-block `g_d8[b]` = amax/127, `g_q8` = round(x/d8) as i8, `g_bsums` (i16 per 16) and `g_bsums32` (i16 per 32) sums of the rounded values. AVX2, ~1 instruction per value |
| `k_sum32`, `k_sum16` | (unused since the int8 path) |
| `k_dot_q4k_row(rcx=row, r8=cols) -> xmm0` | one Q4_K row against `g_q8` (see below) |
| `k_dot_q6k_row(rcx=row, r8=cols) -> xmm0` | one Q6_K row against `g_q8` |
| `k_matvec_job(rcx=MatvecJob*, rdx=tid, r8=nthreads)` | `pool_run` body: claims `job.chunk` rows at a time from `job.next` (lock xadd) until the rows run out, dispatching by type to the row dots; saves xmm6-15 once (the quantised dots use all sixteen ymm registers) |
| `k_rope_table(rcx=out, rdx=n_ctx, r8=head_dim, xmm0=theta)` | out[pos][0..half) = cos, [half..d) = sin, via x87 `fsincos`; publishes head_dim/half for `k_rope` |
| `k_rope(rcx=head f32*, rdx=table_row)` | NeoX rotation in place (half from the published global) |
| `k_attn_job(rcx=AttnJob*, rdx=tid, r8=nthreads)` | `pool_run` body: splits the query heads by tid; scores (÷√head_dim), softmax, weighted V sum per head. Per-thread score row at `scores + tid·g_n_ctx` |
| `kernels_cli(rcx=Model*)` (`--kernels`) | micro-benchmark through the real `model_matvec` path: the Q6_K output projection and the Q4_K token-embedding table (both 2048×151936, streaming from DRAM) and one 1.2 MB Q4_K tensor that stays in L2 (the kernel's compute rate); each prints rows, cols, type, iters, ms, effective GB/s and three check rows (kernel vs an f32 dot of the dequantised row); then the cost of an empty `pool_run` |

Quant dots work on the int8 activation. `k_quant_x` rounds x to i8 per 256-value
block (`d8` = amax/127) and keeps the exact sums of the rounded values per 16 and
per 32. Each dot then reproduces `model_dequant_row`'s bit extraction in SIMD and
does the q·q8 arithmetic in integers: `vpmaddubsw` (u8 code × s8 q8 → i16 pair
sums; |·| ≤ 2·63·127 fits) then `vpmaddwd` by the sub-block scale (i16, broadcast
with `vpshufb` from a register holding the block's scales) into eight i32 lanes
that accumulate over the block; one `vcvtdq2ps` + FMA by d·d8 per block, one
horizontal sum per row. Q4_K: `qs` chunk c gives sub-block 2c (low nibbles) and
2c+1 (high nibbles); dot += d8·(d·Σ sc[s]·Σ q·q8 − dmin·Σ m[s]·bsums32[s]), the
mins term one `vpmaddwd` of the 8 mins with the 8 per-32 sums. Q6_K: the four
32-value groups per half come from `ql`/`qh` with shifts and masks (`0x0F`,
`0x30`), each group vector holding two 16-value sub-blocks, one per 128-bit lane;
dot += d8·d·(Σ sc·q·q8 − 32·Σ sc[S]·bsums[S]), the offset term one `vpmaddwd` of
the 16 scales with the 16 per-16 sums. Nothing in the loops touches the stack: on
Gracemont a store followed by narrower loads from it stalled each block (~25%).
`vpmulld` is avoided (slow on this core), as are horizontal sums inside a block.
F16/F32 rows and the attention q·K / p·V use 8-wide ymm FMA.

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
| `main(rcx=argc, rdx=argv) -> eax` | `asmrun.exe <gguf> [-n N] [-t T] [-c CTX] [-p file] [-i] [--bw] [--cpuid] [--gguf] [--tokenize] [--kernels]`; `-i` reads prompt after prompt from stdin / the console (each generation starts at position 0, so the KV cache is simply overwritten) until an empty line |

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

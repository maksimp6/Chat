# asmrun — a from-scratch assembly runner for a GGUF Qwen3 model

`asmrun` runs a quantised Qwen3 model (the format Ollama stores on disk) and
generates text, written entirely in x86-64 assembly with **no libc, no linker,
and no third-party code**. It exists to answer one research question: *how
efficient can a language-model runner be on this CPU if nothing stands between
the code and the hardware?*

It is a research artifact, not a product. It targets exactly one model family
(Qwen3) and one platform (Windows x64 with AVX2 + FMA + F16C, e.g. the Intel
N100 this was written for). The architecture is specified in [DESIGN.md](DESIGN.md).

## Status: builds, runs, and generates correct text — but it is slow

It assembles with NASM 3.02 into a 28 KB PE, runs on the Intel N100, and
generates coherent output ("The capital of France is" → "Paris. The capital of
the United States is Washington, D.C."). Note that unsigned native code must be
allowed to run (e.g. Smart App Control off); the assembler and the produced
`asmrun.exe` are both unsigned.

Bring-up took three assembler-caught fixes: two three-register effective
addresses in `tokenizer.asm` (x86-64 allows only base+index) and one bitwise `&`
on a label-relative value in the hand-written PE header (`inc/pe.inc`, NASM
`-f bin` forbids it). The compute kernels assembled clean.

First measurement (qwen3:1.7b, 4 threads) — **correct but about 20× slower than
Ollama**:

| metric | asmrun | reference |
| --- | --- | --- |
| generation | 0.69 tok/s | Ollama 12.5–14.5 tok/s |
| effective matvec bandwidth | 0.94 GB/s | raw bus ~13.9 GB/s (`--bw`) |

The quantized matvec reaches only ~7% of memory bandwidth, so it is
**compute-bound, not bandwidth-bound**: the fused dequant does scalar work per
4-bit weight (int→float convert, multiply, dependent add) on a serialized
accumulator. The win is vectorising that inner dot — and `--cpuid` reports
`avx_vnni=1`, so this CPU has `vpdpbusd` (int8 dot-product), exactly the
instruction for an int8 activation path. That is the "second version" noted in
[DESIGN.md](DESIGN.md), now confirmed necessary by measurement rather than
assumed.

## Build

Needs [NASM](https://www.nasm.us) (3.x). There is no linker step: `asmrun.asm` is
one `nasm -f bin` translation unit that emits a complete PE executable.

```powershell
.\build.ps1
```

`build.ps1` finds `nasm` on `PATH` (or pass `-Nasm C:\path\to\nasm.exe`) and runs:

```bash
nasm -f bin -O2 -I inc/ -I src/ -o build/asmrun.exe asmrun.asm
```

If NASM cannot start, the machine is almost certainly blocking it (Smart App
Control / WDAC); build elsewhere.

## Run

```bash
asmrun.exe <model.gguf> -p prompt.txt -n 64 -c 1024 -t 4
```

- `<model.gguf>` is an Ollama model blob (see "Pointing it at an Ollama model").
- `-p FILE` reads the prompt from a UTF-8 file (otherwise it reads stdin).
- `-n` new tokens, `-c` context, `-t` threads.

Diagnostic modes (each needs the model path except `--cpuid`):

| Flag | What it does |
| --- | --- |
| `--cpuid` | print the detected ISA features and core count, then exit |
| `--bw` | measure raw memory read bandwidth (GB/s) with the thread pool |
| `--gguf` | parse and dump the GGUF metadata and tensor table |
| `--tokenize` | tokenise the prompt and print the ids |
| `--kernels` | micro-benchmark the quantised matvec: time repeated matvecs of the output projection and print rows, cols, iterations, ms and **effective GB/s** |

Generation prints the detected features, a model summary, and timing to **stderr**
(`load_ms`, `prompt_tok_s`, `gen_tok_s`, `weights_gb_per_token`, `effective_gb_s`);
the generated text goes to **stdout**, so you can redirect one without the other.

## Choosing the model: smallest wins

Pick the **most-compressed (smallest) Qwen3** you can, to keep RAM free. The
weights are memory-mapped **read-only**, so the runner's own resident footprint
stays small no matter the model size — but a smaller model also means fewer bytes
read per token, which is what sets the speed (see below). Good choices, smallest
first:

| Model | On-disk size (approx) |
| --- | --- |
| `qwen3:0.6b` | ~0.5 GB |
| `qwen3:1.7b` | ~1.4 GB |

The design notes and the default scratch sizing assume `qwen3:1.7b` (28 layers,
embedding 2048, FFN 6144, 16 query / 8 KV heads, head_dim 128, vocab 151936), but
nothing is hard-coded to it: every dimension is read from the GGUF metadata, so
`qwen3:0.6b` runs the same way and leaves more of the ~8 GB of RAM unused.

### Pointing it at an Ollama model

Ollama stores each model as a content-addressed blob. Resolve the GGUF blob from
the manifest (this is what [bench.ps1](bench.ps1) does automatically):

```powershell
$m = Get-Content "$env:USERPROFILE\.ollama\models\manifests\registry.ollama.ai\library\qwen3\1.7b" -Raw | ConvertFrom-Json
$layer = $m.layers | Where-Object { $_.mediaType -eq 'application/vnd.ollama.image.model' }
$blob = "$env:USERPROFILE\.ollama\models\blobs\" + ($layer.digest -replace ':','-')
```

`asmrun` reads that blob directly; it never writes it and never needs Ollama
running.

## Why efficiency here means memory bandwidth

Generating one token reads **every weight once**. Nothing reuses a weight within
a token, so a dense decode is bound by DRAM bandwidth, not arithmetic:

```
tokens/sec  ≈  memory_bandwidth  /  bytes_read_per_token
```

On this CPU the measured read bandwidth is about **23 GB/s** (`--bw`). With a
~1.4 GB model that puts the ceiling near **16 tok/s**; with a ~0.5 GB model
nearer **45 tok/s**. For reference, Ollama (llama.cpp) on the same machine and
model generates about **12.5–14.5 tok/s** — already close to that roofline, which
is the honest baseline to beat.

So the interesting number `asmrun` reports is `effective_gb_s`: how close the
quantised matvec gets to the DRAM roofline. Hand-written assembly does not change
the roofline; it only removes overhead between the code and it. The compute kernels
are AVX2 + FMA; the quantised dot fuses dequantisation into the dot and streams
each weight block exactly once. Transcendentals (the exp in SiLU/softmax, the
sin/cos of the RoPE table) use the x87 FPU for exactness — they are far from the
bottleneck, since per token they touch `O(n_ff)` and `O(seqlen)` values against
the hundreds of millions of weight bytes the matvec streams.

## Benchmark against Ollama

```powershell
.\bench.ps1 -Model qwen3:1.7b -Runs 3 -Threads 4
```

Runs `asmrun` and Ollama's `/api/generate` (raw, greedy, same token budget) on
the same prompts and prints a median side-by-side table of `gen_tok_s`,
`prompt_tok_s` and `asmrun`'s `effective_gb_s`, plus the model's bytes-per-token.

## Layout

```
asmrun.asm        top level: sets org/bits, %includes everything in order
build.ps1         assemble with NASM (no linker)
bench.ps1         compare against Ollama on the same model + prompts
DESIGN.md         full architecture, struct layouts and per-function contracts
inc/
  win.inc         Win32 constants, the winimp() import-call macro
  common.inc      Tensor/Layer/Model/MatvecJob/AttnJob structs, GGML/GGUF consts, WINPROLOGUE
  pe.inc          hand-written PE32+ header + kernel32 import tables (the likely first-build fix)
src/
  runtime.asm     entry, I/O, VirtualAlloc, file mapping, QPC timing, CPUID gate, thread pool, --bw
  gguf.asm        GGUF v3 parse into the Model struct
  kernels.asm     matvec over Q4_K/Q6_K/F16/F32, RMSNorm, RoPE, attention, SwiGLU, argmax, sums
  tokenizer.asm   byte-level BPE encode/decode (qwen2 pre-tokeniser)
  model.asm       the Qwen3 forward pass, KV cache, generation loop, and CLI
```

`build/` (the assembled `asmrun.exe`) is git-ignored. The model blob is never
committed.

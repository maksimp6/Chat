# asmrun — a from-scratch assembly runner for a GGUF Qwen3 model

`asmrun` runs a quantised Qwen3 model (the format Ollama stores on disk) and
generates text, written entirely in x86-64 assembly with **no libc, no linker,
and no third-party code**. It exists to answer one research question: *how
efficient can a language-model runner be on this CPU if nothing stands between
the code and the hardware?*

It is a research artifact, not a product. It targets exactly one model family
(Qwen3) and one CPU class (x86-64 with AVX2 + FMA + F16C, e.g. the Intel N100 this
was written for), as a Windows console program or as a **UEFI application that
boots from a USB stick with no operating system at all** (same sources, a
different runtime half). The architecture is specified in [DESIGN.md](DESIGN.md).

## Status: builds, runs, generates correct text, and runs at the memory bus

It assembles with NASM 3.02 into a 32 KB PE, runs on the Intel N100, and
generates coherent output ("The capital of France is" → "Paris. The capital of
the United States is Washington, D.C. The capital of the United Kingdom is
London."). Note that unsigned native code must be allowed to run (e.g. Smart App
Control off); the assembler and the produced `asmrun.exe` are both unsigned.

On qwen3:1.7b with 4 threads it generates **17–18.3 tokens/s**, reading the
1.178 GB of weights a token needs at up to **21.5 GB/s** — 94% of the 22.9 GB/s
the machine's memory bus delivers to a plain streaming read (`--bw -t 4`).
Ollama (llama.cpp) on the same machine and model generates **16–17.8 tokens/s**
across runs. The side-by-side script (`bench.ps1`, median of 3 runs of 64 new
tokens each) measured asmrun 17.1 vs Ollama 16.1 tok/s on a 5-token prompt and
16.9 vs 17.2 on a 25-token prompt: the two runners are at parity, both bound by
the same bus, and the differences are inside the run-to-run noise of a desktop
machine (±5%). asmrun is far behind on the *prompt* phase (17 tok/s against
Ollama's 74+, which evaluates the prompt as one batch); see the benchmark notes.

The no-OS build (`build\BOOTX64.EFI`, 45 KB) assembles and its PE header is what
the EDK2 loader expects, but it has **not been booted yet**: there is no VM on
the authoring machine, so the first run is the real one (see "Boot it from a USB
stick"). What it will print on that run is laid out there.

Bring-up took three assembler-caught fixes: two three-register effective
addresses in `tokenizer.asm` (x86-64 allows only base+index) and one bitwise `&`
on a label-relative value in the hand-written PE header (`inc/pe.inc`, NASM
`-f bin` forbids it). One more was caught at run time: NASM silently encodes an
operand form that exists only in AVX-512 (`vpsrlw ymm, [mem], imm`) as EVEX,
which the N100 rejects with an illegal-instruction exit; the build now gets a
listing scan for `62`-prefixed instructions.

### Performance arc (qwen3:1.7b, 4 threads), generation always correct

| version | gen tok/s | effective GB/s | what changed |
| --- | --- | --- | --- |
| v1: scalar fused dequant | 0.69 | 0.8 | one int→float convert and a dependent add per weight |
| v2: AVX2 vectorised dequant+dot | 2.17 | 2.6 | unpack 4-/6-bit codes with SIMD, dot 8-wide with FMA |
| v3: fold scale + 4 FMA accumulators | 4.83 | 5.7 | reduce once per 256-value block, not per sub-block |
| v4: int8 `vpdpbusd` for Q4_K | 6.81 | 8.0 | quantise the activation to int8 once per matvec |
| v5: int8 `vpdpbusd` for Q4_K and Q6_K | 8.3 | 9.8 | — but still a horizontal sum per 16 weights and `vpmulld` per scale |
| v6: `vpmaddubsw`/`vpmaddwd`, 8-lane integer accumulate | 16.85 | 19.9 | the llama.cpp shape: no reduction inside a block, no `vpmulld`; exp and quantisation vectorised |
| v7: scales kept in registers | 18.3 | 21.5 | no stack round trip per block (Gracemont stalled on it: +38% in-cache) |
| v8: dynamic row chunks | 17–18.3 | 20–21.6 | threads claim rows with `lock xadd`; a stalled thread no longer stalls the matvec |
| Ollama (llama.cpp), same model | 16–17.8 | 19–21 | |
| memory bus (`--bw -t 4`) | 19.4 (ceiling) | 22.9 | |

Effective GB/s is the weight bytes a token reads (1.178 GB: every tensor except
the token-embedding table, of which a token reads one row) divided by the time
per token. Earlier versions of this table counted the embedding table too
(1.353 GB); the rows above are restated in the corrected metric.

What the arc taught, in order of effect:

- **Shape of the integer dot (v5 → v6, 2×).** Doing `vpdpbusd` per 16-value
  sub-block and then a horizontal sum and a scalar multiply by the sub-block's
  scale costs ~250 instructions per 256-value block. `vpmaddubsw` (u8 weight ×
  s8 activation → i16 pair sums) followed by `vpmaddwd` by the scale broadcast as
  i16 lands scaled i32 partial sums in eight lanes that just accumulate over the
  block; one convert and one FMA per block, one horizontal sum per row. The min
  and −32 offset terms become one `vpmaddwd` each against per-16 / per-32 sums of
  the int8 activation. `vpmulld` is never used — it is slow on this core.
- **Stack traffic inside the block loop (v6 → v7, +9% end to end, +38% with the
  weights in L2).** v6 unpacked the 6-bit scales in scalar code, stored them, and
  broadcast them back with `vpbroadcastd` loads. On Gracemont a narrow load from
  a wider, very recent store waits for the store to retire; two of those per
  block cost ~25 cycles of a ~70-cycle block. The scales are now unpacked in xmm
  registers (i16 lanes, so shifts cannot leak between bytes) and broadcast with
  `vpshufb` from a register holding them in both lanes. Nothing in either kernel
  touches memory except the weights, the activation and constants.
- **x87 is not free (v6).** `f2xm1`/`fscale` per element made SiLU cost ~8 ms
  of an 80 ms token. A cephes-style polynomial exp on eight lanes (`EXP8`) costs
  0.3 ms. Activation quantisation went from ~10 scalar instructions per value to
  ~1 with AVX2 (`vcvtps2dq`, two `vpackssdw`, `vpacksswb`, one `vpermd` to undo the
  lane interleave; the per-16 sums fall out of the i16 stage with `vpmaddwd` by
  ones).
- **Memory, not compute, is now the limit.** The Q4_K kernel runs at 28 GB/s on
  4 threads when its tensor sits in L2 and 21.9 GB/s from DRAM; Q6_K 22.7 GB/s
  from DRAM; the bus gives 22.9. Per token, 97% of the time is in the matvecs
  (`prof_ms` line below); the rest is ~1.5 ms of attention, norms, quantisation,
  SiLU and argmax.

Every row above is checked: `--kernels` compares three rows of each benchmarked
tensor against a plain f32 dot of the dequantised row and they agree to the
printed precision, and generation text is unchanged across v6–v8.

## Build

Needs [NASM](https://www.nasm.us) (3.x). There is no linker step: `asmrun.asm` is
one `nasm -f bin` translation unit that emits a complete PE32+ image, twice:

```powershell
.\build.ps1              # both images; -Target win or -Target efi for one
```

```bash
nasm -f bin -O2 -I inc/ -I src/ -o build/asmrun.exe asmrun.asm                  # Windows console program
nasm -f bin -O2 -I inc/ -I src/ -D ASMRUN_EFI -o build/BOOTX64.EFI asmrun.asm   # UEFI application (no OS)
```

`build.ps1` finds `nasm` on `PATH` (or pass `-Nasm C:\path\to\nasm.exe`), keeps
the listings in `build\*.lst`, scans them for EVEX (AVX-512) encodings — NASM
emits those without complaint for operand forms that have no VEX encoding, and
the N100 cannot execute them — and prints the PE header of each image. If NASM
cannot start, the machine is almost certainly blocking it (Smart App Control /
WDAC); build elsewhere.

## Run

```bash
asmrun.exe <model.gguf> -p prompt.txt -n 64 -c 1024 -t 4
```

- `<model.gguf>` is an Ollama model blob (see "Pointing it at an Ollama model").
- `-p FILE` reads the prompt from a UTF-8 file (otherwise it reads stdin).
- `-n` new tokens, `-c` context, `-t` threads.
- `-i` interactive: a prompt per line, generation and statistics after each,
  until an empty line (or end of input). This is what the USB stick runs.

## Boot it from a USB stick (no operating system)

The same runner boots directly from the firmware: no Windows, no Linux, no
loader of ours. The stick carries three files, and `make-usb.ps1` puts them
there (it formats only with `-Format`, after a confirmation):

```powershell
.\build.ps1 -Target efi
.\make-usb.ps1 -Drive E:                          # copies BOOTX64.EFI, model.gguf (1.36 GB) and asmrun.cfg
.\make-usb.ps1 -Drive E: -Args "model.gguf -p prompt.txt -n 64 -t 4"   # a one-shot run instead of the prompt loop
```

```
\EFI\BOOT\BOOTX64.EFI   the runner (the firmware's default boot file name)
\model.gguf             the Ollama blob of qwen3:1.7b
\asmrun.cfg             the arguments, in the Windows command-line syntax
```

Then on the PC: turn **Secure Boot off** in the firmware setup (the image is
unsigned; with Secure Boot on the firmware refuses it before a byte of ours
runs) and boot from the stick (the boot-device menu, or the stick first in the
boot order). The stick must be **FAT32**: the firmware reads nothing else.

What the screen shows, in order: the banner, `AVX state enabled`, `tsc_mhz=`,
`boot volume opened`, the arguments, `reading 1296 MB from the boot volume`
with a dot per 32 MB and the MB/s (the firmware's USB driver sets the pace; a
minute is normal), the model summary with `threads=`, `processors=4 (the boot
processor and 3 application processors through EFI_MP_SERVICES_PROTOCOL)`,
`load_ms`, and a `>` prompt. Type a prompt, press Enter: the text is generated
on all four cores and the same statistics lines as on Windows follow
(`gen_tok_s`, `effective_gb_s`, `prof_ms`). An empty line or Esc ends the
session; the exit code is printed, a key press returns to the firmware's boot
manager, which continues with the next boot option. Every step before the model
loads prints one line and every firmware error its EFI status, so if the boot
stops, the last line says where.

`asmrun.cfg` takes the same arguments as the Windows command line, so the
diagnostic modes work bare-metal too: `model.gguf --bw` for the memory bus
without an OS, `model.gguf --kernels` for the kernels. On the first boot the
numbers to compare with the Windows runs are `tsc_mhz`, `load_ms` (now a read
from the stick), `gen_tok_s` and `effective_gb_s`.

Known assumptions (DESIGN.md, "No operating system"): the firmware provides
`EFI_MP_SERVICES_PROTOCOL` (EDK2-derived firmware such as AMI Aptio does;
without it the runner says so and uses one core), XSAVE for enabling AVX state,
and leaves the application processors to us for the whole run. Nothing here is
Windows-specific: the runtime half is 900 lines of UEFI calls, and the rest of
the program is the same bytes as `asmrun.exe`.

Diagnostic modes (each needs the model path except `--cpuid` and `--bw`):

| Flag | What it does |
| --- | --- |
| `--cpuid` | print the detected ISA features and core count, then exit |
| `--bw` | measure raw memory read bandwidth (GB/s) with `-t` threads |
| `--gguf` | parse and dump the GGUF metadata and tensor table |
| `--tokenize` | tokenise the prompt and print the ids |
| `--kernels` | micro-benchmark the quantised matvec through the real `model_matvec` path: the Q6_K output projection and the Q4_K token-embedding table (both stream from DRAM) and a 1.2 MB Q4_K tensor that stays in L2 (the kernel's compute rate). Each prints rows, cols, type, iterations, ms, **effective GB/s** and three check rows (`kernel=` vs `ref_f32=`); then the cost of an empty `pool_run` |

Generation prints the detected features, a model summary, timing (`load_ms`,
`prompt_tok_s`, `gen_tok_s`, `weights_gb_per_token`, `effective_gb_s`) and a
per-phase profile (`prof_ms quant= qkv= attn= wo= gateup= silu= down= out=
norm_add= argmax=`, totals over all forwards) to **stderr**; the generated text
goes to **stdout**, so you can redirect one without the other.

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

Ollama's `qwen3:1.7b` blob mixes types: 155 Q4_K tensors (most of the weight),
15 Q6_K (the output projection and half the `ffn_down`s), 28 F16 (`attn_v`) and
the F32 norms. asmrun reads each in its stored type; the kernels are chosen per
tensor.

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

On this CPU the measured read bandwidth is **22.9 GB/s** with four threads
(`--bw -t 4`; a single core manages 15.5). With the 1.178 GB a qwen3:1.7b token
reads that puts the ceiling near **19.4 tok/s**; with a ~0.5 GB model nearer
45 tok/s. Ollama (llama.cpp) generates about 17.8 tok/s here — 92% of that
roofline — and asmrun 17–18.3, up to 94%. Neither can go much further on this
machine without reading fewer bytes (a smaller or more compressed model), which
is also why the hand-written kernels stop mattering once they are fast enough:
below the roofline, kernel quality decided everything (v1–v7 above); at it, the
bus does.

So the interesting number `asmrun` reports is `effective_gb_s`: how close the
quantised matvec gets to the DRAM roofline. Hand-written assembly does not change
the roofline; it only removes overhead between the code and it. The compute
kernels are AVX2 + FMA; the quantised dot works on an int8 copy of the
activation and streams each weight block exactly once. Only the RoPE table's
sin/cos (once at load) still uses the x87 unit.

## Benchmark against Ollama

```powershell
.\bench.ps1 -Model qwen3:1.7b -Runs 3 -Threads 4
```

Runs `asmrun` and Ollama's `/api/generate` (raw, greedy, same token budget) on
the same prompts and prints a median side-by-side table of `gen_tok_s`,
`prompt_tok_s` and `asmrun`'s `effective_gb_s`, plus the model's bytes-per-token.
Note the prompt phase: Ollama evaluates the prompt as one batch (weights read
once for all prompt tokens), asmrun one token at a time, so `prompt_tok_s` is
where Ollama is far ahead and where asmrun's next large win would be.

## Layout

```
asmrun.asm        top level: org, the .text/.data sections, %includes everything in order
build.ps1         assemble both images with NASM (no linker), EVEX scan, PE header dump
make-usb.ps1      put BOOTX64.EFI, the model and asmrun.cfg on a FAT32 USB stick
bench.ps1         compare against Ollama on the same model + prompts
DESIGN.md         full architecture, struct layouts and per-function contracts
inc/
  win.inc         Win32 constants, the winimp() import-call macro
  efi.inc         UEFI table/protocol offsets and constants
  common.inc      Tensor/Layer/Model/MatvecJob/AttnJob structs, GGML/GGUF consts, WINPROLOGUE
  pe.inc          hand-written PE32+ header: kernel32 import tables (Windows) or the no-op relocation block (UEFI)
src/
  runtime.asm     shared: console formatting, CPUID gate, thread pool (pool_worker/pool_run), --bw, helpers
  win.asm         Windows: entry, WriteFile, VirtualAlloc, file mapping, QPC, CreateThread
  efi.asm         UEFI: entry, ConOut/ConIn, AllocatePages, file reads from the stick, TSC, EFI_MP_SERVICES workers
  gguf.asm        GGUF v3 parse into the Model struct
  kernels.asm     int8 matvec over Q4_K/Q6_K, F16/F32 rows, activation quantisation, RMSNorm, RoPE, attention, SwiGLU, exp, argmax
  tokenizer.asm   byte-level BPE encode/decode (qwen2 pre-tokeniser)
  model.asm       the Qwen3 forward pass, KV cache, generation loop, phase profile, and CLI
```

`build/` (the assembled `asmrun.exe` and `BOOTX64.EFI`) is git-ignored. The
model blob is never committed.

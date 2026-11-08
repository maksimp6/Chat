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

## Status: runs at the memory bus on Windows, and from a USB stick with no OS

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

The no-OS build (`build\BOOTX64.EFI`, 48 KB) **has booted and run.** On
2026-11-06 a DEXP MINI (Intel N100, AMI Aptio firmware) booted it from a FAT32
USB stick with Secure Boot off, read qwen3:1.7b from the stick at 141 MB/s,
started all four cores through `EFI_MP_SERVICES_PROTOCOL`, and generated correct
text at **14–16.8 tok/s** (effective 16.5–19.5 GB/s). That is at parity with the
Windows build (17–18.3 tok/s) and bound by the same memory bus, **not faster**:
the runner already reads at 94% of the raw bus on Windows, so there was no OS tax
to reclaim, and with boot services still live the firmware's own timer and USB
interrupts land on the boot core mid-kernel, which accounts for the lower end and
the run-to-run variance. On bare metal the runner owns the clock: every processor
enables Hardware P-states and requests its highest level, since without an OS
nothing else would raise the cores above the firmware's base ratio (800 MHz on
the N100). The `cpu clock:` line scrolled off before it was photographed on this
boot, so one question is open: whether the rigid min=max HWP pin sustains the
N100's clock under its power limit as well as Windows' adaptive governor, or
whether a low floor with a high ceiling (what Windows does) would sustain more.
A bare-metal `--bw` run and a photo of the top lines settle it.

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
- `-i` interactive: a prompt per line (on a Windows console each Enter submits
  the line), generation and statistics after each, until an empty line (or end
  of input). A prompt longer than the context is refused with a message and the
  loop goes on.
- `--chat` wraps each line as a Qwen3 chat turn
  (`<|im_start|>user\n…<|im_end|>\n<|im_start|>assistant\n`) with `/no_think`
  appended, so the model answers as an assistant and stops at `<|im_end|>`
  (this GGUF's eos) instead of continuing raw text to the `-n` cap. Without it,
  `-i` and `-p` do raw completion (what the benchmarks use). The USB stick runs
  `-i --chat -n 512`.

## Picture viewer

The first piece of text-to-image generation in asmrun: a viewer for the
pictures the image model will produce, in both builds, from the same code
(`src/view.asm` plus `rt_view_show` in each platform half).

```bash
asmrun.exe --testcard            # a 512x512 test card
asmrun.exe --view picture.bmp    # an uncompressed 24- or 32-bit BMP
```

- A picture in memory is 32-bit BGRX, top row first: the layout of a top-down
  GDI DIB and of `EFI_GRAPHICS_OUTPUT_BLT_PIXEL`, so neither screen converts it.
- **Windows:** a window (user32 + gdi32, imported by the hand-written PE header)
  sized to the picture, or to 7/8 of the screen for a larger one; it can be
  resized or maximised and redraws the picture fitted and centred on black,
  nearest neighbour. Esc or the close button returns.
- **No operating system:** the whole screen through
  `EFI_GRAPHICS_OUTPUT_PROTOCOL` (one `Blt` of a screen-sized buffer), the
  picture fitted and centred on black; a key press clears back to the text
  console. In `asmrun.cfg`: `--view picture.bmp` (a file on the stick) or
  `--testcard`.
- Fitting: a picture that fits is enlarged by the largest whole factor (a
  512x512 picture is 1024x1024 on a 1080-line screen); a larger one is shrunk,
  keeping its proportions.
- The test card's top three quarters are a gradient (red rises left to right,
  blue falls, green rises downwards), its bottom quarter eight bars: white,
  yellow, cyan, green, magenta, red, blue, black. A swapped channel or a flipped
  picture shows at once.

## Text to image: SD-Turbo in assembly

asmrun generates pictures from text: [SD-Turbo](https://huggingface.co/stabilityai/sd-turbo)
(Stable Diffusion 2.1 distilled to one step) with every part of the pipeline
in this program, on the N100's four cores. The first picture, "a photo of a
cat sitting on a wooden table, sharp focus" (seed 42), took **16.8 s**: text
encoder 0.6 s, the UNet step 13.9 s, the decoder 2.3 s.

```bash
asmrun --image --sd sd_turbo-f16-q8_0.gguf --taesd taesd.safetensors --vocab vocab.json --merges merges.txt
asmrun --gen   --sd ... --taesd ... --vocab ... --merges ... -p prompt.txt -o picture.png [--seed N] [--vpred]
```

`--image` is the window with the prompt box and the Generate button (the
picture appears above the box; on the USB stick an `image>` line, the picture
on the whole screen until a key). `--gen` makes one picture of the prompt and
writes it as PNG or BMP (by the extension). The model files are loaded on the
first prompt. The files (never committed; the paths are given on the command
line or in `asmrun.cfg`): `sd_turbo-f16-q8_0.gguf` (Green-Sky/SD-Turbo-GGUF,
2,023,745,376 bytes: 614 f32, 170 f16, 539 q8_0 tensors), CLIP's `vocab.json`
and `merges.txt` (stabilityai/sd-turbo), and `taesd.safetensors`
(madebyollin/taesd's `diffusion_pytorch_model.safetensors`, 9.8 MB).

The pipeline (`src/image.asm`), SD-Turbo's single step:

| Stage | Module | What it is |
| --- | --- | --- |
| tokens | `cliptok.asm` | CLIP's byte-level BPE; "a photo of a cat" is `49406 320 1125 539 320 2368 49407` |
| context | `clipenc.asm` | OpenCLIP ViT-H text tower, 23 of 24 layers (the penultimate) + final LayerNorm: 77 x 1024 |
| noise | `image.asm` | N(0,1) 64x64x4 from `--seed` (xorshift64*, twelve uniforms summed) |
| eps | `unet.asm` | the SD 2.x UNet at t = 999: ResBlocks, SpatialTransformers (self-, cross-attention, GEGLU), down/upsampling |
| x0 | `image.asm` | `(noise - sqrt(1-a) eps) / sqrt(a)`, a = alphas_cumprod[999] of the scaled-linear schedule (`--vpred` for v-prediction) |
| picture | `taesd.asm` | the Tiny AutoEncoder's decoder: convolutions and ReLUs, 64x64 -> 512x512 |

How it is computed (`src/sdk.asm`, `src/unet.asm`):

- Activations are padded NHWC (a one-pixel zero border), so a 3x3 convolution
  is nine matrix multiplies over all pixels at once, each tap the same buffer
  shifted by a constant; the weights are gathered to f32 `[tap][cout][cin]`
  per convolution.
- The matrix multiply (`sg_matmul`) is built for the cache: a 3x4 output tile
  in all 16 ymm registers; a subpanel of 4 weight rows in an L1-sized slice
  (16 KB) while 3 activation rows pass over it; groups of 16 weight rows
  (64 KB) in L2, dequantised once; the column groups split over every core.
  It is checked against a scalar reference (`--clipenc` prints both) and gives
  bit-identical results on 1 and 4 cores.
- Attention is two matrix multiplies per head (Q K^T, then P V over the
  transposed values) with a vectorised softmax between; GroupNorm keeps its
  statistics in f64; GELU is exact (erf); SiLU and exp are the AVX2 polynomial.
- Diagnostics along the way: `--sdinfo FILE.gguf` lists any GGUF's tensors,
  `--cliptok` prints the token ids, `--clipenc` runs the text encoder alone and
  prints the kernel check, the time and the context's statistics.

Not done yet: int8 (AVX-VNNI) for the q8_0 layers, the UNet's softmax and
GroupNorm on one core only, no thread pinning on Windows, and the no-OS
build's graphics path has assembled but not yet booted.

## Qwen3.5 (hybrid Gated DeltaNet + attention)

Since 2026-11-07 the runner also runs the **Qwen3.5** family (`general.architecture
= qwen35`): 3 linear-attention layers (Gated DeltaNet: `attn_qkv`, the depthwise
causal conv, l2-normed q/k, the recurrent delta rule over a 128×128 state per
value head, the gated RMSNorm, `ssm_out`) for every full-attention layer (fused
q+gate projection, q/k norms, partial rotary over 64 of 256 dims, θ = 1e7,
sigmoid output gate), SwiGLU FFN, Q6_K / Q8_0 / F32 tensors as the GGUF stores
them, tied embeddings when `output.weight` is absent. `src/q35.asm` holds the
layers, forward pass, generation and the ChatML chat (`<|im_start|>`,
`<|im_end|>`, `<think>`, `</think>` as the vocabulary's own ids; thinking off),
`src/q35k.asm` the kernels the family adds. Verified on
`Qwen3.5-0.8B-Q6_K.gguf` ("What is the capital of France?" → "Paris is the
capital of France."; a name given in turn one is recalled in turn two; Cyrillic
output decodes) and `Qwen3.5-9B-Q6_K.gguf` (coherent sentences; the 7.46 GB file
pages from the SSD on the 8 GB Windows machine, so its speed is only measurable
from the stick). `--trace` prints every layer's residual statistics and the
logits' argmax, the tool that found the port's first bug (a register clobbered
across the exp macro in the conv step). The second was only visible on the 4B
and 9B, whose 32 value heads share 16 key heads: in this GGUF layout value head
*v* reads key head *v mod 16* (the heads are tiled), not *v / 2* as the
reference's `repeat_interleave` reads; the 0.8B (16 of each) could not show it.
With the fix the 4B answers "The capital of France is Paris." and lists
"Paris – France, Berlin – Germany, Madrid – Spain" on request.

**First bare-metal run of a 9B (2026-11-07 evening, DEXP MINI N100, 8 GB
DDR4-3200, the stick above):** `Qwen3.5-9B-Uncensored-HauhauCS-Aggressive`
Q6_K (7.36 GB in two FAT32 parts) loaded next to the firmware in 7.74 GiB of
RAM and wrote a 731-token answer at **2.91 tok/s**, reading 6.514 GB of
weights per token at **18.97 GB/s effective** (`bus_util_pct=88.2` of the 22.9
GB/s bus). The waste line: `waste_ms_per_token=59.0 floor=284.5 total=343.5
serial=21.0 barrier=1.2 jobs_per_token=257`; the tick line: boot core idle
0.35%, the three application processors 11.9% each, `wasted_ticks_pct=9.0`,
`ipc_in_jobs_per_core=1.08 1.15 1.15 1.15` (the fixed counters, ring 0). So
the 9B sits at 83% of the bus ceiling (22.9 / 6.514 = 3.52 tok/s); what is
left is the 21 ms of serial work per token (the 248,320-way argmax, the norms,
the recurrent states on one core) and the barriers the workers wait at.

Second boot the same evening, after the linear layers went parallel (the delta
rule, the conv and the q/k norms as pool jobs over the cores; qkv+z+beta+alpha
as one fused matvec): **2.96 tok/s, 19.31 GB/s effective, waste 52.9 ms/token,
serial 7.6 ms, barrier 1.2 ms, worker idle 8.1%, wasted ticks 6.2%**. The
build after that also fuses q(+gate)/k/v and gate/up (185 jobs per token on
the 4B instead of 233).

**The dialog is saved on the stick.** Everything the runner prints while a
Qwen3.5 chat runs — the model summary, every `> ` prompt with the line typed,
the answer, the statistics lines — is copied into a 1 MB buffer and appended
to `\dialog.txt` on the boot volume (the current directory on Windows) at the
end of every turn, through the firmware's own FAT driver (`rt_append_file`:
open read+write+create, seek to the end, write, flush, close). A write that
fails is reported once and the chat goes on.

**Execution trace.** `--trace` (in `asmrun.cfg` or on the command line) also
prints, for the first generated token of every turn, one line per step of the
forward pass: `asmrun: exec L<layer> <step> us=<microseconds> [kb=<weight
bytes/1024> gb_s=<bytes/ns>]` — embed, norm+sums, the fused projection, the
conv, the delta rule, sums, ssm_out, ffn_gate+up, silu, ffn_down, add, and for
the attention layers the q/k/v projection, qk_norm+rope, the attention job,
the gate, attn_output; then output_norm, lm_head and argmax. On the 0.8B every
weight read runs at 18–22 GB/s, the conv takes 50 µs and the delta rule 100 µs
per layer on four cores, a norm 10–30 µs. Those lines go into `dialog.txt`
too, so a boot can be checked step by step from the file afterwards.

## Boot it from a USB stick (no operating system)

The same runner boots directly from the firmware: no Windows, no Linux, no
loader of ours. The stick carries three files, and `make-usb.ps1` puts them
there (it formats only with `-Format`, after a confirmation):

```powershell
.\build.ps1 -Target efi
.\make-usb.ps1 -Drive E:                          # copies BOOTX64.EFI, model.gguf (1.36 GB) and asmrun.cfg
.\make-usb.ps1 -Drive E: -ModelPath C:\Users\Claude\models\Qwen3.5-9B-Q6_K.gguf   # a GGUF file: written in FAT32-sized parts
.\make-usb.ps1 -Drive E: -Args "model.gguf -p prompt.txt -n 64 -t 4"   # a one-shot run instead of the prompt loop
```

```
\EFI\BOOT\BOOTX64.EFI   the runner (the firmware's default boot file name)
\model.gguf             the model; a model over 4 GiB continues in \model.gguf.1, \model.gguf.2, ...
\asmrun.cfg             the arguments, in the Windows command-line syntax
```

A second model on the same stick: `.\make-usb.ps1 -Drive E: -ModelPath
<file> -Name science.gguf` writes it as `science.gguf` (+ `.1`, …) and leaves
`asmrun.cfg` alone (the script rewrites the cfg only when `-Args` is given);
the cfg's first word picks the file that boots. On the stick of 2026-11-07:
`model.gguf` = the uncensored Qwen3.5-9B, `science.gguf` = Intern-S1-mini
(Shanghai AI Lab's 8B Qwen3 body pretrained on 2.5T scientific tokens;
`qwen3` architecture, so it runs on the runner's original path: "the boiling
point of ethanol … 78.37 °C … hydrogen bonds weaker than in water" on its
first prompt).

**A menu of models.** When `asmrun.cfg` names no model (the stick's cfg is now
`-i --chat -n 1024 -c 2048 --trace --system system.txt`), the runner lists
every `*.gguf` on the boot volume with its size (a model's `.gguf.N` parts
counted in) as a numbered list and asks `model number:`. The stick of
2026-11-07 carries, under names that say what they are: `qwen35-9b-uncensored`
(the talker: HauhauCS Aggressive, 0 refusals), `qwen35-9b-stock`,
`qwen35-4b-stock`, `qwen35-0.8b-fast`, `intern-s1-mini-science`,
`omnicoder-9b-agent` (Qwen3.5-9B on 425K frontier agent traces),
`qwen38-9b-reasoning` (the Qwen3.8 distill, thinks first),
`qwen35-9b-opus-distill`, `ii-medical-8b`, `deepseek-r1-qwen3-8b-math`,
`qwen35-9b-unredacted`. All are Qwen3 or Qwen3.5 bodies, so one `BOOTX64.EFI`
runs them all.

**Q5_K and NextN.** The Qwen3.8-9B distill's GGUF is Q5_K_M (223 Q5_K tensors,
the embedding table among them) and carries its MTP draft head as a 33rd block
(`qwen35.nextn_predict_layers = 1`, `blk.32.nextn.*`). The runner now has a
Q5_K dot kernel (`k_dot_q5k_row`: Q4_K's int8 kernel with the extra high bit
shifted into place and masked, as Q6_K does; checked against the scalar dequant
on three rows, 17 GB/s on Windows with a copy running) and a Q5_K row dequant,
and the loader subtracts the NextN blocks from the layer count (the draft head's
tensors are read but unused — speculative decoding's weights, for later).

**What the counters said (boot of 2026-11-08 02:14, Qwen3.8-9B Q5_K_M, 5.93
GB per token, the firmware console).** Per core inside the jobs: `ghz_in_jobs
= 2.095` on all four (the chip sits at its power limit under load; the
boot-time 2116 MHz was the same thing), `ipc = 1.25–1.31`, `mem_stall_pct =
15.8–17.3`, `dram_stall_pct = 8.9–9.4`. So 83% of the job cycles are issuing
instructions and only 9% wait on DRAM: at 2.1 GHz the Q5_K/Q6_K kernels are
instruction-bound, which is why they reach 17–21 GB/s and not 22.9. The
exec trace of one token (the print time now excluded) puts a Q6_K projection
at 20–21 GB/s, Q5_K ones at 17–19, the delta rule at 213 µs per layer, the
conv at 50, the attention job at 0.9 ms, the sampler at 79 µs, the lm_head at
40 ms (815 MB at 20.7 GB/s). The statistics window now starts after the
traced token (its printing had shown up as 120 ms of "serial" waste), and
`--trace` is off in the stick's cfg: it costs the console ~25 s per turn.
The other number from that boot: the 735-token first prompt (tools block +
system prompt) took 232 s at 3.17 tok/s — every prompt token is a full weight
read, so prompt batching is the next lever, worth ~4× on prompts.

**Prompt batching (2026-11-08).** A prompt is fed in slices of up to 32
tokens (`ACT_SLOTS`), and every weight is read once per slice: the int8
activation buffers became 32 slots (`ActSlot`, `k_quant_slot`), each dot kernel
takes its slot as an argument, and the matvec job dots every row with every
slot while the row sits in L1 (`MatvecJob.nx`). The parts that depend on token
order — the causal conv, the delta rule, the attention — loop over the tokens
inside their head jobs (`src/q35f.asm`: `q35_forward_batch`; generation is the
batch of one). Only the last token's logits are computed. The greedy answer to
the same 616-token prompt is byte-identical to the one-token path, and the
0.8B's prompt rate on Windows went from 23 to 58 tok/s; the 9B's 232-second
first prompt on the stick should drop to roughly 100 s at the 2.1 GHz the
cores hold (the kernels are instruction-bound there, so the gain is bounded
by compute, not by the 32× fewer weight reads). The Qwen3 path
(`model_forward`) is not batched yet.

**The model's file tools.** Every Qwen3.5 conversation now opens with the
chat template's own tools block declaring four functions on the stick:
`list_files()`, `read_file(name, offset)` (4000 bytes per call), `write_file(name,
content)`, `append_file(name, content)`. When the assistant's turn contains a
`<tool_call>…</tool_call>` in the template's format, `src/tools.asm` runs it
through the firmware's FAT driver (`rt_list_root`, `rt_read_optional`,
`rt_write_file` — delete then create — and `rt_append_file`), prints the
result under `[tool result]`, feeds the template's tool turn
(`<|im_start|>user\n<tool_response>\n…\n</tool_response><|im_end|>`) and lets
the model go on; up to 32 calls per typed line. Names are plain root file
names; the models, `asmrun.cfg`, `system.txt` and `dialog.txt` cannot be
written. Verified on the 0.8B: "Create a file named notes.txt containing …" →
a `write_file` call, `wrote 20 bytes to notes.txt`, the file on disk.

**The model behind HTTP: a page for the phone and an API.** `--http PORT`
(`src/http.asm`) makes the chat loop take its prompts from the network: the
runner listens, serves `GET /` (a one-column chat page: the log above, the
input below, Enter sends) and answers `POST /api/chat` whose body is the
prompt, plain UTF-8 text, with the model's answer as plain text. Everything
the model writes to stdout during the turn is captured (`http_capture`, a
hook in `rt_write_h`) and sent back when the loop asks for the next prompt;
the statistics stay on stderr, so they are not in the response. One
connection at a time, the conversation continues across requests like on the
console. The transport is the platform's: `src/tcp4.asm` on the firmware,
`EFI_TCP4_PROTOCOL` from the BIOS network stack on the Ethernet port (the
station address is the firmware's DHCP one, waited for up to 30 s, or `--ip
a.b.c.d --mask m --gw g`), printing `http: listening on http://a.b.c.d:80`;
when the firmware has no TCP4 the message says so and the console takes the
prompts. `src/wsock.asm` is the Windows bench (Winsock); verified on this PC
with the 0.8B: two turns from the page, the second one remembering the
first. The stick's cfg of 2026-11-08 ends in `--http 80`: with an Ethernet
cable from the PC to the router and the BIOS network stack on, the phone on
the home Wi-Fi opens `http://<the printed address>/`. Wi-Fi on the PC itself
has no firmware driver (see below). The other way is the USB cable with the
phone's tethering (`src/usbnet.asm`): the phone is then an RNDIS network card
on `EFI_USB_IO_PROTOCOL` (control transfers for the RNDIS setup, bulk
transfers for the frames), and on top of it the runner speaks ARP, IPv4, ICMP
echo and a TCP server for one connection at a time (stop-and-wait, a
retransmission after 300 ms, in-order receive only). Our address on the
tethering subnet is 192.168.42.50 unless `--ip` says otherwise; the phone's
browser opens `http://192.168.42.50/`. `net_listen` tries TCP4 first and the
USB link second (`g_net_kind`). Written against the specifications, not yet
run: the first boot with tethering on tells.

The PC's own Wi-Fi is in fact the phone's hotspot (the Redmi 9 is
192.168.10.4, the PC 192.168.10.6), so under Windows the whole path already
works without a cable: `asmrun.exe … --http 8080` on the PC, and a `curl` from
the phone (through its Desktop Commander shell) got the model's answer in
9.8 s on 2026-11-08 (the 0.8B; the Windows firewall already allowed
asmrun.exe inbound). Temperature for the persona: 1.0 wandered (a concrete
bench, a flat rock on the street), so the cfg uses 0.8 with top-p 0.9 and
top-k 40.

**Ollama's API, for Logos.** The owner's app on the phone (Logos) talked to
Ollama; Ollama is gone (owner, 2026-11-08) and the runner answers its API
instead (`src/api.asm`, on the same `--http` port, 11434 in the stick's cfg):
`POST /api/chat` with Ollama's JSON (`messages` with `system`/`user`/
`assistant`/`tool` roles, `tool_calls` in the history, a `tools` list) answers
one NDJSON line (`message.content`, `message.tool_calls`, `done_reason`
`stop`/`length`, `prompt_eval_count`, `eval_count`); `GET /api/version`,
`/api/tags`, `/api/ps` and `POST /api/show` describe the loaded model;
`/api/embed` answers 501 until an embedding model is served. The dialog is
Ollama's, stateless, but the model keeps **one context from boot** (owner:
"single threaded and single context", "just keep one context since boot"):
the first request renders the system turn (the system messages, then the
request's tools verbatim in the `<tools>` block) and every message to the
ChatML text the model sees (history tool calls in Qwen's `<tool_call>` form,
tool results in `<tool_response>` user turns); a later request renders only
the messages after the ones already fed, the model's own answer counted as
fed (second turn of a dialog: 21 prompt tokens instead of 97). Nothing is
reset: a new dialog in the client continues the model's context until the
context is full (`q35_reset` exists for a later switch). The model's
`<tool_call>` blocks come back as `tool_calls` with the parameter values as
JSON where they parse as JSON. Verified from the phone with the 0.8B: version,
tags, a two-turn dialog continuing from state, a tool result in the history
answered correctly.
Logos's client keeps its code; its settings name `asmrun-qwen35-9b` (commit on
`develop`, 2026-11-08). A plain-text body on `/api/chat` is still the page's own API.

**The runner owns no tools (owner, 2026-11-08: "remove all tool from asm
leave them to logos").** `src/tools.asm` and its file tools (list, read,
write, append on the stick), the built-in tools block, the `--no-tools` flag
and the console tool loop are gone. The runner never executes a tool: in the
API it renders the tools a request carries into the `<tools>` block, lets the
model write its `<tool_call>`, and returns the call as JSON (`api_call_json`)
for the caller to run; in the console a `<tool_call>` is just text. All tools,
and running them, belong to the caller (Logos). Verified: a plain chat injects
no tools block (31 prompt tokens, not 700), and a turn carrying one tool
returns `{"function":{"name":…,"arguments":{…}}}` without the runner touching it.

**The tool schemas that never arrived (court case #1843).** The first turns
with Logos's filesystem and git tools went wrong twice: a call without the
`tool` field, then a claimed read of AGENTS.md with invented contents. The
runner's fault first: the renderers copied each tool's JSON with `rt_memcpy`
and advanced the cursor by a register that call clobbers, so the text after
overwrote the schema and 12,750 characters of tools rendered to 491 tokens.
Fixed in three places; every turn request now logs `render_bytes`, `ids`
and `tools_sent`. Then the size: the registry's full form is ~21,000 tokens
for the two categories, 40 minutes of prompt on the 4B and far beyond the
attention window, so Logos's turn client now sends each category as one
function with one line per action (`compact_tools`). A model that answers
"file read" without a tool call is the failure to watch for when the model
works on the repository: every claim needs a tool result behind it.

**RAM and the batch (owner: "what about ram", "optimazi maximum").** The
attention kernel now takes its per-thread score stride from the job
(`AttnJob.stride`), so the embedder runs on its own 1024 positions
(`EMBED_CTX`; Logos's 2000-character chunks tokenise to ~550) instead of
inheriting the chat model's 2048: its cache shrinks from 0.44 to 0.22 GiB,
and the budget on the stick is 5.24 (Q4 9B) + 0.60 (embedder) + 0.13 + 0.22
+ ~0.1 GiB ≈ 6.3 of 7.74. `model_forward_batch` (`q3f.asm`) runs the Qwen3
path for up to 32 tokens at once, the weights read once per layer for all of
them; the vectors are identical to the token-by-token ones (0.646 / 0.192).
But the measured gain is small: a 542-token chunk takes 14.2 s batched
(26 ms per token) against ~22 ms per token unbatched on short texts. The
reason is the one the counters gave for the 9B: the dot kernels are
instruction-bound, and the batched kernel repeats the whole row dot, unpack
included, for every slot, so a batch saves weight loads this CPU was not
waiting for. The gain is in the kernel: unpack each weight block once and dot
it against all the slots (5 + 4·T instructions per 32 weights instead of
9·T), about 2x on prompts and embeddings. That rewrite of the Q6_K/Q4_K/
Q5_K/Q8_0 kernels is the next piece of work.

**Embeddings (owner: "ok then use embeddings").** `--embed FILE` loads
Qwen3-Embedding-0.6B (Q8_0, 639 MB; `qwen3-embedding-0.6b.gguf` on the
stick) as a second model beside the Qwen3.5 chat model: it takes the Qwen3
path's scratch buffers, shares the pool and the activation slots, and keeps
its own tokenizer tables (`g_tok_main` / `g_tok_embed`, 80 bytes of globals
swapped around every embedding; `tok_init` returns early on built tables, so
the flag is cleared before the second build). `POST /api/embed` with Ollama's
body (`input`: a string or an array) answers Ollama's shape: each text is
tokenised with the embedder's vocabulary, `<|endoftext|>` is appended, the
tokens run through `model_forward` with `g_embed_only` set (the final norm's
output instead of word scores), and the last token's state scaled to unit
length is the vector, Qwen3-Embedding's last-token pooling, the same as
Ollama produced, so Logos's stored vectors stay comparable. Checked from the
phone: 1024 dims, unit norm, two Russian sentences about a dog 0.65 apart,
dog vs tax report 0.19; the chat context is untouched by an embedding. The
Qwen3 path is not batched yet, so an embedding costs one weight pass per
token (about 22 ms on the 0.6B); batching it is the next step (owner: "big
scenarios are better because batch is cheaper"). The stick's cfg runs the
Q4_K_M 9B beside the embedder to fit the 8 GB.

**The turn API, the runner's own (owner: "replace olama api with better
one", "i need to see temperature in ui of logos", "fully autonomous since
boot").** `POST /v1/turn` takes only what is new: `{"message": …}` or
`{"tool_results": [{"name", "content"}]}`, plus `"system"` and `"tools"`
(Ollama's function format), used when the state is fresh; tools that arrive
later go in once as a system turn. The answer streams as NDJSON
(`{"delta": text}` per generated piece, nothing from the first `<tool_call>`
on) and ends with one line: `done`, `content`, `tool_calls`, `stop`
(`stop`/`length`), `prompt_tokens`, `gen_tokens`, `prompt_ms`, `gen_ms`,
`mhz` (core 0's clock inside the jobs), `temp_c` (per core) and `pkg_temp_c`
from the digital thermal sensors (`rt_temp_read`: IA32_THERM_STATUS on every
core through a pool job, IA32_PACKAGE_THERM_STATUS, TjMax from
IA32_TEMPERATURE_TARGET; `null` under Windows), `ctx_used`, `ctx`. `GET
/v1/status` gives the same numbers between turns. The ticks line prints
`temp_c_per_core= pkg_temp_c=` on the no-OS build too. Logos's client
(`ollama_client/turn.py`, develop 2026-11-08) speaks this; its asmrun tab shows
the temperatures, the clock and the context. The stick's cfg names the model
(`qwen35-9b-opus-distill.gguf …`), so a boot asks nothing and serves at once.
Detecting a tool call: this fine-tune spells `<tool_call>` in pieces, not as
the vocabulary's special token, so the text's tail is watched (the token id is
checked too, for a model that emits it whole).

**Where the waste is, and why AVX-VNNI does not remove it (owner: "to much
waste").** On the 0.8B half of a token is waste because the token's own
work is 18 ms; on the 9B it is 16% (52.9 of 338 ms): 7.6 ms serial (norms,
quantisation, sampling, dispatch on one core), 1.2 ms barriers, and ~44 ms of
the matvecs running at 19–21 GB/s instead of 22.9. The counters say why: IPC
1.3 with 17% memory stalls, the cores are issuing instructions, not waiting.
Per 32 weights the Q6_K kernel unpacks (5 instructions) and dots (4:
`vpmaddubsw`, a `vpshufb` for the sub-block scale, `vpmaddwd` that applies it
while reducing, `vpaddd`); `vpdpbusd` would replace the first and third but
the scale changes every 16 values, so it would need its own convert and FMA
per group: the same count, and on Gracemont every 256-bit instruction is two
uops either way. The format is the lever: Q4_K_M reads 24% fewer bytes and
unpacks in 3 instructions instead of 5, so at the same instruction rate the
9B goes from ~3.0 to ~3.6 tok/s and leaves 1.7 GB more RAM. Q4_K_M copies of
the opus-distill and the uncensored 9B go on the stick beside the Q6_K ones
(`-q4` in the name) for a side-by-side on the next boot.

**First boot with the probe (2026-11-08 12:24, from dialog.txt).** The
firmware enumerated 8 USB interfaces (the stick, mouse, two keyboards, the
Bluetooth radio) and no phone at all, so the phone's cable carries no data
(Windows had seen no phone on it either); `net: snp=3 tcp4=1 http=1 wifi2=0`:
the BIOS network stack is on, with three network interfaces and a TCP4
service, and no Wi-Fi driver. TCP4 setup failed with status
0x8000000000000011 = EFI_NO_MAPPING ("no address yet"): the code had the
wrong value for it (0x17), so it never waited for DHCP; fixed. The next
boot also prints each interface as `net <mac> type N state N link up|down`
(`efi_probe_snp`), to see which of the three has a link. The 0.8B on bare
metal: prompt 49 tok/s, generation 26.6 tok/s, cores at 2.095 GHz.

**The context never fills (owner: "can we garbage collect context").** Three
of every four Qwen3.5 layers are Gated DeltaNet with a fixed-size state; only
the attention layers keep a cache that grows with the position. That cache is
now a ring of `n_ctx` slots (`q35f.asm`: position p lives in slot p mod n_ctx,
the attention window is min(p+1, n_ctx) slots), so once the context is full
the oldest attention entries are overwritten and the dialog goes on, the
recurrent layers keeping what came before. Each token of a prompt batch is
written and attended in turn, so no token sees a later one's slot. The
position counter and the rope table run to `Q35_POS_MAX` (65,536 positions, a
16 MB table at 64 rotary dims) or the model's trained length; "context full"
is printed only there. Checked on the 0.8B with a 48-position window: the text
is identical to the full-context run up to the wrap (greedy), coherent after it.
This is the model-level layer of context compression; the Logos-side layers
(summaries, hierarchical memory, retrieval) live in Logos and need embeddings,
which the owner set aside for the no-OS work.

**What can a phone link run on?** Right after the banner the no-OS build
prints what the firmware's own bus drivers offer (`src/usb.asm`,
`efi_probe_links`): one line per USB interface the firmware has enumerated
(`usb VVVV:PPPP if N class CC/SS/PP <name> "Product"`, from
`EFI_USB_IO_PROTOCOL`'s device, interface and string descriptors; Android's
MTP interface is `FF/FF/00`, its ADB interface `FF/42/01`, a PTP camera
`06/01/01`) and the handle counts of the network protocols
(`net: snp= tcp4= http= wifi2=`: `EFI_SIMPLE_NETWORK` on the Ethernet port
when the BIOS network stack is on, the TCP4 and HTTP service bindings above
it, and `EFI_WIRELESS_MAC_CONNECTION_II`, which only firmware with a Wi-Fi
driver installs). A phone set to "File transfer" needs no driver, root or ADB
key to show up here: the firmware's USB bus driver creates the interface
handle, and `UsbControlTransfer`/`UsbBulkTransfer` on it are enough for an
MTP session (the planned link: the phone drops a prompt file into a folder,
the runner answers into another). Wi-Fi on the box itself is out of reach:
the firmware has no driver for the card and one would mean loading the
radio's own firmware blob and speaking its command set, a driver the size of
the runner.

**Is the core waiting for memory?** On the no-OS build the `ticks:` line now
also carries, per core, `mem_stall_pct_per_core` (the share of the job cycles
the core spent stalled on a demand load that missed L1: Gracemont's
`MEM_BOUND_STALLS.LOAD`, event 0x34 umask 0x07, from Intel's perfmon list),
`dram_stall_pct_per_core` (the part of it served by DRAM, umask 0x04) and
`ghz_in_jobs_per_core` (cycles ÷ TSC time inside the jobs: the clock the core
really ran at under load, not the boot-time measurement). The two general
counters are programmed next to the fixed ones in `efi_pmu_enable`; the
accounting around every job is in `pool_worker` / `pool_run`. Read together:
a kernel at bus speed shows high DRAM stalls and a low IPC; a kernel that is
instruction-bound shows few stalls at the same IPC — and the GHz says whether
the chip holds its turbo or sits at the power limit.

**System prompt and sampling.** `--system FILE` opens every conversation with a
ChatML system turn holding the file's text (`system.txt` on the stick: "you are
an executor, not an assistant …", which turned "how to hire me" into a direct
answer). The next token is sampled (`src/sample.asm`): the `--top-k` greatest
logits (20) over `--temp` (0.7), cut to `--top-p` (0.8) of the mass, one drawn
by xorshift64* from `--seed` (or the TSC); `--temp 0` is the greedy argmax,
`--top-k 1` reproduces it exactly. The selection is one AVX pass over the
248,320 logits (8 compares at a time against the smallest kept logit), so the
whole sampler costs 67 µs per token against the ~300 ms the weights take. The
values are the model authors' for Qwen3.5 without thinking.

**A person instead of an executor.** The first bare-metal conversation under
`system.txt` ("be creative", temperature 1.5) answered "think about money" with
a 264-token prose poem: the tools block (700 tokens of function schemas) plus
an executor prompt plus a hot sampler make a text generator, not someone to
talk to. `--no-tools` leaves the tools block out of the system turn (and runs
no tool calls), so the system turn is the `--system` file alone and the first
prompt costs seconds instead of minutes; `persona.txt` on the stick describes
one person (name, town, job, dog, how she texts: short turns, questions back,
never "as an AI") and the cfg of 2026-11-08 is `-i --chat -n 1024 -c 2048
--no-tools --system persona.txt --temp 1.0 --top-p 0.95 --top-k 50`. The
model that holds a person best is `qwen35-9b-opus-distill` (trained on Claude
conversations), then `qwen35-9b-uncensored`. The persona lasted a day: on
2026-11-08 the owner wanted "an effective war machine", not a friend, and the
cfg went back to `system.txt` (the executor) with the model authors' sampling
(0.7 / 0.8 / 20); `persona.txt` stays on the stick for whoever wants it back.

**Autonomous: `--auto` (owner, 2026-11-08: "i want it to always non stop
work").** Without the flag the runner is a program that waits for a human at
three places: a fatal error prints and waits for a key before the reset, a
full context (`-c`) ends the conversation with "start a new conversation", and
the HTTP accept blocks until a client connects (nothing else can happen while
it does). With `--auto` nobody is expected at the box:

- A fatal error, an exit and a stuck pool pause five seconds (so a screen, if
  one is attached, shows the message), then **reset the machine**; the stick
  boots the runner again. Under Windows the process exits as before.
- The firmware **watchdog** is re-armed for ten minutes on every pass of the
  idle loop (`rt_kick`): a hang anywhere resets the box the same way.
- A **full context** is reset (`q35_reset`: the DeltaNet states, the
  attention ring and the position) and the next prompt starts from the system
  turn again; the conversation goes on instead of dying.
- The **accept** waits a second at a time (the TCP4 listen token stays out
  between passes; Winsock uses `select`), and between the waits the idle loop
  looks at the stick's **task queue** and the keyboard. A file
  `task-<name>.txt` on the boot volume (or in the working directory under
  Windows) is read as a prompt, answered as a plain-text request is (the
  stdout capture), the answer written to `task-<name>.answer.txt` and the
  task deleted; a keystroke makes the loop read one prompt from the console,
  then it goes back to the network. The owner can drop a task file on the
  stick from any PC and collect the answer; nothing on the stick ever waits.

The first run under Windows (0.8B, `task-hello.txt` in the folder) answered
into `task-hello.answer.txt` and deleted the task within the first idle
second after the load. The cfg of 2026-11-08 evening:
`qwen35-9b-opus-distill-q4.gguf -i --chat -n 1024 -c 2048 --system system.txt
--temp 0.7 --top-p 0.8 --top-k 20 --http 11434 --embed
qwen3-embedding-0.6b.gguf --auto`.

FAT32 holds at most 4 GiB per file and the firmware reads nothing but FAT32,
so `make-usb.ps1` writes a larger model in 4,000,000,000-byte parts and the
runner opens `model.gguf`, `model.gguf.1`, ... as one file (a tensor crossing a
part boundary is read piecewise). The default `asmrun.cfg` is now `model.gguf -i
--chat -n 1024 -c 2048`: Qwen3.5-9B Q6_K is 6.95 GiB of weights on a 7.74 GiB
machine, and the firmware and the integrated graphics keep some of it, so the
context is what the rest of the memory allows (each context position costs 64
KB of KV cache over the 8 full-attention layers). If the load stops with
`AllocatePages failed (out of memory)`, the next quantisation down (Q5_K_M,
6.4 GB, or Q4_K_M, 5.6 GB) is the answer.

Then on the PC: turn **Secure Boot off** in the firmware setup (the image is
unsigned; with Secure Boot on the firmware refuses it before a byte of ours
runs) and boot from the stick (the boot-device menu, or the stick first in the
boot order). The stick must be **FAT32**: the firmware reads nothing else.

What the screen shows, in order: the banner, `AVX state enabled`, `tsc_mhz=`,
`cpu clock: N MHz before, M MHz now` (the boot processor measured with
APERF/MPERF before and after the clock request) and `hwp enabled here` or `hwp
was already on` with the hardware's performance levels, `boot volume opened`,
the arguments, `reading 1296 MB from the boot volume` with a dot per 32 MB and
the MB/s (the firmware's USB driver sets the pace; a minute is normal), the
model summary with `threads=`, `processors=4 (the boot processor and 3
application processors through EFI_MP_SERVICES_PROTOCOL)`, `load_ms`, and a `>`
prompt. Keys pressed while the model was loading are discarded at that first
prompt, so a stray Enter during the load does not end the session. Type a
prompt, press Enter: the text is generated on all four cores and the same
statistics lines as on Windows follow (`gen_tok_s`, `effective_gb_s`,
`prof_ms`). An empty line or Esc ends the session; the exit code is printed, a
key press returns to the firmware's boot manager, which continues with the next
boot option. Every step before the model loads prints one line and every
firmware error its EFI status, so if the boot stops, the last line says where.
If a worker core ever fails to come back (a hung job, which the pool reports
after tens of seconds), the runner says so and resets the machine after the key
press instead of handing the firmware an image a core still executes.

The firmware console is only required to have Latin glyphs: text the model
writes in other scripts (Cyrillic, CJK) may show as blanks on the bare-metal
screen. The Windows build is unaffected.

`asmrun.cfg` takes the same arguments as the Windows command line, so the
diagnostic modes work bare-metal too: `model.gguf --bw` for the memory bus
without an OS, `model.gguf --kernels` for the kernels. On the first boot the
numbers to compare with the Windows runs are `tsc_mhz`, `load_ms` (now a read
from the stick), `gen_tok_s` and `effective_gb_s`.

Known assumptions (DESIGN.md, "No operating system"): the firmware provides
`EFI_MP_SERVICES_PROTOCOL` (EDK2-derived firmware such as AMI Aptio does;
without it the runner says so and uses one core), XSAVE and AVX for enabling
AVX state, and leaves the application processors to us for the whole run. The
clock request uses Hardware P-states (every Intel core since Skylake and
Goldmont Plus); on a CPU without them the runner says so and the clock stays as
the firmware set it. Nothing here is Windows-specific: the runtime half is 1400
lines of UEFI and MSR code, and the rest of the program is the same bytes as
`asmrun.exe`.

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
  pe.inc          hand-written PE32+ header: kernel32, user32, gdi32 import tables (Windows) or the no-op relocation block (UEFI)
src/
  runtime.asm     shared: console formatting, CPUID gate, thread pool (pool_worker/pool_run), --bw, helpers
  win.asm         Windows: entry, WriteFile, VirtualAlloc, file mapping, QPC, CreateThread
  efi.asm         UEFI: entry, ConOut/ConIn, AllocatePages, file reads from the stick, TSC, EFI_MP_SERVICES workers
  gguf.asm        GGUF v3 parse into the Model struct
  kernels.asm     int8 matvec over Q4_K/Q6_K, F16/F32 rows, activation quantisation, RMSNorm, RoPE, attention, SwiGLU, exp, argmax
  tokenizer.asm   byte-level BPE encode/decode (qwen2 pre-tokeniser)
  view.asm        pictures: BMP loading, the test card, fit-and-centre scaling (rt_view_show in win.asm / efi.asm)
  sd.asm          any GGUF indexed by tensor name (the SD-Turbo weights); --sdinfo
  cliptok.asm     CLIP's BPE tokenizer (vocab.json + merges.txt)
  sdk.asm         the image kernels: cache-blocked matmul, LayerNorm, GELU, attention
  clipenc.asm     the text encoder (OpenCLIP ViT-H text tower)
  unet.asm        the UNet: conv3x3, GroupNorm, attention, ResBlock, SpatialTransformer, the forward pass
  taesd.asm       the TAESD decoder from safetensors
  image.asm       the pipeline (--image / --gen): noise, scheduler, PNG/BMP output, the console prompt loop
  model.asm       the Qwen3 forward pass, KV cache, generation loop, phase profile, and CLI
```

`build/` (the assembled `asmrun.exe` and `BOOTX64.EFI`) is git-ignored. The
model blob is never committed.

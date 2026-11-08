; asmrun.asm — top level of the asmrun research runner.
;
; One `nasm -f bin` translation unit that emits a complete PE32+ image: the
; headers come first (from `org`), then .text with the imports, code and
; constants of every module, then .data with their mutable globals. Two
; targets from the same sources:
;
;   nasm -f bin -O2 -I inc/ -I src/ -o build/asmrun.exe asmrun.asm
;       a Windows console executable (src/win.asm is the platform half)
;   nasm -f bin -O2 -I inc/ -I src/ -D ASMRUN_EFI -o build/BOOTX64.EFI asmrun.asm
;       a UEFI application: copied to \EFI\BOOT\BOOTX64.EFI on a FAT32 USB
;       stick it boots on a PC with no operating system (src/efi.asm)
;
; There is no linker. The code is position-independent (RIP-relative only), so
; the firmware may load the UEFI image anywhere. See DESIGN.md for the
; architecture and README.md for building, the USB stick and the runs.

bits 64
default rel

%define IMAGE_BASE 0x140000000
org IMAGE_BASE

%include "common.inc"   ; structs, GGML/GGUF constants, WINPROLOGUE/WINEPILOGUE
%ifdef ASMRUN_EFI
  %include "efi.inc"    ; UEFI table offsets, protocol GUID layout, status codes
%else
  %include "win.inc"    ; Win32 constants, winimp() call macro
%endif
%include "pe.inc"       ; PE headers (+ Windows import tables); .text's body starts at 0x1000

; .data follows the code on the next page boundary. Declared once here; a
; module switches with `section .data` for its globals and back with
; `section .text`. In the bin format a section's file offset equals its RVA,
; which is what the header arithmetic in pe.inc relies on.
section .data align=4096 follows=.text
data_start:
section .text

; The modules, in dependency order. Every module is code and initialised data
; only: mutable globals are `dq 0` / `db 0` bytes in .data, never `resb` (the
; loader copies the file; no section has a zero-filled tail). Big buffers come
; from rt_alloc at run time.
%include "runtime.asm"   ; shared: console formatting, cpuid gate, thread pool, bandwidth probe, helpers
%ifdef ASMRUN_EFI
  %include "efi.asm"     ; UEFI: entry, console, files, pages, TSC clock, EFI_MP_SERVICES workers
  %include "usb.asm"     ; UEFI: the USB interfaces and network protocols the firmware enumerated (a link to the phone)
  %include "tcp4.asm"    ; UEFI: the network transport, EFI_TCP4_PROTOCOL (the BIOS network stack), and the net_* dispatcher
  %include "usbnet.asm"  ; UEFI: the phone's USB tethering as the network card: RNDIS, ARP, IP, ICMP, a one-connection TCP
%else
  %include "win.asm"     ; Windows: entry, console, files, VirtualAlloc, QPC, CreateThread workers
  %include "wsock.asm"   ; Windows: the network transport, Winsock (the HTTP test bench)
%endif
%include "http.asm"      ; the model behind HTTP (--http PORT): the phone's page and POST /api/chat
%include "api.asm"       ; Ollama's API on the same port: JSON /api/chat with tools, /api/tags, /api/version (Logos's client)
%include "gguf.asm"      ; GGUF parsing into Model
%include "sd.asm"        ; the SD-Turbo GGUF indexed by tensor name (--sdinfo lists it)
%include "cliptok.asm"   ; the CLIP BPE tokenizer of SD-Turbo's text encoder (--cliptok)
%include "kernels.asm"   ; matvec / rmsnorm / softmax / silu / exp / rope / attention
%include "sample.asm"    ; the next token: temperature, top-k, top-p, a seeded random draw
%include "sdk.asm"       ; the image generator's kernels: cache-blocked matmul, LayerNorm, GELU, attention
%include "clipenc.asm"   ; SD-Turbo's text encoder (OpenCLIP ViT-H text tower, 23 layers)
%include "q35k.asm"      ; Qwen3.5's extra kernels: gated delta rule, causal conv, gated norm, partial rope
%include "q35.asm"       ; Qwen3.5: layers, setup, generation, chat
%include "q35f.asm"      ; Qwen3.5: the forward pass for a batch of tokens and its pool jobs
%include "tools.asm"     ; the model's file tools on the stick: list, read, write, append
%include "unet.asm"      ; SD-Turbo's UNet: one denoising step (convolutions, GroupNorm, attention)
%include "taesd.asm"     ; the Tiny AutoEncoder's decoder: latent -> 512x512 picture
%include "tokenizer.asm" ; byte-level BPE encode and decode
%include "view.asm"      ; pictures: BMP loading, test card, fit-and-centre scaling (rt_view_show shows them)
%include "image.asm"     ; text to image (--image): the prompt, img_generate, the console prompt loop
%include "model.asm"     ; forward pass, generation loop, main() and CLI dispatch
%include "q3f.asm"       ; the Qwen3 path's forward pass for a batch of tokens (the embedder's texts)

; End of the code: pe.inc sizes .text from here. The bytes up to data_start are
; zero padding written by NASM.
section .text
text_end:

; End of the data: pad to a page so SizeOfRawData is a multiple of FileAlignment
; and mark the image end (there is no BSS beyond this point).
section .data
  align 0x1000, db 0
SECTION_RAW_END:
IMAGE_END:

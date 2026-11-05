; asmrun.asm — top level of the asmrun research runner.
;
; One `nasm -f bin` translation unit: the PE headers come first (from `org`),
; then the section holds the imports, code and data of every module. Build:
;
;   nasm -f bin -O2 -I inc/ -I src/ -o build/asmrun.exe asmrun.asm
;
; There is no linker. See DESIGN.md for the architecture and README.md for how
; this is built and run (it cannot be built on the authoring machine, whose
; Smart App Control blocks the assembler and any unsigned executable).

bits 64
default rel

%define IMAGE_BASE 0x140000000
org IMAGE_BASE

%include "win.inc"      ; Win32 constants, winimp() call macro
%include "common.inc"   ; structs, GGML/GGUF constants, WINPROLOGUE/WINEPILOGUE
%include "pe.inc"       ; PE headers, import tables, IAT (opens the section at 0x1000)

; The section continues with the modules, in dependency order. Every module is
; plain code and initialised data only: mutable globals are `dq 0` / `db 0`
; style initialised bytes, never `resb` (a BSS gap in the middle of the section
; would be written to the file as zeros). Big buffers come from rt_alloc.
%include "runtime.asm"   ; entry, I/O, memory, mapping, timing, cpuid, thread pool, bandwidth
%include "gguf.asm"      ; GGUF parsing into Model
%include "kernels.asm"   ; matvec / rmsnorm / softmax / silu / exp / rope / attention
%include "tokenizer.asm" ; byte-level BPE encode and decode
%include "model.asm"     ; forward pass, generation loop, main() and CLI dispatch

; End of the section: pad the initialised bytes to a page so SizeOfRawData is a
; multiple of FileAlignment, and mark the image end (no BSS beyond this point).
  align 0x1000, db 0
SECTION_RAW_END:
IMAGE_END:

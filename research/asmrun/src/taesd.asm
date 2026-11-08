; taesd.asm — the Tiny AutoEncoder's decoder (madebyollin/taesd, the diffusers
; AutoencoderTiny weights as one safetensors file): a 64x64x4 latent in the
; UNet's scale becomes the 512x512 picture. 3x3 convolutions and ReLUs only:
;
;   x = 3 tanh(latent / 3)
;   conv 4->64, relu; 3 blocks; up x2, conv (no bias); 3 blocks; up, conv;
;   3 blocks; up, conv; 1 block; conv 64->3          block: relu(conv(relu(conv(relu(conv x)))) + x)
;   pixel = clamp(out, 0, 1) * 255
;
; The convolutions are unet.asm's un_conv3 (padded NHWC, f32 weights gathered
; per call); the buffers come from the arena after the UNet's are released.
;
;   ta_load(rcx=path utf16)      read and index the safetensors (once)
;   ta_decode(rcx=latent padded [66][66][8]) -> rax = BGRX 512x512
;
; %included into asmrun.asm (bits 64, default rel) after unet.asm.

section .text

TA_MAX_TENSORS equ 256
TA_C           equ 64
TA_OUT         equ 512

; TA_EXPECT ch — the next JSON byte must be ch (inside ta_load)
%macro TA_EXPECT 1
  cmp  rsi, rbx
  jae  .bad
  cmp  byte [rsi], %1
  jne  .bad
  inc  rsi
%endmacro

; ta_ws — rsi past JSON whitespace, not beyond rbx. Clobbers al.
ta_ws:
  cmp  rsi, rbx
  jae  .d
  mov  al, [rsi]
  cmp  al, ' '
  je   .s
  cmp  al, 9
  jb   .d
  cmp  al, 13
  ja   .d
.s:
  inc  rsi
  jmp  ta_ws
.d:
  ret

; ta_int — the decimal at rsi -> rax, rsi past it. Clobbers rcx, rdx.
ta_int:
  xor  eax, eax
.l:
  cmp  rsi, rbx
  jae  .d
  movzx edx, byte [rsi]
  sub  edx, '0'
  cmp  edx, 9
  ja   .d
  imul rax, rax, 10
  add  rax, rdx
  inc  rsi
  jmp  .l
.d:
  ret

; ta_load(rcx=path utf16) — map the safetensors file: u64 header length, the
; JSON header {"name":{"dtype":..,"shape":[..],"data_offsets":[a,b]},..,
; "__metadata__":{..}}, then the data. Every tensor becomes a Tensor record
; (shape reversed into ne0..ne3, like GGUF). Preserves the nonvolatile
; registers.
ta_load:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32] file length, [+40] data base, [+48] field length,
                               ; [+56..88) shape, [+88] shape count
  lea  rdx, [rsp+32]
  call rt_read_file
  mov  r12, rax
  mov  rax, [r12]
  lea  rsi, [r12 + 8]
  lea  rbx, [rsi + rax]
  mov  [rsp+40], rbx
  mov  ecx, TA_MAX_TENSORS * Tensor_size
  call rt_alloc
  mov  [rel g_ta_tensors], rax
  mov  rdi, rax
  mov  qword [rel g_ta_n], 0
  mov  ecx, TA_OUT * TA_OUT * 4
  call rt_alloc
  mov  [rel g_ta_pixels], rax
  call ta_ws
  TA_EXPECT '{'
.entry:
  call ta_ws
  cmp  rsi, rbx
  jae  .bad
  mov  al, [rsi]
  cmp  al, '}'
  je   .done
  cmp  al, ','
  jne  .key
  inc  rsi
  jmp  .entry
.key:
  TA_EXPECT '"'
  mov  r13, rsi                ; the name
.kn:
  cmp  rsi, rbx
  jae  .bad
  cmp  byte [rsi], '"'
  je   .ke
  inc  rsi
  jmp  .kn
.ke:
  mov  r14, rsi
  sub  r14, r13                ; its length
  inc  rsi
  call ta_ws
  TA_EXPECT ':'
  call ta_ws
  TA_EXPECT '{'
  mov  rcx, r13
  mov  rdx, r14
  lea  r8, [rel ta_s_meta]
  call gguf_zeq
  test rax, rax
  jz   .tensor
  mov  ecx, 1                  ; skip the metadata object (strings may hold braces)
.sk:
  cmp  rsi, rbx
  jae  .bad
  mov  al, [rsi]
  inc  rsi
  cmp  al, '"'
  jne  .sk1
.sq:
  cmp  rsi, rbx
  jae  .bad
  mov  al, [rsi]
  inc  rsi
  cmp  al, '\'
  jne  .sq2
  inc  rsi
  jmp  .sq
.sq2:
  cmp  al, '"'
  jne  .sq
  jmp  .sk
.sk1:
  cmp  al, '{'
  jne  .sk2
  inc  ecx
  jmp  .sk
.sk2:
  cmp  al, '}'
  jne  .sk
  dec  ecx
  jnz  .sk
  jmp  .entry
.tensor:
  cmp  qword [rel g_ta_n], TA_MAX_TENSORS
  jae  .bad
  mov  [rdi + Tensor.name], r13
  mov  [rdi + Tensor.namelen], r14
  mov  qword [rdi + Tensor.ne0], 1
  mov  qword [rdi + Tensor.ne1], 1
  mov  qword [rdi + Tensor.ne2], 1
  mov  qword [rdi + Tensor.ne3], 1
  mov  dword [rdi + Tensor.ndims], 0
.field:
  call ta_ws
  cmp  rsi, rbx
  jae  .bad
  mov  al, [rsi]
  cmp  al, '}'
  je   .tend
  cmp  al, ','
  jne  .fkey
  inc  rsi
  jmp  .field
.fkey:
  TA_EXPECT '"'
  mov  r15, rsi
.fn:
  cmp  rsi, rbx
  jae  .bad
  cmp  byte [rsi], '"'
  je   .fe
  inc  rsi
  jmp  .fn
.fe:
  mov  r8, rsi
  sub  r8, r15
  mov  [rsp+48], r8
  inc  rsi
  call ta_ws
  TA_EXPECT ':'
  call ta_ws
  mov  rcx, r15
  mov  rdx, [rsp+48]
  lea  r8, [rel ta_s_dtype]
  call gguf_zeq
  test rax, rax
  jnz  .dtype
  mov  rcx, r15
  mov  rdx, [rsp+48]
  lea  r8, [rel ta_s_shape]
  call gguf_zeq
  test rax, rax
  jnz  .shape
  mov  rcx, r15
  mov  rdx, [rsp+48]
  lea  r8, [rel ta_s_offs]
  call gguf_zeq
  test rax, rax
  jnz  .offs
  ; an unknown field: skip a string, or up to the next ',' / '}'
  cmp  byte [rsi], '"'
  jne  .skv
  inc  rsi
.sks:
  cmp  rsi, rbx
  jae  .bad
  mov  al, [rsi]
  inc  rsi
  cmp  al, '"'
  jne  .sks
  jmp  .field
.skv:
  cmp  rsi, rbx
  jae  .bad
  mov  al, [rsi]
  cmp  al, ','
  je   .field
  cmp  al, '}'
  je   .field
  inc  rsi
  jmp  .skv
.dtype:
  TA_EXPECT '"'
  cmp  byte [rsi], 'F'
  jne  .bad
  mov  eax, GGML_F32
  cmp  byte [rsi+1], '3'
  je   .dt
  mov  eax, GGML_F16
  cmp  byte [rsi+1], '1'
  jne  .bad
.dt:
  mov  [rdi + Tensor.type], eax
.dq:
  cmp  rsi, rbx
  jae  .bad
  mov  al, [rsi]
  inc  rsi
  cmp  al, '"'
  jne  .dq
  jmp  .field
.shape:
  TA_EXPECT '['
  mov  qword [rsp+88], 0
.sh:
  call ta_ws
  cmp  rsi, rbx
  jae  .bad
  cmp  byte [rsi], ']'
  je   .she
  cmp  byte [rsi], ','
  jne  .shn
  inc  rsi
  jmp  .sh
.shn:
  call ta_int
  mov  rcx, [rsp+88]
  cmp  rcx, 4
  jae  .bad
  mov  [rsp+56 + rcx*8], rax
  inc  qword [rsp+88]
  jmp  .sh
.she:
  inc  rsi
  mov  rcx, [rsp+88]
  mov  [rdi + Tensor.ndims], ecx
  xor  edx, edx
.rev:
  cmp  rdx, rcx
  jae  .field
  mov  rax, rcx
  dec  rax
  sub  rax, rdx                ; shape[n-1-d] -> ne_d
  mov  rax, [rsp+56 + rax*8]
  mov  [rdi + Tensor.ne0 + rdx*8], rax
  inc  rdx
  jmp  .rev
.offs:
  TA_EXPECT '['
  call ta_ws
  call ta_int
  mov  [rdi + Tensor.data], rax
  call ta_ws
  TA_EXPECT ','
  call ta_ws
  call ta_int
  sub  rax, [rdi + Tensor.data]
  mov  [rdi + Tensor.nbytes], rax
  call ta_ws
  TA_EXPECT ']'
  jmp  .field
.tend:
  inc  rsi
  mov  rax, [rsp+40]
  add  [rdi + Tensor.data], rax
  add  rdi, Tensor_size
  inc  qword [rel g_ta_n]
  jmp  .entry
.done:
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.bad:
  lea  rcx, [rel ta_err_json]
  call rt_fatal

; ta_t(rcx=zname) -> rax = the Tensor* of that name, rt_fatal when missing.
ta_t:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rsi, rcx
  mov  rdi, [rel g_ta_tensors]
  mov  rbx, [rel g_ta_n]
.l:
  test rbx, rbx
  jz   .missing
  mov  rcx, [rdi + Tensor.name]
  mov  rdx, [rdi + Tensor.namelen]
  mov  r8, rsi
  call gguf_zeq
  test rax, rax
  jnz  .hit
  add  rdi, Tensor_size
  dec  rbx
  jmp  .l
.hit:
  mov  rax, rdi
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.missing:
  lea  rcx, [rel ta_s_missing]
  call rt_err_str
  mov  rcx, rsi
  call rt_err_str
  lea  rcx, [rel ta_s_nl]
  call rt_err_str
  lea  rcx, [rel ta_err_missing]
  call rt_fatal

; ta_lt(rcx=layer, rdx=suffix zstr) -> rax = Tensor* "decoder.layers.<layer>.<suffix>".
ta_lt:
  push rsi
  push rdi
  WINPROLOGUE
  lea  rdi, [rel g_ta_name]
  lea  rsi, [rel ta_s_layers]
.p:
  lodsb
  test al, al
  jz   .pd
  stosb
  jmp  .p
.pd:
  mov  rax, rcx
  cmp  rax, 10
  jb   .one
  push rdx
  push rdx
  xor  edx, edx
  mov  ecx, 10
  div  rcx
  add  al, '0'
  stosb
  mov  rax, rdx
  pop  rdx
  pop  rdx
.one:
  add  al, '0'
  stosb
  mov  al, '.'
  stosb
  mov  rsi, rdx
.s:
  lodsb
  stosb
  test al, al
  jnz  .s
  lea  rcx, [rel g_ta_name]
  call ta_t
  WINEPILOGUE
  pop  rdi
  pop  rsi
  ret

; ta_tanh3(rcx=x, rdx=n) — x = 3 tanh(x/3) in place, n a multiple of 8:
; tanh u = 1 - 2 / (e^(2u) + 1). Leaf; clobbers rax, ymm0-ymm5.
ta_tanh3:
  xor  eax, eax
.l:
  cmp  rax, rdx
  jae  .d
  vmovups ymm4, [rcx + rax*4]
  vmulps ymm0, ymm4, [rel ta8_twothirds] ; 2u = 2x/3
  EXP8
  vaddps ymm0, ymm0, [rel kc8_one]
  vmovaps ymm1, [rel ta8_two]
  vdivps ymm1, ymm1, ymm0
  vmovaps ymm0, [rel kc8_one]
  vsubps ymm0, ymm0, ymm1                ; tanh
  vmulps ymm0, ymm0, [rel ta8_three]
  vmovups [rcx + rax*4], ymm0
  add  rax, 8
  jmp  .l
.d:
  ret

; ta_conv(rcx=in, rdx=out, r8=cin, r9=cout, [5]=h, [6]=w, [7]=weight Tensor*,
; [8]=bias f32* or 0) — un_conv3 on TAESD's tensors (f32, [kx ky cin cout]).
ta_conv:
  push rbx
  WINPROLOGUE
  lea  rbx, [rel g_un_conv]
  mov  [rbx + UnConv.in], rcx
  mov  [rbx + UnConv.out], rdx
  mov  [rbx + UnConv.cin], r8
  mov  [rbx + UnConv.cout], r9
  mov  rax, [rbp + 48 + 8]
  mov  [rbx + UnConv.h], rax
  mov  rax, [rbp + 56 + 8]
  mov  [rbx + UnConv.w], rax
  mov  rax, [rbp + 64 + 8]
  mov  [rbx + UnConv.wt], rax
  mov  rax, [rbp + 72 + 8]
  mov  [rbx + UnConv.bias], rax
  mov  qword [rbx + UnConv.stride], 1
  mov  rcx, rbx
  call un_conv3
  WINEPILOGUE
  pop  rbx
  ret

; ta_decode's frame (after WINPROLOGUE): [rsp+32..64) the 5th..8th arguments
; of the calls it makes, then its own slots:
%define TD_H    [rsp+64]
%define TD_W    [rsp+72]
%define TD_WT   [rsp+80]
%define TD_BIAS [rsp+88]
%define TD_X    [rsp+96]
%define TD_T    [rsp+104]
%define TD_U    [rsp+112]

; TA_CONV in, out, cin, cout, layer, wsuffix, bsuffix or 0 — inside ta_decode;
; a bias tensor's data is f32.
%macro TA_CONV 7
  mov  ecx, %5
  lea  rdx, [rel %6]
  call ta_lt
  mov  TD_WT, rax
%ifidn %7, 0
  mov  qword TD_BIAS, 0
%else
  mov  ecx, %5
  lea  rdx, [rel %7]
  call ta_lt
  mov  rax, [rax + Tensor.data]
  mov  TD_BIAS, rax
%endif
  mov  rcx, %1
  mov  rdx, %2
  mov  r8d, %3
  mov  r9d, %4
  mov  rax, TD_H
  mov  [rsp+32], rax
  mov  rax, TD_W
  mov  [rsp+40], rax
  mov  rax, TD_WT
  mov  [rsp+48], rax
  mov  rax, TD_BIAS
  mov  [rsp+56], rax
  call ta_conv
%endmacro

; TA_RELU buf — relu over the whole padded buffer of TA_C channels at TD_H x TD_W
%macro TA_RELU 1
  mov  rcx, %1
  mov  rdx, TD_H
  add  rdx, 2
  mov  rax, TD_W
  add  rax, 2
  imul rdx, rax
  imul rdx, rdx, TA_C
  call un_relu
%endmacro

; TA_BLOCK layer — inside ta_decode: x -> t = relu(conv4(relu(conv2(relu(conv0 x)))) + x),
; then the buffers rotate (x = t, t = u, u = the old x)
%macro TA_BLOCK 1
  TA_CONV TD_X, TD_T, TA_C, TA_C, %1, ta_s_c0w, ta_s_c0b
  TA_RELU TD_T
  TA_CONV TD_T, TD_U, TA_C, TA_C, %1, ta_s_c2w, ta_s_c2b
  TA_RELU TD_U
  TA_CONV TD_U, TD_T, TA_C, TA_C, %1, ta_s_c4w, ta_s_c4b
  mov  rcx, TD_T
  mov  rdx, TD_X
  mov  r8, TD_H
  add  r8, 2
  mov  rax, TD_W
  add  rax, 2
  imul r8, rax
  imul r8, r8, TA_C
  call k_add
  TA_RELU TD_T
  mov  rax, TD_X
  mov  rcx, TD_T
  mov  TD_X, rcx
  mov  rcx, TD_U
  mov  TD_T, rcx
  mov  TD_U, rax
%endmacro

; TA_UP layer — inside ta_decode: x -> upsample into t, conv (layer, no bias)
; into u at twice the size; x = u, u = the old x; h, w doubled
%macro TA_UP 1
  mov  rcx, TD_X
  mov  rdx, TD_T
  mov  r8d, TA_C
  mov  r9, TD_H
  mov  rax, TD_W
  mov  [rsp+32], rax
  call un_upsample
  shl  qword TD_H, 1
  shl  qword TD_W, 1
  TA_CONV TD_T, TD_U, TA_C, TA_C, %1, ta_s_w, 0
  mov  rax, TD_X
  mov  rcx, TD_U
  mov  TD_X, rcx
  mov  TD_U, rax
%endmacro

; ta_decode(rcx=latent padded [66][66][8], the UNet's scale) -> rax = the
; picture, BGRX 512x512 (g_ta_pixels). Uses the arena (reset it first).
; Preserves the nonvolatile registers.
ta_decode:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 128                ; the TD_* slots above
  mov  r12, rcx
  mov  ecx, 66 * 66 * 8 * 4
  call un_aalloc
  mov  rbx, rax
  mov  rcx, rbx
  mov  rdx, r12
  mov  r8d, 66 * 66 * 8 * 4
  call rt_memcpy
  mov  rcx, rbx
  mov  edx, 66 * 66 * 8
  call ta_tanh3
  mov  ecx, (TA_OUT + 2) * (TA_OUT + 2) * TA_C * 4
  call un_aalloc
  mov  TD_X, rax
  mov  ecx, (TA_OUT + 2) * (TA_OUT + 2) * TA_C * 4
  call un_aalloc
  mov  TD_T, rax
  mov  ecx, (TA_OUT + 2) * (TA_OUT + 2) * TA_C * 4
  call un_aalloc
  mov  TD_U, rax
  mov  qword TD_H, UN_LAT
  mov  qword TD_W, UN_LAT
  ; layers.0: 4 (as 8) -> 64, relu
  TA_CONV rbx, TD_X, 8, TA_C, 0, ta_s_w, ta_s_b
  TA_RELU TD_X
  TA_BLOCK 2
  TA_BLOCK 3
  TA_BLOCK 4
  TA_UP 6
  TA_BLOCK 7
  TA_BLOCK 8
  TA_BLOCK 9
  TA_UP 11
  TA_BLOCK 12
  TA_BLOCK 13
  TA_BLOCK 14
  TA_UP 16
  TA_BLOCK 17
  ; layers.18: 64 -> 3 (as 4), with bias (padded to 4)
  mov  ecx, 18
  lea  rdx, [rel ta_s_b]
  call ta_lt
  mov  rax, [rax + Tensor.data]
  lea  rcx, [rel g_ta_bias4]
  mov  edx, [rax]
  mov  [rcx], edx
  mov  edx, [rax+4]
  mov  [rcx+4], edx
  mov  edx, [rax+8]
  mov  [rcx+8], edx
  mov  dword [rcx+12], 0
  mov  ecx, 18
  lea  rdx, [rel ta_s_w]
  call ta_lt
  mov  TD_WT, rax
  lea  rax, [rel g_ta_bias4]
  mov  TD_BIAS, rax
  mov  rcx, TD_X
  mov  rdx, TD_T
  mov  r8d, TA_C
  mov  r9d, 4
  mov  rax, TD_H
  mov  [rsp+32], rax
  mov  rax, TD_W
  mov  [rsp+40], rax
  mov  rax, TD_WT
  mov  [rsp+48], rax
  mov  rax, TD_BIAS
  mov  [rsp+56], rax
  call ta_conv
  ; pixels: clamp(v, 0, 1) * 255; R G B -> B G R X
  mov  rsi, TD_T
  mov  rdi, [rel g_ta_pixels]
  vxorps xmm3, xmm3, xmm3
  vmovss xmm4, [rel kc_one]
  vmovss xmm5, [rel ta_255]
  xor  r12d, r12d              ; y
.py:
  cmp  r12d, TA_OUT
  jae  .done
  xor  r13d, r13d              ; x
.px:
  cmp  r13d, TA_OUT
  jae  .pyn
  lea  rax, [r12 + 1]
  imul rax, rax, TA_OUT + 2
  lea  rax, [rax + r13 + 1]
  shl  rax, 4                  ; 4 channels * 4 bytes
  lea  rbx, [rsi + rax]
  xor  r14d, r14d
.pc:
  cmp  r14d, 3
  jae  .pstore
  vmovss xmm0, [rbx + r14*4]
  vmaxss xmm0, xmm0, xmm3
  vminss xmm0, xmm0, xmm4
  vmulss xmm0, xmm0, xmm5
  vcvtss2si eax, xmm0
  mov  ecx, 2
  sub  ecx, r14d               ; channel 0 (R) -> byte 2, 2 (B) -> byte 0
  mov  [rdi + rcx], al
  inc  r14d
  jmp  .pc
.pstore:
  mov  byte [rdi + 3], 0
  add  rdi, 4
  inc  r13d
  jmp  .px
.pyn:
  inc  r12d
  jmp  .py
.done:
  vzeroupper
  mov  rax, [rel g_ta_pixels]
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

section .data
  align 32
ta8_twothirds: times 8 dd 0.66666666666666667
ta8_two:       times 8 dd 2.0
ta8_three:     times 8 dd 3.0
ta_255:        dd 255.0
  align 16
g_ta_bias4:    times 4 dd 0
  align 8
g_ta_tensors:  dq 0
g_ta_n:        dq 0
g_ta_pixels:   dq 0
g_ta_name:     times 96 db 0
ta_s_meta:     db "__metadata__", 0
ta_s_dtype:    db "dtype", 0
ta_s_shape:    db "shape", 0
ta_s_offs:     db "data_offsets", 0
ta_s_layers:   db "decoder.layers.", 0
ta_s_w:        db "weight", 0
ta_s_b:        db "bias", 0
ta_s_c0w:      db "conv.0.weight", 0
ta_s_c0b:      db "conv.0.bias", 0
ta_s_c2w:      db "conv.2.weight", 0
ta_s_c2b:      db "conv.2.bias", 0
ta_s_c4w:      db "conv.4.weight", 0
ta_s_c4b:      db "conv.4.bias", 0
ta_s_missing:  db "asmrun: missing TAESD tensor ", 0
ta_s_nl:       db 10, 0
ta_err_json:   db "the TAESD file is not the safetensors file expected (diffusers AutoencoderTiny)", 0
ta_err_missing: db "the TAESD file lacks a decoder tensor", 0
section .text

; image.asm — text to image: asmrun --image / --gen. You type a prompt; the
; picture comes back as an IMG_SIDE x IMG_SIDE BGRX picture for rt_view_show
; (or a BMP file with --gen -o).
;
; Where you type: on Windows a prompt box along the bottom of the picture
; window (rt_image_ui in win.asm); with no operating system an "image> " line
; on the screen (img_console, which efi.asm's rt_image_ui runs), the picture
; then fills the screen until a key.
;
; The pipeline, SD-Turbo's single step, every part in this program:
;   ids     = CLIP tokens of the prompt                     (cliptok.asm)
;   context = text encoder(ids)                             (clipenc.asm)
;   noise   ~ N(0, 1), 64x64x4, from the seed
;   eps     = UNet(noise, t = 999, context)                 (unet.asm)
;   x0      = (noise - sqrt(1 - a) eps) / sqrt(a)           a = alphas_cumprod[999]
;             (--vpred: x0 = sqrt(a) noise - sqrt(1 - a) eps)
;   picture = TAESD decoder(x0)                             (taesd.asm)
;
;   img_generate(rcx=utf8 prompt, rdx=len) -> rax = pixels or 0
;   img_console()                          the prompt loop on a text screen
;   img_write(rcx=path utf16, rdx=pixels, r8=w, r9=h) -> rax = 1 or 0  (PNG by extension, else BMP)
;
; %included into asmrun.asm (bits 64, default rel) after taesd.asm.

section .text

IMG_SIDE  equ 512            ; SD-Turbo's picture: 512x512 (a 64x64 latent, 8x upsampled)
IMG_LAT   equ 64
IMG_T     equ 999            ; the one timestep ("trailing" spacing, one step)

; img_generate(rcx=utf8 prompt, rdx=len) -> rax = a new IMG_SIDE x IMG_SIDE
; BGRX picture of the prompt, or 0 when no picture was made (the reason on
; stderr). The model files are loaded on the first call. Preserves the
; nonvolatile registers.
img_generate:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32] t0, [+40] t1, [+48] y, [+56] x
  mov  rbx, rcx
  mov  rsi, rdx
  lea  rcx, [rel img_s_need_sd]
  cmp  qword [rel g_img_sd_path], 0
  je   .missing
  lea  rcx, [rel img_s_need_vocab]
  cmp  qword [rel g_ct_vocab_path], 0
  je   .missing
  lea  rcx, [rel img_s_need_merges]
  cmp  qword [rel g_ct_merges_path], 0
  je   .missing
  lea  rcx, [rel img_s_need_taesd]
  cmp  qword [rel g_img_taesd_path], 0
  je   .missing
  cmp  qword [rel g_img_loaded], 0
  jne  .loaded
  call rt_now_ns
  mov  [rsp+32], rax
  lea  rcx, [rel img_s_loading]
  call rt_err_str
  mov  rcx, [rel g_img_sd_path]
  call sd_index
  mov  rcx, [rel g_ct_vocab_path]
  mov  rdx, [rel g_ct_merges_path]
  call ct_load
  call ce_load
  mov  rcx, [rel g_img_taesd_path]
  call ta_load
  call un_init
  call img_sched
  mov  ecx, 66 * 66 * 8 * 4
  call rt_alloc
  mov  [rel g_img_x0], rax
  mov  qword [rel g_img_loaded], 1
  lea  rcx, [rel img_s_loaded]
  call rt_err_str
  call rt_now_ns
  mov  rdx, rax
  sub  rdx, [rsp+32]
  call model_emit_ms
  lea  rcx, [rel img_s_nl]
  call rt_err_str
.loaded:
  call rt_now_ns
  mov  [rsp+32], rax
  ; tokens and context
  mov  rcx, rbx
  mov  rdx, rsi
  lea  r8, [rel g_ct_ids]
  call ct_encode
  mov  r12, rax
  lea  rcx, [rel img_s_tokens]
  call rt_err_str
  mov  rcx, r12
  call rt_err_u64
  lea  rcx, [rel img_s_nl]
  call rt_err_str
  lea  rcx, [rel g_ct_ids]
  call ce_forward
  mov  r12, rax                ; the context, [80][1024]
  lea  rcx, [rel img_s_context]
  call rt_err_str
  call rt_now_ns
  mov  rdx, rax
  sub  rdx, [rsp+32]
  call model_emit_ms
  lea  rcx, [rel img_s_nl]
  call rt_err_str
  ; the noise, in the 8-channel padded latent (channels 4..7 stay zero)
  call un_arena_reset
  mov  ecx, 66 * 66 * 8 * 4
  call un_aalloc
  mov  r13, rax
  mov  rax, [rel g_img_seed]
  test rax, rax
  jnz  .seeded
  mov  rax, 0x9E3779B97F4A7C15
.seeded:
  mov  [rel g_img_rng], rax
  mov  qword [rsp+48], 1
.ny:
  cmp  qword [rsp+48], IMG_LAT
  ja   .noise_done
  mov  qword [rsp+56], 1
.nx:
  cmp  qword [rsp+56], IMG_LAT
  ja   .nyn
  mov  rax, [rsp+48]
  imul rax, rax, IMG_LAT + 2
  add  rax, [rsp+56]
  shl  rax, 5                  ; * 8 channels * 4 bytes
  lea  rdi, [r13 + rax]
  xor  r14d, r14d
.nc:
  cmp  r14d, 4
  jae  .ncd
  call img_normal              ; xmm0 = a sample
  vmovss [rdi + r14*4], xmm0
  inc  r14d
  jmp  .nc
.ncd:
  inc  qword [rsp+56]
  jmp  .nx
.nyn:
  inc  qword [rsp+48]
  jmp  .ny
.noise_done:
  ; the UNet
  mov  rcx, r13
  mov  rdx, r12
  mov  r8d, IMG_T
  call un_forward
  mov  r15, rax                ; eps, padded [66][66][4]
  ; x0 into g_img_x0 (8 channels, 4..7 zero)
  mov  rdi, [rel g_img_x0]
  mov  rcx, rdi
  xor  edx, edx
  mov  r8d, 66 * 66 * 8 * 4
  call rt_memset
  vbroadcastss ymm1, [rel g_img_s1]   ; sqrt(1 - a)
  vbroadcastss ymm2, [rel g_img_inv_s0] ; 1 / sqrt(a)
  vbroadcastss ymm3, [rel g_img_s0]   ; sqrt(a)
  mov  qword [rsp+48], 1
.xy:
  cmp  qword [rsp+48], IMG_LAT
  ja   .x0_done
  mov  qword [rsp+56], 1
.xx:
  cmp  qword [rsp+56], IMG_LAT
  ja   .xyn
  mov  rax, [rsp+48]
  imul rax, rax, IMG_LAT + 2
  add  rax, [rsp+56]           ; pixel index
  mov  rdx, rax
  shl  rdx, 4                  ; eps: 4 channels
  shl  rax, 5                  ; latent / x0: 8 channels
  vmovups xmm4, [r13 + rax]    ; noise (4 channels)
  vmovups xmm5, [r15 + rdx]    ; eps
  cmp  qword [rel g_img_vpred], 0
  jne  .vpred
  vmulps xmm5, xmm5, xmm1      ; sqrt(1-a) eps
  vsubps xmm4, xmm4, xmm5      ; noise - ...
  vmulps xmm4, xmm4, xmm2      ; / sqrt(a)
  jmp  .x0st
.vpred:
  vmulps xmm4, xmm4, xmm3      ; sqrt(a) noise
  vmulps xmm5, xmm5, xmm1
  vsubps xmm4, xmm4, xmm5
.x0st:
  vmovups [rdi + rax], xmm4
  inc  qword [rsp+56]
  jmp  .xx
.xyn:
  inc  qword [rsp+48]
  jmp  .xy
.x0_done:
  vzeroupper
  lea  rcx, [rel img_s_unet]
  call rt_err_str
  call rt_now_ns
  mov  rdx, rax
  sub  rdx, [rsp+32]
  call model_emit_ms
  lea  rcx, [rel img_s_nl]
  call rt_err_str
  ; the decoder
  call un_arena_reset
  mov  rcx, [rel g_img_x0]
  call ta_decode
  mov  r14, rax
  lea  rcx, [rel img_s_done]
  call rt_err_str
  call rt_now_ns
  mov  rdx, rax
  sub  rdx, [rsp+32]
  call model_emit_ms
  lea  rcx, [rel img_s_nl]
  call rt_err_str
  mov  rax, r14
  jmp  .ret
.missing:
  call rt_err_str
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; img_normal() -> xmm0 = one N(0, 1) sample: twelve uniforms of xorshift64*
; summed, minus 6. Clobbers rax, rcx, rdx, xmm0, xmm1.
img_normal:
  vxorps xmm0, xmm0, xmm0
  mov  ecx, 12
.l:
  mov  rax, [rel g_img_rng]
  mov  rdx, rax
  shr  rdx, 12
  xor  rax, rdx
  mov  rdx, rax
  shl  rdx, 25
  xor  rax, rdx
  mov  rdx, rax
  shr  rdx, 27
  xor  rax, rdx
  mov  [rel g_img_rng], rax
  mov  rdx, 0x2545F4914F6CDD1D
  imul rax, rdx
  shr  rax, 40                 ; 24 random bits
  vcvtsi2ss xmm1, xmm1, eax
  vmulss xmm1, xmm1, [rel img_2m24]
  vaddss xmm0, xmm0, xmm1
  dec  ecx
  jnz  .l
  vsubss xmm0, xmm0, [rel img_six]
  ret

; img_sched() — alphas_cumprod[IMG_T] of the scaled-linear schedule (betas from
; 0.00085 to 0.012 over 1000 steps, squared from their square roots), in f64;
; then sqrt(a), sqrt(1 - a), 1/sqrt(a) as f32. Prints a.
img_sched:
  push rbx
  WINPROLOGUE
  vmovsd xmm0, [rel img_one64] ; a
  xor  ebx, ebx
.l:
  cmp  ebx, IMG_T
  ja   .d
  vcvtsi2sd xmm1, xmm1, ebx
  vdivsd xmm1, xmm1, [rel img_999]
  vmulsd xmm1, xmm1, [rel img_ds]
  vaddsd xmm1, xmm1, [rel img_s0]       ; sqrt(beta_i)
  vmulsd xmm1, xmm1, xmm1
  vmovsd xmm2, [rel img_one64]
  vsubsd xmm2, xmm2, xmm1
  vmulsd xmm0, xmm0, xmm2
  inc  ebx
  jmp  .l
.d:
  vsqrtsd xmm1, xmm0, xmm0
  vcvtsd2ss xmm1, xmm1, xmm1
  vmovss [rel g_img_s0], xmm1
  vmovsd xmm2, [rel img_one64]
  vdivsd xmm2, xmm2, xmm0
  vsqrtsd xmm2, xmm2, xmm2
  vcvtsd2ss xmm2, xmm2, xmm2
  vmovss [rel g_img_inv_s0], xmm2
  vmovsd xmm2, [rel img_one64]
  vsubsd xmm2, xmm2, xmm0
  vsqrtsd xmm2, xmm2, xmm2
  vcvtsd2ss xmm2, xmm2, xmm2
  vmovss [rel g_img_s1], xmm2
  lea  rcx, [rel img_s_sched]
  call rt_err_str
  vmovss xmm0, [rel g_img_s0]
  call rt_err_f32
  lea  rcx, [rel img_s_sched2]
  call rt_err_str
  vmovss xmm0, [rel g_img_s1]
  call rt_err_f32
  lea  rcx, [rel img_s_nl]
  call rt_err_str
  WINEPILOGUE
  pop  rbx
  ret

; img_write_bmp(rcx=path utf16, rdx=pixels BGRX, r8=w, r9=h) -> rax = 1 when
; written: a 32-bit BI_RGB top-down BMP (the form view_load_bmp reads).
img_write_bmp:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  mov  rcx, rdi
  imul rcx, r12
  shl  rcx, 2
  add  rcx, 54
  call rt_alloc
  mov  rcx, rax
  mov  word [rax], 0x4D42      ; 'BM'
  mov  rdx, rdi
  imul rdx, r12
  shl  rdx, 2
  lea  r8d, [rdx + 54]
  mov  [rax+2], r8d            ; file size
  mov  dword [rax+6], 0
  mov  dword [rax+10], 54      ; bfOffBits
  mov  dword [rax+14], 40      ; biSize
  mov  [rax+18], edi           ; biWidth
  mov  r8d, r12d
  neg  r8d
  mov  [rax+22], r8d           ; biHeight < 0: top row first
  mov  word [rax+26], 1
  mov  word [rax+28], 32
  mov  dword [rax+30], 0       ; BI_RGB
  mov  [rax+34], edx           ; biSizeImage
  mov  dword [rax+38], 2835    ; 72 dpi
  mov  dword [rax+42], 2835
  mov  dword [rax+46], 0
  mov  dword [rax+50], 0
  push rcx
  push rcx
  lea  rcx, [rax + 54]
  mov  r8, rdx
  mov  rdx, rsi
  call rt_memcpy
  pop  rdx
  pop  rdx
  mov  rcx, rbx
  mov  r8, rdi
  imul r8, r12
  shl  r8, 2
  add  r8, 54
  call rt_write_file
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; img_write(rcx=path utf16, rdx=pixels BGRX, r8=w, r9=h) -> rax = 1 when
; written: PNG when the path ends in ".png", else BMP.
img_write:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  xor  eax, eax
.len:
  cmp  word [rbx + rax*2], 0
  je   .ext
  inc  rax
  jmp  .len
.ext:
  cmp  rax, 4
  jb   .bmp
  lea  rcx, [rbx + rax*2 - 8]  ; the last four characters
  cmp  word [rcx], '.'
  jne  .bmp
  movzx edx, word [rcx+2]
  or   edx, 0x20
  cmp  edx, 'p'
  jne  .bmp
  movzx edx, word [rcx+4]
  or   edx, 0x20
  cmp  edx, 'n'
  jne  .bmp
  movzx edx, word [rcx+6]
  or   edx, 0x20
  cmp  edx, 'g'
  jne  .bmp
  mov  rcx, rbx
  mov  rdx, rsi
  mov  r8, rdi
  mov  r9, r12
  call img_write_png
  jmp  .ret
.bmp:
  mov  rcx, rbx
  mov  rdx, rsi
  mov  r8, rdi
  mov  r9, r12
  call img_write_bmp
.ret:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; img_be32(rcx=ptr, edx=value) — store big-endian. Leaf.
img_be32:
  bswap edx
  mov  [rcx], edx
  ret

; img_crc32(rcx=ptr, rdx=len) -> eax: the PNG CRC-32 (reflected 0xEDB88320).
; Leaf; clobbers rax, rcx, rdx, r8, r9.
img_crc32:
  mov  eax, 0xFFFFFFFF
.b:
  test rdx, rdx
  jz   .d
  movzx r8d, byte [rcx]
  xor  eax, r8d
  mov  r9d, 8
.bit:
  mov  r8d, eax
  shr  eax, 1
  and  r8d, 1
  neg  r8d
  and  r8d, 0xEDB88320
  xor  eax, r8d
  dec  r9d
  jnz  .bit
  inc  rcx
  dec  rdx
  jmp  .b
.d:
  not  eax
  ret

; img_adler32(rcx=ptr, rdx=len) -> eax: zlib's Adler-32. Leaf; clobbers rax,
; rcx, rdx, r8, r9, r10.
img_adler32:
  mov  r8d, 1                  ; a
  xor  r9d, r9d                ; b
.b:
  test rdx, rdx
  jz   .d
  movzx eax, byte [rcx]
  add  r8d, eax
  cmp  r8d, 65521
  jb   .a_ok
  sub  r8d, 65521
.a_ok:
  add  r9d, r8d
  cmp  r9d, 65521
  jb   .b_ok
  sub  r9d, 65521
.b_ok:
  inc  rcx
  dec  rdx
  jmp  .b
.d:
  mov  eax, r9d
  shl  eax, 16
  or   eax, r8d
  ret

; img_write_png(rcx=path utf16, rdx=pixels BGRX, r8=w, r9=h) -> rax = 1 when
; written: 8-bit RGB, no interlace, the image data in a zlib stream of stored
; (uncompressed) deflate blocks.
img_write_png:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32] raw stream, [+40] file size, [+48] write pointer, [+56] IDAT start
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r12, r9
  ; the raw stream: per row a filter byte 0, then RGB
  mov  r13, rdi
  imul r13, r13, 3
  inc  r13
  imul r13, r12                ; raw bytes
  mov  rcx, r13
  call rt_alloc
  mov  [rsp+32], rax
  mov  r8, rax                 ; raw write pointer
  mov  r9, rsi                 ; pixel read pointer
  xor  r10d, r10d              ; y
.ry:
  cmp  r10, r12
  jae  .raw_done
  mov  byte [r8], 0
  inc  r8
  xor  r11d, r11d
.rx:
  cmp  r11, rdi
  jae  .ryn
  mov  al, [r9 + 2]            ; R
  mov  [r8], al
  mov  al, [r9 + 1]            ; G
  mov  [r8 + 1], al
  mov  al, [r9]                ; B
  mov  [r8 + 2], al
  add  r8, 3
  add  r9, 4
  inc  r11
  jmp  .rx
.ryn:
  inc  r10
  jmp  .ry
.raw_done:
  ; blocks of at most 65535 bytes
  mov  rax, r13
  add  rax, 65534
  xor  edx, edx
  mov  ecx, 65535
  div  rcx
  mov  r14, rax                ; blocks
  ; file = signature 8 + IHDR 25 + IDAT (12 + 2 + 5 blocks + raw + 4) + IEND 12
  lea  rax, [r14*4]
  add  rax, r14
  add  rax, r13
  add  rax, 8 + 25 + 12 + 2 + 4 + 12
  mov  [rsp+40], rax
  mov  rcx, rax
  call rt_alloc
  mov  r15, rax
  mov  [rsp+48], rax
  ; signature
  mov  rax, 0x0A1A0A0D474E5089
  mov  [r15], rax
  ; IHDR
  lea  rcx, [r15 + 8]
  mov  edx, 13
  call img_be32
  mov  dword [r15 + 12], 'IHDR'
  lea  rcx, [r15 + 16]
  mov  edx, edi
  call img_be32
  lea  rcx, [r15 + 20]
  mov  edx, r12d
  call img_be32
  mov  byte [r15 + 24], 8      ; bit depth
  mov  byte [r15 + 25], 2      ; colour type: RGB
  mov  byte [r15 + 26], 0
  mov  byte [r15 + 27], 0
  mov  byte [r15 + 28], 0
  lea  rcx, [r15 + 12]
  mov  edx, 17
  call img_crc32
  lea  rcx, [r15 + 29]
  mov  edx, eax
  call img_be32
  ; IDAT
  lea  rcx, [r15 + 33]
  lea  rdx, [r14*4]
  add  rdx, r14
  add  rdx, r13
  add  rdx, 6                  ; zlib header 2 + adler 4
  call img_be32
  mov  dword [r15 + 37], 'IDAT'
  mov  byte [r15 + 41], 0x78   ; zlib: deflate, 32 KB window
  mov  byte [r15 + 42], 0x01   ; no preset dictionary, fastest
  lea  r8, [r15 + 43]          ; write pointer
  mov  r9, [rsp+32]            ; raw read pointer
  mov  r10, r13                ; raw bytes left
.blk:
  test r10, r10
  jz   .blk_done
  mov  r11, r10
  cmp  r11, 65535
  jbe  .last
  mov  r11d, 65535
  mov  byte [r8], 0            ; BFINAL 0, BTYPE 00 (stored)
  jmp  .hdr
.last:
  mov  byte [r8], 1            ; the final block
.hdr:
  mov  [r8 + 1], r11w          ; LEN
  mov  eax, r11d
  not  eax
  mov  [r8 + 3], ax            ; NLEN
  add  r8, 5
  mov  [rsp+56], r8
  mov  rcx, r8
  mov  rdx, r9
  mov  r8, r11
  push r9
  push r10
  push r11
  push r11
  call rt_memcpy
  pop  r11
  pop  r11
  pop  r10
  pop  r9
  mov  r8, [rsp+56]
  add  r8, r11
  add  r9, r11
  sub  r10, r11
  jmp  .blk
.blk_done:
  mov  [rsp+56], r8
  mov  rcx, [rsp+32]
  mov  rdx, r13
  call img_adler32
  mov  rcx, [rsp+56]
  mov  edx, eax
  call img_be32
  add  qword [rsp+56], 4
  lea  rcx, [r15 + 37]         ; CRC over "IDAT" + the zlib stream
  mov  rdx, [rsp+56]
  sub  rdx, rcx
  call img_crc32
  mov  rcx, [rsp+56]
  mov  edx, eax
  call img_be32
  add  qword [rsp+56], 4
  ; IEND
  mov  rcx, [rsp+56]
  xor  edx, edx
  call img_be32
  mov  rcx, [rsp+56]
  mov  dword [rcx + 4], 'IEND'
  lea  rcx, [rcx + 8]
  mov  edx, 0xAE426082
  call img_be32
  mov  rcx, rbx
  mov  rdx, r15
  mov  r8, [rsp+40]
  call rt_write_file
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; img_console() — the prompt loop where the screen has no text box (the UEFI
; build): "image> ", one typed line, img_generate, the picture on the whole
; screen until a key (rt_view_show), again; an empty line (or Esc) ends it.
; Preserves the nonvolatile registers.
img_console:
  push rbx
  push rsi
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = line length
  mov  qword [rel g_stdin_line], 1 ; rt_read_stdin returns at the end of a line
.again:
  lea  rcx, [rel i_prompt_mark]
  call rt_out_str
  lea  rcx, [rsp+32]
  call rt_read_stdin
  mov  rsi, rax
  mov  rbx, [rsp+32]
.strip:                        ; drop the line end ("\n", "\r\n" or "\r")
  test rbx, rbx
  jz   .done
  mov  al, [rsi + rbx - 1]
  cmp  al, 10
  je   .cut
  cmp  al, 13
  jne  .have
.cut:
  dec  rbx
  jmp  .strip
.have:
  mov  rcx, rsi
  mov  rdx, rbx
  call img_generate
  test rax, rax
  jz   .again                  ; the reason was printed
  mov  rcx, rax
  mov  edx, IMG_SIDE
  mov  r8d, IMG_SIDE
  call rt_view_show
  jmp  .again
.done:
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

section .data
  align 8
g_img_sd_path:    dq 0        ; --sd FILE (utf16), the SD-Turbo GGUF
g_img_taesd_path: dq 0        ; --taesd FILE, the TAESD safetensors
g_img_out_path:   dq 0        ; -o FILE (--gen)
g_img_seed:       dq 0        ; --seed N
g_img_vpred:      dq 0        ; --vpred
g_img_loaded:     dq 0
g_img_rng:        dq 0
g_img_x0:         dq 0        ; the denoised latent, padded [66][66][8]
img_one64:        dq 1.0
img_999:          dq 999.0
img_s0:           dq 0.029154759474226502   ; sqrt(0.00085)
img_ds:           dq 0.080389752026806708   ; sqrt(0.012) - sqrt(0.00085)
img_2m24:         dd 5.9604644775390625e-8  ; 2^-24
img_six:          dd 6.0
g_img_s0:         dd 0        ; sqrt(a)
g_img_s1:         dd 0        ; sqrt(1 - a)
g_img_inv_s0:     dd 0        ; 1 / sqrt(a)
i_prompt_mark:    db 10, "image> ", 0
img_s_need_sd:    db "asmrun: no picture: --sd <sd_turbo.gguf> was not given", 10, 0
img_s_need_vocab: db "asmrun: no picture: --vocab <vocab.json> was not given", 10, 0
img_s_need_merges: db "asmrun: no picture: --merges <merges.txt> was not given", 10, 0
img_s_need_taesd: db "asmrun: no picture: --taesd <taesd.safetensors> was not given", 10, 0
img_s_loading:    db "asmrun: loading SD-Turbo, the tokenizer and TAESD", 10, 0
img_s_loaded:     db "asmrun: loaded, ms=", 0
img_s_sched:      db "asmrun: scheduler t=999 sqrt(a)=", 0
img_s_sched2:     db " sqrt(1-a)=", 0
img_s_tokens:     db "asmrun: clip_tokens=", 0
img_s_context:    db "asmrun: text encoder done, ms=", 0
img_s_unet:       db "asmrun: unet step done, ms=", 0
img_s_done:       db "asmrun: picture done, ms=", 0
img_s_nl:         db 10, 0
section .text

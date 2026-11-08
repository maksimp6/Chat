; view.asm — the picture viewer's shared half: a picture in memory, loaded from
; a BMP file or drawn as a test card, and the scaling both screens use. The
; platform half is rt_view_show: a window on Windows (win.asm, user32 + gdi32),
; the whole screen through EFI_GRAPHICS_OUTPUT_PROTOCOL with no operating
; system (efi.asm).
;
; A picture is 32-bit BGRX, top row first, 4*w bytes per row, no padding: the
; pixel layout of a top-down 32-bit GDI DIB and of EFI_GRAPHICS_OUTPUT_BLT_PIXEL
; alike, so neither half converts it. The image generator will hand its output
; to rt_view_show in the same form.
;
;   view_load_bmp(rcx=path utf16) -> rax=pixels, rdx=w, r8=h
;   view_testcard()               -> rax=pixels (VIEW_CARD x VIEW_CARD)
;   view_fit(rcx=w, rdx=h, r8=SW, r9=SH) -> rax=dw, rdx=dh
;   view_blit(rcx=src, rdx=w, r8=h, r9=dst, [5]=SW, [6]=SH)
;
; %included into asmrun.asm (bits 64, default rel) after tokenizer.asm, which
; ends in .data: the code below must say .text itself.

section .text

VIEW_CARD     equ 512        ; the test card's side, SD-Turbo's output size
VIEW_MAX_SIDE equ 16384      ; a BMP side larger than this is refused

; view_load_bmp(rcx=path utf16) -> rax = pixels, rdx = w, r8 = h. Reads an
; uncompressed BMP (24-bit, or 32-bit BI_RGB, or 32-bit BI_BITFIELDS with the
; plain X8R8G8B8 masks), bottom-up or top-down, into a new BGRX picture. Any
; other file is refused with rt_fatal. Preserves the nonvolatile registers.
view_load_bmp:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = file length
  lea  rdx, [rsp+32]
  call rt_read_file            ; rax = the file's bytes
  mov  rsi, rax
  mov  rbx, [rsp+32]
  lea  rcx, [rel v_err_bmp]
  cmp  rbx, 54                 ; BITMAPFILEHEADER (14) + BITMAPINFOHEADER (40)
  jb   .bad
  cmp  word [rsi], 0x4D42      ; 'BM'
  jne  .bad
  cmp  dword [rsi+14], 40      ; biSize: BITMAPINFOHEADER or a later, longer header
  jb   .bad
  movsxd r12, dword [rsi+18]   ; biWidth
  movsxd r13, dword [rsi+22]   ; biHeight: negative = top row first
  xor  r14d, r14d              ; 1 = top-down rows
  test r13, r13
  jns  .hpos
  neg  r13
  mov  r14d, 1
.hpos:
  test r12, r12
  jle  .bad
  cmp  r12, VIEW_MAX_SIDE
  ja   .bad
  test r13, r13
  jz   .bad
  cmp  r13, VIEW_MAX_SIDE
  ja   .bad
  movzx r15d, word [rsi+28]    ; biBitCount
  mov  eax, [rsi+30]           ; biCompression
  cmp  r15d, 24
  je   .c24
  cmp  r15d, 32
  jne  .badfmt
  test eax, eax                ; BI_RGB
  jz   .fmt_ok
  cmp  eax, 3                  ; BI_BITFIELDS: only the plain BGRX masks
  jne  .badfmt
  cmp  rbx, 66                 ; the R, G, B masks at offset 54 (in a V4/V5 header or after a 40-byte one)
  jb   .bad
  cmp  dword [rsi+54], 0x00FF0000
  jne  .badfmt
  cmp  dword [rsi+58], 0x0000FF00
  jne  .badfmt
  cmp  dword [rsi+62], 0x000000FF
  jne  .badfmt
  jmp  .fmt_ok
.c24:
  test eax, eax
  jnz  .badfmt
.fmt_ok:
  mov  rax, r12                ; stored row = w*bpp/8 bytes, padded to 4
  imul rax, r15
  shr  rax, 3
  add  rax, 3
  and  rax, -4
  mov  rdi, rax
  mov  eax, [rsi+10]           ; bfOffBits: where the rows start
  mov  rcx, rdi
  imul rcx, r13
  add  rcx, rax
  cmp  rcx, rbx                ; every row inside the file
  ja   .short
  add  rsi, rax                ; rsi = the first stored row
  mov  rcx, r12
  imul rcx, r13
  shl  rcx, 2
  call rt_alloc
  mov  rbx, rax                ; the picture
  xor  r9d, r9d                ; y: output row, top first
.yl:
  cmp  r9, r13
  jae  .ydone
  mov  rax, r9
  test r14d, r14d
  jnz  .td
  mov  rax, r13                ; bottom-up file: output row y is stored row h-1-y
  dec  rax
  sub  rax, r9
.td:
  imul rax, rdi
  lea  r10, [rsi + rax]        ; source row
  mov  rax, r9
  imul rax, r12
  lea  r11, [rbx + rax*4]      ; destination row
  xor  ecx, ecx                ; x
  cmp  r15d, 24
  jne  .x32
.x24:
  cmp  rcx, r12
  jae  .ynext
  movzx eax, word [r10]        ; B, G
  movzx edx, byte [r10+2]      ; R
  shl  edx, 16
  or   eax, edx
  mov  [r11 + rcx*4], eax
  add  r10, 3
  inc  rcx
  jmp  .x24
.x32:
  cmp  rcx, r12
  jae  .ynext
  mov  eax, [r10 + rcx*4]
  and  eax, 0x00FFFFFF         ; the X byte is not alpha here: clear it
  mov  [r11 + rcx*4], eax
  inc  rcx
  jmp  .x32
.ynext:
  inc  r9
  jmp  .yl
.ydone:
  mov  rax, rbx
  mov  rdx, r12
  mov  r8, r13
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.badfmt:
  lea  rcx, [rel v_err_fmt]
  jmp  .bad
.short:
  lea  rcx, [rel v_err_short]
.bad:
  call rt_fatal

; view_testcard() -> rax = a new VIEW_CARD x VIEW_CARD BGRX picture: the top
; three quarters a gradient (red rises left to right, blue falls, green rises
; top to bottom), the bottom quarter eight bars in the order white, yellow,
; cyan, green, magenta, red, blue, black. A wrong channel order or a flipped
; picture is visible at a glance.
view_testcard:
  WINPROLOGUE
  mov  ecx, VIEW_CARD * VIEW_CARD * 4
  call rt_alloc
  mov  r11, rax
  mov  r8, rax                 ; write pointer
  xor  r9d, r9d                ; y
.y:
  cmp  r9d, VIEW_CARD
  jae  .done
  xor  r10d, r10d              ; x
.x:
  cmp  r10d, VIEW_CARD
  jae  .yn
  cmp  r9d, VIEW_CARD * 3 / 4
  jae  .bar
  mov  eax, r10d
  shr  eax, 1                  ; R = x/2: 0..255
  lea  edx, [r9d + r9d]
  imul edx, edx, 0xAAAB
  shr  edx, 17                 ; G = 2y/3: 0..255 over the 384 gradient rows
  mov  ecx, 255
  sub  ecx, eax                ; B = 255 - R
  shl  eax, 16
  shl  edx, 8
  or   eax, edx
  or   eax, ecx
  jmp  .st
.bar:
  mov  eax, r10d
  shr  eax, 6                  ; bar 0..7, 64 pixels wide
  lea  rdx, [rel v_bars]
  mov  eax, [rdx + rax*4]
.st:
  mov  [r8], eax
  add  r8, 4
  inc  r10d
  jmp  .x
.yn:
  inc  r9d
  jmp  .y
.done:
  mov  rax, r11
  WINEPILOGUE
  ret

; view_fit(rcx=w, rdx=h, r8=SW, r9=SH) -> rax = dw, rdx = dh: the size the
; picture is drawn at in an SW x SH area. A picture that fits is enlarged by
; the largest whole factor that still fits (pixels stay square and sharp); a
; larger one is shrunk to fit, keeping its proportions. All four inputs >= 1.
; Leaf; clobbers rax, rcx, rdx, r10, r11.
view_fit:
  mov  r10, rcx
  mov  r11, rdx
  cmp  r10, r8
  ja   .shrink
  cmp  r11, r9
  ja   .shrink
  mov  rax, r8
  xor  edx, edx
  div  r10                     ; SW / w
  mov  rcx, rax
  mov  rax, r9
  xor  edx, edx
  div  r11                     ; SH / h
  cmp  rax, rcx
  jbe  .s
  mov  rax, rcx
.s:
  mov  rcx, rax                ; the whole factor
  mov  rax, r10
  imul rax, rcx
  mov  rdx, r11
  imul rdx, rcx
  ret
.shrink:
  mov  rax, r10
  imul rax, r9                 ; w*SH
  mov  rcx, r11
  imul rcx, r8                 ; h*SW
  cmp  rax, rcx
  ja   .by_width
  xor  edx, edx                ; the height limits: dh = SH, dw = w*SH/h
  div  r11
  test rax, rax
  jnz  .hw
  mov  eax, 1
.hw:
  mov  rdx, r9
  ret
.by_width:
  mov  rax, rcx                ; the width limits: dw = SW, dh = h*SW/w
  xor  edx, edx
  div  r10
  test rax, rax
  jnz  .wh
  mov  eax, 1
.wh:
  mov  rdx, rax
  mov  rax, r8
  ret

; view_blit(rcx=src, rdx=w, r8=h, r9=dst, [5]=SW, [6]=SH) — write the whole
; SW x SH BGRX area dst (4*SW bytes per row): the picture at view_fit's size,
; centred, nearest neighbour; black around it. Preserves the nonvolatile
; registers.
view_blit:
  push rbx
  push rbp
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  mov  rsi, rcx                ; src
  mov  r12, rdx                ; w
  mov  r13, r8                 ; h
  mov  rdi, r9                 ; dst (write pointer)
  mov  r14, [rsp + 64 + 40]    ; SW (8 pushes above the caller's arguments)
  mov  r15, [rsp + 64 + 48]    ; SH
  mov  rcx, r12
  mov  rdx, r13
  mov  r8, r14
  mov  r9, r15
  call view_fit
  mov  rbx, rax                ; dw
  mov  rbp, rdx                ; dh
  sub  rsp, 32                 ; [rsp] = left margin, [rsp+8] = top margin, [rsp+16] = dy
  mov  rax, r14
  sub  rax, rbx
  shr  rax, 1
  mov  [rsp], rax
  mov  rax, r15
  sub  rax, rbp
  shr  rax, 1
  mov  [rsp+8], rax
  mov  qword [rsp+16], 0
.row:
  mov  rax, [rsp+16]
  cmp  rax, r15
  jae  .done
  sub  rax, [rsp+8]            ; dy - top: above the picture it wraps to a huge value
  cmp  rax, rbp
  jae  .blackrow
  mul  r13
  div  rbp                     ; sy = (dy-top)*h/dh
  mul  r12
  lea  r8, [rsi + rax*4]       ; source row
  xor  ecx, ecx                ; dx
.col:
  cmp  rcx, r14
  jae  .nextrow
  mov  rax, rcx
  sub  rax, [rsp]
  cmp  rax, rbx
  jae  .black
  mul  r12
  div  rbx                     ; sx = (dx-left)*w/dw
  mov  eax, [r8 + rax*4]
  mov  [rdi], eax
  jmp  .adv
.black:
  mov  dword [rdi], 0
.adv:
  add  rdi, 4
  inc  rcx
  jmp  .col
.blackrow:
  mov  rcx, r14
.zr:
  mov  dword [rdi], 0
  add  rdi, 4
  dec  rcx
  jnz  .zr
.nextrow:
  inc  qword [rsp+16]
  jmp  .row
.done:
  add  rsp, 32
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbp
  pop  rbx
  ret

section .data
  align 4
v_bars:       dd 0x00FFFFFF, 0x00FFFF00, 0x0000FFFF, 0x0000FF00, 0x00FF00FF, 0x00FF0000, 0x000000FF, 0x00000000
v_err_bmp:    db "not a BMP picture asmrun can read (a BMP file, 1..16384 pixels a side)", 0
v_err_fmt:    db "this BMP is not uncompressed 24-bit or 32-bit BGR(X)", 0
v_err_short:  db "the BMP file ends before its last row", 0
section .text

; cliptok.asm — the CLIP BPE tokenizer of SD-Turbo's text encoder (OpenCLIP
; ViT-H/14 text tower): a prompt in, 77 token ids out.
;
; The rules, as CLIP's simple_tokenizer.py and the Hugging Face CLIPTokenizer
; apply them:
;   - ASCII letters are lowercased; whitespace separates words and is dropped
;   - a pre-token is a contraction ('s 't 're 've 'm 'll 'd), a run of letters,
;     one digit, or a run of anything else that is not whitespace (bytes >= 0x80
;     count as letters: non-ASCII words stay whole)
;   - each byte becomes a character of CLIP's bytes_to_unicode table (UTF-8),
;     the last character of a pre-token gets "</w>", and adjacent symbols are
;     merged lowest merge rank first (merges.txt, line order) until no
;     adjacent pair has a rank
;   - every final symbol is looked up in vocab.json
;   - ids = <|startoftext|> (49406), up to 75 text tokens, <|endoftext|>
;     (49407), then 0 up to 77 (OpenCLIP pads with 0)
;
;   ct_load(rcx=vocab.json path, rdx=merges.txt path)  utf16 paths
;   ct_encode(rcx=utf8, rdx=len, r8=u32 ids[77]) -> rax = ids before the padding
;
; %included into asmrun.asm (bits 64, default rel) after gguf.asm.

section .text

CT_HT_BITS  equ 17
CT_HT_SIZE  equ 1 << CT_HT_BITS  ; slots per hash table: vocab 49408, merges ~48894
CT_HT_MASK  equ CT_HT_SIZE - 1
CT_CTX      equ 77               ; the text encoder's positions
CT_BOS      equ 49406            ; <|startoftext|>
CT_EOS      equ 49407            ; <|endoftext|>
CT_WORD_MAX equ 256              ; bytes of one pre-token (a longer one is cut)

; ct_hash(rcx=ptr, rdx=len) -> eax: FNV-1a. Leaf; clobbers rax, r8, r9.
ct_hash:
  mov  eax, 0x811C9DC5
  xor  r8d, r8d
.l:
  cmp  r8, rdx
  jae  .d
  movzx r9d, byte [rcx + r8]
  xor  eax, r9d
  imul eax, eax, 0x01000193
  inc  r8
  jmp  .l
.d:
  ret

; ct_put(rcx=table, rdx=ptr, r8=len, r9d=value) — add a key (the bytes stay
; where they are: the table keeps the pointer). A slot is 16 bytes: u64 ptr,
; u32 len, u32 value; linear probing. Preserves the nonvolatile registers.
ct_put:
  push rbx
  push rsi
  push rdi
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  r10d, r9d
  mov  rcx, rsi
  mov  rdx, rdi
  call ct_hash
  and  eax, CT_HT_MASK
.p:
  mov  ecx, eax
  shl  rcx, 4
  cmp  qword [rbx + rcx], 0
  je   .empty
  inc  eax
  and  eax, CT_HT_MASK
  jmp  .p
.empty:
  mov  [rbx + rcx], rsi
  mov  [rbx + rcx + 8], edi
  mov  [rbx + rcx + 12], r10d
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ct_get(rcx=table, rdx=ptr, r8=len) -> eax = the value of the first key equal
; to the len bytes at ptr, or -1. Preserves the nonvolatile registers.
ct_get:
  push rbx
  push rsi
  push rdi
  push r12
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  mov  rcx, rsi
  mov  rdx, rdi
  call ct_hash
  and  eax, CT_HT_MASK
  mov  r12d, eax
.p:
  mov  ecx, r12d
  shl  rcx, 4
  mov  r8, [rbx + rcx]
  test r8, r8
  jz   .miss
  cmp  [rbx + rcx + 8], edi
  jne  .next
  xor  r9d, r9d
.c:
  cmp  r9, rdi
  jae  .hit
  mov  al, [r8 + r9]
  cmp  al, [rsi + r9]
  jne  .next
  inc  r9
  jmp  .c
.hit:
  mov  eax, [rbx + rcx + 12]
  jmp  .ret
.next:
  inc  r12d
  and  r12d, CT_HT_MASK
  jmp  .p
.miss:
  mov  eax, -1
.ret:
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ct_ws — advance rsi over JSON whitespace, not past rbx. Leaf; clobbers al.
ct_ws:
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
  jmp  ct_ws
.d:
  ret

; ct_hex4 — four hex digits at rsi (not past rbx) -> eax, rsi += 4. A bad
; digit is rt_fatal. Clobbers rax, rcx, rdx.
ct_hex4:
  lea  rax, [rsi + 4]
  cmp  rax, rbx
  ja   .bad
  xor  eax, eax
  mov  ecx, 4
.l:
  movzx edx, byte [rsi]
  inc  rsi
  shl  eax, 4
  sub  edx, '0'
  cmp  edx, 9
  jbe  .add
  sub  edx, 'A' - '0'
  and  edx, ~0x20 & 0xFF       ; 'a'..'f' -> 'A'..'F' (relative)
  cmp  edx, 5
  ja   .bad
  add  edx, 10
.add:
  or   eax, edx
  dec  ecx
  jnz  .l
  ret
.bad:
  lea  rcx, [rel ct_err_json]
  call rt_fatal

; ct_utf8_put(eax=code point) — write it as UTF-8 at rdi, advance rdi. Leaf;
; clobbers rax, rdx.
ct_utf8_put:
  cmp  eax, 0x80
  jae  .b2
  mov  [rdi], al
  inc  rdi
  ret
.b2:
  cmp  eax, 0x800
  jae  .b3
  mov  edx, eax
  shr  edx, 6
  or   dl, 0xC0
  mov  [rdi], dl
  and  al, 0x3F
  or   al, 0x80
  mov  [rdi+1], al
  add  rdi, 2
  ret
.b3:
  cmp  eax, 0x10000
  jae  .b4
  mov  edx, eax
  shr  edx, 12
  or   dl, 0xE0
  mov  [rdi], dl
  mov  edx, eax
  shr  edx, 6
  and  dl, 0x3F
  or   dl, 0x80
  mov  [rdi+1], dl
  and  al, 0x3F
  or   al, 0x80
  mov  [rdi+2], al
  add  rdi, 3
  ret
.b4:
  mov  edx, eax
  shr  edx, 18
  or   dl, 0xF0
  mov  [rdi], dl
  mov  edx, eax
  shr  edx, 12
  and  dl, 0x3F
  or   dl, 0x80
  mov  [rdi+1], dl
  mov  edx, eax
  shr  edx, 6
  and  dl, 0x3F
  or   dl, 0x80
  mov  [rdi+2], dl
  and  al, 0x3F
  or   al, 0x80
  mov  [rdi+3], al
  add  rdi, 4
  ret

; ct_load(rcx=vocab.json path, rdx=merges.txt path) — read both files into
; the two hash tables and build the bytes_to_unicode table. A malformed file
; is rt_fatal. Preserves the nonvolatile registers.
ct_load:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32] vocab length, [+40] merges path, [+48] merges length, [+56] vocab path
  mov  [rsp+56], rcx
  mov  [rsp+40], rdx
  mov  ecx, CT_HT_SIZE * 16
  call rt_alloc
  mov  [rel g_ct_vocab], rax
  mov  ecx, CT_HT_SIZE * 16
  call rt_alloc
  mov  [rel g_ct_merges], rax

  ; bytes_to_unicode: printable bytes ('!'..'~', 0xA1..0xAC, 0xAE..0xFF) map
  ; to themselves, the other 68 to 256, 257, ... in byte order
  xor  ecx, ecx                ; byte
  xor  r8d, r8d                ; n
  lea  r9, [rel g_ct_b2u]
.bt:
  cmp  ecx, 256
  jae  .btdone
  mov  eax, ecx
  cmp  ecx, '!'
  jb   .other
  cmp  ecx, '~'
  jbe  .self
  cmp  ecx, 0xA1
  jb   .other
  cmp  ecx, 0xAC
  jbe  .self
  cmp  ecx, 0xAE
  jae  .self
.other:
  lea  eax, [r8d + 256]
  inc  r8d
.self:
  cmp  eax, 0x80
  jae  .two
  mov  byte [r9 + rcx*4], 1
  mov  [r9 + rcx*4 + 1], al
  jmp  .btnext
.two:
  mov  byte [r9 + rcx*4], 2
  mov  edx, eax
  shr  edx, 6
  or   dl, 0xC0
  mov  [r9 + rcx*4 + 1], dl
  and  al, 0x3F
  or   al, 0x80
  mov  [r9 + rcx*4 + 2], al
.btnext:
  inc  ecx
  jmp  .bt
.btdone:

  ; vocab.json: { "token": id, ... }; keys decoded (JSON escapes) into an arena
  mov  rcx, [rsp+56]
  lea  rdx, [rsp+32]
  call rt_read_file
  mov  rsi, rax
  mov  rbx, [rsp+32]
  add  rbx, rsi                ; end
  mov  rcx, [rsp+32]           ; a decoded key is never longer than its JSON form
  add  rcx, 16
  call rt_alloc
  mov  rdi, rax                ; arena write pointer
  call ct_ws
  cmp  rsi, rbx
  jae  .badj
  cmp  byte [rsi], '{'
  jne  .badj
  inc  rsi
.entry:
  call ct_ws
  cmp  rsi, rbx
  jae  .badj
  cmp  byte [rsi], '}'
  je   .vdone
  cmp  byte [rsi], ','
  jne  .key
  inc  rsi
  jmp  .entry
.key:
  cmp  byte [rsi], '"'
  jne  .badj
  inc  rsi
  mov  r12, rdi                ; the key's first byte in the arena
.ch:
  cmp  rsi, rbx
  jae  .badj
  mov  al, [rsi]
  inc  rsi
  cmp  al, '"'
  je   .kend
  cmp  al, '\'
  jne  .put
  cmp  rsi, rbx
  jae  .badj
  mov  al, [rsi]
  inc  rsi
  cmp  al, 'u'
  je   .uesc
  cmp  al, 'n'
  jne  .e2
  mov  al, 10
  jmp  .put
.e2:
  cmp  al, 't'
  jne  .e3
  mov  al, 9
  jmp  .put
.e3:
  cmp  al, 'r'
  jne  .e4
  mov  al, 13
  jmp  .put
.e4:
  cmp  al, 'b'
  jne  .e5
  mov  al, 8
  jmp  .put
.e5:
  cmp  al, 'f'
  jne  .put                    ; \" \\ \/ are the character itself
  mov  al, 12
.put:
  mov  [rdi], al
  inc  rdi
  jmp  .ch
.uesc:
  call ct_hex4
  mov  r13d, eax
  cmp  eax, 0xD800             ; a high surrogate followed by \u + a low one
  jb   .cp
  cmp  eax, 0xDC00
  jae  .cp
  lea  rax, [rsi + 2]
  cmp  rax, rbx
  ja   .cp
  cmp  word [rsi], 'u' * 256 + '\'
  jne  .cp
  add  rsi, 2
  call ct_hex4
  sub  eax, 0xDC00
  mov  ecx, r13d
  sub  ecx, 0xD800
  shl  ecx, 10
  add  eax, ecx
  add  eax, 0x10000
  mov  r13d, eax
.cp:
  mov  eax, r13d
  call ct_utf8_put
  jmp  .ch
.kend:
  mov  r13, rdi
  sub  r13, r12                ; key length
  call ct_ws
  cmp  rsi, rbx
  jae  .badj
  cmp  byte [rsi], ':'
  jne  .badj
  inc  rsi
  call ct_ws
  xor  r14d, r14d
.dig:
  cmp  rsi, rbx
  jae  .badj
  movzx eax, byte [rsi]
  sub  eax, '0'
  cmp  eax, 9
  ja   .dend
  imul r14d, r14d, 10
  add  r14d, eax
  inc  rsi
  jmp  .dig
.dend:
  mov  rcx, [rel g_ct_vocab]
  mov  rdx, r12
  mov  r8, r13
  mov  r9d, r14d
  call ct_put
  jmp  .entry
.vdone:

  ; merges.txt: "#version" line, then "left right" per line; rank = line order
  mov  rcx, [rsp+40]
  lea  rdx, [rsp+48]
  call rt_read_file
  mov  rsi, rax
  mov  rbx, [rsp+48]
  add  rbx, rsi
  xor  r14d, r14d              ; rank
  cmp  rsi, rbx
  jae  .mdone
  cmp  byte [rsi], '#'
  jne  .mline
.skip1:
  cmp  rsi, rbx
  jae  .mdone
  lodsb
  cmp  al, 10
  jne  .skip1
.mline:
  cmp  rsi, rbx
  jae  .mdone
  mov  r12, rsi
.f:
  cmp  rsi, rbx
  jae  .fe
  cmp  byte [rsi], 10
  je   .fe
  inc  rsi
  jmp  .f
.fe:
  mov  r13, rsi
  sub  r13, r12
  test r13, r13
  jz   .mnext
  cmp  byte [r12 + r13 - 1], 13
  jne  .mput
  dec  r13
  jz   .mnext
.mput:
  mov  rcx, [rel g_ct_merges]
  mov  rdx, r12
  mov  r8, r13
  mov  r9d, r14d
  call ct_put
  inc  r14d
.mnext:
  cmp  rsi, rbx
  jae  .mdone
  inc  rsi
  jmp  .mline
.mdone:
  mov  [rel g_ct_n_merges], r14
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.badj:
  lea  rcx, [rel ct_err_json]
  call rt_fatal

; ct_word(rcx=ptr, rdx=len) — one pre-token: map, BPE, append its ids to
; g_ct_out while fewer than CT_CTX-1 ids are there. Preserves the nonvolatile
; registers.
ct_word:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = index of the best pair
  mov  rsi, rcx
  mov  r12, rdx
  cmp  r12, CT_WORD_MAX
  jbe  .lenok
  mov  r12d, CT_WORD_MAX
.lenok:
  lea  rdi, [rel g_ct_word]    ; mapped characters, then "</w>"
  lea  r13, [rel g_ct_sym]     ; symbols: u32 offset in g_ct_word, u32 length
  xor  r14d, r14d              ; symbols
  xor  ebx, ebx
.m:
  cmp  rbx, r12
  jae  .mend
  movzx eax, byte [rsi + rbx]
  lea  ecx, [rax - 'A']
  cmp  ecx, 25
  ja   .nl
  add  eax, 32                 ; ASCII lowercase
.nl:
  lea  rcx, [rel g_ct_b2u]
  movzx edx, byte [rcx + rax*4]
  lea  r8, [rel g_ct_word]
  mov  r9, rdi
  sub  r9, r8
  mov  [r13 + r14*8], r9d
  mov  [r13 + r14*8 + 4], edx
  mov  r8b, [rcx + rax*4 + 1]
  mov  [rdi], r8b
  cmp  edx, 2
  jb   .one
  mov  r8b, [rcx + rax*4 + 2]
  mov  [rdi+1], r8b
.one:
  add  rdi, rdx
  inc  r14d
  inc  rbx
  jmp  .m
.mend:
  test r14d, r14d
  jz   .ret
  mov  dword [rdi], '</w>'     ; the end-of-word mark joins the last symbol
  lea  eax, [r14d - 1]
  add  dword [r13 + rax*8 + 4], 4

.merge:                        ; merge the adjacent pair with the lowest rank
  cmp  r14d, 1
  jbe  .emit
  mov  r15d, -1                ; best rank (unsigned: -1 = none)
  xor  ebx, ebx
.pair:
  lea  eax, [r14d - 1]
  cmp  ebx, eax
  jae  .pdone
  lea  rdi, [rel g_ct_key]     ; key = left + " " + right
  mov  ecx, [r13 + rbx*8 + 4]
  mov  esi, [r13 + rbx*8]
  lea  rax, [rel g_ct_word]
  add  rsi, rax
  rep  movsb
  mov  byte [rdi], ' '
  inc  rdi
  mov  ecx, [r13 + rbx*8 + 12]
  mov  esi, [r13 + rbx*8 + 8]
  add  rsi, rax
  rep  movsb
  lea  rdx, [rel g_ct_key]
  mov  r8, rdi
  sub  r8, rdx
  mov  rcx, [rel g_ct_merges]
  call ct_get
  cmp  eax, r15d
  jae  .pnext
  mov  r15d, eax
  mov  [rsp+32], ebx
.pnext:
  inc  ebx
  jmp  .pair
.pdone:
  cmp  r15d, -1
  je   .emit
  mov  ebx, [rsp+32]           ; symbol i absorbs i+1 (they are adjacent bytes)
  mov  eax, [r13 + rbx*8 + 12]
  add  [r13 + rbx*8 + 4], eax
  lea  ecx, [ebx + 1]
.shift:
  lea  eax, [r14d - 1]
  cmp  ecx, eax
  jae  .shifted
  mov  rax, [r13 + rcx*8 + 8]
  mov  [r13 + rcx*8], rax
  inc  ecx
  jmp  .shift
.shifted:
  dec  r14d
  jmp  .merge

.emit:
  xor  ebx, ebx
.e:
  cmp  ebx, r14d
  jae  .ret
  mov  rax, [rel g_ct_n]
  cmp  rax, CT_CTX - 1         ; keep the last slot for <|endoftext|>
  jae  .ret
  mov  edx, [r13 + rbx*8]
  lea  rax, [rel g_ct_word]
  add  rdx, rax
  mov  r8d, [r13 + rbx*8 + 4]
  mov  rcx, [rel g_ct_vocab]
  call ct_get
  cmp  eax, -1
  je   .unknown
  mov  rcx, [rel g_ct_out]
  mov  rdx, [rel g_ct_n]
  mov  [rcx + rdx*4], eax
  inc  qword [rel g_ct_n]
  inc  ebx
  jmp  .e
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
.unknown:
  lea  rcx, [rel ct_err_vocab]
  call rt_fatal

; ct_encode(rcx=utf8, rdx=len, r8=u32 ids[CT_CTX]) -> rax = ids before the
; padding (BOS, text, EOS). Preserves the nonvolatile registers.
ct_encode:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  mov  rsi, rcx
  lea  rbx, [rcx + rdx]        ; end
  mov  [rel g_ct_out], r8
  mov  dword [r8], CT_BOS
  mov  qword [rel g_ct_n], 1
.scan:
  cmp  qword [rel g_ct_n], CT_CTX - 1
  jae  .fin
  cmp  rsi, rbx
  jae  .fin
  movzx eax, byte [rsi]
  call ct_class                ; eax = 0 space, 1 letter, 2 digit, 3 other
  test eax, eax
  jnz  .tok
  inc  rsi
  jmp  .scan
.tok:
  mov  r12, rsi                ; the pre-token's first byte
  cmp  eax, 2
  je   .digit
  cmp  eax, 1
  je   .letters
  cmp  byte [rsi], 39          ; ' : a contraction?
  jne  .other
  lea  rcx, [rsi + 1]
  cmp  rcx, rbx
  jae  .other
  movzx eax, byte [rcx]
  or   eax, 0x20
  cmp  eax, 's'
  je   .c1
  cmp  eax, 't'
  je   .c1
  cmp  eax, 'm'
  je   .c1
  cmp  eax, 'd'
  je   .c1
  lea  rdx, [rsi + 2]
  cmp  rdx, rbx
  jae  .other
  movzx edx, byte [rsi + 2]
  or   edx, 0x20
  cmp  eax, 'r'
  jne  .cv
  cmp  edx, 'e'
  je   .c2
.cv:
  cmp  eax, 'v'
  jne  .cl
  cmp  edx, 'e'
  je   .c2
.cl:
  cmp  eax, 'l'
  jne  .other
  cmp  edx, 'l'
  jne  .other
.c2:
  add  rsi, 3
  jmp  .word
.c1:
  add  rsi, 2
  jmp  .word
.digit:
  inc  rsi
  jmp  .word
.letters:
  inc  rsi
  cmp  rsi, rbx
  jae  .word
  movzx eax, byte [rsi]
  call ct_class
  cmp  eax, 1
  je   .letters
  jmp  .word
.other:
  inc  rsi
  cmp  rsi, rbx
  jae  .word
  movzx eax, byte [rsi]
  call ct_class
  cmp  eax, 3
  je   .other
.word:
  mov  rcx, r12
  mov  rdx, rsi
  sub  rdx, r12
  call ct_word
  jmp  .scan
.fin:
  mov  rcx, [rel g_ct_out]
  mov  rdx, [rel g_ct_n]
  mov  dword [rcx + rdx*4], CT_EOS
  inc  rdx
  mov  rax, rdx
.pad:
  cmp  rdx, CT_CTX
  jae  .done
  mov  dword [rcx + rdx*4], 0
  inc  rdx
  jmp  .pad
.done:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ct_class(eax=byte) -> eax = 0 whitespace, 1 letter (ASCII letter or any
; byte >= 0x80), 2 digit, 3 anything else. Leaf; clobbers rax, rcx.
ct_class:
  cmp  eax, 0x80
  jae  .letter
  cmp  eax, ' '
  je   .space
  cmp  eax, 9
  jb   .nsp
  cmp  eax, 13
  jbe  .space
.nsp:
  lea  ecx, [rax - '0']
  cmp  ecx, 9
  jbe  .digit
  mov  ecx, eax
  or   ecx, 0x20
  sub  ecx, 'a'
  cmp  ecx, 25
  jbe  .letter
  mov  eax, 3
  ret
.space:
  xor  eax, eax
  ret
.letter:
  mov  eax, 1
  ret
.digit:
  mov  eax, 2
  ret

section .data
  align 8
g_ct_vocab:      dq 0          ; hash table: token string -> id
g_ct_merges:     dq 0          ; hash table: "left right" -> rank
g_ct_n_merges:   dq 0
g_ct_out:        dq 0          ; ct_encode's ids
g_ct_n:          dq 0          ; ids written so far
g_ct_vocab_path: dq 0          ; --vocab FILE (utf16), set by main
g_ct_merges_path: dq 0         ; --merges FILE
g_ct_ids:        times CT_CTX dd 0 ; the prompt's ids (--cliptok, the generator)
g_ct_b2u:        times 256 dd 0    ; per byte: u8 length, then its UTF-8 bytes
g_ct_sym:        times CT_WORD_MAX * 2 dd 0
g_ct_word:       times CT_WORD_MAX * 2 + 8 db 0
g_ct_key:        times CT_WORD_MAX * 4 + 16 db 0
ct_err_json:     db "vocab.json is not the JSON object of token -> id it should be", 0
ct_err_vocab:    db "a BPE symbol is missing from vocab.json (wrong vocab.json/merges.txt pair?)", 0
section .text

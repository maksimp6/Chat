; tokenizer.asm — GPT-2 byte-level BPE with the qwen2 pre-tokenizer.
;
; %included into asmrun.asm (bits 64, default rel already set). Code is in .text;
; the globals at the end switch to .data (`section .data`), and no storage uses
; `resb`: mutable globals are zero-initialised `dq 0` / `db 0` / `times N dw 0`
; bytes; the hash tables, the id->string index and the per-encode scratch all
; come from rt_alloc at run time.
;
; Public surface (Windows x64 everywhere, also between modules):
;   tok_init(rcx=Model*)                         byte maps, vocab + merge tables
;   tok_encode(rcx=text,rdx=len,r8=ids,r9=max)   pre-tokenize + BPE -> rax count
;   tok_encode_chat(rcx=text,rdx=len,r8=ids,r9=max) the same text wrapped as a
;                                                Qwen3 chat turn (--chat) -> rax count
;   tok_decode(rcx=id, rdx=out_buf) -> rax       token bytes (0 for control ids)
;   tok_is_stop(rcx=id) -> rax                   1 if eos / bos / pad, else 0
;   tok_cli(rcx=Model*)                          --tokenize: stdin -> ids + text
;
; Internal: tok_class, tok_pretok_next, tok_bpe_chunk, tok_pair_rank,
;           tok_vocab_lookup, tok_merge_lookup, tok_vocab_insert, tok_merge_insert.
;
; Model facts (DESIGN "The model"): the GGUF vocabulary is gpt2 byte-level with
; the qwen2 pre-tokenizer, 151936 tokens, 151387 merges. The first 151643 ids are
; mergeable byte-level tokens; ids >= 151643 are the special / control / added
; tokens (<|endoftext|>=151643, <|im_start|>=151644, <|im_end|>=151645, ...),
; whose text is not emitted when decoding a stream.

; Open-addressing hash tables: 2^19 slots, FNV-1a over the UTF-8 key bytes.
HT_SLOTS        equ (1 << 19)
HT_MASK         equ (HT_SLOTS - 1)

; FNV-1a 64-bit parameters.
FNV_OFFSET      equ 0xcbf29ce484222325
FNV_PRIME       equ 0x100000001b3

; First special / control token id for this model (see the note above): decoding
; a token id >= this emits no bytes. This equals n_vocab minus the number of
; added/special tokens and is a model-specific constant (see risks).
TOK_SPECIAL_MIN equ 151643

; Qwen3 chat control ids, emitted directly by tok_encode_chat (model-specific,
; like TOK_SPECIAL_MIN above). <|im_end|> is also this GGUF's eos, so a chat turn
; stops on it through tok_is_stop with no extra case.
TOK_IM_START    equ 151644   ; <|im_start|>
TOK_IM_END      equ 151645   ; <|im_end|>

; Byte classes returned by tok_class.
CLASS_WS        equ 0   ; space / tab / VT / FF  (whitespace that is not CR or LF)
CLASS_CRLF      equ 1   ; CR or LF
CLASS_LETTER    equ 2   ; ASCII letter or any byte >= 0x80  (\p{L} approximation)
CLASS_DIGIT     equ 3   ; ASCII digit             (\p{N} approximation)
CLASS_SYM       equ 4   ; everything else (non-whitespace, non-letter, non-digit)

; ===========================================================================
; Byte classification
; ===========================================================================

; tok_class(rcx=byte in cl) -> rax = CLASS_*. Classifies one raw byte for the
; qwen2 pre-tokenizer: CR/LF, other whitespace, letter (ASCII A-Za-z or >=0x80),
; ASCII digit, or symbol. Leaf; clobbers rax only.
tok_class:
  movzx eax, cl
  cmp  eax, 0x0A               ; LF
  je   .crlf
  cmp  eax, 0x0D               ; CR
  je   .crlf
  cmp  eax, 0x20               ; space
  je   .ws
  cmp  eax, 0x09               ; tab
  je   .ws
  cmp  eax, 0x0B               ; VT
  je   .ws
  cmp  eax, 0x0C               ; FF
  je   .ws
  cmp  eax, 0x30               ; '0'
  jb   .not_digit
  cmp  eax, 0x39               ; '9'
  jbe  .digit
.not_digit:
  cmp  eax, 0x41               ; 'A'
  jb   .not_upper
  cmp  eax, 0x5A               ; 'Z'
  jbe  .letter
.not_upper:
  cmp  eax, 0x61               ; 'a'
  jb   .not_lower
  cmp  eax, 0x7A               ; 'z'
  jbe  .letter
.not_lower:
  cmp  eax, 0x80               ; any byte >= 0x80 is treated as a letter
  jae  .letter
  mov  eax, CLASS_SYM
  ret
.ws:
  mov  eax, CLASS_WS
  ret
.crlf:
  mov  eax, CLASS_CRLF
  ret
.letter:
  mov  eax, CLASS_LETTER
  ret
.digit:
  mov  eax, CLASS_DIGIT
  ret

; ===========================================================================
; Hash-table lookups (FNV-1a open addressing, linear probing)
; ===========================================================================

; tok_vocab_lookup(rcx=key ptr, rdx=key len) -> rax = token id, or -1 if absent.
; Hashes the (already unicode-mapped) UTF-8 key bytes and probes g_vocab_slots;
; each non-empty slot holds id+1 and is confirmed by comparing the stored token
; bytes from g_tok_ptr/g_tok_len. Leaf; saves rbx,rsi,rdi,r12,r13,r14.
tok_vocab_lookup:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  mov  rsi, rcx                ; key ptr
  mov  rdi, rdx                ; key len
  mov  rax, FNV_OFFSET
  mov  r11, FNV_PRIME
  mov  r8, rcx                 ; hash cursor
  mov  r9, rdx                 ; byte count
  test r9, r9
  jz   .hdone
.hloop:
  movzx ecx, byte [r8]
  xor  rax, rcx
  imul rax, r11
  inc  r8
  dec  r9
  jnz  .hloop
.hdone:
  and  rax, HT_MASK            ; slot index
  mov  rbx, [rel g_vocab_slots]
  mov  r12, [rel g_tok_ptr]
  mov  r13, [rel g_tok_len]
.probe:
  mov  ecx, [rbx + rax*4]      ; id+1 (0 = empty)
  test ecx, ecx
  jz   .miss
  dec  ecx                     ; candidate id
  mov  r9, [r13 + rcx*8]       ; stored len
  cmp  r9, rdi
  jne  .next
  mov  r8, [r12 + rcx*8]       ; stored ptr
  mov  r10, rsi                ; key cursor
  mov  rdx, rdi                ; remaining
  test rdx, rdx
  jz   .match
.cmp:
  mov  r11b, [r8]
  mov  r14b, [r10]
  cmp  r11b, r14b
  jne  .next
  inc  r8
  inc  r10
  dec  rdx
  jnz  .cmp
.match:
  mov  rax, rcx                ; found id
  jmp  .ret
.next:
  inc  rax
  and  rax, HT_MASK
  jmp  .probe
.miss:
  mov  rax, -1
.ret:
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; tok_merge_lookup(rcx=key ptr, rdx=key len) -> rax = merge rank, or -1 if absent.
; Same scheme as tok_vocab_lookup over g_merge_slots: a slot holds rank+1 and is
; confirmed against the merge string ("A B") from g_merge_ptr/g_merge_len. The
; rank is the merge's index in tokenizer.ggml.merges (lower = merged first). Leaf.
tok_merge_lookup:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  mov  rsi, rcx                ; key ptr
  mov  rdi, rdx                ; key len
  mov  rax, FNV_OFFSET
  mov  r11, FNV_PRIME
  mov  r8, rcx
  mov  r9, rdx
  test r9, r9
  jz   .hdone
.hloop:
  movzx ecx, byte [r8]
  xor  rax, rcx
  imul rax, r11
  inc  r8
  dec  r9
  jnz  .hloop
.hdone:
  and  rax, HT_MASK
  mov  rbx, [rel g_merge_slots]
  mov  r12, [rel g_merge_ptr]
  mov  r13, [rel g_merge_len]
.probe:
  mov  ecx, [rbx + rax*4]      ; rank+1 (0 = empty)
  test ecx, ecx
  jz   .miss
  dec  ecx                     ; candidate rank
  mov  r9, [r13 + rcx*8]       ; stored len
  cmp  r9, rdi
  jne  .next
  mov  r8, [r12 + rcx*8]       ; stored ptr
  mov  r10, rsi
  mov  rdx, rdi
  test rdx, rdx
  jz   .match
.cmp:
  mov  r11b, [r8]
  mov  r14b, [r10]
  cmp  r11b, r14b
  jne  .next
  inc  r8
  inc  r10
  dec  rdx
  jnz  .cmp
.match:
  mov  rax, rcx                ; found rank
  jmp  .ret
.next:
  inc  rax
  and  rax, HT_MASK
  jmp  .probe
.miss:
  mov  rax, -1
.ret:
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; tok_vocab_insert(rcx=id) — insert token `id` into g_vocab_slots. Hashes the
; token bytes (g_tok_ptr/g_tok_len[id]), linear-probes to the first empty slot
; and stores id+1. Leaf; saves rbx,rsi.
tok_vocab_insert:
  push rbx
  push rsi
  mov  rsi, rcx                ; id
  mov  r10, [rel g_tok_ptr]
  mov  r8, [r10 + rsi*8]       ; ptr
  mov  r10, [rel g_tok_len]
  mov  r9, [r10 + rsi*8]       ; len
  mov  rax, FNV_OFFSET
  mov  r11, FNV_PRIME
  mov  r10, r8                 ; cursor
  mov  rcx, r9                 ; count
  test rcx, rcx
  jz   .hd
.hl:
  movzx edx, byte [r10]
  xor  rax, rdx
  imul rax, r11
  inc  r10
  dec  rcx
  jnz  .hl
.hd:
  and  rax, HT_MASK
  mov  rbx, [rel g_vocab_slots]
.pr:
  mov  edx, [rbx + rax*4]
  test edx, edx
  jz   .empty
  inc  rax
  and  rax, HT_MASK
  jmp  .pr
.empty:
  lea  edx, [rsi+1]            ; id+1
  mov  [rbx + rax*4], edx
  pop  rsi
  pop  rbx
  ret

; tok_merge_insert(rcx=rank) — insert merge `rank` into g_merge_slots. Hashes the
; merge string "A B" (g_merge_ptr/g_merge_len[rank]) and stores rank+1 at the
; first empty slot. Leaf; saves rbx,rsi.
tok_merge_insert:
  push rbx
  push rsi
  mov  rsi, rcx                ; rank
  mov  r10, [rel g_merge_ptr]
  mov  r8, [r10 + rsi*8]
  mov  r10, [rel g_merge_len]
  mov  r9, [r10 + rsi*8]
  mov  rax, FNV_OFFSET
  mov  r11, FNV_PRIME
  mov  r10, r8
  mov  rcx, r9
  test rcx, rcx
  jz   .hd
.hl:
  movzx edx, byte [r10]
  xor  rax, rdx
  imul rax, r11
  inc  r10
  dec  rcx
  jnz  .hl
.hd:
  and  rax, HT_MASK
  mov  rbx, [rel g_merge_slots]
.pr:
  mov  edx, [rbx + rax*4]
  test edx, edx
  jz   .empty
  inc  rax
  and  rax, HT_MASK
  jmp  .pr
.empty:
  lea  edx, [rsi+1]
  mov  [rbx + rax*4], edx
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; tok_init
; ===========================================================================

; tok_init(rcx=Model*) — build everything the encoder and decoder need:
;   (1) the GPT-2 byte<->unicode maps g_b2u (byte->code point) and g_u2b
;       (code point->byte); (2) the id->string index g_tok_ptr/g_tok_len from the
;       packed Model.tokens_arr; (3) the vocab hash table; (4) the merge-rank hash
;       table from Model.merges_arr. Idempotent (guarded by g_tok_ready). Clobbers
;       caller-saved registers; saves rbx,rsi,rdi,r12-r15.
tok_init:
  push rbp
  mov  rbp, rsp
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  and  rsp, -16
  sub  rsp, 32                 ; 32-byte call shadow
  cmp  qword [rel g_tok_ready], 0
  jne  .ret
  mov  [rel g_model], rcx
  mov  rbx, rcx                ; Model*
  mov  rax, [rbx + Model.n_tokens]
  mov  [rel g_n_tokens], rax
  mov  rax, [rbx + Model.n_merges]
  mov  [rel g_n_merges], rax

  ; --- (1) byte<->unicode maps ---
  ; Pass 1: bytes 33-126, 161-172, 174-255 map to the code point of their own
  ; value; the other 68 bytes are marked 0xFFFF for now.
  lea  rdi, [rel g_b2u]
  lea  r15, [rel g_u2b]
  xor  r12, r12
.bm1:
  cmp  r12, 256
  jae  .bm1_done
  mov  word [rdi + r12*2], 0xFFFF
  mov  eax, r12d
  cmp  eax, 33
  jb   .bm1_next
  cmp  eax, 126
  jbe  .bm1_direct
  cmp  eax, 161
  jb   .bm1_next
  cmp  eax, 172
  jbe  .bm1_direct
  cmp  eax, 174
  jb   .bm1_next
  ; 174..255
.bm1_direct:
  mov  word [rdi + r12*2], r12w
.bm1_next:
  inc  r12
  jmp  .bm1
.bm1_done:
  ; Pass 2: the unmarked bytes get code points 256,257,... in increasing byte order.
  xor  r12, r12
  mov  r13d, 256
.bm2:
  cmp  r12, 256
  jae  .bm2_done
  cmp  word [rdi + r12*2], 0xFFFF
  jne  .bm2_next
  mov  [rdi + r12*2], r13w
  inc  r13d
.bm2_next:
  inc  r12
  jmp  .bm2
.bm2_done:
  ; Reverse map g_u2b[code point] = byte, for all 256 bytes.
  xor  r12, r12
.bm3:
  cmp  r12, 256
  jae  .bm3_done
  movzx eax, word [rdi + r12*2]
  mov  [r15 + rax], r12b
  inc  r12
  jmp  .bm3
.bm3_done:

  ; --- (2) id->string index from the packed tokens array ---
  mov  rcx, [rel g_n_tokens]
  shl  rcx, 3
  call rt_alloc
  mov  [rel g_tok_ptr], rax
  mov  rcx, [rel g_n_tokens]
  shl  rcx, 3
  call rt_alloc
  mov  [rel g_tok_len], rax
  ; --- (3) vocab hash table ---
  mov  rcx, HT_SLOTS * 4
  call rt_alloc
  mov  [rel g_vocab_slots], rax
  ; Walk the packed strings: [u64 len][len bytes] per token.
  mov  rsi, [rbx + Model.tokens_arr]
  mov  r12, [rel g_tok_ptr]
  mov  r13, [rel g_tok_len]
  xor  r14, r14                ; i
.tok_walk:
  cmp  r14, [rel g_n_tokens]
  jae  .tok_walk_done
  mov  rax, [rsi]              ; length
  lea  rcx, [rsi+8]            ; bytes
  mov  [r12 + r14*8], rcx
  mov  [r13 + r14*8], rax
  lea  rsi, [rsi + rax + 8]    ; next string
  inc  r14
  jmp  .tok_walk
.tok_walk_done:
  xor  r14, r14
.vins:
  cmp  r14, [rel g_n_tokens]
  jae  .vins_done
  mov  rcx, r14
  call tok_vocab_insert
  inc  r14
  jmp  .vins
.vins_done:

  ; --- (4) merge index and merge-rank hash table ---
  mov  rcx, [rel g_n_merges]
  shl  rcx, 3
  call rt_alloc
  mov  [rel g_merge_ptr], rax
  mov  rcx, [rel g_n_merges]
  shl  rcx, 3
  call rt_alloc
  mov  [rel g_merge_len], rax
  mov  rcx, HT_SLOTS * 4
  call rt_alloc
  mov  [rel g_merge_slots], rax
  mov  rsi, [rbx + Model.merges_arr]
  mov  r12, [rel g_merge_ptr]
  mov  r13, [rel g_merge_len]
  xor  r14, r14
.mrg_walk:
  cmp  r14, [rel g_n_merges]
  jae  .mrg_walk_done
  mov  rax, [rsi]
  lea  rcx, [rsi+8]
  mov  [r12 + r14*8], rcx
  mov  [r13 + r14*8], rax
  lea  rsi, [rsi + rax + 8]
  inc  r14
  jmp  .mrg_walk
.mrg_walk_done:
  xor  r14, r14
.mins:
  cmp  r14, [rel g_n_merges]
  jae  .mins_done
  mov  rcx, r14
  call tok_merge_insert
  inc  r14
  jmp  .mins
.mins_done:
  mov  qword [rel g_tok_ready], 1
.ret:
  mov  r15, [rbp-56]
  mov  r14, [rbp-48]
  mov  r13, [rbp-40]
  mov  r12, [rbp-32]
  mov  rdi, [rbp-24]
  mov  rsi, [rbp-16]
  mov  rbx, [rbp-8]
  mov  rsp, rbp
  pop  rbp
  ret

; ===========================================================================
; Pre-tokenizer (qwen2)
; ===========================================================================

; tok_pretok_next(rcx=p, rdx=end) -> rax = end of the chunk that starts at p.
; Applies the five qwen2 rules in order (first match wins) to the raw bytes at p
; (DESIGN "Pre-tokenizer"); the caller guarantees p < end, and the result is
; always > p so the caller makes progress. Saves rbx,rsi,rdi,r12-r15; calls
; tok_class, so it keeps a 32-byte shadow and 16-aligned rsp.
tok_pretok_next:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  sub  rsp, 32
  mov  rsi, rcx                ; p
  mov  rdi, rdx                ; end
  movzx ecx, byte [rsi]
  mov  r15d, ecx               ; b0
  call tok_class
  mov  rbx, rax                ; class0

  ; ---- Rule 1: contractions 's 't 're 've 'm 'll 'd (case-insensitive) ----
  cmp  r15d, 0x27              ; "'"
  jne  .r2
  lea  rcx, [rsi+1]
  cmp  rcx, rdi
  jae  .r2                     ; nothing after the quote
  movzx eax, byte [rsi+1]
  mov  edx, eax
  or   edx, 0x20               ; ASCII lowercase (letters only; harmless otherwise)
  cmp  edx, 's'
  je   .c_two
  cmp  edx, 't'
  je   .c_two
  cmp  edx, 'm'
  je   .c_two
  cmp  edx, 'd'
  je   .c_two
  cmp  edx, 'r'
  je   .c_re
  cmp  edx, 'v'
  je   .c_ve
  cmp  edx, 'l'
  je   .c_ll
  jmp  .r2
.c_two:
  lea  rax, [rsi+2]
  jmp  .ret
.c_re:
  lea  rcx, [rsi+2]
  cmp  rcx, rdi
  jae  .r2
  movzx eax, byte [rsi+2]
  or   eax, 0x20
  cmp  eax, 'e'
  jne  .r2
  lea  rax, [rsi+3]
  jmp  .ret
.c_ve:
  lea  rcx, [rsi+2]
  cmp  rcx, rdi
  jae  .r2
  movzx eax, byte [rsi+2]
  or   eax, 0x20
  cmp  eax, 'e'
  jne  .r2
  lea  rax, [rsi+3]
  jmp  .ret
.c_ll:
  lea  rcx, [rsi+2]
  cmp  rcx, rdi
  jae  .r2
  movzx eax, byte [rsi+2]
  or   eax, 0x20
  cmp  eax, 'l'
  jne  .r2
  lea  rax, [rsi+3]
  jmp  .ret

  ; ---- Rule 2: [^\r\n\p{L}\p{N}]? \p{L}+  (optional non-letter/digit/CRLF + letters) ----
.r2:
  cmp  rbx, CLASS_LETTER
  je   .r2_from_p              ; starts on a letter: no prefix
  cmp  rbx, CLASS_WS
  je   .r2_prefix
  cmp  rbx, CLASS_SYM
  je   .r2_prefix
  jmp  .r3                     ; class0 is CRLF or DIGIT: rule 2 cannot start here
.r2_prefix:
  lea  rcx, [rsi+1]
  cmp  rcx, rdi
  jae  .r3                     ; a prefix needs at least one letter after it
  movzx ecx, byte [rsi+1]
  call tok_class
  cmp  rax, CLASS_LETTER
  jne  .r3
  lea  r12, [rsi+1]            ; consume the prefix byte, letters start at p+1
  jmp  .r2_consume
.r2_from_p:
  mov  r12, rsi
.r2_consume:
  cmp  r12, rdi
  jae  .r2_end
  movzx ecx, byte [r12]
  call tok_class
  cmp  rax, CLASS_LETTER
  jne  .r2_end
  inc  r12
  jmp  .r2_consume
.r2_end:
  mov  rax, r12
  jmp  .ret

  ; ---- Rule 3: \p{N}  (a single ASCII digit) ----
.r3:
  cmp  rbx, CLASS_DIGIT
  jne  .r4
  lea  rax, [rsi+1]
  jmp  .ret

  ; ---- Rule 4:  ?[^\s\p{L}\p{N}]+[\r\n]*  (optional space, symbols, CR/LF run) ----
.r4:
  mov  r12, rsi                ; q
  movzx eax, byte [rsi]
  cmp  eax, 0x20               ; optional single leading space
  jne  .r4_syms
  lea  rcx, [rsi+1]
  cmp  rcx, rdi
  jae  .r4_syms                ; a space at end cannot start rule 4
  movzx ecx, byte [rsi+1]
  call tok_class
  cmp  rax, CLASS_SYM
  jne  .r4_syms                ; space must be followed by a symbol
  lea  r12, [rsi+1]            ; consume the space
.r4_syms:
  cmp  r12, rdi
  jae  .r5                     ; no symbol here -> whitespace rule
  movzx ecx, byte [r12]
  call tok_class
  cmp  rax, CLASS_SYM
  jne  .r5                     ; rule 4 requires at least one symbol
.r4_symloop:
  cmp  r12, rdi
  jae  .r4_crlf
  movzx ecx, byte [r12]
  call tok_class
  cmp  rax, CLASS_SYM
  jne  .r4_crlf
  inc  r12
  jmp  .r4_symloop
.r4_crlf:
  cmp  r12, rdi
  jae  .r4_done
  movzx ecx, byte [r12]
  call tok_class
  cmp  rax, CLASS_CRLF
  jne  .r4_done
  inc  r12
  jmp  .r4_crlf
.r4_done:
  mov  rax, r12
  jmp  .ret

  ; ---- Rule 5: whitespace runs ----
  ; A run containing CR/LF ends after its last CR/LF. A run of n >= 2 other
  ; whitespace bytes followed by a non-space yields its first n-1 bytes (the last
  ; space belongs to the next word). Otherwise the whole run.
.r5:
  mov  r12, rsi                ; scan cursor
  xor  r13, r13                ; pointer just after the last CR/LF (0 = none seen)
.r5_scan:
  cmp  r12, rdi
  jae  .r5_runend
  movzx ecx, byte [r12]
  call tok_class
  cmp  rax, CLASS_CRLF
  je   .r5_crlf
  cmp  rax, CLASS_WS
  je   .r5_ws
  jmp  .r5_runend              ; first non-whitespace byte ends the run
.r5_crlf:
  inc  r12
  mov  r13, r12                ; remember position after this CR/LF
  jmp  .r5_scan
.r5_ws:
  inc  r12
  jmp  .r5_scan
.r5_runend:
  test r13, r13
  jz   .r5_nocrlf
  mov  rax, r13                ; chunk ends after the last CR/LF
  jmp  .ret
.r5_nocrlf:
  mov  rax, r12
  sub  rax, rsi                ; n = run length
  cmp  rax, 2
  jb   .r5_whole               ; n == 1: whole run
  cmp  r12, rdi
  jae  .r5_whole               ; run reaches EOF: whole run
  lea  rax, [r12-1]            ; n >= 2 and followed by a non-space: first n-1 bytes
  jmp  .ret
.r5_whole:
  mov  rax, r12
  ; fall through to .ret
.ret:
  add  rsp, 32
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; BPE within one pre-token chunk
; ===========================================================================

; tok_pair_rank(rcx=i) -> rax = merge rank of (symbol i, symbol i+1), or -1 if the
; pair has no merge. Builds the key "A<space>B" from the two symbols' unicode
; bytes (g_sym entries are {u32 off, u32 len} into g_enc_buf) into g_key and
; looks it up. Saves rbx,rsi,rdi,r12-r15; keeps a 32-byte shadow for the call.
tok_pair_rank:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  sub  rsp, 32
  mov  rbx, rcx                ; i
  mov  rsi, [rel g_sym]
  mov  r12, [rel g_enc_buf]
  mov  r13, [rel g_key]
  mov  r8d, [rsi + rbx*8]      ; A.off
  mov  r9d, [rsi + rbx*8 + 4]  ; A.len
  mov  r10d, [rsi + rbx*8 + 8] ; B.off
  mov  r11d, [rsi + rbx*8 + 12]; B.len
  xor  rcx, rcx                ; dst cursor in g_key
  xor  rdx, rdx                ; src index into A
  add  r8, r12                 ; A base = g_enc_buf + A.off (x86-64 addr allows only 2 regs)
.ca:
  cmp  rdx, r9
  jae  .ca_done
  mov  al, [r8 + rdx]
  mov  [r13 + rcx], al
  inc  rcx
  inc  rdx
  jmp  .ca
.ca_done:
  mov  byte [r13 + rcx], 0x20  ; the separating space of "A B"
  inc  rcx
  xor  rdx, rdx                ; src index into B
  add  r10, r12                ; B base = g_enc_buf + B.off
.cb:
  cmp  rdx, r11
  jae  .cb_done
  mov  al, [r10 + rdx]
  mov  [r13 + rcx], al
  inc  rcx
  inc  rdx
  jmp  .cb
.cb_done:
  mov  rdx, rcx                ; key length
  mov  rcx, r13                ; key ptr
  call tok_merge_lookup        ; -> rax = rank or -1
  add  rsp, 32
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; tok_bpe_chunk(rcx=cs, rdx=ce) — encode the raw bytes [cs,ce) and append the
; resulting token ids to the current encode session (g_out_ids / g_out_max /
; g_out_count). Maps each byte to its unicode UTF-8 piece in g_enc_buf, starts
; from one symbol per code point, repeatedly merges the adjacent pair of lowest
; merge rank, then looks each final symbol up in the vocabulary. rt_fatal if a
; piece is missing. Saves rbx,rsi,rdi,r12-r15; keeps shadow space.
tok_bpe_chunk:
  push rbp
  mov  rbp, rsp
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  and  rsp, -16
  sub  rsp, 32
  mov  rsi, rcx                ; cs
  mov  rdi, rdx                ; ce

  ; --- map chunk bytes to unicode pieces and build the initial symbols ---
  mov  r12, [rel g_enc_buf]    ; unicode byte buffer base
  xor  r13, r13                ; u = write offset into g_enc_buf
  mov  r14, [rel g_sym]        ; symbols array base
  xor  r15, r15                ; nsym
  mov  rbx, rsi                ; raw byte cursor
  lea  r11, [rel g_b2u]
.map_loop:
  cmp  rbx, rdi
  jae  .map_done
  movzx ecx, byte [rbx]
  movzx ecx, word [r11 + rcx*2]   ; code point (0..323)
  mov  [r14 + r15*8], r13d         ; symbol.off = u
  cmp  ecx, 0x80
  jae  .enc2
  mov  [r12 + r13], cl             ; 1-byte UTF-8
  inc  r13
  mov  dword [r14 + r15*8 + 4], 1
  jmp  .map_next
.enc2:
  mov  eax, ecx                    ; 2-byte UTF-8: 110xxxxx 10xxxxxx
  shr  eax, 6
  or   eax, 0xC0
  mov  [r12 + r13], al
  mov  eax, ecx
  and  eax, 0x3F
  or   eax, 0x80
  mov  [r12 + r13 + 1], al
  add  r13, 2
  mov  dword [r14 + r15*8 + 4], 2
.map_next:
  inc  r15
  inc  rbx
  jmp  .map_loop
.map_done:
  test r15, r15
  jz   .emit_done

  ; --- merge the lowest-rank adjacent pair until none applies ---
.merge_loop:
  cmp  r15, 2
  jb   .merge_done
  mov  r13, -1                 ; best rank (unsigned sentinel; absent pairs read -1)
  mov  r14, -1                 ; best i
  xor  r12, r12                ; i
  mov  rbx, r15
  dec  rbx                     ; number of adjacent pairs
.find:
  cmp  r12, rbx
  jae  .find_done
  mov  rcx, r12
  call tok_pair_rank
  cmp  rax, r13                ; strictly lower unsigned rank wins (absent = -1 never does)
  jae  .find_next
  mov  r13, rax
  mov  r14, r12
.find_next:
  inc  r12
  jmp  .find
.find_done:
  cmp  r14, -1
  je   .merge_done
  ; merge symbols r14 and r14+1 (contiguous in g_enc_buf): grow A, drop B.
  mov  rsi, [rel g_sym]
  mov  eax, [rsi + r14*8 + 4]      ; A.len
  add  eax, [rsi + r14*8 + 12]     ; + B.len
  mov  [rsi + r14*8 + 4], eax
  lea  rdi, [r14+1]                ; j = r14+1 (delete this entry)
.shift:
  lea  rax, [rdi+1]
  cmp  rax, r15
  jae  .shift_done
  mov  rcx, [rsi + rax*8]          ; sym[j+1] (off+len as one qword)
  mov  [rsi + rdi*8], rcx          ; sym[j] = sym[j+1]
  mov  rdi, rax
  jmp  .shift
.shift_done:
  dec  r15                         ; nsym--
  jmp  .merge_loop
.merge_done:

  ; --- look each final symbol up and append its id ---
  mov  rsi, [rel g_sym]
  mov  r12, [rel g_enc_buf]
  xor  r13, r13                    ; k
.emit:
  cmp  r13, r15
  jae  .emit_done
  mov  r8d, [rsi + r13*8]          ; off
  mov  r9d, [rsi + r13*8 + 4]      ; len
  lea  rcx, [r12 + r8]             ; piece ptr
  mov  rdx, r9                     ; piece len
  call tok_vocab_lookup            ; -> rax = id or -1
  cmp  rax, -1
  je   .notfound
  mov  rcx, [rel g_out_count]
  cmp  rcx, [rel g_out_max]
  jae  .skip_store                 ; out of room: keep counting, do not overflow
  mov  r10, [rel g_out_ids]
  mov  [r10 + rcx*4], eax          ; store id as u32
.skip_store:
  inc  qword [rel g_out_count]
  inc  r13
  jmp  .emit
.notfound:
  lea  rcx, [rel k_tok_err_piece]
  call rt_fatal
.emit_done:
  mov  r15, [rbp-56]
  mov  r14, [rbp-48]
  mov  r13, [rbp-40]
  mov  r12, [rbp-32]
  mov  rdi, [rbp-24]
  mov  rsi, [rbp-16]
  mov  rbx, [rbp-8]
  mov  rsp, rbp
  pop  rbp
  ret

; ===========================================================================
; tok_encode
; ===========================================================================

; tok_encode(rcx=text, rdx=len, r8=out_ids u32*, r9=max) -> rax = token count.
; Pre-tokenizes the UTF-8 bytes into chunks and BPE-encodes each chunk, appending
; up to `max` ids to out_ids and returning the total number produced. The scratch
; buffers (unicode bytes, symbols, merge-key) are sized to `len` from rt_alloc.
; Saves rbx,rsi,rdi,r12-r15.
tok_encode:
  push rbp
  mov  rbp, rsp
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  and  rsp, -16
  sub  rsp, 32
  mov  [rel g_out_ids], r8
  mov  [rel g_out_max], r9
  mov  qword [rel g_out_count], 0
  mov  rsi, rcx                ; text
  mov  rdi, rcx
  add  rdi, rdx                ; end
  mov  r13, rdx                ; len
  test rdx, rdx
  jz   .done
  ; scratch: g_enc_buf = 2*len+16 (each byte -> at most 2 unicode bytes)
  lea  rcx, [rdx*2 + 16]
  call rt_alloc
  mov  [rel g_enc_buf], rax
  ; symbols: (len+1) entries of 8 bytes
  lea  rcx, [r13 + 1]
  shl  rcx, 3
  call rt_alloc
  mov  [rel g_sym], rax
  ; merge key: up to 2*len+1 bytes
  lea  rcx, [r13*2 + 16]
  call rt_alloc
  mov  [rel g_key], rax
  mov  rbx, rsi                ; p
.chunk_loop:
  cmp  rbx, rdi
  jae  .done
  mov  rcx, rbx
  mov  rdx, rdi
  call tok_pretok_next         ; rax = chunk end
  mov  rcx, rbx                ; cs
  mov  rdx, rax                ; ce
  mov  rbx, rax                ; advance p
  call tok_bpe_chunk
  jmp  .chunk_loop
.done:
  mov  rax, [rel g_out_count]
  mov  r15, [rbp-56]
  mov  r14, [rbp-48]
  mov  r13, [rbp-40]
  mov  r12, [rbp-32]
  mov  rdi, [rbp-24]
  mov  rsi, [rbp-16]
  mov  rbx, [rbp-8]
  mov  rsp, rbp
  pop  rbp
  ret

; tok_encode_chat(rcx=content, rdx=len, r8=ids, r9=max) -> rax = id count.
; Wraps the user text as one Qwen3 chat turn and appends "/no_think", so the
; model answers directly and stops at <|im_end|> (this GGUF's eos):
;   <|im_start|>user\n{content} /no_think<|im_end|>\n<|im_start|>assistant\n
; The <|im_*|> ids are stored directly (u32); every text span goes through
; tok_encode, which preserves rbx/rsi/rdi/r12-r15, so the cursor survives each
; call. The caller sizes ids for the content plus a few control ids (len+64).
%macro TC_ID 1                 ; append the u32 id %1, respecting the capacity
  cmp  rsi, r13
  jae  %%skip
  mov  dword [rbx + rsi*4], %1
%%skip:
  inc  rsi
  cmp  rsi, r13
  jbe  %%ok
  mov  rsi, r13
%%ok:
%endmacro
%macro TC_ENCODE 2             ; encode text at %1 (reg), length %2 (reg/imm) at the cursor
  mov  rcx, %1
  mov  rdx, %2
  lea  r8, [rbx + rsi*4]
  mov  r9, r13
  sub  r9, rsi
  call tok_encode
  add  rsi, rax
  cmp  rsi, r13
  jbe  %%ok
  mov  rsi, r13
%%ok:
%endmacro
tok_encode_chat:
  push rbp
  mov  rbp, rsp
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  and  rsp, -16
  sub  rsp, 32
  mov  rbx, r8                 ; ids base
  mov  r13, r9                 ; capacity (ids)
  mov  rdi, rcx                ; content
  mov  r12, rdx                ; content len
  xor  esi, esi                ; ids so far
  TC_ID TOK_IM_START
  lea  rax, [rel tc_user]
  TC_ENCODE rax, TC_USER_LEN
  TC_ENCODE rdi, r12           ; the user's text
  lea  rax, [rel tc_nothink]
  TC_ENCODE rax, TC_NOTHINK_LEN
  TC_ID TOK_IM_END
  lea  rax, [rel tc_nl]
  TC_ENCODE rax, 1
  TC_ID TOK_IM_START
  lea  rax, [rel tc_asst]
  TC_ENCODE rax, TC_ASST_LEN
  mov  rax, rsi                ; count
  lea  rsp, [rbp - 40]
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  pop  rbp
  ret
%undef TC_ID
%undef TC_ENCODE

; ===========================================================================
; tok_decode / tok_is_stop
; ===========================================================================

; tok_decode(rcx=id, rdx=out_buf) -> rax = bytes written. Writes the raw bytes of
; token `id` by parsing its unicode UTF-8 representation (code points 0..323, so
; 1- or 2-byte sequences only) and mapping each code point back through g_u2b.
; Returns 0 for a control/special id (>= TOK_SPECIAL_MIN), emitting nothing. Leaf;
; saves rbx,rsi,rdi.
tok_decode:
  push rbx
  push rsi
  push rdi
  cmp  rcx, TOK_SPECIAL_MIN
  jae  .zero
  mov  rsi, [rel g_tok_ptr]
  mov  rsi, [rsi + rcx*8]      ; token byte ptr
  mov  rbx, [rel g_tok_len]
  mov  rbx, [rbx + rcx*8]      ; token byte len
  mov  rdi, rdx                ; out buffer
  lea  r8, [rel g_u2b]
  xor  r9, r9                  ; out count
  xor  r10, r10                ; i (index into the token bytes)
.dl:
  cmp  r10, rbx
  jae  .ddone
  movzx eax, byte [rsi + r10]
  cmp  eax, 0x80
  jb   .one
  mov  edx, eax                ; 2-byte UTF-8 -> code point
  and  edx, 0x1F
  shl  edx, 6
  movzx ecx, byte [rsi + r10 + 1]
  and  ecx, 0x3F
  or   edx, ecx
  add  r10, 2
  jmp  .map
.one:
  mov  edx, eax                ; 1-byte UTF-8 -> code point = byte
  inc  r10
.map:
  movzx eax, byte [r8 + rdx]   ; raw byte for this code point
  mov  [rdi + r9], al
  inc  r9
  jmp  .dl
.ddone:
  mov  rax, r9
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.zero:
  xor  eax, eax
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; tok_is_stop(rcx=id) -> rax = 1 if id is a generation stop (eos, or bos/pad,
; which coincide with <|endoftext|> for this model), else 0. Leaf.
tok_is_stop:
  mov  rax, [rel g_model]
  mov  rdx, [rax + Model.eos_id]
  cmp  rcx, rdx
  je   .yes
  mov  rdx, [rax + Model.bos_id]
  cmp  rcx, rdx
  je   .yes
  mov  rdx, [rax + Model.pad_id]
  cmp  rcx, rdx
  je   .yes
  xor  eax, eax
  ret
.yes:
  mov  eax, 1
  ret

; ===========================================================================
; tok_cli (--tokenize)
; ===========================================================================

; tok_cli(rcx=Model*) — read all of stdin (stripping one trailing CR/LF pair),
; tokenize it, and print the count, the ids and the round-trip decoded text to
; stderr. Saves rbx,rsi,rdi,r12-r15.
tok_cli:
  push rbp
  mov  rbp, rsp
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  and  rsp, -16
  sub  rsp, 64                 ; [rsp+32]=&len/len, [rsp+40]=text, [rsp+48]=len, [rsp+56]=decode scratch
  mov  rbx, rcx                ; Model*
  mov  rcx, rbx
  call tok_init
  lea  rcx, [rsp+32]
  call rt_read_stdin           ; rax=ptr, [rsp+32]=len
  mov  [rsp+40], rax           ; text
  mov  rcx, [rsp+32]
  mov  [rsp+48], rcx           ; len
  ; strip one trailing '\n', then a preceding '\r' (handles "\n" and "\r\n"),
  ; or a lone trailing '\r'.
  mov  rsi, rax
  mov  rdx, rcx
  test rdx, rdx
  jz   .encode
  mov  al, [rsi + rdx - 1]
  cmp  al, 0x0A
  jne  .chkcr
  dec  rdx
  test rdx, rdx
  jz   .setlen
  mov  al, [rsi + rdx - 1]
  cmp  al, 0x0D
  jne  .setlen
  dec  rdx
  jmp  .setlen
.chkcr:
  cmp  al, 0x0D
  jne  .encode
  dec  rdx
.setlen:
  mov  [rsp+48], rdx
.encode:
  ; out_ids buffer: (len+1) ids (count never exceeds len)
  mov  rcx, [rsp+48]
  inc  rcx
  shl  rcx, 2
  call rt_alloc
  mov  r12, rax                ; out_ids
  mov  rcx, 1024               ; decode scratch (one token's bytes)
  call rt_alloc
  mov  [rsp+56], rax
  mov  rcx, [rsp+40]           ; text
  mov  rdx, [rsp+48]           ; len
  mov  r8, r12
  mov  r9, [rsp+48]
  inc  r9                      ; max = len+1
  call tok_encode
  mov  r13, rax                ; count
  ; "asmrun: tokens=<count>\n"
  lea  rcx, [rel k_tok_count]
  call rt_err_str
  mov  rcx, r13
  call rt_err_u64
  lea  rcx, [rel k_nl]
  mov  rdx, 1
  call rt_err
  ; "asmrun: ids=<id> <id> ...\n"
  lea  rcx, [rel k_tok_ids]
  call rt_err_str
  xor  r14, r14
.idloop:
  cmp  r14, r13
  jae  .idloop_done
  mov  ecx, [r12 + r14*4]      ; id (zero-extended into rcx)
  call rt_err_u64
  lea  rcx, [rel k_sp]
  mov  rdx, 1
  call rt_err
  inc  r14
  jmp  .idloop
.idloop_done:
  lea  rcx, [rel k_nl]
  mov  rdx, 1
  call rt_err
  ; "asmrun: text=<round-trip decoded>\n"
  lea  rcx, [rel k_tok_text]
  call rt_err_str
  xor  r14, r14
.txtloop:
  cmp  r14, r13
  jae  .txtloop_done
  mov  ecx, [r12 + r14*4]      ; id
  mov  rdx, [rsp+56]           ; scratch
  call tok_decode              ; rax = bytes (0 for control tokens)
  test rax, rax
  jz   .txtnext
  mov  rcx, [rsp+56]
  mov  rdx, rax
  call rt_err
.txtnext:
  inc  r14
  jmp  .txtloop
.txtloop_done:
  lea  rcx, [rel k_nl]
  mov  rdx, 1
  call rt_err
  mov  r15, [rbp-56]
  mov  r14, [rbp-48]
  mov  r13, [rbp-40]
  mov  r12, [rbp-32]
  mov  rdi, [rbp-24]
  mov  rsi, [rbp-16]
  mov  rbx, [rbp-8]
  mov  rsp, rbp
  pop  rbp
  ret

; ===========================================================================
; Mutable globals and constants (zero-initialised bytes in .data).
; ===========================================================================
section .data
  align 8

g_tok_ready:    dq 0           ; 1 once tok_init has built the tables
g_model:        dq 0           ; Model* (for eos/bos/pad in tok_is_stop)
g_n_tokens:     dq 0
g_n_merges:     dq 0
g_tok_ptr:      dq 0           ; -> qword[n_tokens], per-id token byte pointer
g_tok_len:      dq 0           ; -> qword[n_tokens], per-id token byte length
g_vocab_slots:  dq 0           ; -> u32[HT_SLOTS], id+1 (0 = empty)
g_merge_ptr:    dq 0           ; -> qword[n_merges], per-rank merge-string pointer
g_merge_len:    dq 0           ; -> qword[n_merges], per-rank merge-string length
g_merge_slots:  dq 0           ; -> u32[HT_SLOTS], rank+1 (0 = empty)

; Per-encode session scratch (single-threaded tokenizer; set up by tok_encode).
g_enc_buf:      dq 0           ; unicode-mapped UTF-8 bytes of the current chunk
g_sym:          dq 0           ; symbol array: {u32 off, u32 len} into g_enc_buf
g_key:          dq 0           ; merge-key scratch "A<space>B"
g_out_ids:      dq 0           ; caller's u32 id buffer
g_out_max:      dq 0           ; capacity of g_out_ids in ids
g_out_count:    dq 0           ; ids produced so far

g_b2u:          times 256 dw 0 ; byte -> unicode code point (0..323)
g_u2b:          times 324 db 0 ; unicode code point -> byte

k_tok_count:     db "asmrun: tokens=", 0
k_tok_ids:       db "asmrun: ids=", 0
k_tok_text:      db "asmrun: text=", 0
k_tok_err_piece: db "tokenizer: piece not in vocabulary", 0
k_sp:            db ' '
k_nl:            db 10

; tok_encode_chat spans (ASCII; BPE-encoded around the <|im_*|> control ids)
tc_user:        db "user", 10
TC_USER_LEN     equ $ - tc_user
tc_nothink:     db " /no_think"
TC_NOTHINK_LEN  equ $ - tc_nothink
tc_nl:          db 10
tc_asst:        db "assistant", 10
TC_ASST_LEN     equ $ - tc_asst

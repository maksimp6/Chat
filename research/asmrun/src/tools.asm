; tools.asm — the model's file tools on the boot volume (the USB stick):
;   list_files()                   every file in the root, with its size
;   read_file(name, offset)        up to TL_READ_MAX bytes of a file, from offset
;   write_file(name, content)      the file replaced by the text
;   append_file(name, content)     the text added at the end (created if absent)
; The model asks in its own format (Qwen3.5's chat template):
;   <tool_call>
;   <function=NAME>
;   <parameter=P>
;   VALUE
;   </parameter>
;   </function>
;   </tool_call>
; tl_run finds the first call in the assistant's text, runs it and leaves the
; result text in g_tl_res; tl_encode_response makes the ids of the tool turn
; that the generator feeds back. A name is a plain file name in the root (no
; separators, no ".."); the model files (*.gguf, *.gguf.N) and the runner's own
; files (asmrun.cfg, system.txt, dialog.txt) cannot be written. The tools block
; of the system turn (tl_sys_text) is the template's own wording.
;
; %included into asmrun.asm after q35.asm (bits 64, default rel).

section .text

TL_RES_CAP   equ 8192          ; the result text the model gets back (bytes)
TL_READ_MAX  equ 4000          ; read_file returns this much per call (offset for the rest)
TL_TURN_CAP  equ 262144        ; the assistant's text kept for the call search
TL_IDS_CAP   equ 8192          ; ids of a tool turn
TL_ROUNDS    equ 32            ; tool calls answered in a row for one user line

; tl_setup() — the buffers. Clobbers the volatile registers.
tl_setup:
  WINPROLOGUE
  mov  rcx, TL_TURN_CAP
  call rt_alloc
  mov  [rel g_tl_turn], rax
  mov  rcx, TL_RES_CAP + 64
  call rt_alloc
  mov  [rel g_tl_res], rax
  mov  rcx, TL_IDS_CAP * 4
  call rt_alloc
  mov  [rel g_tl_ids], rax
  WINEPILOGUE
  ret

; tl_turn_add(rcx=ptr, rdx=len) — the assistant's text as it is generated.
; Leaf; clobbers rax, r8, r9.
tl_turn_add:
  mov  r8, [rel g_tl_turnlen]
  mov  r9, [rel g_tl_turn]
.l:
  test rdx, rdx
  jz   .d
  cmp  r8, TL_TURN_CAP
  jae  .d
  mov  al, [rcx]
  mov  [r9 + r8], al
  inc  rcx
  inc  r8
  dec  rdx
  jmp  .l
.d:
  mov  [rel g_tl_turnlen], r8
  ret

; tl_find(rcx=hay, rdx=len, r8=needle zstr) -> rax = the first match, or 0.
; Leaf; clobbers rcx.
tl_find:
  push rsi
  push rdi
  push rbx
  mov  rsi, rcx
  xor  ebx, ebx
.nl:
  cmp  byte [r8 + rbx], 0
  je   .nld
  inc  rbx
  jmp  .nl
.nld:
  xor  edi, edi
.outer:
  lea  rax, [rdi + rbx]
  cmp  rax, rdx
  ja   .no
  xor  ecx, ecx
.inner:
  cmp  rcx, rbx
  jae  .yes
  lea  r9, [rsi + rdi]
  mov  al, [r9 + rcx]
  cmp  al, [r8 + rcx]
  jne  .next
  inc  rcx
  jmp  .inner
.next:
  inc  rdi
  jmp  .outer
.yes:
  lea  rax, [rsi + rdi]
  jmp  .ret
.no:
  xor  eax, eax
.ret:
  pop  rbx
  pop  rdi
  pop  rsi
  ret

; tl_param(rcx=text, rdx=len, r8=tag zstr like "<parameter=name>") -> rax =
; the value's first byte, rdx = its length (0, 0 when the parameter is absent).
; The value runs from after the tag (one newline skipped) to "</parameter>"
; (one newline before it dropped). Clobbers rcx, r8, r9.
tl_param:
  push rsi
  push rdi
  push rbx
  mov  rsi, rcx
  mov  rdi, rdx
  mov  rbx, r8
  call tl_find
  test rax, rax
  jz   .none
  xor  ecx, ecx
.nl:
  cmp  byte [rbx + rcx], 0
  je   .nld
  inc  rcx
  jmp  .nl
.nld:
  add  rax, rcx                ; after the tag
  cmp  byte [rax], 10
  jne  .v
  inc  rax
.v:
  mov  rbx, rax                ; the value
  mov  rcx, rax
  lea  rdx, [rsi + rdi]
  sub  rdx, rax                ; the rest of the text
  lea  r8, [rel tl_s_endparam]
  call tl_find
  test rax, rax
  jz   .none
  mov  rdx, rax
  sub  rdx, rbx
  test rdx, rdx
  jz   .ok
  cmp  byte [rbx + rdx - 1], 10
  jne  .ok
  dec  rdx
.ok:
  mov  rax, rbx
  jmp  .ret
.none:
  xor  eax, eax
  xor  edx, edx
.ret:
  pop  rbx
  pop  rdi
  pop  rsi
  ret

; tl_res_add(rcx=ptr, rdx=len) — into the result (cut at TL_RES_CAP). Leaf.
tl_res_add:
  mov  r8, [rel g_tl_reslen]
  mov  r9, [rel g_tl_res]
.l:
  test rdx, rdx
  jz   .d
  cmp  r8, TL_RES_CAP
  jae  .d
  mov  al, [rcx]
  mov  [r9 + r8], al
  inc  rcx
  inc  r8
  dec  rdx
  jmp  .l
.d:
  mov  [rel g_tl_reslen], r8
  ret

; tl_res_str(rcx=zstr) — the same for a zero-terminated string. Leaf.
tl_res_str:
  xor  edx, edx
.l:
  cmp  byte [rcx + rdx], 0
  je   tl_res_add
  inc  rdx
  jmp  .l

; tl_res_u64(rcx=value) — the decimal digits into the result.
tl_res_u64:
  WINPROLOGUE
  lea  rdx, [rel g_tl_digits + 24]
  call rt_u64_digits           ; rax = first digit
  mov  rcx, rax
  lea  rdx, [rel g_tl_digits + 24]
  sub  rdx, rax
  call tl_res_add
  WINEPILOGUE
  ret

; tl_name_is(rcx=ptr, rdx=len, r8=zstr) -> rax = 1 when the bytes are exactly
; the string. Leaf.
tl_name_is:
  xor  r9d, r9d
.l:
  cmp  r9, rdx
  jae  .end
  mov  al, [r8 + r9]
  test al, al
  jz   .no
  cmp  al, [rcx + r9]
  jne  .no
  inc  r9
  jmp  .l
.end:
  cmp  byte [r8 + r9], 0
  jne  .no
  mov  eax, 1
  ret
.no:
  xor  eax, eax
  ret

; tl_name_ok(rcx=ptr, rdx=len) -> rax = 1 and g_tl_path (UTF-16) filled when
; the name is a plain root file name the model may touch; 0 with the reason in
; the result otherwise. rcx/rdx preserved for the caller's message.
tl_name_ok:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rsi, rcx
  mov  rdi, rdx
  test rdi, rdi
  jz   .bad
  cmp  rdi, 120
  ja   .bad
  xor  ebx, ebx
.c:
  cmp  rbx, rdi
  jae  .chars_ok
  movzx eax, byte [rsi + rbx]
  cmp  eax, 0x21
  jb   .bad
  cmp  eax, 0x7E
  ja   .bad
  cmp  eax, '\'
  je   .bad
  cmp  eax, '/'
  je   .bad
  cmp  eax, ':'
  je   .bad
  cmp  eax, '*'
  je   .bad
  cmp  eax, '?'
  je   .bad
  cmp  eax, '"'
  je   .bad
  cmp  eax, '<'
  je   .bad
  cmp  eax, '>'
  je   .bad
  cmp  eax, '|'
  je   .bad
  inc  rbx
  jmp  .c
.chars_ok:
  ; not "..", not a model file, not the runner's files (case-insensitive)
  mov  rcx, rsi
  mov  rdx, rdi
  lea  r8, [rel tl_s_dotdot]
  call tl_name_is
  test rax, rax
  jnz  .bad
  ; lower-case copy for the suffix and name checks
  lea  rdx, [rel g_tl_lower]
  xor  ebx, ebx
.lc:
  cmp  rbx, rdi
  jae  .lcd
  movzx eax, byte [rsi + rbx]
  cmp  eax, 'A'
  jb   .lk
  cmp  eax, 'Z'
  ja   .lk
  add  eax, 32
.lk:
  mov  [rdx + rbx], al
  inc  rbx
  jmp  .lc
.lcd:
  mov  byte [rdx + rbx], 0
  lea  rcx, [rel g_tl_lower]
  mov  rdx, rdi
  lea  r8, [rel tl_s_gguf_dot]
  call tl_find                 ; ".gguf." anywhere: a model part
  test rax, rax
  jnz  .bad
  cmp  rdi, 5
  jb   .names
  lea  rcx, [rel g_tl_lower]
  add  rcx, rdi
  sub  rcx, 5
  mov  edx, 5
  lea  r8, [rel tl_s_gguf]
  call tl_name_is              ; ends in ".gguf": a model
  test rax, rax
  jnz  .bad
.names:
  lea  rcx, [rel g_tl_lower]
  mov  rdx, rdi
  lea  r8, [rel tl_s_cfg]
  call tl_name_is
  test rax, rax
  jnz  .bad
  lea  rcx, [rel g_tl_lower]
  mov  rdx, rdi
  lea  r8, [rel tl_s_system]
  call tl_name_is
  test rax, rax
  jnz  .bad
  lea  rcx, [rel g_tl_lower]
  mov  rdx, rdi
  lea  r8, [rel tl_s_dialog]
  call tl_name_is
  test rax, rax
  jnz  .bad
  ; UTF-16
  lea  rdx, [rel g_tl_path]
  xor  ebx, ebx
.w:
  cmp  rbx, rdi
  jae  .wd
  movzx eax, byte [rsi + rbx]
  mov  [rdx + rbx*2], ax
  inc  rbx
  jmp  .w
.wd:
  mov  word [rdx + rbx*2], 0
  mov  eax, 1
  jmp  .ret
.bad:
  lea  rcx, [rel tl_s_badname]
  call tl_res_str
  mov  rcx, rsi
  mov  rdx, rdi
  call tl_res_add
  xor  eax, eax
.ret:
  mov  rcx, rsi
  mov  rdx, rdi
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; tl_run(rcx=text, rdx=len) -> rax = 1 when the text held a tool call, which
; was run (its result in g_tl_res, g_tl_reslen); 0 when it held none.
; Preserves the nonvolatile registers.
tl_run:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32] file bytes (out), [rsp+40] offset, [rsp+48] bytes given
  mov  rsi, rcx
  mov  rdi, rdx
  mov  qword [rel g_tl_reslen], 0
  lea  r8, [rel tl_s_toolcall]
  call tl_find
  test rax, rax
  jz   .none
  ; the text from the call on
  mov  rdx, rsi
  add  rdx, rdi
  sub  rdx, rax
  mov  rsi, rax
  mov  rdi, rdx
  lea  r8, [rel tl_s_function]
  mov  rcx, rsi
  mov  rdx, rdi
  call tl_find
  test rax, rax
  jz   .none
  add  rax, 10                 ; after "<function="
  mov  r12, rax                ; the name
  xor  r13d, r13d
.nm:
  lea  rax, [r12 + r13]
  mov  rcx, rsi
  add  rcx, rdi
  cmp  rax, rcx
  jae  .none
  cmp  byte [r12 + r13], '>'
  je   .named
  inc  r13
  cmp  r13, 40
  jb   .nm
  jmp  .none
.named:
  mov  rcx, r12
  mov  rdx, r13
  lea  r8, [rel tl_s_list]
  call tl_name_is
  test rax, rax
  jnz  .list
  mov  rcx, r12
  mov  rdx, r13
  lea  r8, [rel tl_s_read]
  call tl_name_is
  test rax, rax
  jnz  .read
  mov  rcx, r12
  mov  rdx, r13
  lea  r8, [rel tl_s_write]
  call tl_name_is
  test rax, rax
  jnz  .write
  mov  rcx, r12
  mov  rdx, r13
  lea  r8, [rel tl_s_append]
  call tl_name_is
  test rax, rax
  jnz  .append
  lea  rcx, [rel tl_s_unknown]
  call tl_res_str
  mov  rcx, r12
  mov  rdx, r13
  call tl_res_add
  jmp  .ran
.list:
  mov  rcx, [rel g_tl_res]
  mov  edx, TL_RES_CAP
  call rt_list_root
  mov  [rel g_tl_reslen], rax
  test rax, rax
  jnz  .ran
  lea  rcx, [rel tl_s_nofiles]
  call tl_res_str
  jmp  .ran
.read:
  mov  rcx, rsi
  mov  rdx, rdi
  lea  r8, [rel tl_s_p_name]
  call tl_param
  test rax, rax
  jz   .noname
  mov  r14, rax
  mov  r15, rdx
  mov  rcx, rsi
  mov  rdx, rdi
  lea  r8, [rel tl_s_p_offset]
  call tl_param
  xor  ecx, ecx                ; the offset: decimal digits, or 0
  mov  qword [rsp+40], 0
.od:
  cmp  rcx, rdx
  jae  .odd
  movzx r8d, byte [rax + rcx]
  sub  r8d, '0'
  cmp  r8d, 9
  ja   .odd
  imul r9, [rsp+40], 10
  add  r9, r8
  mov  [rsp+40], r9
  inc  rcx
  jmp  .od
.odd:
  mov  rcx, r14
  mov  rdx, r15
  call tl_name_ok
  test rax, rax
  jz   .ran
  lea  rcx, [rel g_tl_path]
  lea  rdx, [rsp+32]
  call rt_read_optional
  test rax, rax
  jz   .nofile
  mov  rbx, rax                ; the bytes
  mov  rcx, r14
  mov  rdx, r15
  call tl_res_add
  lea  rcx, [rel tl_s_paren]
  call tl_res_str
  mov  rcx, [rsp+32]
  call tl_res_u64
  lea  rcx, [rel tl_s_bytes]
  call tl_res_str
  cmp  qword [rsp+40], 0
  je   .nooff
  lea  rcx, [rel tl_s_from]
  call tl_res_str
  mov  rcx, [rsp+40]
  call tl_res_u64
.nooff:
  lea  rcx, [rel tl_s_colon_nl]
  call tl_res_str
  mov  rax, [rsp+40]
  cmp  rax, [rsp+32]
  jae  .ran                    ; the offset is past the end: nothing more
  mov  rcx, rbx
  add  rcx, rax
  mov  rdx, [rsp+32]
  sub  rdx, rax                ; bytes left from the offset
  cmp  rdx, TL_READ_MAX
  jbe  .whole
  mov  edx, TL_READ_MAX
.whole:
  mov  [rsp+48], rdx           ; bytes given
  call tl_res_add
  mov  rax, [rsp+40]
  add  rax, [rsp+48]           ; the next offset
  cmp  rax, [rsp+32]
  jae  .ran                    ; that was the end of the file
  mov  [rsp+40], rax
  lea  rcx, [rel tl_s_more1]
  call tl_res_str
  mov  rcx, [rsp+32]
  sub  rcx, [rsp+40]
  call tl_res_u64
  lea  rcx, [rel tl_s_more2]
  call tl_res_str
  mov  rcx, [rsp+40]
  call tl_res_u64
  lea  rcx, [rel tl_s_more3]
  call tl_res_str
  jmp  .ran
.nofile:
  lea  rcx, [rel tl_s_nofile]
  call tl_res_str
  mov  rcx, r14
  mov  rdx, r15
  call tl_res_add
  jmp  .ran
.write:
  mov  r13d, 0                 ; 0: write, 1: append
  jmp  .wa
.append:
  mov  r13d, 1
.wa:
  mov  rcx, rsi
  mov  rdx, rdi
  lea  r8, [rel tl_s_p_name]
  call tl_param
  test rax, rax
  jz   .noname
  mov  r14, rax
  mov  r15, rdx
  mov  rcx, rsi
  mov  rdx, rdi
  lea  r8, [rel tl_s_p_content]
  call tl_param
  mov  rbx, rax                ; content (0, 0 when absent: an empty file)
  mov  r12, rdx
  mov  rcx, r14
  mov  rdx, r15
  call tl_name_ok
  test rax, rax
  jz   .ran
  lea  rcx, [rel g_tl_path]
  mov  rdx, rbx
  mov  r8, r12
  test r13d, r13d
  jnz  .do_append
  call rt_write_file
  jmp  .wrote
.do_append:
  call rt_append_file
.wrote:
  test eax, eax
  jz   .wfail
  lea  rcx, [rel tl_s_wrote]
  test r13d, r13d
  jz   .msg
  lea  rcx, [rel tl_s_appended]
.msg:
  call tl_res_str
  mov  rcx, r12
  call tl_res_u64
  lea  rcx, [rel tl_s_bytes_to]
  call tl_res_str
  mov  rcx, r14
  mov  rdx, r15
  call tl_res_add
  jmp  .ran
.wfail:
  lea  rcx, [rel tl_s_wfail]
  call tl_res_str
  mov  rcx, r14
  mov  rdx, r15
  call tl_res_add
  jmp  .ran
.noname:
  lea  rcx, [rel tl_s_noname]
  call tl_res_str
.ran:
  mov  eax, 1
  jmp  .ret
.none:
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

; tl_encode_response(rcx=ids, rdx=max) -> rax = the ids of the tool turn:
;   <|im_end|>\n<|im_start|>user\n<tool_response>\n{result}\n</tool_response><|im_end|>\n
;   <|im_start|>assistant\n<think>\n\n</think>\n\n
; (the assistant's own <|im_end|> was never fed: it comes first). Uses q35's
; cursor helpers (rbx = ids, rsi = cursor, r13 = capacity).
tl_encode_response:
  push rbx
  push rsi
  push r13
  WINPROLOGUE
  mov  rbx, rcx
  mov  r13, rdx
  xor  esi, esi
  mov  rax, [rel g35_im_end]
  call q35_chat_id
  lea  rcx, [rel q35_s_lf]
  mov  edx, 1
  call q35_chat_text
  mov  rax, [rel g35_im_start]
  call q35_chat_id
  lea  rcx, [rel tl_s_user_resp]
  mov  edx, TL_USER_RESP_LEN
  call q35_chat_text
  mov  rcx, [rel g_tl_res]
  mov  rdx, [rel g_tl_reslen]
  call q35_chat_text
  lea  rcx, [rel tl_s_end_resp]
  mov  edx, TL_END_RESP_LEN
  call q35_chat_text
  mov  rax, [rel g35_im_end]
  call q35_chat_id
  lea  rcx, [rel q35_s_lf]
  mov  edx, 1
  call q35_chat_text
  mov  rax, [rel g35_im_start]
  call q35_chat_id
  lea  rcx, [rel q35_s_asst]
  mov  edx, 10
  call q35_chat_text
  mov  rax, [rel g35_think]
  test rax, rax
  jz   .done
  call q35_chat_id
  lea  rcx, [rel q35_s_lflf]
  mov  edx, 2
  call q35_chat_text
  mov  rax, [rel g35_think_end]
  call q35_chat_id
  lea  rcx, [rel q35_s_lflf]
  mov  edx, 2
  call q35_chat_text
.done:
  mov  rax, rsi
  WINEPILOGUE
  pop  r13
  pop  rsi
  pop  rbx
  ret

section .data
  align 8
g_tl_turn:    dq 0            ; the assistant's text of the turn
g_tl_turnlen: dq 0
g_tl_res:     dq 0            ; the result text
g_tl_reslen:  dq 0
g_tl_ids:     dq 0            ; the tool turn's ids
g_tl_rounds:  dq 0            ; tool calls answered for the current user line
g_tl_digits:  times 32 db 0
g_tl_lower:   times 136 db 0
g_tl_path:    times 136 dw 0
tl_s_toolcall:  db "<tool_call>", 0
tl_s_function:  db "<function=", 0
tl_s_endparam:  db "</parameter>", 0
tl_s_p_name:    db "<parameter=name>", 0
tl_s_p_offset:  db "<parameter=offset>", 0
tl_s_p_content: db "<parameter=content>", 0
tl_s_list:      db "list_files", 0
tl_s_read:      db "read_file", 0
tl_s_write:     db "write_file", 0
tl_s_append:    db "append_file", 0
tl_s_dotdot:    db "..", 0
tl_s_gguf:      db ".gguf", 0
tl_s_gguf_dot:  db ".gguf.", 0
tl_s_cfg:       db "asmrun.cfg", 0
tl_s_system:    db "system.txt", 0
tl_s_dialog:    db "dialog.txt", 0
tl_s_unknown:   db "unknown function: ", 0
tl_s_nofiles:   db "(no files)", 0
tl_s_noname:    db "the name parameter is missing", 0
tl_s_nofile:    db "no such file: ", 0
tl_s_badname:   db "not a file the model may use (a plain name in the stick's root; model files, asmrun.cfg, system.txt and dialog.txt are off limits): ", 0
tl_s_paren:     db " (", 0
tl_s_bytes:     db " bytes", 0
tl_s_from:      db ", from byte ", 0
tl_s_colon_nl:  db "):", 10, 0
tl_s_wrote:     db "wrote ", 0
tl_s_appended:  db "appended ", 0
tl_s_bytes_to:  db " bytes to ", 0
tl_s_wfail:     db "could not write ", 0
tl_s_more1:     db 10, "[", 0
tl_s_more2:     db " more bytes follow: read_file with offset ", 0
tl_s_more3:     db "]", 0
tl_s_user_resp: db "user", 10, "<tool_response>", 10
TL_USER_RESP_LEN equ $ - tl_s_user_resp
tl_s_end_resp:  db 10, "</tool_response>"
TL_END_RESP_LEN equ $ - tl_s_end_resp
tl_s_shown:     db 10, "[tool result]", 10, 0
; the tools block of the system turn: the chat template's own wording around the four functions
tl_sys_text:
  db `# Tools\n\nYou have access to the following functions:\n\n<tools>\n`
  db `{"type": "function", "function": {"name": "list_files", "description": "List every file on the USB stick this computer runs from: one line per file, name and size in bytes.", "parameters": {"type": "object", "properties": {}, "required": []}}}\n`
  db `{"type": "function", "function": {"name": "read_file", "description": "Read a text file from the USB stick: up to 4000 bytes from the given byte offset (0 when omitted); the reply says how many bytes the file has.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "the file name on the stick"}, "offset": {"type": "integer", "description": "the byte to start from"}}, "required": ["name"]}}}\n`
  db `{"type": "function", "function": {"name": "write_file", "description": "Create or replace a file on the USB stick with the given text.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "the file name on the stick"}, "content": {"type": "string", "description": "the whole text of the file"}}, "required": ["name", "content"]}}}\n`
  db `{"type": "function", "function": {"name": "append_file", "description": "Add text to the end of a file on the USB stick (the file is created when absent).", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "the file name on the stick"}, "content": {"type": "string", "description": "the text to add"}}, "required": ["name", "content"]}}}\n`
  db `</tools>\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:\n\n<tool_call>\n<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n</parameter>\n<parameter=example_parameter_2>\nThis is the value for the second parameter\nthat can span\nmultiple lines\n</parameter>\n</function>\n</tool_call>\n\n<IMPORTANT>\nReminder:\n- Function calls MUST follow the specified format: an inner <function=...></function> block must be nested within <tool_call></tool_call> XML tags\n- Required parameters MUST be specified\n- You may provide optional reasoning for your function call in natural language BEFORE the function call, but NOT after\n- If there is no function call available, answer the question like normal with your current knowledge and do not tell the user about function calls\n</IMPORTANT>`
TL_SYS_LEN equ $ - tl_sys_text
section .text

; http.asm — the model behind an HTTP server: a page for a phone's browser and a
; one-call API. `--http PORT` makes the chat loop take its prompts from the
; network instead of the keyboard: http_wait_prompt accepts connections until a
; `POST /api/chat` arrives, returns its body as the prompt, and everything the
; model then writes to stdout is captured (http_capture, called from
; rt_write_h) and sent back as the response when the loop asks for the next
; prompt. `GET /` serves the page below; anything else is 404.
;
; The transport is the platform's: src/tcp4.asm on the firmware
; (EFI_TCP4_PROTOCOL: the BIOS network stack on the Ethernet port) or
; src/wsock.asm on Windows (the test bench). Both give net_listen, net_accept,
; net_recv, net_send, net_close. One connection at a time: the model is busy
; for the whole request anyway.

section .text

HTTP_REQ_CAP equ 65536               ; request bytes kept (headers + body)
HTTP_CAP_CAP equ 1048576             ; answer bytes captured
HTTP_HDR_CAP equ 512
HTTP_TASK_CHARS equ 128              ; a task file's name (UTF-16 units, with the terminator)

; http_wait_prompt(rcx=&len) -> rax = prompt bytes (UTF-8), [rcx] = length;
; rax = 0 when the server could not start (g_http_port is then cleared and the
; caller reads the console instead). Finishes the previous request first.
http_wait_prompt:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  sub  rsp, 48
  mov  r12, rcx                ; &len
  ; the previous request: its answer is the captured stdout
  mov  qword [rel g_http_cap_on], 0
  cmp  qword [rel g_http_task], 0
  je   .notask
  call http_task_finish        ; --auto: the answer to a task file goes to its .answer.txt, the task is deleted
.notask:
  mov  rcx, [rel g_http_conn]
  test rcx, rcx
  jz   .nofinish
  cmp  qword [rel g_api_mode], 0
  jne  .api_finish
  lea  rdx, [rel http_s_200]
  lea  r8, [rel http_s_ct_text]
  mov  r9, [rel g_http_cap]
  mov  rax, [rel g_http_cap_len]
  mov  [rsp+32], rax
  call http_respond
  jmp  .closeprev
.api_finish:
  cmp  qword [rel g_api_mode], 2
  je   .turn_finish
  call api_answer_json         ; rax = the NDJSON line's length in g_api_out
  mov  [rsp+32], rax
  mov  rcx, [rel g_http_conn]
  lea  rdx, [rel http_s_200]
  lea  r8, [rel api_s_ct_json]
  mov  r9, [rel g_api_out]
  call http_respond
  jmp  .api_done
.turn_finish:
  mov  qword [rel g_api_stream], 0
  call api_turn_json           ; the final line, with the turn's numbers and temperatures
  mov  rcx, [rel g_http_conn]
  mov  rdx, [rel g_api_out]
  mov  r8, rax
  call http_chunk
  mov  rcx, [rel g_http_conn]
  call http_stream_end
.api_done:
  mov  qword [rel g_api_mode], 0
  mov  qword [rel g_api_nids], 0
.closeprev:
  mov  rcx, [rel g_http_conn]
  call net_close
  mov  qword [rel g_http_conn], 0
.nofinish:
  cmp  qword [rel g_http_up], 0
  jne  .accept
  cmp  qword [rel g_http_req], 0
  jne  .bufs
  mov  rcx, HTTP_REQ_CAP
  call rt_alloc
  mov  [rel g_http_req], rax
  mov  rcx, HTTP_CAP_CAP
  call rt_alloc
  mov  [rel g_http_cap], rax
.bufs:
  mov  rcx, [rel g_http_port]
  call net_listen
  test rax, rax
  jnz  .fail
  mov  qword [rel g_http_up], 1
.accept:
  call net_accept
  test rax, rax
  jnz  .got
  call rt_kick                 ; the heartbeat (the watchdog, under --auto)
  cmp  qword [rel g_auto], 0
  je   .accept
  ; idle under --auto: a task file on the stick, or a key on the console
  lea  rcx, [rel g_http_taskname]
  mov  edx, HTTP_TASK_CHARS
  call rt_find_task
  test rax, rax
  jnz  .task
  call rt_key_ready
  test rax, rax
  jz   .accept
  mov  qword [rel g_http_key], 1 ; the caller reads this one prompt from the console
  xor  eax, eax
  jmp  .ret
.task:
  lea  rcx, [rel g_http_taskname]
  lea  rdx, [rel g_http_tasklen]
  call rt_read_optional
  test rax, rax
  jz   .accept                 ; gone before it could be read
  mov  rbx, rax
  lea  rcx, [rel http_s_task]
  call rt_err_str
  lea  rcx, [rel g_http_taskname]
  lea  rdx, [rel g_http_taskname8]
  mov  r8d, HTTP_TASK_CHARS*3
  call rt_utf16_to_utf8
  lea  rcx, [rel g_http_taskname8]
  mov  rdx, rax
  call rt_err
  lea  rcx, [rel http_s_nl]
  call rt_err_str
  ; the file is the prompt, without a UTF-8 BOM (Notepad writes one) and its
  ; trailing line ends; the answer is captured
  mov  rdx, [rel g_http_tasklen]
  cmp  rdx, 3
  jb   .tstrip
  cmp  byte [rbx], 0xEF
  jne  .tstrip
  cmp  byte [rbx+1], 0xBB
  jne  .tstrip
  cmp  byte [rbx+2], 0xBF
  jne  .tstrip
  add  rbx, 3
  sub  rdx, 3
.tstrip:
  test rdx, rdx
  jz   .tlen
  cmp  byte [rbx + rdx - 1], 0x20
  ja   .tlen
  dec  rdx
  jmp  .tstrip
.tlen:
  mov  [r12], rdx
  mov  qword [rel g_http_task], 1
  mov  qword [rel g_http_cap_len], 0
  mov  qword [rel g_http_cap_on], 1
  mov  qword [rel g_api_mode], 0
  mov  rax, rbx
  jmp  .ret
.got:
  mov  rbx, rax                ; the connection
  ; read the request: headers to their end, then Content-Length body bytes
  mov  rsi, [rel g_http_req]
  xor  r13d, r13d              ; bytes so far
  xor  edi, edi                ; body length (Content-Length)
  mov  r14, -1                 ; header length when found, -1 before
.more:
  mov  rcx, rbx
  lea  rdx, [rsi + r13]
  mov  r8, HTTP_REQ_CAP - 1
  sub  r8, r13
  jz   .full
  call net_recv
  test rax, rax
  jz   .drop                   ; closed before a complete request
  add  r13, rax
  cmp  r14, -1
  jne  .body
  mov  rcx, rsi
  mov  rdx, r13
  call http_find_hdr_end       ; rax = header length incl. the blank line, or -1
  cmp  rax, -1
  je   .more
  mov  r14, rax
  mov  rcx, rsi
  mov  rdx, r14
  call http_content_length     ; rax = Content-Length or 0
  mov  rdi, rax
.body:
  lea  rax, [r14 + rdi]        ; header + body
  cmp  rax, HTTP_REQ_CAP - 1
  ja   .full
  cmp  r13, rax
  jb   .more
  ; a complete request: Ollama's API first (api.asm): handled and to be closed
  ; (0), a chat accepted (1), or not one of its paths (2)
  mov  rcx, rbx
  mov  rdx, rsi
  lea  r8, [rsi + r14]
  mov  r9, rdi
  call api_route
  test rax, rax
  jz   .drop
  cmp  rax, 1
  je   .api_prompt
  ; then the runner's own: method and path
  cmp  dword [rsi], 'GET '
  jne  .notget
  lea  rcx, [rsi + 4]
  call http_path_is_root
  test rax, rax
  jz   .notfound
  mov  rcx, rbx
  lea  rdx, [rel http_s_200]
  lea  r8, [rel http_s_ct_html]
  lea  r9, [rel http_page]
  mov  qword [rsp+32], HTTP_PAGE_LEN
  call http_respond
  jmp  .drop
.notget:
  cmp  dword [rsi], 'POST'
  jne  .notfound
  lea  rcx, [rsi + 5]
  call http_path_is_chat
  test rax, rax
  jz   .notfound
  test rdi, rdi
  jnz  .prompt
  mov  rcx, rbx
  lea  rdx, [rel http_s_400]
  lea  r8, [rel http_s_ct_text]
  lea  r9, [rel http_s_empty]
  mov  qword [rsp+32], HTTP_S_EMPTY_LEN
  call http_respond
  jmp  .drop
.notfound:
  mov  rcx, rbx
  lea  rdx, [rel http_s_404]
  lea  r8, [rel http_s_ct_text]
  lea  r9, [rel http_s_nf]
  mov  qword [rsp+32], HTTP_S_NF_LEN
  call http_respond
  jmp  .drop
.full:
  mov  rcx, rbx
  lea  rdx, [rel http_s_413]
  lea  r8, [rel http_s_ct_text]
  lea  r9, [rel http_s_big]
  mov  qword [rsp+32], HTTP_S_BIG_LEN
  call http_respond
.drop:
  mov  rcx, rbx
  call net_close
  cmp  qword [rel g_api_reboot], 0
  je   .accept
  xor  ecx, ecx                ; POST /v1/model answered and closed: the runner restarts on the new cfg
  call rt_exit
  jmp  .accept
.prompt:
  ; the body is the prompt; the answer is captured from here on
  mov  [rel g_http_conn], rbx
  mov  qword [rel g_http_cap_len], 0
  mov  qword [rel g_http_cap_on], 1
  mov  [r12], rdi
  lea  rax, [rsi + r14]
  jmp  .ret
.api_prompt:
  ; an API request: the ids are ready in api.asm; the loop gets the last user
  ; message as the line to log. The turn API streams: headers now, deltas as
  ; the model writes, the final line when the loop comes back
  mov  [rel g_http_conn], rbx
  mov  qword [rel g_http_cap_len], 0
  cmp  qword [rel g_api_mode], 2
  jne  .api_cap
  mov  rcx, rbx
  lea  rdx, [rel api_s_ct_ndjson]
  call http_stream_begin
  mov  qword [rel g_api_stream], 1
.api_cap:
  mov  qword [rel g_http_cap_on], 1
  mov  rax, [rel g_api_userlen]
  test rax, rax
  jz   .api_noline
  mov  [r12], rax
  mov  rax, [rel g_api_user]
  jmp  .ret
.api_noline:
  mov  qword [r12], HTTP_S_APITURN_LEN
  lea  rax, [rel http_s_apiturn]
  jmp  .ret
.fail:
  lea  rcx, [rel http_s_fail]
  call rt_err_str
  mov  qword [rel g_http_port], 0
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; http_task_finish — the answer captured for the task file in g_http_taskname
; goes to <name>.answer.txt beside it (task-x.txt -> task-x.answer.txt), then
; the task file is deleted so the queue moves on. Preserves the nonvolatile
; registers.
http_task_finish:
  push rbx
  push rsi
  WINPROLOGUE
  mov  qword [rel g_http_task], 0
  ; the answer's name: the task's without ".txt", then ".answer.txt" and the terminator
  lea  rbx, [rel g_http_taskname]
  xor  esi, esi
.len:
  cmp  word [rbx + rsi*2], 0
  je   .lend
  inc  rsi
  jmp  .len
.lend:
  sub  rsi, 4
  lea  rcx, [rel g_http_answname]
  mov  rdx, rbx
  lea  r8, [rsi*2]
  call rt_memcpy
  lea  rcx, [rel g_http_answname]
  lea  rcx, [rcx + rsi*2]
  lea  rdx, [rel http_w_answer_txt]
  mov  r8d, 24
  call rt_memcpy
  lea  rcx, [rel g_http_answname]
  mov  rdx, [rel g_http_cap]
  mov  r8, [rel g_http_cap_len]
  call rt_write_file
  test rax, rax
  jz   .keep                   ; the answer could not be written: the task stays for the next pass
  lea  rcx, [rel g_http_taskname]
  call rt_delete_file
  lea  rcx, [rel http_s_task_done]
  call rt_err_str
  jmp  .ret
.keep:
  lea  rcx, [rel http_s_task_fail]
  call rt_err_str
.ret:
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; http_capture(rcx=ptr, rdx=len) — stdout bytes into the answer buffer (called
; from rt_write_h while g_http_cap_on). Preserves the nonvolatile registers.
http_capture:
  WINPROLOGUE
  cmp  qword [rel g_api_stream], 0
  je   .buffer
  cmp  qword [rel g_api_mute], 0
  jne  .buffer                 ; inside a tool call: kept for the final line, not streamed
  push rcx
  push rdx
  sub  rsp, 32
  mov  r8, rdx
  mov  rdx, rcx
  mov  rcx, [rel g_http_conn]
  call api_delta_chunk         ; the piece goes out at once
  add  rsp, 32
  pop  rdx
  pop  rcx
.buffer:
  mov  rax, [rel g_http_cap_len]
  mov  r8, rax
  add  r8, rdx
  cmp  r8, HTTP_CAP_CAP
  ja   .done                   ; the rest of a huge answer is dropped
  mov  [rel g_http_cap_len], r8
  mov  r8, rdx
  mov  rdx, rcx
  mov  rcx, [rel g_http_cap]
  add  rcx, rax
  call rt_memcpy
  ; a model that spells <tool_call> in pieces (this fine-tune does) has no single
  ; token to catch: the captured text's tail is checked instead, and the stream
  ; is muted from the marker on
  cmp  qword [rel g_api_mute], 0
  jne  .done
  mov  rax, [rel g_http_cap_len]
  cmp  rax, 11
  jb   .done
  mov  rcx, [rel g_http_cap]
  add  rcx, rax
  cmp  dword [rcx - 11], '<too'
  jne  .done
  cmp  dword [rcx - 7], 'l_ca'
  jne  .done
  cmp  word [rcx - 3], 'll'
  jne  .done
  cmp  byte [rcx - 1], '>'
  jne  .done
  mov  qword [rel g_api_mute], 1
.done:
  WINEPILOGUE
  ret

; http_respond(rcx=conn, rdx=status zstr, r8=content-type zstr, r9=body,
; [rsp+40]=body length) — the status line, the headers and the body.
http_respond:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32..52) digits
  mov  rbx, rcx
  mov  r12, r9
  mov  r13, [rbp + 48 + 40]    ; the fifth argument: 5 pushes + rbp + return address above the frame
  lea  rdi, [rel g_http_hdr]   ; write cursor
  lea  rsi, [rel http_s_ver]
  call http_cat
  mov  rsi, rdx
  call http_cat
  lea  rsi, [rel http_s_h1]
  call http_cat
  mov  rsi, r8
  call http_cat
  lea  rsi, [rel http_s_h2]
  call http_cat
  mov  rcx, r13
  lea  rdx, [rsp+52]
  call rt_u64_digits           ; rax = first digit, digits end at [rsp+52]
.dg:
  lea  rcx, [rsp+52]
  cmp  rax, rcx
  jae  .dgd
  mov  cl, [rax]
  mov  [rdi], cl
  inc  rdi
  inc  rax
  jmp  .dg
.dgd:
  lea  rsi, [rel http_s_h3]
  call http_cat
  mov  rcx, rbx
  lea  rdx, [rel g_http_hdr]
  mov  r8, rdi
  sub  r8, rdx
  call net_send
  test rax, rax
  jnz  .done
  mov  rcx, rbx
  mov  rdx, r12
  mov  r8, r13
  call net_send
.done:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; --- a streamed response: headers with Transfer-Encoding: chunked, then chunks ---

; http_stream_begin(rcx=conn, rdx=content-type zstr) -> rax = 0 when the headers went out.
http_stream_begin:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rbx, rcx
  lea  rdi, [rel g_http_hdr]
  lea  rsi, [rel http_s_ver]
  call http_cat
  lea  rsi, [rel http_s_200]
  call http_cat
  lea  rsi, [rel http_s_h1]
  call http_cat
  mov  rsi, rdx
  call http_cat
  lea  rsi, [rel http_s_chunked]
  call http_cat
  mov  rcx, rbx
  lea  rdx, [rel g_http_hdr]
  mov  r8, rdi
  sub  r8, rdx
  call net_send
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; http_chunk(rcx=conn, rdx=bytes, r8=len) -> rax = 0 ok. One chunk: "<hex>\r\n", the bytes, "\r\n".
http_chunk:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  sub  rsp, 32
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  test rdi, rdi
  jz   .ok
  ; the size line, hex digits backwards from [rsp+52)
  lea  rcx, [rsp+52]
  mov  rax, rdi
  lea  r9, [rel k_hex_digits]
.hex:
  dec  rcx
  mov  rdx, rax
  and  edx, 15
  mov  dl, [r9 + rdx]
  mov  [rcx], dl
  shr  rax, 4
  jnz  .hex
  mov  word [rsp+52], 0x0A0D
  mov  rdx, rcx
  lea  r8, [rsp+54]
  sub  r8, rcx
  mov  rcx, rbx
  call net_send
  test rax, rax
  jnz  .ret
  mov  rcx, rbx
  mov  rdx, rsi
  mov  r8, rdi
  call net_send
  test rax, rax
  jnz  .ret
  mov  rcx, rbx
  lea  rdx, [rel http_s_crlf]
  mov  r8d, 2
  call net_send
  jmp  .ret
.ok:
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; http_stream_end(rcx=conn) — the last, empty chunk.
http_stream_end:
  WINPROLOGUE
  lea  rdx, [rel http_s_lastchunk]
  mov  r8d, 5
  call net_send
  WINEPILOGUE
  ret

; http_cat — append the zero-terminated string at rsi to rdi (both advance; rdi
; stays at the end). Leaf; clobbers al.
http_cat:
  mov  al, [rsi]
  test al, al
  jz   .done
  mov  [rdi], al
  inc  rsi
  inc  rdi
  jmp  http_cat
.done:
  ret

; http_find_hdr_end(rcx=buf, rdx=len) -> rax = offset just past "\r\n\r\n", or -1.
http_find_hdr_end:
  xor  eax, eax
.scan:
  lea  r8, [rax + 4]
  cmp  r8, rdx
  ja   .none
  cmp  dword [rcx + rax], 0x0A0D0A0D
  je   .found
  inc  rax
  jmp  .scan
.found:
  add  rax, 4
  ret
.none:
  mov  rax, -1
  ret

; http_content_length(rcx=hdr, rdx=hdrlen) -> rax = the Content-Length value or 0.
; The header name is matched case-insensitively at the start of a line.
http_content_length:
  xor  eax, eax                ; cursor
.line:
  lea  r8, [rax + 16]
  cmp  r8, rdx
  ja   .zero
  xor  r9d, r9d
.cmp:
  lea  r11, [rcx + rax]
  movzx r10d, byte [r11 + r9]
  or   r10d, 0x20
  lea  r11, [rel http_s_clen]
  cmp  r10b, [r11 + r9]
  jne  .next
  inc  r9
  cmp  r9, 15
  jb   .cmp
  ; the value: digits after optional spaces
  lea  r8, [rax + 15]
  xor  eax, eax
.sp:
  cmp  r8, rdx
  jae  .ret
  cmp  byte [rcx + r8], ' '
  jne  .dig
  inc  r8
  jmp  .sp
.dig:
  cmp  r8, rdx
  jae  .ret
  movzx r9d, byte [rcx + r8]
  sub  r9d, '0'
  cmp  r9d, 9
  ja   .ret
  imul rax, rax, 10
  add  rax, r9
  inc  r8
  jmp  .dig
.next:
  ; to the start of the next line
  cmp  byte [rcx + rax], 0x0A
  lea  rax, [rax + 1]
  jne  .next_chk
  jmp  .line
.next_chk:
  cmp  rax, rdx
  jb   .next
.zero:
  xor  eax, eax
.ret:
  ret

; http_path_is_root(rcx=path start) -> rax = 1 for "/ " or "/?..." or "/index.html"
http_path_is_root:
  xor  eax, eax
  cmp  byte [rcx], '/'
  jne  .no
  mov  dl, [rcx + 1]
  cmp  dl, ' '
  je   .yes
  cmp  dl, '?'
  je   .yes
  cmp  dword [rcx + 1], 'inde'
  jne  .no
.yes:
  mov  eax, 1
.no:
  ret

; http_path_is_chat(rcx=path start) -> rax = 1 for "/api/chat" followed by ' ' or '?'
http_path_is_chat:
  xor  eax, eax
  cmp  dword [rcx], '/api'
  jne  .no
  cmp  dword [rcx + 4], '/cha'
  jne  .no
  cmp  byte [rcx + 8], 't'
  jne  .no
  mov  dl, [rcx + 9]
  cmp  dl, ' '
  je   .yes
  cmp  dl, '?'
  jne  .no
.yes:
  mov  eax, 1
.no:
  ret

; http_wparse_ip(rcx=UTF-16 "a.b.c.d") -> eax = the four octets, first octet in
; the low byte (network order in memory). 0 when malformed.
http_wparse_ip:
  xor  eax, eax                ; result
  xor  r8d, r8d                ; octets done
.octet:
  xor  edx, edx                ; value
  xor  r9d, r9d                ; digits
.dig:
  movzx r10d, word [rcx]
  sub  r10d, '0'
  cmp  r10d, 9
  ja   .sep
  imul edx, edx, 10
  add  edx, r10d
  cmp  edx, 255
  ja   .bad
  inc  r9d
  add  rcx, 2
  jmp  .dig
.sep:
  test r9d, r9d
  jz   .bad
  mov  r11d, eax               ; place the octet: result |= value << (8*octets)
  mov  r10d, r8d
  shl  r10d, 3
  xchg ecx, r10d               ; cl = shift (rcx kept in r10)
  shl  edx, cl
  xchg ecx, r10d
  or   r11d, edx
  mov  eax, r11d
  inc  r8d
  cmp  r8d, 4
  je   .ret
  cmp  word [rcx], '.'
  jne  .bad
  add  rcx, 2
  jmp  .octet
.ret:
  ret
.bad:
  xor  eax, eax
  ret

http_s_ver:     db "HTTP/1.1 ", 0
http_s_200:     db "200 OK", 0
http_s_400:     db "400 Bad Request", 0
http_s_404:     db "404 Not Found", 0
http_s_413:     db "413 Payload Too Large", 0
http_s_h1:      db 13, 10, "Content-Type: ", 0
http_s_ct_text: db "text/plain; charset=utf-8", 0
http_s_ct_html: db "text/html; charset=utf-8", 0
http_s_h2:      db 13, 10, "Content-Length: ", 0
http_s_h3:      db 13, 10, "Connection: close", 13, 10, "Cache-Control: no-store", 13, 10, "Access-Control-Allow-Origin: *", 13, 10, 13, 10, 0
http_s_chunked: db 13, 10, "Transfer-Encoding: chunked", 13, 10, "Connection: close", 13, 10, "Cache-Control: no-store", 13, 10, "Access-Control-Allow-Origin: *", 13, 10, 13, 10, 0
http_s_crlf:    db 13, 10
http_s_lastchunk: db "0", 13, 10, 13, 10
http_s_clen:    db "content-length:"
http_s_empty:   db "empty prompt"
HTTP_S_EMPTY_LEN equ $ - http_s_empty
http_s_nf:      db "not found: GET / for the page, POST /api/chat with the prompt as the body"
HTTP_S_NF_LEN equ $ - http_s_nf
http_s_big:     db "request too large"
HTTP_S_BIG_LEN equ $ - http_s_big
http_s_apiturn: db "(api turn)"
HTTP_S_APITURN_LEN equ $ - http_s_apiturn
http_s_fail:    db "asmrun: http: the server could not start; the console takes the prompts", 10, 0
http_s_task:    db "asmrun: auto: task ", 0
http_s_nl:      db 10, 0
http_s_task_done: db "asmrun: auto: the answer was written, the task deleted", 10, 0
http_s_task_fail: db "asmrun: auto: the answer could not be written; the task stays", 10, 0
  align 2
http_w_answer_txt: dw __utf16__(".answer.txt"), 0

; The page: one column, the log above, the input below; a POST of the text to
; /api/chat, the plain-text answer appended.
http_page:
  db `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>asmrun</title>`
  db `<style>body{font:16px system-ui,sans-serif;margin:0;background:#111;color:#eee;display:flex;flex-direction:column;height:100vh}`
  db `#log{flex:1;overflow:auto;padding:12px;white-space:pre-wrap;word-wrap:break-word}.u{color:#8cf;margin-top:10px}.a{color:#eee;margin-top:4px}`
  db `form{display:flex;padding:8px;gap:8px;border-top:1px solid #333}textarea{flex:1;font:inherit;background:#222;color:#eee;border:1px solid #444;border-radius:8px;padding:8px;resize:none}`
  db `button{font:inherit;padding:8px 16px;border-radius:8px;border:0;background:#4a8;color:#000}button:disabled{background:#355}</style></head>`
  db `<body><div id="log"></div><form id="f"><textarea id="t" rows="2" placeholder="say something"></textarea><button id="b">send</button></form>`
  db `<script>const L=document.getElementById('log'),T=document.getElementById('t'),F=document.getElementById('f'),B=document.getElementById('b');`
  db `function add(c,s){const d=document.createElement('div');d.className=c;d.textContent=s;L.appendChild(d);L.scrollTop=L.scrollHeight;return d}`
  db `F.onsubmit=async e=>{e.preventDefault();const q=T.value.trim();if(!q)return;T.value='';B.disabled=true;add('u','> '+q);const w=add('a','...');`
  db `try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'text/plain;charset=utf-8'},body:q});w.textContent=(await r.text()).trim()}`
  db `catch(x){w.textContent='error: '+x}B.disabled=false;T.focus()};T.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();F.requestSubmit()}};</script></body></html>`
HTTP_PAGE_LEN equ $ - http_page

section .data
  align 8
g_http_port:    dq 0           ; --http PORT; 0 = the console
g_http_up:      dq 0           ; 1 once net_listen succeeded
g_http_req:     dq 0           ; the request buffer (HTTP_REQ_CAP)
g_http_cap:     dq 0           ; the answer buffer (HTTP_CAP_CAP)
g_http_cap_len: dq 0
g_http_cap_on:  dq 0           ; 1: rt_write_h copies stdout into the answer buffer
g_http_conn:    dq 0           ; the connection waiting for its answer, or 0
g_http_key:     dq 0           ; --auto: 1 when http_wait_prompt came back for a keystroke; the loop reads the console once
g_http_task:    dq 0           ; --auto: 1 while the prompt being answered came from a task file
g_http_tasklen: dq 0
g_http_taskname: times HTTP_TASK_CHARS*2 db 0   ; the task file's name (UTF-16, zero-terminated)
g_http_answname: times HTTP_TASK_CHARS*2 + 24 db 0 ; its answer's
g_http_taskname8: times HTTP_TASK_CHARS*3 db 0  ; the name in UTF-8 for the log
g_http_ip:      dq 0           ; --ip: the station address (network order), 0 = the firmware's own (DHCP)
g_http_mask:    dq 0           ; --mask
g_http_gw:      dq 0           ; --gw
g_http_hdr:     times HTTP_HDR_CAP db 0
section .text

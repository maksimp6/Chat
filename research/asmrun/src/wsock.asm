; wsock.asm — the Windows test bench's network transport for http.asm: Winsock
; 2 (ws2_32.dll), blocking calls, one connection at a time. The same five
; functions as src/tcp4.asm on the firmware. Windows only.

section .text

WS_AF_INET      equ 2
WS_SOCK_STREAM  equ 1
WS_IPPROTO_TCP  equ 6
WS_INVALID      equ -1

; net_listen(rcx=port) -> rax = 0 when listening, 1 on failure.
net_listen:
  push rbx
  WINPROLOGUE
  sub  rsp, 416                ; [rsp+32..432) WSADATA (400 bytes), [rsp+432..448) sockaddr_in
  mov  rbx, rcx
  mov  ecx, 0x0202
  lea  rdx, [rsp+32]
  call winimp(WSAStartup)
  test eax, eax
  jnz  .fail
  mov  ecx, WS_AF_INET
  mov  edx, WS_SOCK_STREAM
  mov  r8d, WS_IPPROTO_TCP
  call winimp(socket)
  cmp  rax, WS_INVALID
  je   .fail
  mov  [rel g_ws_listen], rax
  ; sockaddr_in: family, port (big-endian), INADDR_ANY, zero
  mov  word [rsp+432], WS_AF_INET
  mov  eax, ebx
  xchg al, ah
  mov  [rsp+434], ax
  mov  dword [rsp+436], 0
  mov  qword [rsp+440], 0
  mov  rcx, [rel g_ws_listen]
  lea  rdx, [rsp+432]
  mov  r8d, 16
  call winimp(bind)
  test eax, eax
  jnz  .fail
  mov  rcx, [rel g_ws_listen]
  mov  edx, 4
  call winimp(listen)
  test eax, eax
  jnz  .fail
  lea  rcx, [rel s_ws_listen]
  call rt_err_str
  mov  rcx, rbx
  call rt_err_u64
  lea  rcx, [rel s_ws_nl]
  call rt_err_str
  xor  eax, eax
  jmp  .ret
.fail:
  lea  rcx, [rel s_ws_fail]
  call rt_err_str
  mov  eax, 1
.ret:
  WINEPILOGUE
  pop  rbx
  ret

; net_accept() -> rax = the connection (a SOCKET) or 0.
net_accept:
  WINPROLOGUE
  sub  rsp, 544                ; [rsp+40] timeval, [rsp+48..568) fd_set (count, then 64 sockets)
  cmp  qword [rel g_auto], 0
  je   .block
  ; autonomous: a second on select, then back to the idle loop (the task queue)
  mov  dword [rsp+48], 1
  mov  rax, [rel g_ws_listen]
  mov  [rsp+56], rax
  mov  dword [rsp+40], 1       ; tv_sec
  mov  dword [rsp+44], 0       ; tv_usec
  xor  ecx, ecx
  lea  rdx, [rsp+48]
  xor  r8d, r8d
  xor  r9d, r9d
  lea  rax, [rsp+40]
  mov  [rsp+32], rax
  call winimp(select)
  cmp  eax, 1
  jne  .none                   ; 0: nothing in a second; -1: failed
.block:
  mov  rcx, [rel g_ws_listen]
  xor  edx, edx
  xor  r8d, r8d
  call winimp(accept)
  cmp  rax, WS_INVALID
  jne  .ret
.none:
  xor  eax, eax
.ret:
  WINEPILOGUE
  ret

; net_recv(rcx=conn, rdx=buf, r8=cap) -> rax = bytes, 0 when closed or failed.
net_recv:
  WINPROLOGUE
  xor  r9d, r9d
  call winimp(recv)
  test eax, eax
  jg   .ret
  xor  eax, eax
.ret:
  movsxd rax, eax
  WINEPILOGUE
  ret

; net_send(rcx=conn, rdx=buf, r8=len) -> rax = 0 when all bytes went out, else 1.
net_send:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
.more:
  test rdi, rdi
  jz   .ok
  mov  rcx, rbx
  mov  rdx, rsi
  mov  r8, rdi
  xor  r9d, r9d
  call winimp(send)
  test eax, eax
  jle  .fail
  movsxd rax, eax
  add  rsi, rax
  sub  rdi, rax
  jmp  .more
.ok:
  xor  eax, eax
  jmp  .ret
.fail:
  mov  eax, 1
.ret:
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; net_close(rcx=conn)
net_close:
  WINPROLOGUE
  call winimp(closesocket)
  WINEPILOGUE
  ret

s_ws_listen:    db "asmrun: http: listening on http://localhost:", 0
s_ws_nl:        db 10, 0
s_ws_fail:      db "asmrun: http: Winsock setup failed", 10, 0

section .data
  align 8
g_ws_listen:    dq 0
section .text

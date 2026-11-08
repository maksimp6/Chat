; tcp4.asm — the network transport of the no-OS build: EFI_TCP4_PROTOCOL, the
; firmware's own TCP/IP on the Ethernet port (present when the BIOS network
; stack is on: the same stack PXE and HTTP boot use). One listening socket, one
; connection at a time, everything synchronous: each call fills a token, starts
; the operation and polls the protocol until the token's event fires.
;
; The station address is the firmware's own (UseDefaultAddress: DHCP, waited
; for up to 30 s) unless --ip gives a static one (--mask, --gw optional).
;
; UEFI only (included under ASMRUN_EFI).

section .text

; EFI_TCP4_SERVICE_BINDING_PROTOCOL
TCP4SB_CreateChild   equ 0           ; (This, EFI_HANDLE* ChildHandle)
TCP4SB_DestroyChild  equ 8           ; (This, EFI_HANDLE ChildHandle)
; EFI_TCP4_PROTOCOL
TCP4_GetModeData     equ 0           ; (This, State*, ConfigData*, Ip4ModeData*, MnpConfigData*, SnpModeData*)
TCP4_Configure       equ 8           ; (This, EFI_TCP4_CONFIG_DATA*)
TCP4_Routes          equ 16          ; (This, DeleteRoute, Subnet*, Mask*, Gateway*)
TCP4_Accept          equ 32          ; (This, EFI_TCP4_LISTEN_TOKEN*)
TCP4_Transmit        equ 40          ; (This, EFI_TCP4_IO_TOKEN*)
TCP4_Receive         equ 48          ; (This, EFI_TCP4_IO_TOKEN*)
TCP4_Close           equ 56          ; (This, EFI_TCP4_CLOSE_TOKEN*)
TCP4_Cancel          equ 64          ; (This, EFI_TCP4_COMPLETION_TOKEN*)
TCP4_Poll            equ 72          ; (This)
; EFI_TCP4_CONFIG_DATA (32 bytes)
TCFG_TypeOfService   equ 0           ; u8
TCFG_TimeToLive      equ 1           ; u8
TCFG_UseDefault      equ 2           ; BOOLEAN   (EFI_TCP4_ACCESS_POINT from here)
TCFG_StationAddr     equ 3           ; 4 bytes
TCFG_SubnetMask      equ 7           ; 4 bytes
TCFG_StationPort     equ 12          ; u16, host order
TCFG_RemoteAddr      equ 14          ; 4 bytes
TCFG_RemotePort      equ 18          ; u16
TCFG_ActiveFlag      equ 20          ; BOOLEAN: 0 = listen
TCFG_ControlOption   equ 24          ; EFI_TCP4_OPTION* or 0
TCFG_BYTES           equ 32
; tokens: EFI_TCP4_COMPLETION_TOKEN { EFI_EVENT Event; EFI_STATUS Status } then
TTOK_Event           equ 0
TTOK_Status          equ 8
TTOK_Child           equ 16          ; LISTEN: NewChildHandle
TTOK_Data            equ 16          ; IO: RxData* / TxData*
TTOK_Abort           equ 16          ; CLOSE: AbortOnClose (BOOLEAN)
TTOK_BYTES           equ 24
; EFI_TCP4_RECEIVE_DATA / EFI_TCP4_TRANSMIT_DATA (32 bytes with one fragment)
TDAT_Flag            equ 0           ; RX: UrgentFlag; TX: Push (u8), Urgent at 1
TDAT_Length          equ 4           ; u32 DataLength
TDAT_FragCount       equ 8           ; u32
TDAT_FragLen         equ 16          ; u32 FragmentLength
TDAT_FragBuf         equ 24          ; void*
TDAT_BYTES           equ 32

EFI_NO_MAPPING       equ 0x8000000000000011 ; error 17: the station address is not configured yet (DHCP running)
TPL_CALLBACK         equ 8
TCP4_DHCP_WAIT       equ 60          ; x 500 ms
TCP4_RX_TIMEOUT_NS   equ 30000000000 ; a client that stops sending
TCP4_TX_CHUNK        equ 8192

; tcp4_listen(rcx=port) -> rax = 0 when listening, 1 when the firmware has no
; TCP4 or the configuration failed (a message says which).
tcp4_listen:
  push rbx
  push rsi
  push r12
  WINPROLOGUE
  sub  rsp, 80                 ; [rsp+32] 5th/6th args, [rsp+48..80) config out
  mov  r12, rcx                ; port
  lea  rcx, [rel e_guid_tcp4_sb]
  call efi_locate_handles      ; rax = count, rdx = handles
  test rax, rax
  jz   .none
  mov  rbx, [rdx]              ; the first TCP4 service binding handle
  mov  [rel g_tcp_sbh], rbx
  mov  rcx, rdx
  mov  rax, [rel g_bs]
  call [rax + BS_FreePool]
  mov  rax, [rel g_bs]
  mov  rcx, rbx
  lea  rdx, [rel e_guid_tcp4_sb]
  lea  r8, [rel g_tcp_sb]
  call [rax + BS_HandleProtocol]
  test rax, rax
  jnz  .status
  mov  qword [rel g_tcp_lh], 0
  mov  rcx, [rel g_tcp_sb]
  lea  rdx, [rel g_tcp_lh]
  call [rcx + TCP4SB_CreateChild]
  test rax, rax
  jnz  .status
  mov  rax, [rel g_bs]
  mov  rcx, [rel g_tcp_lh]
  lea  rdx, [rel e_guid_tcp4]
  lea  r8, [rel g_tcp_lp]
  call [rax + BS_HandleProtocol]
  test rax, rax
  jnz  .status
  ; the configuration: listen on the port, the firmware's address or --ip's
  lea  rbx, [rel g_tcp_cfg]
  mov  byte [rbx + TCFG_TimeToLive], 64
  mov  [rbx + TCFG_StationPort], r12w
  mov  byte [rbx + TCFG_ActiveFlag], 0
  mov  qword [rbx + TCFG_ControlOption], 0
  mov  byte [rbx + TCFG_UseDefault], 1
  mov  eax, [rel g_http_ip]
  test eax, eax
  jz   .cfg
  mov  byte [rbx + TCFG_UseDefault], 0
  mov  [rbx + TCFG_StationAddr], eax
  mov  eax, [rel g_http_mask]
  mov  [rbx + TCFG_SubnetMask], eax
.cfg:
  mov  esi, TCP4_DHCP_WAIT
.cfg_try:
  mov  rcx, [rel g_tcp_lp]
  mov  rdx, rbx
  call [rcx + TCP4_Configure]
  test rax, rax
  jz   .configured
  mov  rdx, EFI_NO_MAPPING
  cmp  rax, rdx
  jne  .status
  dec  esi
  jz   .status                 ; no address after 30 s
  cmp  esi, TCP4_DHCP_WAIT - 1
  jne  .dot
  lea  rcx, [rel e_s_tcp_dhcp]
  call rt_err_str
.dot:
  lea  rcx, [rel e_s_tdot]
  call rt_err_str
  mov  rax, [rel g_bs]
  mov  ecx, 500000
  call [rax + BS_Stall]
  jmp  .cfg_try
.configured:
  cmp  dword [rel g_http_gw], 0
  je   .route_done
  mov  rcx, [rel g_tcp_lp]
  xor  edx, edx                ; DeleteRoute = FALSE
  lea  r8, [rel g_tcp_zero]    ; subnet 0.0.0.0
  lea  r9, [rel g_tcp_zero]    ; mask 0.0.0.0
  lea  rax, [rel g_http_gw]
  mov  [rsp+32], rax
  call [rcx + TCP4_Routes]
.route_done:
  ; the events the tokens complete on
  lea  rsi, [rel g_tcp_ev]
  mov  r12d, 3
.ev:
  mov  rax, [rel g_bs]
  xor  ecx, ecx                ; Type: a plain event
  mov  edx, TPL_CALLBACK
  xor  r8d, r8d
  xor  r9d, r9d
  mov  [rsp+32], rsi
  call [rax + BS_CreateEvent]
  test rax, rax
  jnz  .status
  add  rsi, 8
  dec  r12d
  jnz  .ev
  ; the address we listen on
  mov  rcx, [rel g_tcp_lp]
  xor  edx, edx
  lea  r8, [rsp+48]
  xor  r9d, r9d
  mov  qword [rsp+32], 0
  mov  qword [rsp+40], 0
  call [rcx + TCP4_GetModeData]
  lea  rcx, [rel e_s_tcp_listen]
  call rt_err_str
  lea  rcx, [rsp+48+TCFG_StationAddr]
  call tcp4_print_ip
  lea  rcx, [rel e_s_colon2]
  call rt_err_str
  movzx ecx, word [rsp+48+TCFG_StationPort]
  call rt_err_u64
  lea  rcx, [rel e_s_nl]
  call rt_err_str
  xor  eax, eax
  jmp  .ret
.none:
  lea  rcx, [rel e_s_tcp_none]
  call rt_err_str
  mov  eax, 1
  jmp  .ret
.status:
  mov  rbx, rax
  lea  rcx, [rel e_s_tcp_fail]
  call rt_err_str
  mov  rcx, rbx
  call rt_err_hex64
  lea  rcx, [rel e_s_nl]
  call rt_err_str
  mov  eax, 1
.ret:
  WINEPILOGUE
  pop  r12
  pop  rsi
  pop  rbx
  ret

; tcp4_print_ip(rcx=4 bytes) — "a.b.c.d" to stderr.
tcp4_print_ip:
  push rbx
  push rsi
  WINPROLOGUE
  mov  rbx, rcx
  xor  esi, esi
.oct:
  movzx ecx, byte [rbx + rsi]
  call rt_err_u64
  inc  esi
  cmp  esi, 4
  je   .done
  lea  rcx, [rel e_s_tdot]
  call rt_err_str
  jmp  .oct
.done:
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; tcp4_wait(rcx=protocol, rdx=event, r8=timeout ns or 0) -> rax = 0 when the
; event fired, 1 on timeout. Polls the protocol (the driver's receive path) and
; checks the event between short stalls.
tcp4_wait:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
  xor  r12d, r12d
  test rdi, rdi
  jz   .loop
  call rt_now_ns
  lea  r12, [rax + rdi]        ; deadline
.loop:
  mov  rcx, rbx
  call [rbx + TCP4_Poll]
  mov  rax, [rel g_bs]
  mov  rcx, rsi
  call [rax + BS_CheckEvent]
  test rax, rax
  jz   .fired
  test r12, r12
  jz   .stall
  call rt_now_ns
  cmp  rax, r12
  ja   .timeout
.stall:
  mov  rax, [rel g_bs]
  mov  ecx, 200
  call [rax + BS_Stall]
  jmp  .loop
.fired:
  xor  eax, eax
  jmp  .ret
.timeout:
  mov  eax, 1
.ret:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; tcp4_accept() -> rax = the connection (its EFI_TCP4_PROTOCOL*) or 0.
tcp4_accept:
  WINPROLOGUE
  cmp  qword [rel g_tcp_lpend], 0
  jne  .pending                ; the listen token from the last call is still out
  lea  rdx, [rel g_tcp_ltok]
  mov  rax, [rel g_tcp_ev]
  mov  [rdx + TTOK_Event], rax
  mov  qword [rdx + TTOK_Status], 0
  mov  qword [rdx + TTOK_Child], 0
  mov  rcx, [rel g_tcp_lp]
  call [rcx + TCP4_Accept]
  test rax, rax
  jnz  .fail
  mov  qword [rel g_tcp_lpend], 1
.pending:
  mov  rcx, [rel g_tcp_lp]
  mov  rdx, [rel g_tcp_ev]
  xor  r8d, r8d                ; wait for a peer
  cmp  qword [rel g_auto], 0
  je   .wait
  mov  r8, 1000000000          ; autonomous: a second, then back to the idle loop (keyboard, queue, heartbeat); the token stays out
.wait:
  call tcp4_wait
  test rax, rax
  jz   .fired
  xor  eax, eax                ; timeout: no peer yet
  WINEPILOGUE
  ret
.fired:
  mov  qword [rel g_tcp_lpend], 0
  cmp  qword [rel g_tcp_ltok + TTOK_Status], 0
  jne  .fail
  mov  rcx, [rel g_tcp_ltok + TTOK_Child]
  mov  [rel g_tcp_ch], rcx
  mov  rax, [rel g_bs]
  lea  rdx, [rel e_guid_tcp4]
  lea  r8, [rel g_tcp_cp]
  call [rax + BS_HandleProtocol]
  test rax, rax
  jnz  .fail
  mov  rax, [rel g_tcp_cp]
  WINEPILOGUE
  ret
.fail:
  mov  rax, [rel g_bs]
  mov  ecx, 100000
  call [rax + BS_Stall]        ; not a tight loop when Accept keeps failing
  xor  eax, eax
  WINEPILOGUE
  ret

; tcp4_recv(rcx=conn, rdx=buf, r8=cap) -> rax = bytes received; 0 when the peer
; closed, failed or sent nothing for 30 s.
tcp4_recv:
  push rbx
  WINPROLOGUE
  mov  rbx, rcx
  lea  rax, [rel g_tcp_rx]
  mov  byte [rax + TDAT_Flag], 0
  mov  [rax + TDAT_Length], r8d
  mov  dword [rax + TDAT_FragCount], 1
  mov  [rax + TDAT_FragLen], r8d
  mov  [rax + TDAT_FragBuf], rdx
  lea  rdx, [rel g_tcp_iotok]
  mov  rcx, [rel g_tcp_ev + 8]
  mov  [rdx + TTOK_Event], rcx
  mov  qword [rdx + TTOK_Status], 0
  mov  [rdx + TTOK_Data], rax
  mov  rcx, rbx
  call [rbx + TCP4_Receive]
  test rax, rax
  jnz  .zero
  mov  rcx, rbx
  mov  rdx, [rel g_tcp_ev + 8]
  mov  r8, TCP4_RX_TIMEOUT_NS
  call tcp4_wait
  test rax, rax
  jz   .done
  mov  rcx, rbx                ; timeout: take the request back
  lea  rdx, [rel g_tcp_iotok]
  call [rbx + TCP4_Cancel]
  jmp  .zero
.done:
  cmp  qword [rel g_tcp_iotok + TTOK_Status], 0
  jne  .zero
  mov  eax, [rel g_tcp_rx + TDAT_Length]
  WINEPILOGUE
  pop  rbx
  ret
.zero:
  xor  eax, eax
  WINEPILOGUE
  pop  rbx
  ret

; tcp4_send(rcx=conn, rdx=buf, r8=len) -> rax = 0 when all bytes were accepted,
; else 1. Sent in chunks of TCP4_TX_CHUNK, each waited for.
tcp4_send:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, r8
.chunk:
  test rdi, rdi
  jz   .ok
  mov  r8, rdi
  cmp  r8, TCP4_TX_CHUNK
  jbe  .sz
  mov  r8d, TCP4_TX_CHUNK
.sz:
  lea  rax, [rel g_tcp_tx]
  mov  word [rax + TDAT_Flag], 1 ; Push = 1, Urgent = 0
  mov  [rax + TDAT_Length], r8d
  mov  dword [rax + TDAT_FragCount], 1
  mov  [rax + TDAT_FragLen], r8d
  mov  [rax + TDAT_FragBuf], rsi
  lea  rdx, [rel g_tcp_iotok]
  mov  rcx, [rel g_tcp_ev + 8]
  mov  [rdx + TTOK_Event], rcx
  mov  qword [rdx + TTOK_Status], 0
  mov  [rdx + TTOK_Data], rax
  mov  rcx, rbx
  call [rbx + TCP4_Transmit]
  test rax, rax
  jnz  .fail
  mov  rcx, rbx
  mov  rdx, [rel g_tcp_ev + 8]
  mov  r8, TCP4_RX_TIMEOUT_NS
  call tcp4_wait
  test rax, rax
  jnz  .fail
  cmp  qword [rel g_tcp_iotok + TTOK_Status], 0
  jne  .fail
  mov  eax, [rel g_tcp_tx + TDAT_Length]
  add  rsi, rax
  sub  rdi, rax
  jmp  .chunk
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

; tcp4_close(rcx=conn) — graceful close, then the child handle is destroyed.
tcp4_close:
  push rbx
  WINPROLOGUE
  mov  rbx, rcx
  lea  rdx, [rel g_tcp_ctok]
  mov  rax, [rel g_tcp_ev + 16]
  mov  [rdx + TTOK_Event], rax
  mov  qword [rdx + TTOK_Status], 0
  mov  byte [rdx + TTOK_Abort], 0
  mov  rcx, rbx
  call [rbx + TCP4_Close]
  test rax, rax
  jnz  .destroy
  mov  rcx, rbx
  mov  rdx, [rel g_tcp_ev + 16]
  mov  r8, 5000000000          ; 5 s
  call tcp4_wait
.destroy:
  mov  rcx, [rel g_tcp_sb]
  mov  rdx, [rel g_tcp_ch]
  call [rcx + TCP4SB_DestroyChild]
  mov  qword [rel g_tcp_cp], 0
  mov  qword [rel g_tcp_ch], 0
  WINEPILOGUE
  pop  rbx
  ret

; ---------------------------------------------------------------------------
; The net_* transport http.asm calls: the firmware's TCP4 when it has one,
; else the phone's USB tethering (usbnet.asm). g_net_kind remembers which.
; ---------------------------------------------------------------------------
net_listen:
  push rbx
  WINPROLOGUE
  mov  rbx, rcx
  call tcp4_listen
  test rax, rax
  jnz  .usb
  mov  qword [rel g_net_kind], 1
  xor  eax, eax
  jmp  .ret
.usb:
  mov  rcx, rbx
  call un_listen
  test rax, rax
  jnz  .ret                    ; rax = 1: neither
  mov  qword [rel g_net_kind], 2
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  rbx
  ret
net_accept:
  cmp  qword [rel g_net_kind], 2
  je   un_accept
  jmp  tcp4_accept
net_recv:
  cmp  qword [rel g_net_kind], 2
  je   un_recv
  jmp  tcp4_recv
net_send:
  cmp  qword [rel g_net_kind], 2
  je   un_send
  jmp  tcp4_send
net_close:
  cmp  qword [rel g_net_kind], 2
  je   un_close
  jmp  tcp4_close

e_guid_tcp4:                   ; EFI_TCP4_PROTOCOL 65530BC7-A359-410F-B010-5AADC7EC2B62
  dd 0x65530BC7
  dw 0xA359, 0x410F
  db 0xB0, 0x10, 0x5A, 0xAD, 0xC7, 0xEC, 0x2B, 0x62

e_s_tcp_none:   db "asmrun: http: the firmware has no TCP4 (BIOS network stack off, or no Ethernet link)", 10, 0
e_s_tcp_fail:   db "asmrun: http: TCP4 setup failed, status ", 0
e_s_tcp_dhcp:   db "asmrun: http: waiting for the firmware's DHCP address ", 0
e_s_tcp_listen: db "asmrun: http: listening on http://", 0
e_s_tdot:       db ".", 0
e_s_colon2:     db ":", 0

section .data
  align 8
g_net_kind:     dq 0           ; 1 = TCP4, 2 = USB tethering
g_tcp_sbh:      dq 0           ; the service binding handle
g_tcp_sb:       dq 0           ; EFI_TCP4_SERVICE_BINDING_PROTOCOL*
g_tcp_lh:       dq 0           ; the listening child handle
g_tcp_lp:       dq 0           ; its EFI_TCP4_PROTOCOL*
g_tcp_ch:       dq 0           ; the accepted connection's handle
g_tcp_cp:       dq 0           ; its EFI_TCP4_PROTOCOL*
g_tcp_ev:       dq 0, 0, 0     ; events: listen, io, close
g_tcp_lpend:    dq 0           ; 1 while a listen token is out (tcp4_accept came back on a timeout)
g_tcp_zero:     dd 0
  align 8
g_tcp_cfg:      times TCFG_BYTES db 0
g_tcp_ltok:     times TTOK_BYTES db 0
g_tcp_iotok:    times TTOK_BYTES db 0
g_tcp_ctok:     times TTOK_BYTES db 0
g_tcp_rx:       times TDAT_BYTES db 0
g_tcp_tx:       times TDAT_BYTES db 0
section .text

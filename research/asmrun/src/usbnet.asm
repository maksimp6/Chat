; usbnet.asm — the phone as the network card: Android's USB tethering is an
; RNDIS device, and the firmware hands us its two interfaces through
; EFI_USB_IO_PROTOCOL (control transfers for the RNDIS messages, bulk
; transfers for the Ethernet frames). On top of it, written here: ARP, IPv4,
; ICMP echo and a TCP server for one connection at a time, enough for
; http.asm. The phone's browser then opens http://192.168.42.50/ (our fixed
; address on the tethering subnet unless --ip says otherwise) over the cable,
; and the phone's own Wi-Fi or mobile data stays the phone's.
;
; Design: one frame in flight each way. Sending waits for the ACK of each
; segment before the next (stop-and-wait, a retransmission after 300 ms);
; receiving keeps in-order bytes only and re-ACKs anything else, so the peer
; retransmits. USB is reliable, the path is two devices long, this is enough
; for text. No DHCP: the address is ours to state; the phone learns it by ARP.
;
; UEFI only (included under ASMRUN_EFI). Exposes un_listen, un_accept,
; un_recv, un_send, un_close (the net_* dispatcher is in tcp4.asm).

section .text

; EFI_USB_IO_PROTOCOL
USBIO_ControlTransfer       equ 0      ; (This, EFI_USB_DEVICE_REQUEST*, Direction, Timeout ms, Data, DataLength, UINT32* Status)
USBIO_BulkTransfer          equ 8      ; (This, Endpoint u8, Data, UINTN* DataLength, Timeout ms, UINT32* Status)
USBIO_GetEndpointDescriptor equ 72     ; (This, Index u8, EFI_USB_ENDPOINT_DESCRIPTOR*)
EfiUsbDataIn   equ 0
EfiUsbDataOut  equ 1
EfiUsbNoData   equ 2
USB_EP_Address equ 2                   ; bEndpointAddress (bit 7: IN)
USB_EP_Attr    equ 3                   ; bmAttributes (low 2 bits: 2 = bulk)
USB_EP_BYTES   equ 7

; RNDIS (MS-RNDIS, little-endian u32 fields)
RNDIS_PACKET_MSG      equ 0x00000001
RNDIS_INITIALIZE_MSG  equ 0x00000002
RNDIS_QUERY_MSG       equ 0x00000004
RNDIS_SET_MSG         equ 0x00000005
RNDIS_CMPLT           equ 0x80000000
OID_802_3_CURRENT_ADDRESS     equ 0x01010102
OID_GEN_CURRENT_PACKET_FILTER equ 0x0001010E
RNDIS_FILTER   equ 0x0000000B          ; directed | multicast | broadcast
RNDIS_HDR      equ 44                  ; REMOTE_NDIS_PACKET_MSG header
RNDIS_RESP_CAP equ 1024
UN_RXBUF       equ 16384               ; one bulk-in transfer
UN_TXBUF       equ 2048                ; RNDIS header + one frame
UN_RING        equ 65536               ; in-order TCP bytes waiting for un_recv
UN_MSS         equ 1460
UN_RTO_NS      equ 300000000           ; retransmit after 300 ms
UN_RETRIES     equ 20
UN_RX_TIMEOUT_NS equ 30000000000       ; un_recv gives up after 30 s of silence
UN_DEFAULT_IP  equ 0x322AA8C0          ; 192.168.42.50 in memory order (first octet in the low byte)

; TCP states
TS_CLOSED      equ 0
TS_LISTEN      equ 1
TS_SYN_RCVD    equ 2
TS_ESTABLISHED equ 3
TS_CLOSE_WAIT  equ 4
TS_FIN_WAIT1   equ 5
TS_FIN_WAIT2   equ 6
TS_LAST_ACK    equ 7
; flags
TF_FIN equ 0x01
TF_SYN equ 0x02
TF_RST equ 0x04
TF_PSH equ 0x08
TF_ACK equ 0x10

; ---------------------------------------------------------------------------
; un_listen(rcx=port) -> rax = 0 when the RNDIS link is up and the port is
; open, 1 otherwise (a message says what is missing).
; ---------------------------------------------------------------------------
un_listen:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 96                 ; [rsp+32..56) call args, [rsp+64..82) if descr, [rsp+82..89) ep descr
  mov  [rel g_un_port], cx
  ; the interfaces: RNDIS control (E0/01/03, EF/04/01 or 02/02/FF) and CDC data (0A)
  lea  rcx, [rel e_guid_usb_io]
  call efi_locate_handles      ; rax = count, rdx = handles
  test rax, rax
  jz   .nousb
  mov  rbx, rax
  mov  rsi, rdx
  xor  r12d, r12d
.each:
  cmp  r12, rbx
  jae  .scanned
  mov  rax, [rel g_bs]
  mov  rcx, [rsi + r12*8]
  lea  rdx, [rel e_guid_usb_io]
  lea  r8, [rsp+56]
  call [rax + BS_HandleProtocol]
  test rax, rax
  jnz  .next
  mov  rdi, [rsp+56]           ; EFI_USB_IO_PROTOCOL*
  mov  rcx, rdi
  lea  rdx, [rsp+64]
  call [rdi + USBIO_GetInterfaceDescriptor]
  test rax, rax
  jnz  .next
  movzx eax, byte [rsp+64+USB_IF_bInterfaceClass]
  movzx ecx, byte [rsp+64+USB_IF_bInterfaceSubClass]
  movzx edx, byte [rsp+64+USB_IF_bInterfaceProtocol]
  cmp  al, 0x0A
  je   .data_if
  cmp  al, 0xE0
  jne  .c2
  cmp  cl, 1
  jne  .next
  cmp  dl, 3
  jne  .next
  jmp  .ctl_if
.c2:
  cmp  al, 0xEF
  jne  .c3
  cmp  cl, 4
  jne  .next
  cmp  dl, 1
  jne  .next
  jmp  .ctl_if
.c3:
  cmp  al, 0x02
  jne  .next
  cmp  cl, 2
  jne  .next
  cmp  dl, 0xFF
  jne  .next
.ctl_if:
  cmp  qword [rel g_un_ctl], 0
  jne  .next                   ; the first one
  mov  [rel g_un_ctl], rdi
  movzx eax, byte [rsp+64+USB_IF_bInterfaceNumber]
  mov  [rel g_un_ctl_if], eax
  jmp  .next
.data_if:
  cmp  qword [rel g_un_ctl], 0
  je   .next                   ; the data interface follows its control interface
  cmp  qword [rel g_un_data], 0
  jne  .next
  ; its bulk endpoints
  movzx r13d, byte [rsp+64+4]  ; bNumEndpoints
  xor  eax, eax
  mov  [rsp+88], eax           ; in | out<<8 found so far
.ep:
  test r13d, r13d
  jz   .ep_done
  dec  r13d
  mov  rcx, rdi
  mov  edx, r13d
  lea  r8, [rsp+82]
  call [rdi + USBIO_GetEndpointDescriptor]
  test rax, rax
  jnz  .ep
  movzx eax, byte [rsp+82+USB_EP_Attr]
  and  al, 3
  cmp  al, 2
  jne  .ep
  movzx eax, byte [rsp+82+USB_EP_Address]
  test al, 0x80
  jz   .ep_out
  mov  [rel g_un_ep_in], al
  jmp  .ep
.ep_out:
  mov  [rel g_un_ep_out], al
  jmp  .ep
.ep_done:
  cmp  byte [rel g_un_ep_in], 0
  je   .next
  cmp  byte [rel g_un_ep_out], 0
  je   .next
  mov  [rel g_un_data], rdi
.next:
  inc  r12
  jmp  .each
.scanned:
  mov  rax, [rel g_bs]
  mov  rcx, rsi
  call [rax + BS_FreePool]
  cmp  qword [rel g_un_ctl], 0
  je   .nordnis
  cmp  qword [rel g_un_data], 0
  je   .nordnis
  ; RNDIS: initialise, ask the MAC, open the packet filter
  lea  rbx, [rel g_un_msg]
  mov  dword [rbx], RNDIS_INITIALIZE_MSG
  mov  dword [rbx+4], 24
  mov  dword [rbx+8], 1        ; RequestId
  mov  dword [rbx+12], 1       ; MajorVersion
  mov  dword [rbx+16], 0
  mov  dword [rbx+20], UN_RXBUF ; MaxTransferSize
  mov  rcx, rbx
  mov  edx, 24
  call un_rndis_cmd            ; rax = response ptr or 0
  test rax, rax
  jz   .rndis_fail
  cmp  dword [rax+12], 0       ; Status
  jne  .rndis_fail
  mov  dword [rbx], RNDIS_QUERY_MSG
  mov  dword [rbx+4], 28
  mov  dword [rbx+8], 2
  mov  dword [rbx+12], OID_802_3_CURRENT_ADDRESS
  mov  dword [rbx+16], 0       ; InformationBufferLength
  mov  dword [rbx+20], 20      ; InformationBufferOffset
  mov  dword [rbx+24], 0
  mov  rcx, rbx
  mov  edx, 28
  call un_rndis_cmd
  test rax, rax
  jz   .rndis_fail
  cmp  dword [rax+12], 0
  jne  .rndis_fail
  cmp  dword [rax+16], 6       ; InformationBufferLength
  jb   .rndis_fail
  mov  ecx, [rax+20]           ; InformationBufferOffset, from the RequestId field
  lea  rdx, [rax + 8 + rcx]
  mov  eax, [rdx]
  mov  [rel g_un_mac], eax
  mov  ax, [rdx+4]
  mov  [rel g_un_mac+4], ax
  mov  dword [rbx], RNDIS_SET_MSG
  mov  dword [rbx+4], 32
  mov  dword [rbx+8], 3
  mov  dword [rbx+12], OID_GEN_CURRENT_PACKET_FILTER
  mov  dword [rbx+16], 4
  mov  dword [rbx+20], 20
  mov  dword [rbx+24], 0
  mov  dword [rbx+28], RNDIS_FILTER
  mov  rcx, rbx
  mov  edx, 32
  call un_rndis_cmd
  test rax, rax
  jz   .rndis_fail
  cmp  dword [rax+12], 0
  jne  .rndis_fail
  ; our address
  mov  eax, [rel g_http_ip]
  test eax, eax
  jnz  .ip
  mov  eax, UN_DEFAULT_IP
.ip:
  mov  [rel g_un_ip], eax
  mov  qword [rel g_un_state], TS_LISTEN
  mov  qword [rel g_un_rx_rd], 0
  mov  qword [rel g_un_rx_wr], 0
  lea  rcx, [rel e_s_un_up]
  call rt_err_str
  lea  rcx, [rel g_un_mac]
  call un_print_mac
  lea  rcx, [rel e_s_un_url]
  call rt_err_str
  lea  rcx, [rel g_un_ip]
  call tcp4_print_ip
  lea  rcx, [rel e_s_un_url2]
  call rt_err_str
  movzx ecx, word [rel g_un_port]
  call rt_err_u64
  lea  rcx, [rel e_s_un_url3]
  call rt_err_str
  xor  eax, eax
  jmp  .ret
.nousb:
  lea  rcx, [rel e_s_un_nousb]
  call rt_err_str
  mov  eax, 1
  jmp  .ret
.nordnis:
  lea  rcx, [rel e_s_un_nornd]
  call rt_err_str
  mov  eax, 1
  jmp  .ret
.rndis_fail:
  lea  rcx, [rel e_s_un_fail]
  call rt_err_str
  mov  eax, 1
.ret:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_print_mac(rcx=6 bytes) — "aa:bb:cc:dd:ee:ff" to stderr.
un_print_mac:
  push rbx
  push rsi
  WINPROLOGUE
  mov  rbx, rcx
  xor  esi, esi
.b:
  movzx ecx, byte [rbx + rsi]
  call efi_err_hex8
  inc  esi
  cmp  esi, 6
  je   .done
  lea  rcx, [rel e_s_un_colon]
  call rt_err_str
  jmp  .b
.done:
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; un_rndis_cmd(rcx=message, rdx=length) -> rax = the response (in g_un_resp,
; with the matching RequestId) or 0. SEND_ENCAPSULATED_COMMAND, then
; GET_ENCAPSULATED_RESPONSE polled until the device has one.
un_rndis_cmd:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32..56) args 5-7, [rsp+56] status u32, [rsp+64..72) EFI_USB_DEVICE_REQUEST
  mov  rbx, rcx
  mov  rsi, rdx
  mov  rdi, [rel g_un_ctl]
  ; the command
  mov  byte [rsp+64], 0x21     ; host-to-device, class, interface
  mov  byte [rsp+65], 0x00     ; SEND_ENCAPSULATED_COMMAND
  mov  word [rsp+66], 0
  mov  eax, [rel g_un_ctl_if]
  mov  [rsp+68], ax
  mov  [rsp+70], si
  mov  rcx, rdi
  lea  rdx, [rsp+64]
  mov  r8d, EfiUsbDataOut
  mov  r9d, 1000
  mov  [rsp+32], rbx
  mov  [rsp+40], rsi
  lea  rax, [rsp+56]
  mov  [rsp+48], rax
  call [rdi + USBIO_ControlTransfer]
  test rax, rax
  jnz  .fail
  ; the response: up to a second of asking
  mov  esi, 1000
.poll:
  lea  rcx, [rel g_un_resp]
  xor  edx, edx
  mov  r8d, RNDIS_RESP_CAP
  call rt_memset
  mov  byte [rsp+64], 0xA1     ; device-to-host, class, interface
  mov  byte [rsp+65], 0x01     ; GET_ENCAPSULATED_RESPONSE
  mov  word [rsp+66], 0
  mov  eax, [rel g_un_ctl_if]
  mov  [rsp+68], ax
  mov  word [rsp+70], RNDIS_RESP_CAP
  mov  rcx, rdi
  lea  rdx, [rsp+64]
  mov  r8d, EfiUsbDataIn
  mov  r9d, 1000
  lea  rax, [rel g_un_resp]
  mov  [rsp+32], rax
  mov  qword [rsp+40], RNDIS_RESP_CAP
  lea  rax, [rsp+56]
  mov  [rsp+48], rax
  call [rdi + USBIO_ControlTransfer]
  test rax, rax
  jnz  .fail
  lea  rax, [rel g_un_resp]
  cmp  dword [rax], 0
  je   .again                  ; nothing yet (a zero byte)
  mov  ecx, [rbx+8]            ; our RequestId
  cmp  [rax+8], ecx
  jne  .again                  ; a keepalive or an old answer: ask again
  mov  ecx, [rbx]
  or   ecx, RNDIS_CMPLT
  cmp  [rax], ecx
  jne  .again
  jmp  .ret
.again:
  dec  esi
  jz   .fail
  mov  rax, [rel g_bs]
  mov  ecx, 1000
  call [rax + BS_Stall]
  jmp  .poll
.fail:
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_tx_frame(rcx=frame, rdx=length) — one Ethernet frame to the phone inside
; a REMOTE_NDIS_PACKET_MSG (the frame is already in g_un_tx + RNDIS_HDR when
; rcx points there; otherwise it is copied). Clobbers the volatile registers.
un_tx_frame:
  push rbx
  push rsi
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32] DataLength (in/out), [rsp+40] status u32, [rsp+48] arg 5
  mov  rsi, rdx
  lea  rbx, [rel g_un_tx]
  lea  rax, [rbx + RNDIS_HDR]
  cmp  rcx, rax
  je   .inplace
  mov  rdx, rcx
  mov  rcx, rax
  mov  r8, rsi
  call rt_memcpy
.inplace:
  lea  rax, [rsi + RNDIS_HDR]
  test eax, 511
  jnz  .len                    ; a multiple of the packet size would need a zero-length packet: one pad byte instead
  mov  byte [rbx + rax], 0
  inc  rax
.len:
  mov  dword [rbx], RNDIS_PACKET_MSG
  mov  [rbx+4], eax
  mov  dword [rbx+8], RNDIS_HDR - 8  ; DataOffset, from the DataOffset field
  mov  [rbx+12], esi
  xor  ecx, ecx
  mov  [rbx+16], rcx
  mov  [rbx+24], rcx
  mov  [rbx+32], rcx
  mov  [rbx+40], ecx
  mov  [rsp+32], rax
  mov  rcx, [rel g_un_data]
  movzx edx, byte [rel g_un_ep_out]
  mov  r8, rbx
  lea  r9, [rsp+32]
  mov  qword [rsp+48], 1000    ; timeout ms
  lea  rax, [rsp+40]
  mov  [rsp+56], rax
  call [rcx + USBIO_BulkTransfer]
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; un_pump() — one bulk-in transfer (2 ms at most) and every frame in it handled.
; Clobbers the volatile registers.
un_pump:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  sub  rsp, 32
  mov  qword [rsp+32], UN_RXBUF
  mov  rcx, [rel g_un_data]
  movzx edx, byte [rel g_un_ep_in]
  lea  r8, [rel g_un_rx]
  lea  r9, [rsp+32]
  mov  qword [rsp+48], 2       ; timeout ms
  lea  rax, [rsp+40]
  mov  [rsp+56], rax
  call [rcx + USBIO_BulkTransfer]
  test rax, rax
  jnz  .done
  mov  rsi, [rsp+32]           ; bytes
  lea  rbx, [rel g_un_rx]
  xor  edi, edi                ; offset
.msg:
  lea  rax, [rdi + RNDIS_HDR]
  cmp  rax, rsi
  ja   .done
  mov  ecx, [rbx + rdi + 4]    ; MessageLength
  test ecx, ecx
  jz   .done
  cmp  dword [rbx + rdi], RNDIS_PACKET_MSG
  jne  .skip
  mov  eax, [rbx + rdi + 8]    ; DataOffset
  mov  edx, [rbx + rdi + 12]   ; DataLength
  lea  rcx, [rbx + rdi + 8]
  add  rcx, rax                ; the frame
  lea  r8, [rcx + rdx]
  lea  r9, [rbx + rsi]
  cmp  r8, r9
  ja   .done                   ; truncated
  cmp  edx, 14
  jb   .skip
  call un_frame
  mov  ecx, [rbx + rdi + 4]
.skip:
  add  rdi, rcx
  jmp  .msg
.done:
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_frame(rcx=frame, rdx=length) — ARP for our address is answered, an ICMP
; echo is answered, TCP to our port goes to un_tcp_in. Clobbers the volatile
; registers.
un_frame:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  mov  rbx, rcx
  mov  rsi, rdx
  movzx eax, word [rbx + 12]   ; EtherType, big-endian
  xchg al, ah
  cmp  eax, 0x0806
  je   .arp
  cmp  eax, 0x0800
  jne  .done
  ; IPv4
  cmp  rsi, 34
  jb   .done
  mov  eax, [rbx + 14 + 16]    ; destination
  cmp  eax, [rel g_un_ip]
  jne  .done
  movzx ecx, byte [rbx + 14]
  and  ecx, 0x0F
  shl  ecx, 2                  ; IHL bytes
  movzx edx, word [rbx + 14 + 2]
  xchg dl, dh                  ; total length
  lea  rax, [rdx + 14]
  cmp  rax, rsi
  ja   .done
  movzx eax, byte [rbx + 14 + 9]
  cmp  al, 6
  je   .tcp
  cmp  al, 1
  jne  .done
  ; ICMP echo request -> reply in place: swap addresses, type 0, checksums
  lea  rdi, [rbx + 14 + rcx]   ; ICMP header
  cmp  byte [rdi], 8
  jne  .done
  mov  byte [rdi], 0
  mov  r12, rdx
  sub  r12, rcx                ; ICMP length
  mov  word [rdi + 2], 0
  mov  rcx, rdi
  mov  rdx, r12
  xor  r8d, r8d
  call un_csum
  mov  [rdi + 2], ax
  mov  eax, [rbx + 14 + 12]
  mov  edx, [rbx + 14 + 16]
  mov  [rbx + 14 + 12], edx
  mov  [rbx + 14 + 16], eax
  call un_ip_finish            ; rbx = frame: IP checksum, MACs swapped from the request
  mov  rcx, rbx
  mov  rdx, rsi
  call un_tx_frame
  jmp  .done
.tcp:
  mov  r8, rdx                 ; IP total length
  mov  rdx, rcx                ; IHL
  mov  rcx, rbx
  call un_tcp_in
  jmp  .done
.arp:
  cmp  rsi, 42
  jb   .done
  cmp  word [rbx + 14 + 6], 0x0100 ; opcode 1 (request), big-endian
  jne  .done
  mov  eax, [rbx + 14 + 24]    ; target protocol address
  cmp  eax, [rel g_un_ip]
  jne  .done
  ; the reply into the transmit frame
  lea  rdi, [rel g_un_tx + RNDIS_HDR]
  mov  rcx, rdi
  lea  rdx, [rbx + 6]          ; destination = the sender's MAC
  mov  r8d, 6
  call rt_memcpy
  lea  rcx, [rdi + 6]
  lea  rdx, [rel g_un_mac]
  mov  r8d, 6
  call rt_memcpy
  mov  word [rdi + 12], 0x0608
  mov  word [rdi + 14], 0x0100 ; hardware type 1
  mov  word [rdi + 16], 0x0008 ; protocol 0x0800
  mov  byte [rdi + 18], 6
  mov  byte [rdi + 19], 4
  mov  word [rdi + 20], 0x0200 ; opcode 2 (reply)
  lea  rcx, [rdi + 22]
  lea  rdx, [rel g_un_mac]
  mov  r8d, 6
  call rt_memcpy
  mov  eax, [rel g_un_ip]
  mov  [rdi + 28], eax
  lea  rcx, [rdi + 32]
  lea  rdx, [rbx + 22]         ; the sender's MAC and address become the target's
  mov  r8d, 10
  call rt_memcpy
  mov  rcx, rdi
  mov  edx, 42
  call un_tx_frame
.done:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_csum(rcx=ptr, rdx=len, r8=initial sum) -> ax = the one's-complement
; Internet checksum (folded, complemented, in memory byte order). Leaf;
; clobbers rax,rcx,rdx,r8,r9.
un_csum:
  mov  rax, r8
.w:
  cmp  rdx, 2
  jb   .odd
  movzx r9d, word [rcx]
  add  rax, r9
  add  rcx, 2
  sub  rdx, 2
  jmp  .w
.odd:
  test rdx, rdx
  jz   .fold
  movzx r9d, byte [rcx]
  add  rax, r9
.fold:
  mov  rdx, rax
  shr  rdx, 16
  and  eax, 0xFFFF
  add  rax, rdx
  mov  rdx, rax
  shr  rdx, 16
  add  rax, rdx
  cmp  eax, 0xFFFF
  jbe  .nf
  sub  eax, 0xFFFF
.nf:
  not  eax
  and  eax, 0xFFFF
  ret

; un_sum16(rcx=ptr, rdx=len) -> rax = plain sum of 16-bit words (for a
; pseudo-header). Leaf; clobbers rax,rcx,rdx,r9.
un_sum16:
  xor  eax, eax
.w:
  cmp  rdx, 2
  jb   .odd
  movzx r9d, word [rcx]
  add  rax, r9
  add  rcx, 2
  sub  rdx, 2
  jmp  .w
.odd:
  test rdx, rdx
  jz   .done
  movzx r9d, byte [rcx]
  add  rax, r9
.done:
  ret

; un_ip_finish — rbx = a frame whose IP header is filled: the IP checksum is
; computed, and the Ethernet addresses are set (destination = the frame's
; current source, source = ours). Used for the in-place ICMP reply.
un_ip_finish:
  WINPROLOGUE
  mov  word [rbx + 14 + 10], 0
  movzx ecx, byte [rbx + 14]
  and  ecx, 0x0F
  shl  ecx, 2
  mov  rdx, rcx
  lea  rcx, [rbx + 14]
  xor  r8d, r8d
  call un_csum
  mov  [rbx + 14 + 10], ax
  mov  rcx, rbx
  lea  rdx, [rbx + 6]
  mov  r8d, 6
  call rt_memcpy
  lea  rcx, [rbx + 6]
  lea  rdx, [rel g_un_mac]
  mov  r8d, 6
  call rt_memcpy
  WINEPILOGUE
  ret

; ---------------------------------------------------------------------------
; TCP, one connection
; ---------------------------------------------------------------------------

; un_tcp_in(rcx=frame, rdx=IHL, r8=IP total length)
un_tcp_in:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  mov  rbx, rcx
  lea  rsi, [rbx + 14 + rdx]   ; TCP header
  mov  r12, r8
  sub  r12, rdx                ; TCP length
  movzx eax, word [rsi + 2]
  xchg al, ah
  cmp  ax, [rel g_un_port]
  jne  .done
  movzx r13d, byte [rsi + 12]
  shr  r13d, 4
  shl  r13d, 2                 ; data offset
  cmp  r13, r12
  ja   .done
  mov  r14, r12
  sub  r14, r13                ; payload length
  movzx edi, byte [rsi + 13]   ; flags
  mov  eax, [rsi + 4]
  bswap eax
  mov  [rel g_un_seg_seq], eax
  mov  eax, [rsi + 8]
  bswap eax
  mov  [rel g_un_seg_ack], eax
  mov  rax, [rel g_un_state]
  cmp  rax, TS_LISTEN
  je   .listen
  ; an existing connection: it must be the same peer
  mov  eax, [rbx + 14 + 12]
  cmp  eax, [rel g_un_peer_ip]
  jne  .done
  movzx eax, word [rsi]
  cmp  ax, [rel g_un_peer_port]
  jne  .done
  test edi, TF_RST
  jnz  .reset
  ; ACK processing
  test edi, TF_ACK
  jz   .noack
  mov  eax, [rel g_un_seg_ack]
  mov  ecx, [rel g_un_snd_una]
  mov  edx, [rel g_un_snd_nxt]
  sub  eax, ecx                ; ack - una
  sub  edx, ecx                ; nxt - una
  cmp  eax, edx
  ja   .noack                  ; beyond what we sent
  add  ecx, eax
  mov  [rel g_un_snd_una], ecx
.noack:
  mov  rax, [rel g_un_state]
  cmp  rax, TS_SYN_RCVD
  je   .synrcvd
  cmp  rax, TS_LAST_ACK
  je   .lastack
  cmp  rax, TS_FIN_WAIT1
  je   .finwait1
  cmp  rax, TS_FIN_WAIT2
  je   .finwait2
  ; ESTABLISHED / CLOSE_WAIT: in-order data, then FIN
  mov  eax, [rel g_un_seg_seq]
  cmp  eax, [rel g_un_rcv_nxt]
  jne  .reack                  ; not the next byte: tell the peer where we are
  test r14, r14
  jz   .fin_chk
  mov  rax, [rel g_un_rx_wr]
  lea  rcx, [rax + r14]
  cmp  rcx, UN_RING
  ja   .reack                  ; no room: the peer retransmits later
  lea  rcx, [rel g_un_ring]
  add  rcx, rax
  lea  rdx, [rsi + r13]
  mov  r8, r14
  call rt_memcpy
  add  [rel g_un_rx_wr], r14
  add  [rel g_un_rcv_nxt], r14d
.fin_chk:
  test edi, TF_FIN
  jz   .ack_data
  inc  dword [rel g_un_rcv_nxt]
  mov  qword [rel g_un_state], TS_CLOSE_WAIT
.ack_data:
  test r14, r14
  jnz  .reack
  test edi, TF_FIN
  jz   .done
.reack:
  mov  ecx, TF_ACK
  xor  edx, edx
  xor  r8d, r8d
  call un_tcp_send
  jmp  .done
.listen:
  test edi, TF_SYN
  jz   .done
  test edi, TF_ACK
  jnz  .done
  ; the peer
  lea  rcx, [rel g_un_peer_mac]
  lea  rdx, [rbx + 6]
  mov  r8d, 6
  call rt_memcpy
  mov  eax, [rbx + 14 + 12]
  mov  [rel g_un_peer_ip], eax
  mov  ax, [rsi]
  mov  [rel g_un_peer_port], ax
  mov  eax, [rel g_un_seg_seq]
  inc  eax
  mov  [rel g_un_rcv_nxt], eax
  rdtsc
  shr  eax, 4
  mov  [rel g_un_snd_una], eax
  mov  [rel g_un_snd_nxt], eax
  mov  qword [rel g_un_rx_rd], 0
  mov  qword [rel g_un_rx_wr], 0
  mov  qword [rel g_un_state], TS_SYN_RCVD
  mov  ecx, TF_SYN | TF_ACK
  xor  edx, edx
  xor  r8d, r8d
  call un_tcp_send             ; advances snd_nxt by one
  jmp  .done
.synrcvd:
  test edi, TF_ACK
  jz   .done
  mov  eax, [rel g_un_seg_ack]
  cmp  eax, [rel g_un_snd_nxt]
  jne  .done
  mov  qword [rel g_un_state], TS_ESTABLISHED
  ; data may ride on the ACK: handle it as established
  test r14, r14
  jnz  .noack
  test edi, TF_FIN
  jnz  .noack
  jmp  .done
.finwait1:
  mov  eax, [rel g_un_seg_ack]
  cmp  eax, [rel g_un_snd_nxt]
  jne  .fw1_fin
  mov  qword [rel g_un_state], TS_FIN_WAIT2
.fw1_fin:
  test edi, TF_FIN
  jz   .done
  inc  dword [rel g_un_rcv_nxt]
  mov  ecx, TF_ACK
  xor  edx, edx
  xor  r8d, r8d
  call un_tcp_send
  mov  qword [rel g_un_state], TS_CLOSED
  jmp  .done
.finwait2:
  test edi, TF_FIN
  jz   .done
  inc  dword [rel g_un_rcv_nxt]
  mov  ecx, TF_ACK
  xor  edx, edx
  xor  r8d, r8d
  call un_tcp_send
  mov  qword [rel g_un_state], TS_CLOSED
  jmp  .done
.lastack:
  mov  eax, [rel g_un_seg_ack]
  cmp  eax, [rel g_un_snd_nxt]
  jne  .done
  mov  qword [rel g_un_state], TS_CLOSED
  jmp  .done
.reset:
  mov  qword [rel g_un_state], TS_CLOSED
.done:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_tcp_send(rcx=flags, rdx=payload, r8=length) — one segment to the peer:
; seq = snd_nxt (snd_nxt advances by the length, +1 for SYN or FIN), ack =
; rcv_nxt, window = the ring's room, an MSS option on SYN. Clobbers the
; volatile registers.
un_tcp_send:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  mov  r12d, ecx               ; flags
  mov  rsi, rdx                ; payload
  mov  rdi, r8                 ; length
  lea  rbx, [rel g_un_tx + RNDIS_HDR]
  ; Ethernet
  mov  rcx, rbx
  lea  rdx, [rel g_un_peer_mac]
  mov  r8d, 6
  call rt_memcpy
  lea  rcx, [rbx + 6]
  lea  rdx, [rel g_un_mac]
  mov  r8d, 6
  call rt_memcpy
  mov  word [rbx + 12], 0x0008
  ; TCP header at +34
  lea  r13, [rbx + 34]
  mov  ax, [rel g_un_port]
  xchg al, ah
  mov  [r13], ax
  mov  ax, [rel g_un_peer_port]
  mov  [r13 + 2], ax
  mov  eax, [rel g_un_snd_nxt]
  bswap eax
  mov  [r13 + 4], eax
  mov  eax, [rel g_un_rcv_nxt]
  bswap eax
  mov  [r13 + 8], eax
  mov  ecx, 20                 ; header length
  test r12d, TF_SYN
  jz   .nomss
  mov  dword [r13 + 20], 0xB4050402 ; kind 2, len 4, MSS 1460
  mov  ecx, 24
.nomss:
  mov  eax, ecx
  shr  eax, 2
  shl  eax, 4
  mov  [r13 + 12], al
  mov  [r13 + 13], r12b
  mov  rax, UN_RING
  sub  rax, [rel g_un_rx_wr]
  add  rax, [rel g_un_rx_rd]
  cmp  rax, 0xFFFF
  jbe  .win
  mov  eax, 0xFFFF
.win:
  xchg al, ah
  mov  [r13 + 14], ax
  mov  word [r13 + 16], 0      ; checksum
  mov  word [r13 + 18], 0
  ; payload
  test rdi, rdi
  jz   .nopay
  lea  rax, [r13 + rcx]
  push rcx
  push rcx
  mov  rdx, rsi
  mov  r8, rdi
  mov  rcx, rax
  call rt_memcpy
  pop  rcx
  pop  rcx
.nopay:
  lea  r8, [rcx + rdi]         ; TCP length
  mov  [rsp+32], r8
  ; IP header at +14
  mov  byte [rbx + 14], 0x45
  mov  byte [rbx + 15], 0
  lea  rax, [r8 + 20]
  xchg al, ah
  mov  [rbx + 16], ax
  mov  ax, [rel g_un_ip_id]
  inc  word [rel g_un_ip_id]
  mov  [rbx + 18], ax
  mov  word [rbx + 20], 0x0040 ; DF
  mov  byte [rbx + 22], 64
  mov  byte [rbx + 23], 6
  mov  word [rbx + 24], 0
  mov  eax, [rel g_un_ip]
  mov  [rbx + 26], eax
  mov  eax, [rel g_un_peer_ip]
  mov  [rbx + 30], eax
  lea  rcx, [rbx + 14]
  mov  edx, 20
  xor  r8d, r8d
  call un_csum
  mov  [rbx + 24], ax
  ; TCP checksum: pseudo-header (addresses, zero, 6, length) + segment
  lea  rcx, [rbx + 26]
  mov  edx, 8
  call un_sum16                ; rax = sum of the two addresses
  mov  rcx, [rsp+32]
  mov  rdx, rcx
  xchg dl, dh                  ; the length, big-endian, as a 16-bit word in memory order
  add  rax, rdx
  add  rax, 0x0600             ; protocol 6 in the high byte of the word
  mov  r8, rax
  mov  rcx, r13
  mov  rdx, [rsp+32]
  call un_csum
  mov  [r13 + 16], ax
  ; sequence bookkeeping
  mov  eax, edi
  test r12d, TF_SYN | TF_FIN
  jz   .adv
  inc  eax
.adv:
  add  [rel g_un_snd_nxt], eax
  mov  rcx, rbx
  mov  rdx, [rsp+32]
  add  rdx, 34
  call un_tx_frame
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ---------------------------------------------------------------------------
; the transport
; ---------------------------------------------------------------------------

; un_accept() -> rax = 1 (the connection) once a peer has completed the
; handshake, 0 after a second of nothing (the caller asks again).
un_accept:
  push rbx
  WINPROLOGUE
  call rt_now_ns
  lea  rbx, [rax + 1000000000]
  cmp  qword [rel g_un_state], TS_CLOSED
  jne  .loop
  mov  qword [rel g_un_state], TS_LISTEN
.loop:
  call un_pump
  cmp  qword [rel g_un_state], TS_ESTABLISHED
  je   .yes
  cmp  qword [rel g_un_state], TS_CLOSE_WAIT
  je   .yes                    ; data and the FIN arrived together: still a request to read
  call rt_now_ns
  cmp  rax, rbx
  jb   .loop
  xor  eax, eax
  WINEPILOGUE
  pop  rbx
  ret
.yes:
  mov  eax, 1
  WINEPILOGUE
  pop  rbx
  ret

; un_recv(rcx=conn, rdx=buf, r8=cap) -> rax = bytes copied out of the ring;
; 0 when the peer has closed with nothing left, or after 30 s of silence.
un_recv:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  mov  rsi, rdx
  mov  rdi, r8
  call rt_now_ns
  mov  r12, UN_RX_TIMEOUT_NS
  add  r12, rax
.wait:
  mov  rax, [rel g_un_rx_wr]
  sub  rax, [rel g_un_rx_rd]
  jnz  .have
  mov  rax, [rel g_un_state]
  cmp  rax, TS_ESTABLISHED
  jne  .zero                   ; closed, closing or reset: nothing more will come
  call un_pump
  call rt_now_ns
  cmp  rax, r12
  jb   .wait
  jmp  .zero
.have:
  cmp  rax, rdi
  jbe  .n
  mov  rax, rdi
.n:
  mov  rbx, rax
  lea  rdx, [rel g_un_ring]
  add  rdx, [rel g_un_rx_rd]
  mov  rcx, rsi
  mov  r8, rbx
  call rt_memcpy
  add  [rel g_un_rx_rd], rbx
  mov  rax, [rel g_un_rx_rd]
  cmp  rax, [rel g_un_rx_wr]
  jne  .ret
  mov  qword [rel g_un_rx_rd], 0 ; empty: back to the start
  mov  qword [rel g_un_rx_wr], 0
.ret:
  mov  rax, rbx
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.zero:
  xor  eax, eax
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_send(rcx=conn, rdx=buf, r8=len) -> rax = 0 when every byte was
; acknowledged, 1 when the connection died. One segment at a time.
un_send:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  mov  rsi, rdx
  mov  rdi, r8
.seg:
  test rdi, rdi
  jz   .ok
  mov  rax, [rel g_un_state]
  cmp  rax, TS_ESTABLISHED
  je   .alive
  cmp  rax, TS_CLOSE_WAIT
  jne  .fail                   ; the peer is gone
.alive:
  mov  rbx, rdi
  cmp  rbx, UN_MSS
  jbe  .sz
  mov  ebx, UN_MSS
.sz:
  mov  r13d, UN_RETRIES
.xmit:
  mov  ecx, TF_PSH | TF_ACK
  mov  rdx, rsi
  mov  r8, rbx
  call un_tcp_send             ; snd_nxt += rbx
  call rt_now_ns
  lea  r12, [rax + UN_RTO_NS]
.ackwait:
  call un_pump
  mov  eax, [rel g_un_snd_una]
  cmp  eax, [rel g_un_snd_nxt]
  je   .acked
  mov  rax, [rel g_un_state]
  cmp  rax, TS_ESTABLISHED
  je   .still
  cmp  rax, TS_CLOSE_WAIT
  jne  .fail
.still:
  call rt_now_ns
  cmp  rax, r12
  jb   .ackwait
  dec  r13d
  jz   .fail
  sub  [rel g_un_snd_nxt], ebx ; retransmit the same bytes
  jmp  .xmit
.acked:
  add  rsi, rbx
  sub  rdi, rbx
  jmp  .seg
.ok:
  xor  eax, eax
  jmp  .ret
.fail:
  mov  eax, 1
.ret:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; un_close(rcx=conn) — our FIN, the peer's ACK (and FIN, when it has not
; closed yet), then back to listening. Gives up after 3 s.
un_close:
  push rbx
  WINPROLOGUE
  mov  rax, [rel g_un_state]
  cmp  rax, TS_ESTABLISHED
  je   .fin
  cmp  rax, TS_CLOSE_WAIT
  jne  .listen
.fin:
  cmp  rax, TS_CLOSE_WAIT
  jne  .fw
  mov  qword [rel g_un_state], TS_LAST_ACK
  jmp  .sendfin
.fw:
  mov  qword [rel g_un_state], TS_FIN_WAIT1
.sendfin:
  mov  ecx, TF_FIN | TF_ACK
  xor  edx, edx
  xor  r8d, r8d
  call un_tcp_send
  call rt_now_ns
  mov  rbx, 3000000000
  add  rbx, rax
.wait:
  call un_pump
  cmp  qword [rel g_un_state], TS_CLOSED
  je   .listen
  call rt_now_ns
  cmp  rax, rbx
  jb   .wait
.listen:
  mov  qword [rel g_un_state], TS_LISTEN
  mov  qword [rel g_un_rx_rd], 0
  mov  qword [rel g_un_rx_wr], 0
  WINEPILOGUE
  pop  rbx
  ret

e_s_un_nousb:   db "asmrun: usbnet: the firmware enumerated no USB device", 10, 0
e_s_un_nornd:   db "asmrun: usbnet: no RNDIS interface on the bus (the phone's USB tethering is off, or the cable carries no data)", 10, 0
e_s_un_fail:    db "asmrun: usbnet: the RNDIS device did not answer the setup", 10, 0
e_s_un_up:      db "asmrun: usbnet: RNDIS link up, our mac ", 0
e_s_un_url:     db "; open http://", 0
e_s_un_url2:    db ":", 0
e_s_un_url3:    db "/ on the phone", 10, 0
e_s_un_colon:   db ":", 0

section .data
  align 8
g_un_ctl:       dq 0           ; EFI_USB_IO_PROTOCOL* of the RNDIS control interface
g_un_data:      dq 0           ; ... of the CDC data interface
g_un_ctl_if:    dd 0           ; bInterfaceNumber of the control interface
g_un_ep_in:     db 0           ; bulk endpoint addresses
g_un_ep_out:    db 0
g_un_mac:       times 6 db 0   ; ours (the device tells us)
g_un_peer_mac:  times 6 db 0
  align 4
g_un_ip:        dd 0           ; ours, memory order
g_un_peer_ip:   dd 0
g_un_peer_port: dw 0           ; network order as received
g_un_port:      dw 0           ; host order
g_un_ip_id:     dw 1
  align 8
g_un_state:     dq TS_CLOSED
g_un_snd_una:   dd 0
g_un_snd_nxt:   dd 0
g_un_rcv_nxt:   dd 0
g_un_seg_seq:   dd 0
g_un_seg_ack:   dd 0
  align 8
g_un_rx_rd:     dq 0
g_un_rx_wr:     dq 0
g_un_msg:       times 64 db 0
g_un_resp:      times RNDIS_RESP_CAP db 0
g_un_tx:        times UN_TXBUF db 0
g_un_rx:        times UN_RXBUF db 0
g_un_ring:      times UN_RING db 0
section .text

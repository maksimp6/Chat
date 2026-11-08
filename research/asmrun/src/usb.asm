; usb.asm — what the firmware's bus drivers give us for a link to the outside:
; the USB devices the firmware has enumerated (one EFI_USB_IO_PROTOCOL handle
; per interface: a phone in "File transfer" mode shows up as an MTP interface
; here, no driver and no root needed) and the network protocols it carries
; (EFI_SIMPLE_NETWORK on the Ethernet port when the BIOS network stack is on;
; EFI_WIRELESS_MAC_CONNECTION_II only on firmware that ships a Wi-Fi driver,
; which consumer boards do not). Printed once at boot by efi_probe_links so
; the dialog shows what a data link can be built on.
;
; UEFI only (included under ASMRUN_EFI).

section .text

; USB descriptors (USB 2.0 §9.6, packed as the firmware returns them)
USB_DEV_idVendor        equ 8            ; u16
USB_DEV_idProduct       equ 10           ; u16
USB_DEV_iProduct        equ 15           ; u8 string index
USB_DEV_BYTES           equ 18
USB_IF_bInterfaceNumber equ 2
USB_IF_bInterfaceClass  equ 5
USB_IF_bInterfaceSubClass equ 6
USB_IF_bInterfaceProtocol equ 7
USB_IF_BYTES            equ 9

; EFI_USB_IO_PROTOCOL function pointers
USBIO_GetDeviceDescriptor    equ 48      ; (This, EFI_USB_DEVICE_DESCRIPTOR*)
USBIO_GetInterfaceDescriptor equ 64      ; (This, EFI_USB_INTERFACE_DESCRIPTOR*)
USBIO_GetStringDescriptor    equ 80      ; (This, LangID u16, StringIndex u8, CHAR16** String) — pool memory
USBIO_GetSupportedLanguages  equ 88      ; (This, u16** LangIDTable, u16* TableSize)

EFI_PROBE_MAX equ 32                      ; handles kept per protocol

; efi_probe_links() — print the USB interfaces and the network protocol handle
; counts. Nothing fatal: a protocol the firmware lacks prints as 0.
efi_probe_links:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 96
  mov  qword [rel g_log_tee], 1 ; from here the screen goes into dialog.txt too (flushed after the first turn)
  ; [rsp+32] NoHandles, [rsp+40] Buffer (EFI_HANDLE*), [rsp+48] CHAR16* / u16* out,
  ; [rsp+56] u16 table size, [rsp+64..82) device descriptor, [rsp+82..91) interface descriptor
  lea  rcx, [rel e_guid_usb_io]
  call efi_locate_handles      ; rax = count, rdx = buffer (0 when none)
  mov  rbx, rax
  mov  rsi, rdx
  lea  rcx, [rel e_s_usb_head]
  call rt_err_str
  mov  rcx, rbx
  call rt_err_u64
  lea  rcx, [rel e_s_usb_head2]
  call rt_err_str
  test rsi, rsi
  jz   .usb_done
  xor  r12d, r12d              ; index
.usb_each:
  cmp  r12, rbx
  jae  .usb_free
  cmp  r12, EFI_PROBE_MAX
  jae  .usb_free
  ; the EFI_USB_IO_PROTOCOL interface of this handle
  mov  rax, [rel g_bs]
  mov  rcx, [rsi + r12*8]
  lea  rdx, [rel e_guid_usb_io]
  lea  r8, [rsp+48]
  call [rax + BS_HandleProtocol]
  test rax, rax
  jnz  .usb_next
  mov  rdi, [rsp+48]           ; EFI_USB_IO_PROTOCOL*
  mov  rcx, rdi
  lea  rdx, [rsp+64]
  call [rdi + USBIO_GetDeviceDescriptor]
  test rax, rax
  jnz  .usb_next
  mov  rcx, rdi
  lea  rdx, [rsp+82]
  call [rdi + USBIO_GetInterfaceDescriptor]
  test rax, rax
  jnz  .usb_next
  lea  rcx, [rel e_s_usb_item]
  call rt_err_str
  movzx ecx, word [rsp+64+USB_DEV_idVendor]
  call efi_err_hex16
  lea  rcx, [rel e_s_colon]
  call rt_err_str
  movzx ecx, word [rsp+64+USB_DEV_idProduct]
  call efi_err_hex16
  lea  rcx, [rel e_s_usb_if]
  call rt_err_str
  movzx ecx, byte [rsp+82+USB_IF_bInterfaceNumber]
  call rt_err_u64
  lea  rcx, [rel e_s_usb_class]
  call rt_err_str
  movzx ecx, byte [rsp+82+USB_IF_bInterfaceClass]
  call efi_err_hex8
  lea  rcx, [rel e_s_slash]
  call rt_err_str
  movzx ecx, byte [rsp+82+USB_IF_bInterfaceSubClass]
  call efi_err_hex8
  lea  rcx, [rel e_s_slash]
  call rt_err_str
  movzx ecx, byte [rsp+82+USB_IF_bInterfaceProtocol]
  call efi_err_hex8
  ; the class as a word, where it names something we can use
  lea  rcx, [rel e_s_usb_cls_other]
  movzx eax, byte [rsp+82+USB_IF_bInterfaceClass]
  cmp  al, 0x03
  jne  .c1
  lea  rcx, [rel e_s_usb_cls_hid]
  jmp  .cls
.c1:
  cmp  al, 0x08
  jne  .c2
  lea  rcx, [rel e_s_usb_cls_msc]
  jmp  .cls
.c2:
  cmp  al, 0x06
  jne  .c3
  lea  rcx, [rel e_s_usb_cls_ptp]
  jmp  .cls
.c3:
  cmp  al, 0x09
  jne  .c4
  lea  rcx, [rel e_s_usb_cls_hub]
  jmp  .cls
.c4:
  cmp  al, 0xFF
  jne  .cls
  lea  rcx, [rel e_s_usb_cls_vendor]
  cmp  byte [rsp+82+USB_IF_bInterfaceSubClass], 0xFF
  jne  .c5
  lea  rcx, [rel e_s_usb_cls_mtp] ; Android MTP: FF/FF/00
.c5:
  cmp  byte [rsp+82+USB_IF_bInterfaceSubClass], 0x42
  jne  .cls
  lea  rcx, [rel e_s_usb_cls_adb] ; Android ADB: FF/42/01
.cls:
  call rt_err_str
  ; the product string, in the device's first language
  movzx eax, byte [rsp+64+USB_DEV_iProduct]
  test eax, eax
  jz   .usb_eol
  mov  rcx, rdi
  lea  rdx, [rsp+48]
  lea  r8, [rsp+56]
  call [rdi + USBIO_GetSupportedLanguages]
  test rax, rax
  jnz  .usb_eol
  cmp  word [rsp+56], 0
  je   .usb_eol
  mov  rax, [rsp+48]
  movzx edx, word [rax]        ; the first LangID
  mov  rcx, rdi
  movzx r8d, byte [rsp+64+USB_DEV_iProduct]
  lea  r9, [rsp+48]
  call [rdi + USBIO_GetStringDescriptor]
  test rax, rax
  jnz  .usb_eol
  mov  r13, [rsp+48]           ; CHAR16* in pool memory
  lea  rcx, [rel e_s_usb_quote]
  call rt_err_str
  mov  rcx, r13
  lea  rdx, [rel g_probe_utf8]
  mov  r8d, 255
  call rt_utf16_to_utf8
  lea  rdx, [rel g_probe_utf8]
  mov  byte [rdx + rax], 0
  lea  rcx, [rel g_probe_utf8]
  call rt_err_str
  lea  rcx, [rel e_s_quote2]
  call rt_err_str
  mov  rax, [rel g_bs]
  mov  rcx, r13
  call [rax + BS_FreePool]
.usb_eol:
  lea  rcx, [rel e_s_nl]
  call rt_err_str
.usb_next:
  inc  r12
  jmp  .usb_each
.usb_free:
  mov  rax, [rel g_bs]
  mov  rcx, rsi
  call [rax + BS_FreePool]
.usb_done:
  ; network: handle counts only
  lea  rcx, [rel e_s_net_head]
  call rt_err_str
  lea  rcx, [rel e_guid_snp]
  call efi_probe_count
  lea  rcx, [rel e_s_net_tcp4]
  call rt_err_str
  lea  rcx, [rel e_guid_tcp4_sb]
  call efi_probe_count
  lea  rcx, [rel e_s_net_http]
  call rt_err_str
  lea  rcx, [rel e_guid_http_sb]
  call efi_probe_count
  lea  rcx, [rel e_s_net_wifi]
  call rt_err_str
  lea  rcx, [rel e_guid_wifi2]
  call efi_probe_count
  lea  rcx, [rel e_s_nl]
  call rt_err_str
  call efi_probe_snp           ; one line per network interface: MAC, type, link
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; EFI_SIMPLE_NETWORK_PROTOCOL: the Mode pointer, and EFI_SIMPLE_NETWORK_MODE fields
SNP_Mode             equ 120         ; after Revision and 13 functions, WaitForPacket
SNM_State            equ 0           ; u32: 0 stopped, 1 started, 2 initialized
SNM_CurrentAddress   equ 552         ; EFI_MAC_ADDRESS (32 bytes), after MCastFilter[16]
SNM_IfType           equ 648         ; u8: 1 = Ethernet
SNM_MediaPresentSupported equ 651
SNM_MediaPresent     equ 652         ; BOOLEAN: link up

; efi_probe_snp() — "  net <mac> type N state N link up|down|unknown" per interface.
efi_probe_snp:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  sub  rsp, 32
  lea  rcx, [rel e_guid_snp]
  call efi_locate_handles
  test rax, rax
  jz   .done
  mov  rbx, rax
  mov  rsi, rdx
  xor  r12d, r12d
.each:
  cmp  r12, rbx
  jae  .free
  mov  rax, [rel g_bs]
  mov  rcx, [rsi + r12*8]
  lea  rdx, [rel e_guid_snp]
  lea  r8, [rsp+32]
  call [rax + BS_HandleProtocol]
  test rax, rax
  jnz  .next
  mov  rdi, [rsp+32]
  mov  rdi, [rdi + SNP_Mode]
  test rdi, rdi
  jz   .next
  lea  rcx, [rel e_s_snp_item]
  call rt_err_str
  lea  rcx, [rdi + SNM_CurrentAddress]
  call un_print_mac
  lea  rcx, [rel e_s_snp_type]
  call rt_err_str
  movzx ecx, byte [rdi + SNM_IfType]
  call rt_err_u64
  lea  rcx, [rel e_s_snp_state]
  call rt_err_str
  mov  ecx, [rdi + SNM_State]
  call rt_err_u64
  lea  rcx, [rel e_s_snp_link]
  call rt_err_str
  lea  rcx, [rel e_s_snp_unknown]
  cmp  byte [rdi + SNM_MediaPresentSupported], 0
  je   .link
  lea  rcx, [rel e_s_snp_down]
  cmp  byte [rdi + SNM_MediaPresent], 0
  je   .link
  lea  rcx, [rel e_s_snp_up]
.link:
  call rt_err_str
  lea  rcx, [rel e_s_nl]
  call rt_err_str
.next:
  inc  r12
  jmp  .each
.free:
  mov  rax, [rel g_bs]
  mov  rcx, rsi
  call [rax + BS_FreePool]
.done:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

e_s_snp_item:   db "  net ", 0
e_s_snp_type:   db " type ", 0
e_s_snp_state:  db " state ", 0
e_s_snp_link:   db " link ", 0
e_s_snp_up:     db "up", 0
e_s_snp_down:   db "down", 0
e_s_snp_unknown: db "unknown", 0

; efi_probe_count(rcx=GUID*) — print the number of handles carrying the protocol.
efi_probe_count:
  WINPROLOGUE
  call efi_locate_handles
  push rax
  push rdx
  sub  rsp, 32
  test rdx, rdx
  jz   .nofree
  mov  rcx, rdx
  mov  rax, [rel g_bs]
  call [rax + BS_FreePool]
.nofree:
  add  rsp, 32
  pop  rdx
  pop  rcx
  call rt_err_u64
  WINEPILOGUE
  ret

; efi_locate_handles(rcx=GUID*) -> rax = handle count, rdx = EFI_HANDLE* buffer
; in pool memory (the caller frees it) or 0 when the firmware has none.
efi_locate_handles:
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32] 5th argument (&Buffer), [rsp+48] NoHandles, [rsp+56] Buffer
  mov  qword [rsp+48], 0
  mov  qword [rsp+56], 0
  mov  rdx, rcx                ; &Guid
  mov  ecx, EFI_BY_PROTOCOL
  xor  r8d, r8d                ; SearchKey
  lea  r9, [rsp+48]            ; &NoHandles
  lea  rax, [rsp+56]
  mov  [rsp+32], rax           ; &Buffer
  mov  rax, [rel g_bs]
  call [rax + BS_LocateHandleBuffer]
  test rax, rax
  jnz  .none
  mov  rax, [rsp+48]
  mov  rdx, [rsp+56]
  WINEPILOGUE
  ret
.none:
  xor  eax, eax
  xor  edx, edx
  WINEPILOGUE
  ret

; efi_err_hex16(rcx=value) — 4 hex digits; efi_err_hex8(rcx=value) — 2 digits.
; Clobbers rax,rcx,rdx,r8-r10.
efi_err_hex8:
  shl  rcx, 56
  mov  edx, 2
  jmp  efi_err_hexn
efi_err_hex16:
  shl  rcx, 48
  mov  edx, 4
efi_err_hexn:                  ; rcx = the digits at the top of the register, edx = how many
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32..36) digits, [rsp+40] count
  mov  r8d, edx
  mov  [rsp+40], rdx
  lea  r9, [rsp+32]
  lea  r10, [rel k_hex_digits]
.dig:
  mov  rax, rcx
  shr  rax, 60
  shl  rcx, 4
  mov  al, [r10 + rax]
  mov  [r9], al
  inc  r9
  dec  r8d
  jnz  .dig
  mov  rcx, [rel g_stderr]
  lea  rdx, [rsp+32]
  mov  r8, [rsp+40]
  call rt_write_h
  WINEPILOGUE
  ret

e_guid_usb_io:                 ; EFI_USB_IO_PROTOCOL 2B2F68D6-0CD2-44CF-8E8B-BBA20B1B5B75
  dd 0x2B2F68D6
  dw 0x0CD2, 0x44CF
  db 0x8E, 0x8B, 0xBB, 0xA2, 0x0B, 0x1B, 0x5B, 0x75
e_guid_snp:                    ; EFI_SIMPLE_NETWORK_PROTOCOL A19832B9-AC25-11D3-9A2D-0090273FC14D
  dd 0xA19832B9
  dw 0xAC25, 0x11D3
  db 0x9A, 0x2D, 0x00, 0x90, 0x27, 0x3F, 0xC1, 0x4D
e_guid_tcp4_sb:                ; EFI_TCP4_SERVICE_BINDING_PROTOCOL 00720665-67EB-4A99-BAF7-D3C33A1C7CC9
  dd 0x00720665
  dw 0x67EB, 0x4A99
  db 0xBA, 0xF7, 0xD3, 0xC3, 0x3A, 0x1C, 0x7C, 0xC9
e_guid_http_sb:                ; EFI_HTTP_SERVICE_BINDING_PROTOCOL BDC8E6AF-D9BC-4379-A72A-E0C4E75DAE1C
  dd 0xBDC8E6AF
  dw 0xD9BC, 0x4379
  db 0xA7, 0x2A, 0xE0, 0xC4, 0xE7, 0x5D, 0xAE, 0x1C
e_guid_wifi2:                  ; EFI_WIRELESS_MAC_CONNECTION_II_PROTOCOL 1B0FB9BF-699D-4FDD-A7C3-2546681BF63B
  dd 0x1B0FB9BF
  dw 0x699D, 0x4FDD
  db 0xA7, 0xC3, 0x25, 0x46, 0x68, 0x1B, 0xF6, 0x3B

e_s_usb_head:   db "usb: ", 0
e_s_usb_head2:  db " interface(s) enumerated by the firmware", 10, 0
e_s_usb_item:   db "  usb ", 0
e_s_colon:      db ":", 0
e_s_slash:      db "/", 0
e_s_usb_if:     db " if ", 0
e_s_usb_class:  db " class ", 0
e_s_usb_cls_hid:    db " hid", 0
e_s_usb_cls_msc:    db " mass-storage", 0
e_s_usb_cls_ptp:    db " still-image/ptp", 0
e_s_usb_cls_hub:    db " hub", 0
e_s_usb_cls_vendor: db " vendor-specific", 0
e_s_usb_cls_mtp:    db " android-mtp", 0
e_s_usb_cls_adb:    db " android-adb", 0
e_s_usb_cls_other:  db "", 0
e_s_usb_quote:  db " ", 0x22, 0
e_s_quote2:     db 0x22, 0
e_s_nl:         db 10, 0
e_s_net_head:   db "net: snp=", 0
e_s_net_tcp4:   db " tcp4=", 0
e_s_net_http:   db " http=", 0
e_s_net_wifi:   db " wifi2=", 0

section .data
g_probe_utf8:   times 256 db 0
section .text

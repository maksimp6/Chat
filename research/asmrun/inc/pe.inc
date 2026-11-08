; pe.inc — a hand-written PE32+ (x86-64) image header for `nasm -f bin`: a
; Windows console executable, or with -D ASMRUN_EFI a UEFI application (the
; same file format; the firmware's loader is a PE loader).
;
; Layout trick: FileAlignment == SectionAlignment == 0x1000, so a byte's file
; offset equals its RVA, and with `org IMAGE_BASE` (set in asmrun.asm) every
; label's assembled address equals its link-time virtual address. Therefore an
; RVA is simply `label - IMAGE_BASE`, both in the headers and in the sections.
;
; Two sections: .text (import tables, code, read-only constants; execute+read)
; from RVA 0x1000, and .data (every module's mutable globals and strings;
; read+write) from the page after the code. A firmware that keeps code read-
; only and data non-executable can therefore protect the image unchanged.
;
; Relocations: the code is position-independent (RIP-relative addressing only;
; no absolute addresses in data). The Windows build says RELOCS_STRIPPED and is
; loaded at IMAGE_BASE. The UEFI build clears that bit so the firmware may load
; it anywhere; its base-relocation directory holds one block with a single
; ABSOLUTE (no-op) entry, the same shape the Linux EFI stub carries, which the
; EDK2 loader walks without changing a byte.
;
; Labels defined elsewhere and used here (all resolved by NASM across passes):
;   _entry          program entry point            (src/win.asm or src/efi.asm)
;   section_start   first byte of .text's body     (end of this file, = 0x1000)
;   text_end        end of the code                (asmrun.asm)
;   data_start      first byte of .data, page-aligned (asmrun.asm)
;   SECTION_RAW_END end of .data, padded to 0x1000 (asmrun.asm)
;   IMAGE_END       end of the image               (asmrun.asm)

%ifndef PE_INC
%define PE_INC

%define RVA(x) ((x) - IMAGE_BASE)

dos_header:
  db 'M','Z'
  times 0x3c - ($ - dos_header) db 0
  dd RVA(pe_signature)          ; e_lfanew at offset 0x3C -> PE header

pe_signature:
  db 'P','E',0,0

coff_header:
  dw 0x8664                     ; Machine = IMAGE_FILE_MACHINE_AMD64
  dw 2                          ; NumberOfSections (.text, .data)
  dd 0                          ; TimeDateStamp
  dd 0                          ; PointerToSymbolTable
  dd 0                          ; NumberOfSymbols
  dw opt_header_end - opt_header ; SizeOfOptionalHeader
%ifdef ASMRUN_EFI
  dw 0x0022                     ; Characteristics: EXECUTABLE_IMAGE|LARGE_ADDRESS_AWARE (relocatable)
%else
  dw 0x0023                     ; Characteristics: RELOCS_STRIPPED|EXECUTABLE_IMAGE|LARGE_ADDRESS_AWARE
%endif

opt_header:
  dw 0x020b                     ; Magic = PE32+
  db 14, 0                      ; Linker version (cosmetic)
  dd RVA(data_start) - 0x1000   ; SizeOfCode (.text raw size)
  dd RVA(SECTION_RAW_END) - RVA(data_start) ; SizeOfInitializedData (.data raw size)
  dd 0                          ; SizeOfUninitializedData
  dd RVA(_entry)                ; AddressOfEntryPoint
  dd 0x1000                     ; BaseOfCode
  dq IMAGE_BASE                 ; ImageBase
  dd 0x1000                     ; SectionAlignment
  dd 0x1000                     ; FileAlignment
  dw 6, 0                       ; MajorMinor OperatingSystemVersion
  dw 0, 0                       ; MajorMinor ImageVersion
  dw 6, 0                       ; MajorMinor SubsystemVersion
  dd 0                          ; Win32VersionValue
  dd RVA(IMAGE_END)             ; SizeOfImage (IMAGE_END is page-aligned by asmrun.asm)
  dd 0x1000                     ; SizeOfHeaders (headers occupy the first page)
  dd 0                          ; CheckSum (not required)
%ifdef ASMRUN_EFI
  dw 10                         ; Subsystem = IMAGE_SUBSYSTEM_EFI_APPLICATION
%else
  dw 3                          ; Subsystem = WINDOWS_CUI (console)
%endif
%ifdef ASMRUN_EFI
  dw 0x0100                     ; DllCharacteristics: NX_COMPAT. The image is NX-clean (code read-only, nothing
                                ; executed from data, pages or a stack), so a firmware that enforces the
                                ; Microsoft memory-mitigation rules keeps its strict mode and need not refuse it
%else
  dw 0                          ; DllCharacteristics (no dynamic base)
%endif
  dq 0x0000000000800000         ; SizeOfStackReserve = 8 MiB
  dq 0x0000000000100000         ; SizeOfStackCommit  = 1 MiB
  dq 0x0000000000100000         ; SizeOfHeapReserve
  dq 0x0000000000001000         ; SizeOfHeapCommit
  dd 0                          ; LoaderFlags
  dd 16                         ; NumberOfRvaAndSizes
  ; --- data directories (RVA, size) ---
  dd 0, 0                       ; 0 Export
%ifdef ASMRUN_EFI
  dd 0, 0                       ; 1 Import (none: everything goes through the system table)
%else
  dd RVA(import_descriptors), import_descriptors_size ; 1 Import
%endif
  dd 0, 0                       ; 2 Resource
  dd 0, 0                       ; 3 Exception
  dd 0, 0                       ; 4 Certificate
%ifdef ASMRUN_EFI
  dd RVA(efi_reloc), EFI_RELOC_SIZE ; 5 BaseReloc: one block of ABSOLUTE entries (nothing to fix up)
%else
  dd 0, 0                       ; 5 BaseReloc
%endif
  dd 0, 0                       ; 6 Debug
  dd 0, 0                       ; 7 Architecture
  dd 0, 0                       ; 8 GlobalPtr
  dd 0, 0                       ; 9 TLS
  dd 0, 0                       ; 10 LoadConfig
  dd 0, 0                       ; 11 BoundImport
%ifdef ASMRUN_EFI
  dd 0, 0                       ; 12 IAT
%else
  dd RVA(iat), iat_end - iat    ; 12 IAT
%endif
  dd 0, 0                       ; 13 DelayImport
  dd 0, 0                       ; 14 CLR
  dd 0, 0                       ; 15 Reserved
opt_header_end:

section_headers:
  ; .text — import tables, code, read-only constants
  db '.text', 0, 0, 0           ; Name[8]
  dd RVA(text_end) - 0x1000     ; VirtualSize
  dd 0x1000                     ; VirtualAddress
  dd RVA(data_start) - 0x1000   ; SizeOfRawData (page multiple: .data starts on a page)
  dd 0x1000                     ; PointerToRawData
  dd 0                          ; PointerToRelocations
  dd 0                          ; PointerToLinenumbers
  dw 0                          ; NumberOfRelocations
  dw 0                          ; NumberOfLinenumbers
  dd 0x60000020                 ; CODE|EXECUTE|READ
  ; .data — mutable globals and strings of every module
  db '.data', 0, 0, 0
  dd RVA(SECTION_RAW_END) - RVA(data_start) ; VirtualSize
  dd RVA(data_start)            ; VirtualAddress
  dd RVA(SECTION_RAW_END) - RVA(data_start) ; SizeOfRawData (padded to a page by asmrun.asm)
  dd RVA(data_start)            ; PointerToRawData
  dd 0
  dd 0
  dw 0
  dw 0
  dd 0xC0000040                 ; INITIALIZED_DATA|READ|WRITE

  ; Pad the headers to one full page; .text's body begins at file offset / RVA 0x1000.
  times 0x1000 - ($ - dos_header) db 0

; ===========================================================================
; .text body begins here (RVA 0x1000).
section_start:

%ifdef ASMRUN_EFI
; The base-relocation block the UEFI build advertises: the loader finds one
; block of 12 bytes (the 32-bit multiple the PE format asks of a block) whose
; two entries are IMAGE_REL_BASED_ABSOLUTE, i.e. no-ops (the Linux EFI stub
; carries one such block too). The code needs no fix-ups.
  align 4
efi_reloc:
  dd RVA(section_start)         ; VirtualAddress: the page this block is in (section_start is page-aligned, the block its first bytes)
  dd 12                         ; SizeOfBlock: this 8-byte header + two 2-byte entries
  dw 0, 0                       ; type 0 (ABSOLUTE), offset 0: nothing to do
EFI_RELOC_SIZE equ 12
  align 16

%else
; --- Import Directory Table: kernel32.dll (console, files, memory, threads),
; user32.dll and gdi32.dll (the picture viewer's window), then a null one ---
import_descriptors:
  dd RVA(ilt)            ; OriginalFirstThunk (Import Lookup Table)
  dd 0                   ; TimeDateStamp
  dd 0                   ; ForwarderChain
  dd RVA(kernel32_name)  ; Name
  dd RVA(iat)            ; FirstThunk (Import Address Table)
  dd RVA(ilt_user32), 0, 0, RVA(user32_name), RVA(iat_user32)
  dd RVA(ilt_gdi32), 0, 0, RVA(gdi32_name), RVA(iat_gdi32)
  dd RVA(ilt_ws2), 0, 0, RVA(ws2_name), RVA(iat_ws2)   ; the HTTP test bench (src/wsock.asm)
  dd 0, 0, 0, 0, 0       ; null terminating descriptor
import_descriptors_size equ $ - import_descriptors

; Import Lookup Table: each entry is the RVA of a hint/name (top bit clear).
ilt:
  dq RVA(h_GetStdHandle)
  dq RVA(h_WriteFile)
  dq RVA(h_ReadFile)
  dq RVA(h_ExitProcess)
  dq RVA(h_VirtualAlloc)
  dq RVA(h_CreateFileW)
  dq RVA(h_CreateFileMappingW)
  dq RVA(h_MapViewOfFile)
  dq RVA(h_CloseHandle)
  dq RVA(h_GetFileSizeEx)
  dq RVA(h_QueryPerformanceCounter)
  dq RVA(h_QueryPerformanceFrequency)
  dq RVA(h_GetCommandLineW)
  dq RVA(h_GetSystemInfo)
  dq RVA(h_CreateThread)
  dq RVA(h_SetThreadAffinityMask)
  dq RVA(h_SetThreadPriority)
  dq RVA(h_AddVectoredExceptionHandler)
  dq RVA(h_RemoveVectoredExceptionHandler)
  dq 0
ilt_user32:
  dq RVA(h_RegisterClassExW)
  dq RVA(h_CreateWindowExW)
  dq RVA(h_DefWindowProcW)
  dq RVA(h_GetMessageW)
  dq RVA(h_DispatchMessageW)
  dq RVA(h_PostQuitMessage)
  dq RVA(h_DestroyWindow)
  dq RVA(h_BeginPaint)
  dq RVA(h_EndPaint)
  dq RVA(h_GetClientRect)
  dq RVA(h_AdjustWindowRect)
  dq RVA(h_GetSystemMetrics)
  dq RVA(h_LoadCursorW)
  dq RVA(h_TranslateMessage)
  dq RVA(h_SendMessageW)
  dq RVA(h_SetFocus)
  dq RVA(h_MoveWindow)
  dq RVA(h_GetWindowTextW)
  dq RVA(h_SetWindowTextW)
  dq RVA(h_InvalidateRect)
  dq RVA(h_DrawTextW)
  dq 0
ilt_gdi32:
  dq RVA(h_StretchDIBits)
  dq RVA(h_SetStretchBltMode)
  dq RVA(h_GetStockObject)
  dq RVA(h_CreateFontW)
  dq RVA(h_CreateSolidBrush)
  dq RVA(h_SelectObject)
  dq RVA(h_SetTextColor)
  dq RVA(h_SetBkMode)
  dq 0
ilt_ws2:
  dq RVA(h_WSAStartup)
  dq RVA(h_socket)
  dq RVA(h_bind)
  dq RVA(h_listen)
  dq RVA(h_accept)
  dq RVA(h_recv)
  dq RVA(h_send)
  dq RVA(h_closesocket)
  dq 0

; Import Address Table: the loader overwrites each slot with the function
; address. Modules call through these labels: call winimp(WriteFile). The IAT
; is in .text on purpose: the Windows loader writes it before the image runs
; and marks the page as it needs.
iat:
__imp_GetStdHandle:             dq RVA(h_GetStdHandle)
__imp_WriteFile:                dq RVA(h_WriteFile)
__imp_ReadFile:                 dq RVA(h_ReadFile)
__imp_ExitProcess:              dq RVA(h_ExitProcess)
__imp_VirtualAlloc:             dq RVA(h_VirtualAlloc)
__imp_CreateFileW:              dq RVA(h_CreateFileW)
__imp_CreateFileMappingW:       dq RVA(h_CreateFileMappingW)
__imp_MapViewOfFile:            dq RVA(h_MapViewOfFile)
__imp_CloseHandle:              dq RVA(h_CloseHandle)
__imp_GetFileSizeEx:            dq RVA(h_GetFileSizeEx)
__imp_QueryPerformanceCounter:  dq RVA(h_QueryPerformanceCounter)
__imp_QueryPerformanceFrequency: dq RVA(h_QueryPerformanceFrequency)
__imp_GetCommandLineW:          dq RVA(h_GetCommandLineW)
__imp_GetSystemInfo:            dq RVA(h_GetSystemInfo)
__imp_CreateThread:             dq RVA(h_CreateThread)
__imp_SetThreadAffinityMask:    dq RVA(h_SetThreadAffinityMask)
__imp_SetThreadPriority:        dq RVA(h_SetThreadPriority)
__imp_AddVectoredExceptionHandler: dq RVA(h_AddVectoredExceptionHandler)
__imp_RemoveVectoredExceptionHandler: dq RVA(h_RemoveVectoredExceptionHandler)
                                dq 0
iat_user32:
__imp_RegisterClassExW:         dq RVA(h_RegisterClassExW)
__imp_CreateWindowExW:          dq RVA(h_CreateWindowExW)
__imp_DefWindowProcW:           dq RVA(h_DefWindowProcW)
__imp_GetMessageW:              dq RVA(h_GetMessageW)
__imp_DispatchMessageW:         dq RVA(h_DispatchMessageW)
__imp_PostQuitMessage:          dq RVA(h_PostQuitMessage)
__imp_DestroyWindow:            dq RVA(h_DestroyWindow)
__imp_BeginPaint:               dq RVA(h_BeginPaint)
__imp_EndPaint:                 dq RVA(h_EndPaint)
__imp_GetClientRect:            dq RVA(h_GetClientRect)
__imp_AdjustWindowRect:         dq RVA(h_AdjustWindowRect)
__imp_GetSystemMetrics:         dq RVA(h_GetSystemMetrics)
__imp_LoadCursorW:              dq RVA(h_LoadCursorW)
__imp_TranslateMessage:         dq RVA(h_TranslateMessage)
__imp_SendMessageW:             dq RVA(h_SendMessageW)
__imp_SetFocus:                 dq RVA(h_SetFocus)
__imp_MoveWindow:               dq RVA(h_MoveWindow)
__imp_GetWindowTextW:           dq RVA(h_GetWindowTextW)
__imp_SetWindowTextW:           dq RVA(h_SetWindowTextW)
__imp_InvalidateRect:           dq RVA(h_InvalidateRect)
__imp_DrawTextW:                dq RVA(h_DrawTextW)
                                dq 0
iat_gdi32:
__imp_StretchDIBits:            dq RVA(h_StretchDIBits)
__imp_SetStretchBltMode:        dq RVA(h_SetStretchBltMode)
__imp_GetStockObject:           dq RVA(h_GetStockObject)
__imp_CreateFontW:              dq RVA(h_CreateFontW)
__imp_CreateSolidBrush:         dq RVA(h_CreateSolidBrush)
__imp_SelectObject:             dq RVA(h_SelectObject)
__imp_SetTextColor:             dq RVA(h_SetTextColor)
__imp_SetBkMode:                dq RVA(h_SetBkMode)
                                dq 0
iat_ws2:
__imp_WSAStartup:               dq RVA(h_WSAStartup)
__imp_socket:                   dq RVA(h_socket)
__imp_bind:                     dq RVA(h_bind)
__imp_listen:                   dq RVA(h_listen)
__imp_accept:                   dq RVA(h_accept)
__imp_recv:                     dq RVA(h_recv)
__imp_send:                     dq RVA(h_send)
__imp_closesocket:              dq RVA(h_closesocket)
                                dq 0
iat_end:

kernel32_name: db 'kernel32.dll', 0
user32_name:   db 'user32.dll', 0
gdi32_name:    db 'gdi32.dll', 0
ws2_name:      db 'ws2_32.dll', 0

; Hint/Name entries: dw Hint (0), then the ASCII name, NUL. Each is 2-byte
; aligned (align before the label, so the label itself is even).
  align 2
h_GetStdHandle:             dw 0
  db 'GetStdHandle', 0
  align 2
h_WriteFile:                dw 0
  db 'WriteFile', 0
  align 2
h_ReadFile:                 dw 0
  db 'ReadFile', 0
  align 2
h_ExitProcess:              dw 0
  db 'ExitProcess', 0
  align 2
h_VirtualAlloc:             dw 0
  db 'VirtualAlloc', 0
  align 2
h_CreateFileW:              dw 0
  db 'CreateFileW', 0
  align 2
h_CreateFileMappingW:       dw 0
  db 'CreateFileMappingW', 0
  align 2
h_MapViewOfFile:            dw 0
  db 'MapViewOfFile', 0
  align 2
h_CloseHandle:              dw 0
  db 'CloseHandle', 0
  align 2
h_GetFileSizeEx:            dw 0
  db 'GetFileSizeEx', 0
  align 2
h_QueryPerformanceCounter:  dw 0
  db 'QueryPerformanceCounter', 0
  align 2
h_QueryPerformanceFrequency: dw 0
  db 'QueryPerformanceFrequency', 0
  align 2
h_GetCommandLineW:          dw 0
  db 'GetCommandLineW', 0
  align 2
h_GetSystemInfo:            dw 0
  db 'GetSystemInfo', 0
  align 2
h_CreateThread:             dw 0
  db 'CreateThread', 0
  align 2
h_SetThreadAffinityMask:    dw 0
  db 'SetThreadAffinityMask', 0
  align 2
h_SetThreadPriority:        dw 0
  db 'SetThreadPriority', 0
h_AddVectoredExceptionHandler: dw 0
  db 'AddVectoredExceptionHandler', 0
h_RemoveVectoredExceptionHandler: dw 0
  db 'RemoveVectoredExceptionHandler', 0
  align 2
h_RegisterClassExW:         dw 0
  db 'RegisterClassExW', 0
  align 2
h_CreateWindowExW:          dw 0
  db 'CreateWindowExW', 0
  align 2
h_DefWindowProcW:           dw 0
  db 'DefWindowProcW', 0
  align 2
h_GetMessageW:              dw 0
  db 'GetMessageW', 0
  align 2
h_DispatchMessageW:         dw 0
  db 'DispatchMessageW', 0
  align 2
h_PostQuitMessage:          dw 0
  db 'PostQuitMessage', 0
  align 2
h_DestroyWindow:            dw 0
  db 'DestroyWindow', 0
  align 2
h_BeginPaint:               dw 0
  db 'BeginPaint', 0
  align 2
h_EndPaint:                 dw 0
  db 'EndPaint', 0
  align 2
h_GetClientRect:            dw 0
  db 'GetClientRect', 0
  align 2
h_AdjustWindowRect:         dw 0
  db 'AdjustWindowRect', 0
  align 2
h_GetSystemMetrics:         dw 0
  db 'GetSystemMetrics', 0
  align 2
h_LoadCursorW:              dw 0
  db 'LoadCursorW', 0
  align 2
h_TranslateMessage:         dw 0
  db 'TranslateMessage', 0
  align 2
h_SendMessageW:             dw 0
  db 'SendMessageW', 0
  align 2
h_SetFocus:                 dw 0
  db 'SetFocus', 0
  align 2
h_MoveWindow:               dw 0
  db 'MoveWindow', 0
  align 2
h_GetWindowTextW:           dw 0
  db 'GetWindowTextW', 0
  align 2
h_SetWindowTextW:           dw 0
  db 'SetWindowTextW', 0
  align 2
h_InvalidateRect:           dw 0
  db 'InvalidateRect', 0
  align 2
h_DrawTextW:                dw 0
  db 'DrawTextW', 0
  align 2
h_CreateFontW:              dw 0
  db 'CreateFontW', 0
  align 2
h_CreateSolidBrush:         dw 0
  db 'CreateSolidBrush', 0
  align 2
h_SelectObject:             dw 0
  db 'SelectObject', 0
  align 2
h_SetTextColor:             dw 0
  db 'SetTextColor', 0
  align 2
h_SetBkMode:                dw 0
  db 'SetBkMode', 0
  align 2
h_WSAStartup:               dw 0
  db 'WSAStartup', 0
  align 2
h_socket:                   dw 0
  db 'socket', 0
  align 2
h_bind:                     dw 0
  db 'bind', 0
  align 2
h_listen:                   dw 0
  db 'listen', 0
  align 2
h_accept:                   dw 0
  db 'accept', 0
  align 2
h_recv:                     dw 0
  db 'recv', 0
  align 2
h_send:                     dw 0
  db 'send', 0
  align 2
h_closesocket:              dw 0
  db 'closesocket', 0
  align 2
h_StretchDIBits:            dw 0
  db 'StretchDIBits', 0
  align 2
h_SetStretchBltMode:        dw 0
  db 'SetStretchBltMode', 0
  align 2
h_GetStockObject:           dw 0
  db 'GetStockObject', 0
  align 16
%endif

%endif ; PE_INC

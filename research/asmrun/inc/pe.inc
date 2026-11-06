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
  dw 0                          ; DllCharacteristics (Windows: no dynamic base; UEFI: no NX_COMPAT claim, so a protecting firmware keeps its compatibility mode)
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
  dd RVA(efi_reloc), EFI_RELOC_SIZE ; 5 BaseReloc: one block, one ABSOLUTE entry (nothing to fix up)
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
; block of 10 bytes whose single entry is IMAGE_REL_BASED_ABSOLUTE, i.e. a no-op
; (the Linux EFI stub carries exactly this). The code needs no fix-ups.
  align 4
efi_reloc:
  dd RVA(efi_reloc) + 10        ; VirtualAddress: the page of the entry below
  dd 10                         ; SizeOfBlock: this 8-byte header + one 2-byte entry
  dw 0                          ; type 0 (ABSOLUTE), offset 0: nothing to do
EFI_RELOC_SIZE equ 10
  align 16

%else
; --- Import Directory Table (one descriptor for kernel32.dll + a null one) ---
import_descriptors:
  dd RVA(ilt)            ; OriginalFirstThunk (Import Lookup Table)
  dd 0                   ; TimeDateStamp
  dd 0                   ; ForwarderChain
  dd RVA(kernel32_name)  ; Name
  dd RVA(iat)            ; FirstThunk (Import Address Table)
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
                                dq 0
iat_end:

kernel32_name: db 'kernel32.dll', 0

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
  align 16
%endif

%endif ; PE_INC

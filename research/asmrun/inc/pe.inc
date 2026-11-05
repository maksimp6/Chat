; pe.inc — a hand-written PE32+ (x86-64) console executable header for `nasm -f bin`.
;
; Layout trick: FileAlignment == SectionAlignment == 0x1000, so a byte's file
; offset equals its RVA, and with `org IMAGE_BASE` (set in asmrun.asm) every
; label's assembled address equals its runtime virtual address. Therefore an
; RVA is simply `label - IMAGE_BASE`, both in the headers and in the section.
;
; One section ".flat" holds imports, code and data (RWX). Relocations are
; stripped and no .reloc is present, so the image must load at IMAGE_BASE
; (0x140000000, the usual exe base, normally free). The section's uninitialised
; tail (BSS) is zero-filled by the loader: VirtualSize > SizeOfRawData.
;
; Labels defined elsewhere and used here (all resolved by NASM across passes):
;   _entry          program entry point            (src/runtime.asm)
;   section_start   first byte of the section      (end of this file, = 0x1000)
;   SECTION_RAW_END end of initialised section bytes, padded to 0x1000 (asmrun.asm)
;   IMAGE_END       end of BSS                       (asmrun.asm)

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
  dw 1                          ; NumberOfSections
  dd 0                          ; TimeDateStamp
  dd 0                          ; PointerToSymbolTable
  dd 0                          ; NumberOfSymbols
  dw opt_header_end - opt_header ; SizeOfOptionalHeader
  dw 0x0023                     ; Characteristics: RELOCS_STRIPPED|EXECUTABLE_IMAGE|LARGE_ADDRESS_AWARE

opt_header:
  dw 0x020b                     ; Magic = PE32+
  db 14, 0                      ; Linker version (cosmetic)
  dd RVA(SECTION_RAW_END) - 0x1000   ; SizeOfCode (section raw size; cosmetic)
  dd 0                          ; SizeOfInitializedData (cosmetic)
  dd 0                          ; SizeOfUninitializedData (cosmetic)
  dd RVA(_entry)                ; AddressOfEntryPoint
  dd 0x1000                     ; BaseOfCode
  dq IMAGE_BASE                 ; ImageBase
  dd 0x1000                     ; SectionAlignment
  dd 0x1000                     ; FileAlignment
  dw 6, 0                       ; MajorMinor OperatingSystemVersion
  dw 0, 0                       ; MajorMinor ImageVersion
  dw 6, 0                       ; MajorMinor SubsystemVersion
  dd 0                          ; Win32VersionValue
  dd (RVA(IMAGE_END) + 0xfff) & ~0xfff   ; SizeOfImage (rounded to SectionAlignment)
  dd 0x1000                     ; SizeOfHeaders (headers occupy the first page)
  dd 0                          ; CheckSum (not required for an exe)
  dw 3                          ; Subsystem = WINDOWS_CUI (console)
  dw 0                          ; DllCharacteristics (no dynamic base: load at ImageBase)
  dq 0x0000000000800000         ; SizeOfStackReserve = 8 MiB
  dq 0x0000000000100000         ; SizeOfStackCommit  = 1 MiB
  dq 0x0000000000100000         ; SizeOfHeapReserve
  dq 0x0000000000001000         ; SizeOfHeapCommit
  dd 0                          ; LoaderFlags
  dd 16                         ; NumberOfRvaAndSizes
  ; --- data directories (RVA, size) ---
  dd 0, 0                       ; 0 Export
  dd RVA(import_descriptors), import_descriptors_size ; 1 Import
  dd 0, 0                       ; 2 Resource
  dd 0, 0                       ; 3 Exception
  dd 0, 0                       ; 4 Certificate
  dd 0, 0                       ; 5 BaseReloc
  dd 0, 0                       ; 6 Debug
  dd 0, 0                       ; 7 Architecture
  dd 0, 0                       ; 8 GlobalPtr
  dd 0, 0                       ; 9 TLS
  dd 0, 0                       ; 10 LoadConfig
  dd 0, 0                       ; 11 BoundImport
  dd RVA(iat), iat_end - iat    ; 12 IAT
  dd 0, 0                       ; 13 DelayImport
  dd 0, 0                       ; 14 CLR
  dd 0, 0                       ; 15 Reserved
opt_header_end:

section_header:
  db '.flat', 0, 0, 0           ; Name[8]
  dd RVA(IMAGE_END) - 0x1000    ; VirtualSize (code + data + BSS)
  dd 0x1000                     ; VirtualAddress
  dd RVA(SECTION_RAW_END) - 0x1000 ; SizeOfRawData (initialised bytes, page-aligned)
  dd 0x1000                     ; PointerToRawData
  dd 0                          ; PointerToRelocations
  dd 0                          ; PointerToLinenumbers
  dw 0                          ; NumberOfRelocations
  dw 0                          ; NumberOfLinenumbers
  dd 0xE0000060                 ; CODE|INITIALIZED_DATA|EXECUTE|READ|WRITE

  ; Pad the headers to one full page; the section begins at file offset / RVA 0x1000.
  times 0x1000 - ($ - dos_header) db 0

; ===========================================================================
; Section body begins here (RVA 0x1000). Imports first, then code and data
; from the %included modules.
section_start:

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
; address. Modules call through these labels: call winimp(WriteFile).
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

%endif ; PE_INC

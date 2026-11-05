; win.inc — Win32 constants and the kernel32 import names.
;
; The IAT itself lives in inc/pe.inc (it is part of the mapped image). This file
; only names the constants the modules use and the `winimp` call macro. Call an
; imported function as:  call winimp(WriteFile)  ->  call [rel __imp_WriteFile]

%ifndef WIN_INC
%define WIN_INC

; call winimp(Name)  expands to  call [rel __imp_Name]  (indirect through the IAT).
%define winimp(f) [rel __imp_ %+ f]

; GetStdHandle
STD_INPUT_HANDLE   equ -10
STD_OUTPUT_HANDLE  equ -11
STD_ERROR_HANDLE   equ -12
INVALID_HANDLE     equ -1

; CreateFileW
GENERIC_READ          equ 0x80000000
FILE_SHARE_READ       equ 0x00000001
OPEN_EXISTING         equ 3
FILE_ATTRIBUTE_NORMAL equ 0x00000080

; CreateFileMappingW / MapViewOfFile
PAGE_READONLY   equ 0x02
PAGE_READWRITE  equ 0x04
SEC_COMMIT      equ 0x08000000
FILE_MAP_READ   equ 0x0004

; VirtualAlloc
MEM_COMMIT   equ 0x00001000
MEM_RESERVE  equ 0x00002000
PAGE_RW      equ 0x04

; CreateThread
CREATE_RUNNING equ 0

%endif ; WIN_INC

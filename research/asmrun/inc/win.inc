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
GENERIC_WRITE         equ 0x40000000
CREATE_ALWAYS         equ 2
FILE_SHARE_READ       equ 0x00000001
OPEN_EXISTING         equ 3
OPEN_ALWAYS           equ 4
FILE_APPEND_DATA      equ 0x00000004
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
THREAD_PRIORITY_HIGHEST equ 2

; The picture viewer's window (user32, gdi32)
WM_DESTROY          equ 0x0002
WM_PAINT            equ 0x000F
WM_KEYDOWN          equ 0x0100
VK_ESCAPE           equ 0x1B
CS_VREDRAW          equ 0x0001
CS_HREDRAW          equ 0x0002
IDC_ARROW           equ 32512
BLACK_BRUSH         equ 4
WS_OVERLAPPEDWINDOW equ 0x00CF0000
WS_VISIBLE          equ 0x10000000
CW_USEDEFAULT       equ 0x80000000
SM_CXSCREEN         equ 0
SM_CYSCREEN         equ 1
WM_SIZE             equ 0x0005
WM_SETFONT          equ 0x0030
VK_RETURN           equ 0x0D
WS_CHILD            equ 0x40000000
WS_EX_CLIENTEDGE    equ 0x00000200
ES_AUTOHSCROLL      equ 0x0080
DEFAULT_GUI_FONT    equ 17
FW_NORMAL           equ 400
DEFAULT_CHARSET     equ 1
CLEARTYPE_QUALITY   equ 5
TRANSPARENT         equ 1
DT_CENTER           equ 0x0001
DT_WORDBREAK        equ 0x0010
WM_COMMAND          equ 0x0111
BS_DEFPUSHBUTTON    equ 0x0001
COLORONCOLOR        equ 3
DIB_RGB_COLORS      equ 0
SRCCOPY             equ 0x00CC0020

%endif ; WIN_INC

; efi.inc — what efi.asm needs from the UEFI specification: offsets of the
; function pointers in the system, boot and runtime service tables and in the
; protocols we call, a few constants, and the status codes we test. Every
; table is an array of 8-byte pointers after a 24-byte EFI_TABLE_HEADER; a
; protocol is an array of 8-byte pointers from its start. The protocol GUIDs
; themselves are bytes in efi.asm's data.
;
; UEFI uses the Microsoft x64 calling convention, the one asmrun already uses:
; rcx rdx r8 r9, then stack slots from [rsp+32], 32 bytes of shadow space, rsp
; 16-byte aligned at the call.

%ifndef EFI_INC
%define EFI_INC

; EFI_STATUS values (high bit set = error)
EFI_SUCCESS            equ 0
EFI_ABORTED            equ 0x8000000000000015

; EFI_SYSTEM_TABLE
ST_ConIn               equ 48          ; EFI_SIMPLE_TEXT_INPUT_PROTOCOL*
ST_ConOut              equ 64          ; EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL*
ST_RuntimeServices     equ 88
ST_BootServices        equ 96

; EFI_BOOT_SERVICES
BS_AllocatePages       equ 40          ; (Type, MemoryType, Pages, &Memory)
BS_FreePages           equ 48
BS_CreateEvent         equ 80          ; (Type, NotifyTpl, NotifyFunction, NotifyContext, &Event)
BS_WaitForEvent        equ 96          ; (NumberOfEvents, Event*, &Index)
BS_CloseEvent          equ 112
BS_HandleProtocol      equ 152         ; (Handle, &Guid, &Interface)
BS_Stall               equ 248         ; (Microseconds)
BS_SetWatchdogTimer    equ 256         ; (Timeout, WatchdogCode, DataSize, WatchdogData)
BS_LocateProtocol      equ 320         ; (&Guid, Registration, &Interface)

; EFI_RUNTIME_SERVICES
RS_ResetSystem         equ 104         ; (ResetType, ResetStatus, DataSize, ResetData)

; EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL
TXT_OutputString       equ 8           ; (This, CHAR16*)
TXT_ClearScreen        equ 48          ; (This)

; EFI_SIMPLE_TEXT_INPUT_PROTOCOL
TXTIN_ReadKeyStroke    equ 8           ; (This, EFI_INPUT_KEY*)  key = u16 ScanCode, u16 UnicodeChar
TXTIN_WaitForKey       equ 16          ; EFI_EVENT (a value, not a function)
SCAN_ESC               equ 0x17

; EFI_LOADED_IMAGE_PROTOCOL
LI_DeviceHandle        equ 24          ; the device we were loaded from (the USB stick)

; EFI_SIMPLE_FILE_SYSTEM_PROTOCOL
SFS_OpenVolume         equ 8           ; (This, &Root)

; EFI_FILE_PROTOCOL
FP_Open                equ 8           ; (This, &NewHandle, CHAR16* Name, OpenMode, Attributes)
FP_Close               equ 16          ; (This)
FP_Read                equ 32          ; (This, &BufferSize, Buffer)
FP_GetInfo             equ 64          ; (This, &InfoGuid, &BufferSize, Buffer)
EFI_FILE_MODE_READ     equ 1
FI_FileSize            equ 8           ; EFI_FILE_INFO.FileSize (u64)

; EFI_MP_SERVICES_PROTOCOL (PI specification)
MP_GetNumberOfProcessors equ 0         ; (This, &Number, &Enabled)
MP_GetProcessorInfo    equ 8           ; (This, ProcessorNumber, EFI_PROCESSOR_INFORMATION*)
MP_StartupThisAP       equ 24          ; (This, Procedure, ProcessorNumber, WaitEvent, Timeout, Argument, &Finished)
MP_WhoAmI              equ 48          ; (This, &ProcessorNumber)
PI_StatusFlag          equ 8           ; EFI_PROCESSOR_INFORMATION.StatusFlag (u32)
PROCESSOR_ENABLED_BIT  equ 2
PROCESSOR_HEALTH_STATUS_BIT equ 4

; AllocatePages arguments
AllocateAnyPages       equ 0
EfiLoaderData          equ 2

; ResetSystem
EfiResetCold           equ 0

%endif ; EFI_INC

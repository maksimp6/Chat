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
EFI_NOT_READY          equ 0x8000000000000006
EFI_ABORTED            equ 0x8000000000000015

; Model-specific registers (Intel SDM vol. 4), read and written in ring 0 on the
; bare cores; each only after CPUID has said the feature exists.
MSR_IA32_MPERF         equ 0xE7         ; ticks at the TSC rate while the core runs (C0)
MSR_IA32_APERF         equ 0xE8         ; actual core cycles while it runs
MSR_IA32_ENERGY_PERF_BIAS equ 0x1B0     ; 0 = performance .. 15 = power saving (CPUID.06H:ECX[3])
MSR_IA32_PM_ENABLE     equ 0x770        ; bit 0: Hardware P-states on (CPUID.06H:EAX[7]); sticky until reset
MSR_IA32_HWP_CAPABILITIES equ 0x771     ; [7:0] highest [15:8] guaranteed [23:16] most efficient [31:24] lowest level
MSR_IA32_HWP_REQUEST   equ 0x774        ; [7:0] minimum [15:8] maximum [23:16] desired [31:24] EPP, per logical processor
MSR_IA32_FIXED_CTR_CTRL equ 0x38D       ; fixed performance counters: 4 bits each (bit 0 OS, bit 1 USR)
MSR_IA32_PERF_GLOBAL_CTRL equ 0x38F     ; bits 32.. enable the fixed counters; bits 0.. the general ones
MSR_IA32_PMC0          equ 0xC1         ; general-purpose counter 0 (0xC2: counter 1)
MSR_IA32_PERFEVTSEL0   equ 0x186        ; its event select (0x187: counter 1's)
; Gracemont (the N100's core) events, from Intel's perfmon list for Alder Lake E-cores:
; MEM_BOUND_STALLS.LOAD (0x34/0x07): cycles the core is stalled on a demand load that
; missed L1 (served by L2, L3 or DRAM); .LOAD_DRAM_HIT (0x34/0x04): the DRAM part.
PMU_EVSEL_MEM_STALL    equ 0x00430734   ; event 0x34, umask 0x07, USR, OS, EN
PMU_EVSEL_DRAM_STALL   equ 0x00430434   ; event 0x34, umask 0x04, USR, OS, EN

; EFI_SYSTEM_TABLE
ST_ConIn               equ 48          ; EFI_SIMPLE_TEXT_INPUT_PROTOCOL*
ST_ConOut              equ 64          ; EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL*
ST_RuntimeServices     equ 88
ST_BootServices        equ 96

; EFI_BOOT_SERVICES
BS_AllocatePages       equ 40          ; (Type, MemoryType, Pages, &Memory)
BS_FreePages           equ 48
BS_FreePool            equ 72          ; (Buffer)
BS_CreateEvent         equ 80          ; (Type, NotifyTpl, NotifyFunction, NotifyContext, &Event)
BS_WaitForEvent        equ 96          ; (NumberOfEvents, Event*, &Index)
BS_CloseEvent          equ 112
BS_CheckEvent          equ 120         ; (Event) -> EFI_SUCCESS when signaled (and cleared), EFI_NOT_READY otherwise
BS_HandleProtocol      equ 152         ; (Handle, &Guid, &Interface)
BS_Stall               equ 248         ; (Microseconds)
BS_SetWatchdogTimer    equ 256         ; (Timeout, WatchdogCode, DataSize, WatchdogData)
BS_LocateHandleBuffer  equ 312         ; (SearchType, &Guid, SearchKey, &NoHandles, &Buffer) — pool memory
BS_LocateProtocol      equ 320         ; (&Guid, Registration, &Interface)
EFI_BY_PROTOCOL        equ 2           ; EFI_LOCATE_SEARCH_TYPE

; thermal sensors (Intel SDM vol. 4): the digital readout is degrees below TjMax
MSR_IA32_THERM_STATUS       equ 0x19C  ; [22:16] readout, [31] reading valid (per core)
MSR_IA32_TEMPERATURE_TARGET equ 0x1A2  ; [23:16] TjMax
MSR_IA32_PACKAGE_THERM_STATUS equ 0x1B1 ; [22:16] readout, [31] valid (package)

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
FP_Delete              equ 24          ; (This): deletes the file and closes the handle
FP_Read                equ 32          ; (This, &BufferSize, Buffer)
FP_Write               equ 40          ; (This, &BufferSize, Buffer)
FP_SetPosition         equ 56          ; (This, Position; ~0 = the end of the file)
FP_GetInfo             equ 64          ; (This, &InfoGuid, &BufferSize, Buffer)
FP_Flush               equ 80          ; (This)
EFI_FILE_MODE_READ     equ 1
EFI_FILE_MODE_WRITE    equ 2
EFI_FILE_MODE_CREATE   equ 0x8000000000000000
FI_FileSize            equ 8           ; EFI_FILE_INFO.FileSize (u64)

; EFI_MP_SERVICES_PROTOCOL (PI specification)
MP_GetNumberOfProcessors equ 0         ; (This, &Number, &Enabled)
MP_GetProcessorInfo    equ 8           ; (This, ProcessorNumber, EFI_PROCESSOR_INFORMATION*)
MP_StartupThisAP       equ 24          ; (This, Procedure, ProcessorNumber, WaitEvent, Timeout, Argument, &Finished)
MP_WhoAmI              equ 48          ; (This, &ProcessorNumber)
PI_StatusFlag          equ 8           ; EFI_PROCESSOR_INFORMATION.StatusFlag (u32)
PROCESSOR_ENABLED_BIT  equ 2
PROCESSOR_HEALTH_STATUS_BIT equ 4

; EFI_GRAPHICS_OUTPUT_PROTOCOL (UEFI 2.x, section 12.9): QueryMode, SetMode,
; Blt, then the Mode pointer
GOP_Blt                equ 16          ; (This, BltBuffer, BltOperation, SrcX, SrcY, DstX, DstY, Width, Height, Delta)
GOP_Mode               equ 24          ; EFI_GRAPHICS_OUTPUT_PROTOCOL_MODE*
GOPM_Info              equ 8           ; Mode->Info: EFI_GRAPHICS_OUTPUT_MODE_INFORMATION*
GOPI_HRes              equ 4           ; Info->HorizontalResolution (u32)
GOPI_VRes              equ 8           ; Info->VerticalResolution (u32)
EfiBltBufferToVideo    equ 2           ; EFI_GRAPHICS_OUTPUT_BLT_OPERATION: our BGRX buffer to the screen

; AllocatePages arguments
AllocateAnyPages       equ 0
EfiLoaderData          equ 2

; ResetSystem
EfiResetCold           equ 0

%endif ; EFI_INC

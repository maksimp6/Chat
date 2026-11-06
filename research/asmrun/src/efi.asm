; efi.asm — the UEFI half of the runtime: asmrun on a machine with no operating
; system. The firmware loads \EFI\BOOT\BOOTX64.EFI from a FAT32 USB stick and
; calls _entry(rcx=ImageHandle, rdx=EFI_SYSTEM_TABLE*) with the Microsoft x64
; ABI, the one the rest of asmrun already uses. Boot services stay available
; for the whole run (ExitBootServices is never called): the firmware's console,
; its FAT driver and its multiprocessor services are the only other code on the
; machine, and they are used only at the edges (load the file, print a token,
; read a key); the forward pass itself runs on bare cores, with interrupts off
; on the application processors.
;
; The machine is ours: every processor runs the model (the boot processor is
; tid 0, the others come through EFI_MP_SERVICES_PROTOCOL), and each one asks
; the hardware for its highest performance level (HWP, see efi_cpu_perf), since
; without an operating system nobody else would: a firmware may leave the cores
; at their base clock (800 MHz on the N100, against a 3.4 GHz burst).
;
; Interrupts stay enabled on the boot processor: the firmware's timer tick, its
; USB polling and MpInitLib's status check arrive through the IDT while the
; kernels hold live ymm state. The firmware's interrupt entry saves and restores
; only the FXSAVE image (x87, MXCSR, xmm0-15), never bits 255:128 of the ymm
; registers; they survive because firmware code is non-VEX (SSE2 at most: a
; legacy SSE instruction leaves the upper halves alone). cli would not remove
; that dependency (SMIs ignore it and are handled the same way).
;
; %included into asmrun.asm after runtime.asm (bits 64, default rel, .text
; current), which lists the names both platform halves implement:
;
;   _entry         save the firmware's registers, enable AVX state, disarm the
;                  boot watchdog, measure the TSC, set the clock, read
;                  \asmrun.cfg into argv (or "model.gguf -i"), call main, rt_exit
;   rt_write_h     UTF-8 -> UCS-2 to ConOut (stdout and stderr are the screen)
;   rt_exit        give the application processors back to the firmware, wait
;                  for a key, return to the boot manager (next boot option);
;                  reset the machine instead if a processor never came back
;   rt_alloc       AllocatePages(EfiLoaderData), zero-filled here
;   rt_map_file    the whole file from the boot volume, read into pages
;   rt_read_file   the same
;   rt_read_stdin  one line typed on the console (ConIn), as UTF-8
;   rt_now_ns      rdtsc scaled by the measured TSC frequency
;   rt_cpu_count   enabled processors from EFI_MP_SERVICES_PROTOCOL (1 without it)
;   pool_start     run pool_worker on nthreads-1 application processors
;
; Internal: efi_warn_status, efi_fatal_status, rt_enable_avx, efi_calibrate_tsc,
; efi_cpu_setup, efi_cpu_perf, efi_measure_clock, efi_open_root, efi_open,
; efi_file_size, efi_pages, efi_read_all, efi_read_optional, efi_build_cmdline,
; efi_mp, efi_wait_key, pool_ap_entry.
;
; Stage messages: every step before the model loads prints one line, so a boot
; that stops shows where. Errors print the EFI status code.
;
; The boot processor keeps the firmware's stack (the UEFI specification gives an
; application at least 128 KiB; no frame here is larger than a few hundred bytes
; and every buffer comes from pages). Memory is never freed and no page attribute
; is changed, so the application processors keep running on the firmware's
; identity map while the boot processor allocates.

EFI_PATH_CHARS   equ 260                 ; CHAR16 path buffer
EFI_FINFO_BYTES  equ 1024                ; EFI_FILE_INFO buffer (80 bytes + the name)
EFI_OUT_CHARS    equ 1024                ; ConOut chunk (CHAR16s) per OutputString
EFI_LINE_BYTES   equ 4096                ; one typed line, UTF-8
EFI_READ_CHUNK   equ 8*1024*1024         ; bytes per EFI_FILE_PROTOCOL.Read
EFI_PROGRESS_MIN equ 64*1024*1024        ; files at least this big show progress
AP_STACK_BYTES   equ 1024*1024           ; stack of each worker processor

; EFI_CHECK msg — after a call that returned EFI_STATUS in rax: on an error
; print "asmrun: <msg> (EFI status 0x...)" and leave. Nothing clobbered on success.
%macro EFI_CHECK 1
  test rax, rax
  jz   %%ok
  mov  rdx, rax
  lea  rcx, [rel %1]
  call efi_fatal_status
%%ok:
%endmacro

; ===========================================================================
; Entry
; ===========================================================================

; _entry(rcx=ImageHandle, rdx=SystemTable*) — efi_main. The firmware calls us
; with rsp%16 == 8 (return address on the stack); rt_exit returns from this
; frame with the firmware's nonvolatile registers restored.
_entry:
  mov  [rel g_fw_rsp], rsp
  mov  [rel g_fw_rbx], rbx
  mov  [rel g_fw_rbp], rbp
  mov  [rel g_fw_rsi], rsi
  mov  [rel g_fw_rdi], rdi
  mov  [rel g_fw_r12], r12
  mov  [rel g_fw_r13], r13
  mov  [rel g_fw_r14], r14
  mov  [rel g_fw_r15], r15
  lea  rax, [rel g_fw_xmm]     ; legacy SSE stores: AVX state is not enabled yet
  movups [rax+0],   xmm6
  movups [rax+16],  xmm7
  movups [rax+32],  xmm8
  movups [rax+48],  xmm9
  movups [rax+64],  xmm10
  movups [rax+80],  xmm11
  movups [rax+96],  xmm12
  movups [rax+112], xmm13
  movups [rax+128], xmm14
  movups [rax+144], xmm15
  stmxcsr [rel g_fw_mxcsr]

  mov  [rel g_image_handle], rcx
  mov  [rel g_systab], rdx
  mov  rax, [rdx + ST_BootServices]
  mov  [rel g_bs], rax
  mov  rax, [rdx + ST_RuntimeServices]
  mov  [rel g_rs], rax
  mov  rax, [rdx + ST_ConOut]
  mov  [rel g_conout], rax
  mov  rax, [rdx + ST_ConIn]
  mov  [rel g_conin], rax
  mov  qword [rel g_stdout], 1
  mov  qword [rel g_stderr], 2

  sub  rsp, 40                 ; shadow space; rsp is now 16-byte aligned

  ; The boot manager arms a 5-minute watchdog before starting an application;
  ; a model that loads for a minute and then chats would be rebooted by it.
  mov  rax, [rel g_bs]
  xor  ecx, ecx                ; Timeout 0 = disable
  xor  edx, edx
  xor  r8d, r8d
  xor  r9d, r9d
  call [rax + BS_SetWatchdogTimer]

  mov  rcx, [rel g_conout]
  call [rcx + TXT_ClearScreen]

  lea  rcx, [rel e_s_banner]
  call rt_err_str

  ; AVX state on this processor (the APs do the same in pool_ap_entry); the
  ; firmware's CR4 and XCR0 are kept for rt_exit to restore
  call rt_enable_avx           ; eax = 1 if enabled; rdx = CR4 before, r8 = XCR0 before
  test eax, eax
  jz   .noavx
  mov  [rel g_fw_cr4], rdx
  mov  [rel g_fw_xcr0], r8
  mov  qword [rel g_avx_on], 1
  ldmxcsr [rel k_mxcsr_init]   ; round to nearest, exceptions masked (what Windows gives a process)
  lea  rcx, [rel e_s_avx]
  call rt_err_str

  call efi_calibrate_tsc       ; g_tsc_hz; prints tsc_mhz
  call efi_cpu_setup           ; this processor at its highest clock; prints what it found
  call efi_open_root           ; g_root: the volume we were loaded from
  call efi_build_cmdline       ; rax = argc, g_argv filled

  mov  rcx, rax
  lea  rdx, [rel g_argv]
  call main                    ; model.asm
  mov  ecx, eax
  call rt_exit
  hlt
.noavx:
  lea  rcx, [rel e_err_xsave]
  call rt_fatal

; rt_enable_avx — on this processor: CR4.OSFXSR|OSXSAVE, XCR0 |= x87|SSE|AVX,
; after CPUID has confirmed XSAVE (leaf 1 ecx[26]), AVX (ecx[28]) and that XCR0
; may hold the three state bits (leaf 0DH eax[2:0]); a bit XCR0 does not support
; would be a #GP, and the firmware's handler stops the machine. Firmware leaves
; AVX state disabled (its code is SSE at most), and the first VEX instruction
; would #UD. Returns eax = 1 when enabled, with rdx = the CR4 and r8 = the XCR0
; the firmware had (for the restore on the way out); eax = 0 and nothing
; changed otherwise. Clobbers rax, rcx, rdx, r8, r9; preserves rbx.
rt_enable_avx:
  push rbx
  mov  eax, 1
  xor  ecx, ecx
  cpuid
  mov  r8d, ecx
  bt   r8d, 26                 ; XSAVE
  jnc  .no
  bt   r8d, 28                 ; AVX
  jnc  .no
  mov  eax, 0x0D
  xor  ecx, ecx
  cpuid                        ; eax[2:0]: x87, SSE and AVX state supported in XCR0
  and  eax, 7
  cmp  eax, 7
  jne  .no
  mov  r9, cr4                 ; the firmware's CR4
  mov  rax, r9
  or   rax, (1 << 9) | (1 << 18) ; OSFXSR, OSXSAVE
  mov  cr4, rax
  xor  ecx, ecx
  xgetbv                       ; edx:eax = XCR0 as the firmware left it
  mov  r8d, eax
  shl  rdx, 32
  or   r8, rdx                 ; r8 = the firmware's XCR0
  mov  rdx, r8
  shr  rdx, 32                 ; high half unchanged
  mov  eax, r8d
  or   eax, 7                  ; x87 | SSE | AVX
  xsetbv
  mov  rdx, r9
  mov  eax, 1
  pop  rbx
  ret
.no:
  xor  eax, eax
  pop  rbx
  ret

; efi_calibrate_tsc — g_tsc_hz from the TSC delta over a 100 ms BootServices
; Stall (the firmware's timer is the reference). When CPUID.15H reports a
; crystal clock the frequency it implies is printed too, and it replaces an
; implausible measurement (below 100 MHz or above 20 GHz: a Stall that returned
; early); with neither, fatal, since rt_now_ns divides by this value. Prints
; "asmrun: tsc_mhz=N".
efi_calibrate_tsc:
  push rbx
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = t0, then the measured Hz; [rsp+40] = CPUID.15H Hz or 0
  mov  qword [rsp+40], 0
  xor  eax, eax
  cpuid
  cmp  eax, 0x15
  jb   .measure
  mov  eax, 0x15
  xor  ecx, ecx
  cpuid                        ; eax=denominator ebx=numerator ecx=crystal Hz (any may be 0)
  test eax, eax
  jz   .measure
  test ebx, ebx
  jz   .measure
  test ecx, ecx
  jz   .measure
  mov  r8d, eax
  mov  eax, ecx
  mul  rbx                     ; crystal * numerator (ebx was zero-extended by cpuid)
  div  r8                      ; / denominator = TSC Hz per CPUID
  mov  [rsp+40], rax
.measure:
  lfence
  rdtsc
  shl  rdx, 32
  or   rax, rdx
  mov  [rsp+32], rax
  mov  rax, [rel g_bs]
  mov  ecx, 100000             ; microseconds
  call [rax + BS_Stall]
  lfence
  rdtsc
  shl  rdx, 32
  or   rax, rdx
  sub  rax, [rsp+32]
  imul rax, rax, 10            ; ticks per second
  mov  [rsp+32], rax
  mov  rcx, 100000000          ; 100 MHz
  cmp  rax, rcx
  jb   .implausible
  mov  rcx, 20000000000        ; 20 GHz
  cmp  rax, rcx
  ja   .implausible
  mov  [rel g_tsc_hz], rax
  jmp  .report
.implausible:
  mov  rax, [rsp+40]
  test rax, rax
  jz   .fatal
  mov  [rel g_tsc_hz], rax
  lea  rcx, [rel e_s_tsc_bad]
  call rt_err_str
  mov  rax, [rsp+32]
  xor  edx, edx
  mov  ecx, 1000000
  div  rcx
  mov  rcx, rax
  call rt_err_u64
  lea  rcx, [rel e_s_tsc_bad2]
  call rt_err_str
.report:
  lea  rcx, [rel e_s_tsc]
  call rt_err_str
  mov  rax, [rel g_tsc_hz]
  xor  edx, edx
  mov  ecx, 1000000
  div  rcx
  mov  rcx, rax
  call rt_err_u64
  mov  rax, [rsp+40]
  test rax, rax
  jz   .nl
  xor  edx, edx
  mov  ecx, 1000000
  div  rcx
  mov  rbx, rax
  lea  rcx, [rel e_s_tsc_cpuid]
  call rt_err_str
  mov  rcx, rbx
  call rt_err_u64
  lea  rcx, [rel e_s_paren_nl]
  call rt_err_str
  jmp  .ret
.nl:
  lea  rcx, [rel k_newline]
  mov  edx, 1
  call rt_err
.ret:
  WINEPILOGUE
  pop  rbx
  ret
.fatal:
  lea  rcx, [rel e_err_tsc]
  call rt_fatal

; ===========================================================================
; The clock: every processor at its highest performance level
; ===========================================================================
;
; Without an operating system nothing sets the P-state after the firmware, and
; a firmware commonly leaves the cores at their base ratio (the N100's is
; 800 MHz against a 3.4 GHz burst). Since Skylake and Goldmont Plus the
; processor picks its own frequency once Hardware P-states (HWP, "Speed Shift")
; are on, inside the window software requests. Per logical processor,
; efi_cpu_perf enables HWP if the firmware has not and requests minimum =
; maximum = the highest level the hardware reports (desired 0: autonomous inside
; that window; EPP 0: performance), and sets the energy/performance bias to
; performance. The hardware still enforces its power and thermal limits; this
; only removes the software limit. Every MSR is touched only when CPUID says the
; feature exists: a wrong MSR is a #GP, and the firmware's handler would stop
; the machine with a register dump.

; efi_cpu_setup — on the boot processor: measure the clock, apply efi_cpu_perf,
; measure again, print both and the HWP levels. The application processors
; apply efi_cpu_perf themselves in pool_ap_entry.
efi_cpu_setup:
  push rbx
  push r12
  push r13
  push r14
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = MHz after
  call efi_measure_clock
  mov  r12, rax                ; MHz before (0 = no APERF/MPERF)
  call efi_cpu_perf
  mov  r13d, eax               ; highest level (0 = no HWP)
  mov  ebx, edx                ; IA32_HWP_CAPABILITIES[31:0]
  mov  r14d, r8d               ; 1 = HWP enabled by us
  mov  rax, [rel g_bs]
  mov  ecx, 10000              ; let the clock settle before measuring again
  call [rax + BS_Stall]
  call efi_measure_clock
  mov  [rsp+32], rax
  test r12, r12
  jz   .nomeas
  lea  rcx, [rel e_s_clock1]
  call rt_err_str
  mov  rcx, r12
  call rt_err_u64
  lea  rcx, [rel e_s_clock2]
  call rt_err_str
  mov  rcx, [rsp+32]
  call rt_err_u64
  lea  rcx, [rel e_s_clock3]
  call rt_err_str
.nomeas:
  test r13d, r13d
  jz   .nohwp
  lea  rcx, [rel e_s_hwp_on]
  test r14d, r14d
  jz   .say
  lea  rcx, [rel e_s_hwp_enabled]
.say:
  call rt_err_str
  lea  rcx, [rel e_s_hwp1]
  call rt_err_str
  mov  ecx, ebx
  shr  ecx, 24                 ; lowest
  call rt_err_u64
  lea  rcx, [rel e_s_hwp2]
  call rt_err_str
  mov  ecx, ebx
  shr  ecx, 8
  and  ecx, 0xFF               ; guaranteed
  call rt_err_u64
  lea  rcx, [rel e_s_hwp3]
  call rt_err_str
  movzx ecx, bl                ; highest
  call rt_err_u64
  lea  rcx, [rel e_s_hwp4]
  call rt_err_str
  jmp  .done
.nohwp:
  lea  rcx, [rel e_s_nohwp]
  call rt_err_str
.done:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rbx
  ret

; efi_cpu_perf — this processor at its highest performance level (see above).
; Returns eax = the highest HWP level (0: no HWP, nothing requested), edx =
; IA32_HWP_CAPABILITIES[31:0], r8d = 1 when this call enabled HWP. Clobbers
; rax, rcx, rdx, r8-r10; preserves rbx. Runs on every processor.
efi_cpu_perf:
  push rbx
  xor  r8d, r8d
  xor  r9d, r9d                ; capabilities
  xor  r10d, r10d              ; highest level
  mov  eax, 6
  xor  ecx, ecx
  cpuid                        ; eax[7] HWP, eax[10] HWP EPP; ecx[3] IA32_ENERGY_PERF_BIAS
  mov  ebx, eax
  bt   ecx, 3
  jnc  .noepb
  mov  ecx, MSR_IA32_ENERGY_PERF_BIAS
  xor  eax, eax                ; 0 = performance
  xor  edx, edx
  wrmsr
.noepb:
  bt   ebx, 7
  jnc  .done
  mov  ecx, MSR_IA32_PM_ENABLE
  rdmsr
  test eax, 1
  jnz  .hwp_on
  or   eax, 1                  ; HWP on for the package (stays on until a reset)
  wrmsr
  mov  r8d, 1
.hwp_on:
  mov  ecx, MSR_IA32_HWP_CAPABILITIES
  rdmsr                        ; [7:0] highest [15:8] guaranteed [23:16] most efficient [31:24] lowest
  mov  r9d, eax
  movzx r10d, al
  mov  eax, r10d               ; minimum = highest
  mov  edx, r10d
  shl  edx, 8
  or   eax, edx                ; maximum = highest; desired 0 (autonomous in [min,max]);
                               ; EPP 0 (performance; reserved-as-zero without the EPP feature)
  xor  edx, edx                ; activity window 0, package control off, valid bits 0
  mov  ecx, MSR_IA32_HWP_REQUEST
  wrmsr
.done:
  mov  eax, r10d
  mov  edx, r9d
  pop  rbx
  ret

; efi_measure_clock -> rax = this processor's clock in MHz over a 50 ms busy
; loop, from IA32_APERF/IA32_MPERF against the TSC (CPUID.06H:ECX[0]); 0 when
; the counters are missing. Needs g_tsc_hz. Clobbers rax, rcx, rdx, r8-r11;
; preserves rbx.
efi_measure_clock:
  push rbx
  mov  eax, 6
  xor  ecx, ecx
  cpuid
  bt   ecx, 0
  jnc  .none
  mov  ecx, MSR_IA32_MPERF
  rdmsr
  shl  rdx, 32
  or   rax, rdx
  mov  r8, rax                 ; mperf at the start
  mov  ecx, MSR_IA32_APERF
  rdmsr
  shl  rdx, 32
  or   rax, rdx
  mov  r9, rax                 ; aperf at the start
  mov  rax, [rel g_tsc_hz]
  xor  edx, edx
  mov  ecx, 20
  div  rcx                     ; ticks in 50 ms
  mov  r11, rax
  rdtsc
  shl  rdx, 32
  or   rax, rdx
  add  r11, rax                ; deadline
.spin:
  rdtsc
  shl  rdx, 32
  or   rax, rdx
  cmp  rax, r11
  jb   .spin
  mov  ecx, MSR_IA32_MPERF
  rdmsr
  shl  rdx, 32
  or   rax, rdx
  sub  rax, r8                 ; mperf ticks: the TSC rate while the core ran
  mov  r8, rax
  mov  ecx, MSR_IA32_APERF
  rdmsr
  shl  rdx, 32
  or   rax, rdx
  sub  rax, r9                 ; actual core cycles meanwhile
  test r8, r8
  jz   .none
  mov  rcx, [rel g_tsc_hz]
  mul  rcx                     ; rdx:rax = cycles * tsc_hz
  div  r8                      ; / mperf = Hz
  xor  edx, edx
  mov  ecx, 1000000
  div  rcx                     ; MHz
  pop  rbx
  ret
.none:
  xor  eax, eax
  pop  rbx
  ret

; ===========================================================================
; Leaving
; ===========================================================================

; efi_warn_status(rcx=zstr, rdx=status) — "asmrun: <msg> (EFI status 0x..)\n".
efi_warn_status:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = msg, [rsp+40] = status
  mov  [rsp+32], rcx
  mov  [rsp+40], rdx
  lea  rcx, [rel k_fatal_prefix]
  mov  rdx, FATAL_PREFIX_LEN
  call rt_err
  mov  rcx, [rsp+32]
  call rt_err_str
  lea  rcx, [rel e_s_status]
  call rt_err_str
  mov  rcx, [rsp+40]
  call rt_err_hex64
  lea  rcx, [rel e_s_paren_nl]
  call rt_err_str
  WINEPILOGUE
  ret

; efi_fatal_status(rcx=zstr, rdx=status) — the same message, then rt_exit(1).
; Never returns.
efi_fatal_status:
  WINPROLOGUE
  call efi_warn_status
  mov  ecx, 1
  call rt_exit
  hlt

; rt_exit(rcx=code) — tell the worker processors to return to the firmware and
; wait until every processor pool_start handed a procedure (g_ap_started) has
; come back (g_ap_exited). Then print the exit code, wait for a key, and return
; from _entry's frame with the firmware's registers, XCR0 and CR4 restored:
; EFI_SUCCESS for code 0, EFI_ABORTED otherwise. A worker that has not returned
; within 3 s is stuck inside a job (the way pool_run's hang detector gets here);
; the image must not be handed back while a core executes it (the boot manager
; frees an application's pages the moment its entry point returns), so after
; the key the machine is reset instead. Never returns to its caller.
rt_exit:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = remaining waits
  mov  [rel g_exit_code], rcx
  mov  qword [rel g_pool_quit], 1
  mov  qword [rsp+32], 3000
.wq:
  mov  rax, [rel g_ap_exited]
  cmp  rax, [rel g_ap_started]
  jae  .quiet
  mov  rax, [rel g_bs]
  mov  ecx, 1000
  call [rax + BS_Stall]
  dec  qword [rsp+32]
  jnz  .wq
  ; a worker processor is still inside asmrun: say so, then reset
  lea  rcx, [rel e_s_exit1]
  call rt_err_str
  mov  rcx, [rel g_exit_code]
  call rt_err_u64
  lea  rcx, [rel e_s_stuck1]
  call rt_err_str
  mov  rcx, [rel g_ap_started]
  sub  rcx, [rel g_ap_exited]
  call rt_err_u64
  lea  rcx, [rel e_s_stuck2]
  call rt_err_str
  call efi_wait_key
  mov  rax, [rel g_rs]
  mov  ecx, EfiResetCold
  mov  rdx, EFI_ABORTED
  xor  r8d, r8d
  xor  r9d, r9d
  call [rax + RS_ResetSystem]  ; does not return
.park:
  hlt                          ; and if it did: never hand the image back
  jmp  .park
.quiet:
  lea  rcx, [rel e_s_exit1]
  call rt_err_str
  mov  rcx, [rel g_exit_code]
  call rt_err_u64
  lea  rcx, [rel e_s_exit2]
  call rt_err_str
  call efi_wait_key
  cmp  qword [rel g_avx_on], 0
  je   .novz
  vzeroupper
.novz:
  ldmxcsr [rel g_fw_mxcsr]
  lea  rax, [rel g_fw_xmm]
  movups xmm6,  [rax+0]
  movups xmm7,  [rax+16]
  movups xmm8,  [rax+32]
  movups xmm9,  [rax+48]
  movups xmm10, [rax+64]
  movups xmm11, [rax+80]
  movups xmm12, [rax+96]
  movups xmm13, [rax+112]
  movups xmm14, [rax+128]
  movups xmm15, [rax+144]
  cmp  qword [rel g_avx_on], 0
  je   .nox
  mov  rax, [rel g_fw_xcr0]    ; XCR0, then CR4, as the firmware had them
  mov  rdx, rax
  shr  rdx, 32
  xor  ecx, ecx
  xsetbv
  mov  rax, [rel g_fw_cr4]
  mov  cr4, rax
.nox:
  mov  rbx, [rel g_fw_rbx]
  mov  rbp, [rel g_fw_rbp]
  mov  rsi, [rel g_fw_rsi]
  mov  rdi, [rel g_fw_rdi]
  mov  r12, [rel g_fw_r12]
  mov  r13, [rel g_fw_r13]
  mov  r14, [rel g_fw_r14]
  mov  r15, [rel g_fw_r15]
  mov  rsp, [rel g_fw_rsp]     ; -> the firmware's return address
  xor  eax, eax
  cmp  qword [rel g_exit_code], 0
  je   .ok
  mov  rax, EFI_ABORTED
.ok:
  ret

; efi_wait_key — block until a key is pressed and consume it (a wake with no
; complete keystroke yet, EFI_NOT_READY, waits again).
efi_wait_key:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = index, [rsp+40] = EFI_INPUT_KEY
.again:
  mov  rax, [rel g_bs]
  mov  ecx, 1
  mov  rdx, [rel g_conin]
  add  rdx, TXTIN_WaitForKey   ; &ConIn->WaitForKey
  lea  r8, [rsp+32]
  call [rax + BS_WaitForEvent]
  mov  rcx, [rel g_conin]
  lea  rdx, [rsp+40]
  call [rcx + TXTIN_ReadKeyStroke]
  mov  rdx, EFI_NOT_READY
  cmp  rax, rdx
  je   .again
  WINEPILOGUE
  ret

; ===========================================================================
; Console
; ===========================================================================

; rt_write_h(rcx=handle (1 or 2: both the screen), rdx=ptr, r8=len) — UTF-8
; bytes to ConOut as UCS-2 with "\r\n" line ends. The decoder state
; (g_utf_need/g_utf_cp) persists across calls because a token's bytes may end
; in the middle of a character; code points outside the BMP and malformed
; sequences print as '?'. Only tid 0 prints, so one output buffer suffices.
rt_write_h:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  mov  rsi, rdx                ; cursor
  lea  rdi, [rdx + r8]         ; end
  xor  r12d, r12d              ; CHAR16s buffered in g_outbuf
  mov  ebx, [rel g_utf_need]   ; continuation bytes still expected
  mov  r13d, [rel g_utf_cp]    ; code point under construction
.next:
  cmp  rsi, rdi
  jae  .end
  movzx eax, byte [rsi]
  inc  rsi
  cmp  eax, 0x80
  jb   .ascii
  cmp  eax, 0xC0
  jb   .cont
  test ebx, ebx                ; a lead byte while a sequence was open
  jz   .lead
  xor  ebx, ebx
  call .emit_q                 ; (clobbers eax)
  movzx eax, byte [rsi-1]      ; the lead byte again
.lead:
  cmp  eax, 0xE0
  jb   .l2
  cmp  eax, 0xF0
  jb   .l3
  cmp  eax, 0xF8
  jb   .l4
  call .emit_q                 ; not a UTF-8 byte at all
  jmp  .next
.l2:
  and  eax, 0x1F
  mov  r13d, eax
  mov  ebx, 1
  jmp  .next
.l3:
  and  eax, 0x0F
  mov  r13d, eax
  mov  ebx, 2
  jmp  .next
.l4:
  and  eax, 0x07
  mov  r13d, eax
  mov  ebx, 3
  jmp  .next
.cont:
  test ebx, ebx
  jnz  .cont_ok
  call .emit_q                 ; a continuation byte with no sequence open
  jmp  .next
.cont_ok:
  and  eax, 0x3F
  shl  r13d, 6
  or   r13d, eax
  dec  ebx
  jnz  .next
  mov  eax, r13d
  cmp  eax, 0x10000
  jb   .emit_cp
  mov  eax, '?'                ; beyond UCS-2
.emit_cp:
  call .emit
  jmp  .next
.ascii:
  test ebx, ebx
  jz   .asc2
  xor  ebx, ebx
  call .emit_q                 ; the open sequence was cut short (clobbers eax)
  movzx eax, byte [rsi-1]      ; the ASCII byte again
.asc2:
  cmp  eax, 10
  jne  .asc3
  mov  eax, 13
  call .emit
  mov  eax, 10
.asc3:
  call .emit
  jmp  .next
.end:
  mov  [rel g_utf_need], ebx
  mov  [rel g_utf_cp], r13d
  call .flush
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret
; local helpers: .emit(eax=CHAR16) buffers one unit, .flush writes the buffer
.emit_q:
  mov  eax, '?'
.emit:
  lea  rcx, [rel g_outbuf]
  mov  [rcx + r12*2], ax
  inc  r12
  cmp  r12, EFI_OUT_CHARS
  jb   .emit_ret
  call .flush
.emit_ret:
  ret
.flush:
  WINPROLOGUE                  ; own frame: callers arrive with either alignment
  test r12, r12
  jz   .flush_ret
  lea  rdx, [rel g_outbuf]
  mov  word [rdx + r12*2], 0
  mov  rcx, [rel g_conout]
  call [rcx + TXT_OutputString]
  xor  r12d, r12d
.flush_ret:
  WINEPILOGUE
  ret

; rt_read_stdin(rcx=&len) -> rax = buffer, *len = bytes. One line typed on the
; console: characters are echoed as typed, Backspace removes the last character,
; Enter ends the line (not stored), Esc returns an empty line. UTF-8 in the
; buffer (UCS-2 from the firmware); the buffer is g_line_buf, consumed before
; the next call. Keys the firmware queued before the first prompt (pressed while
; the model was loading) are discarded once, so a stray Enter during the load
; does not end the session; after that, typing ahead works as on any console.
; A ReadKeyStroke error other than EFI_NOT_READY ends the line.
rt_read_stdin:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32]=&len [rsp+40]=index [rsp+48]=EFI_INPUT_KEY
  mov  [rsp+32], rcx
  lea  rbx, [rel g_line_buf]   ; buffer
  xor  esi, esi                ; bytes so far
  cmp  qword [rel g_stdin_drained], 0
  jne  .key
  mov  qword [rel g_stdin_drained], 1
.drain:
  mov  rcx, [rel g_conin]
  lea  rdx, [rsp+48]
  call [rcx + TXTIN_ReadKeyStroke]
  test rax, rax
  jz   .drain                  ; a queued key: dropped; EFI_NOT_READY (or any error) ends the drain
.key:
  mov  rax, [rel g_bs]
  mov  ecx, 1
  mov  rdx, [rel g_conin]
  add  rdx, TXTIN_WaitForKey
  lea  r8, [rsp+40]
  call [rax + BS_WaitForEvent]
  mov  rcx, [rel g_conin]
  lea  rdx, [rsp+48]
  call [rcx + TXTIN_ReadKeyStroke]
  test rax, rax
  jz   .got
  mov  rdx, EFI_NOT_READY
  cmp  rax, rdx
  je   .key                    ; woken without a complete keystroke: wait again
  jmp  .enter                  ; the keyboard failed: end the line with what there is
.got:
  movzx eax, word [rsp+50]     ; UnicodeChar
  movzx edx, word [rsp+48]     ; ScanCode
  cmp  edx, SCAN_ESC
  je   .esc
  cmp  eax, 13
  je   .enter
  cmp  eax, 10
  je   .enter
  cmp  eax, 8
  je   .bs
  test eax, eax
  jz   .key                    ; a key without a character (arrows, F-keys)
  cmp  eax, 0x80
  jb   .one
  cmp  eax, 0x800
  jb   .two
  lea  rcx, [rsi + 3]          ; three UTF-8 bytes
  cmp  rcx, EFI_LINE_BYTES
  ja   .key
  mov  ecx, eax
  shr  ecx, 12
  or   ecx, 0xE0
  mov  [rbx + rsi], cl
  mov  ecx, eax
  shr  ecx, 6
  and  ecx, 0x3F
  or   ecx, 0x80
  mov  [rbx + rsi + 1], cl
  mov  ecx, eax
  and  ecx, 0x3F
  or   ecx, 0x80
  mov  [rbx + rsi + 2], cl
  mov  edi, 3
  jmp  .echo
.two:
  lea  rcx, [rsi + 2]
  cmp  rcx, EFI_LINE_BYTES
  ja   .key
  mov  ecx, eax
  shr  ecx, 6
  or   ecx, 0xC0
  mov  [rbx + rsi], cl
  mov  ecx, eax
  and  ecx, 0x3F
  or   ecx, 0x80
  mov  [rbx + rsi + 1], cl
  mov  edi, 2
  jmp  .echo
.one:
  lea  rcx, [rsi + 1]
  cmp  rcx, EFI_LINE_BYTES
  ja   .key
  mov  [rbx + rsi], al
  mov  edi, 1
.echo:
  lea  rcx, [rbx + rsi]
  mov  rdx, rdi
  call rt_out                  ; echo the new bytes
  add  rsi, rdi
  jmp  .key
.bs:
  test rsi, rsi
  jz   .key
.bsl:                           ; drop one code point: its continuation bytes, then its first byte
  dec  rsi
  test rsi, rsi
  jz   .bsecho
  movzx eax, byte [rbx + rsi]
  and  eax, 0xC0
  cmp  eax, 0x80
  je   .bsl
.bsecho:
  lea  rcx, [rel e_s_bs]
  mov  edx, 3
  call rt_out
  jmp  .key
.esc:
  xor  esi, esi
.enter:
  lea  rcx, [rel k_newline]
  mov  edx, 1
  call rt_out
  mov  rax, [rsp+32]
  mov  [rax], rsi
  mov  rax, rbx
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; Memory
; ===========================================================================

; efi_pages(rcx=bytes) -> rax = base. AllocatePages(AnyPages, EfiLoaderData),
; rounded up to whole pages, not cleared. Fatal when the firmware has no room.
efi_pages:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = address (out)
  mov  r8, rcx
  add  r8, 4095
  shr  r8, 12                  ; pages
  jnz  .nz
  mov  r8d, 1
.nz:
  mov  rax, [rel g_bs]
  mov  ecx, AllocateAnyPages
  mov  edx, EfiLoaderData
  lea  r9, [rsp+32]
  mov  qword [rsp+32], 0
  call [rax + BS_AllocatePages]
  EFI_CHECK e_err_alloc
  mov  rax, [rsp+32]
  WINEPILOGUE
  ret

; rt_alloc(rcx=bytes) -> rax = zero-filled base (pages are not cleared by the
; firmware; the modules rely on zeroed memory as on Windows).
rt_alloc:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = bytes, [rsp+40] = base
  mov  [rsp+32], rcx
  call efi_pages
  mov  [rsp+40], rax
  mov  rcx, rax
  xor  edx, edx
  mov  r8, [rsp+32]
  add  r8, 4095
  and  r8, -4096
  call rt_memset
  mov  rax, [rsp+40]
  WINEPILOGUE
  ret

; ===========================================================================
; Files on the boot volume
; ===========================================================================

; efi_open_root — g_root = the root directory of the volume this image was
; loaded from: LoadedImage.DeviceHandle -> SimpleFileSystem -> OpenVolume.
efi_open_root:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = interface (out)
  mov  rax, [rel g_bs]
  mov  rcx, [rel g_image_handle]
  lea  rdx, [rel e_guid_loaded_image]
  lea  r8, [rsp+32]
  call [rax + BS_HandleProtocol]
  EFI_CHECK e_err_loaded_image
  mov  rax, [rsp+32]           ; EFI_LOADED_IMAGE_PROTOCOL*
  mov  rcx, [rax + LI_DeviceHandle]
  mov  rax, [rel g_bs]
  lea  rdx, [rel e_guid_sfs]
  lea  r8, [rsp+32]
  call [rax + BS_HandleProtocol]
  EFI_CHECK e_err_sfs
  mov  rcx, [rsp+32]           ; EFI_SIMPLE_FILE_SYSTEM_PROTOCOL*
  lea  rdx, [rel g_root]
  call [rcx + SFS_OpenVolume]
  EFI_CHECK e_err_openvol
  lea  rcx, [rel e_s_volume]
  call rt_err_str
  WINEPILOGUE
  ret

; efi_open(rcx=path utf16) -> rax = EFI_FILE_PROTOCOL* or 0 (status then in
; g_last_status). The path is copied to g_path with '/' turned into '\' and
; opened relative to the volume root.
efi_open:
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32] = Attributes (arg 5), [rsp+48] = handle (out)
  lea  rdx, [rel g_path]
  xor  r8d, r8d
.cp:
  movzx eax, word [rcx + r8*2]
  cmp  eax, '/'
  jne  .nosl
  mov  eax, '\'
.nosl:
  mov  [rdx + r8*2], ax
  test eax, eax
  jz   .cpd
  inc  r8
  cmp  r8, EFI_PATH_CHARS - 1
  jb   .cp
  mov  word [rdx + r8*2], 0
.cpd:
  mov  qword [rsp+48], 0
  mov  rcx, [rel g_root]
  lea  rdx, [rsp+48]           ; &NewHandle
  lea  r8, [rel g_path]        ; FileName
  mov  r9d, EFI_FILE_MODE_READ ; OpenMode
  mov  qword [rsp+32], 0       ; Attributes
  call [rcx + FP_Open]
  mov  [rel g_last_status], rax
  test rax, rax
  jnz  .fail
  mov  rax, [rsp+48]
  WINEPILOGUE
  ret
.fail:
  xor  eax, eax
  WINEPILOGUE
  ret

; efi_file_size(rcx=file) -> rax = FileSize via GetInfo(EFI_FILE_INFO).
efi_file_size:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = buffer size (in/out)
  mov  qword [rsp+32], EFI_FINFO_BYTES
  lea  rdx, [rel e_guid_file_info]
  lea  r8, [rsp+32]
  lea  r9, [rel g_finfo]
  call [rcx + FP_GetInfo]
  EFI_CHECK e_err_getinfo
  mov  rax, [rel g_finfo + FI_FileSize]
  WINEPILOGUE
  ret

; efi_read_all(rcx=file, rdx=size, r8=buf) — read the whole file in
; EFI_READ_CHUNK pieces. Files of EFI_PROGRESS_MIN or more print a progress
; line (a dot per 32 MB) and the rate, since a USB stick through the firmware
; takes a while for a model.
efi_read_all:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32] = chunk (in/out), [rsp+40] = t0
  mov  rbx, rcx                ; file
  mov  rsi, rdx                ; remaining
  mov  rdi, r8                 ; cursor
  mov  r12, rdx                ; total
  xor  r13d, r13d              ; read so far
  cmp  r12, EFI_PROGRESS_MIN
  jb   .nohead
  lea  rcx, [rel e_s_reading]
  call rt_err_str
  mov  rcx, r12
  shr  rcx, 20
  call rt_err_u64
  lea  rcx, [rel e_s_mb]
  call rt_err_str
.nohead:
  call rt_now_ns
  mov  [rsp+40], rax
.loop:
  test rsi, rsi
  jz   .done
  mov  rax, rsi
  cmp  rax, EFI_READ_CHUNK
  jbe  .sz
  mov  rax, EFI_READ_CHUNK
.sz:
  mov  [rsp+32], rax
  mov  rcx, rbx
  lea  rdx, [rsp+32]
  mov  r8, rdi
  call [rbx + FP_Read]
  EFI_CHECK e_err_read
  mov  rax, [rsp+32]
  test rax, rax
  jz   .short
  add  rdi, rax
  sub  rsi, rax
  cmp  r12, EFI_PROGRESS_MIN
  jb   .loop
  mov  rcx, r13
  shr  rcx, 25                 ; 32 MB units before
  add  r13, rax
  mov  rdx, r13
  shr  rdx, 25                 ; and after
  cmp  rcx, rdx
  je   .loop
  lea  rcx, [rel e_s_dot]
  mov  edx, 1
  call rt_err
  jmp  .loop
.done:
  cmp  r12, EFI_PROGRESS_MIN
  jb   .ret
  call rt_now_ns
  sub  rax, [rsp+40]           ; ns
  xor  edx, edx
  mov  ecx, 1000000
  div  rcx                     ; ms
  test rax, rax
  jnz  .msok
  mov  eax, 1
.msok:
  mov  rcx, rax
  mov  rax, r12
  shr  rax, 20                 ; MB
  imul rax, rax, 1000
  xor  edx, edx
  div  rcx                     ; MB/s
  mov  r13, rax
  lea  rcx, [rel e_s_done]
  call rt_err_str
  mov  rcx, r13
  call rt_err_u64
  lea  rcx, [rel e_s_mbs_nl]
  call rt_err_str
.ret:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.short:
  lea  rcx, [rel e_err_short]
  call rt_fatal

; rt_map_file(rcx=path utf16, rdx=&size) -> rax = base. The whole file read into
; pages (not cleared: the read fills them). Fatal, naming the file, if it is
; not on the boot volume.
rt_map_file:
  push rbx
  push rsi
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = size, [rsp+40] = base
  mov  rsi, rdx                ; &size
  mov  rbx, rcx                ; path (for the error message)
  call efi_open
  test rax, rax
  jz   .nofile
  mov  rbx, rax                ; file
  mov  rcx, rax
  call efi_file_size
  mov  [rsi], rax
  mov  [rsp+32], rax
  mov  rcx, rax
  call efi_pages
  mov  [rsp+40], rax
  mov  rcx, rbx
  mov  rdx, [rsp+32]
  mov  r8, rax
  call efi_read_all
  mov  rcx, rbx
  call [rbx + FP_Close]
  mov  rax, [rsp+40]
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret
.nofile:
  lea  rcx, [rel k_fatal_prefix]
  mov  rdx, FATAL_PREFIX_LEN
  call rt_err
  lea  rcx, [rel e_s_cannot_open]
  call rt_err_str
  mov  rcx, rbx                ; the UTF-16 path as UTF-8, g_finfo as scratch
  lea  rdx, [rel g_finfo]
  mov  r8d, EFI_FINFO_BYTES - 1
  call rt_utf16_to_utf8
  lea  rcx, [rel g_finfo]
  mov  rdx, rax
  call rt_err
  lea  rcx, [rel e_s_status]
  call rt_err_str
  mov  rcx, [rel g_last_status]
  call rt_err_hex64
  lea  rcx, [rel e_s_paren_nl]
  call rt_err_str
  mov  ecx, 1
  call rt_exit
  hlt

; rt_read_file(rcx=path utf16, rdx=&len) -> rax = ptr. Same as rt_map_file.
rt_read_file:
  jmp  rt_map_file

; efi_read_optional(rcx=path utf16, rdx=&len) -> rax = ptr (zero-terminated
; copy) or 0 when the file does not exist.
efi_read_optional:
  push rbx
  push rsi
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = size, [rsp+40] = buffer
  mov  rsi, rdx
  call efi_open
  test rax, rax
  jz   .none
  mov  rbx, rax
  mov  rcx, rax
  call efi_file_size
  mov  [rsi], rax
  mov  [rsp+32], rax
  lea  rcx, [rax + 1]
  call rt_alloc
  mov  [rsp+40], rax
  mov  rcx, rbx
  mov  rdx, [rsp+32]
  mov  r8, rax
  call efi_read_all
  mov  rcx, rbx
  call [rbx + FP_Close]
  mov  rax, [rsp+40]
  jmp  .ret
.none:
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; efi_build_cmdline -> rax = argc (g_argv filled). The command line is "asmrun "
; followed by \asmrun.cfg (ASCII; CR, LF and TAB count as spaces; a UTF-8 BOM
; is skipped; other non-ASCII bytes become '?'), or "asmrun model.gguf -i" when
; there is no cfg. Converted to UTF-16 so main's parser and rt_map_file see the
; same argument vector as on Windows.
efi_build_cmdline:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32]=cfg len [rsp+40]=UTF-16 buffer [rsp+48]=cfg bytes
  lea  rcx, [rel e_w_cfg]
  lea  rdx, [rsp+32]
  call efi_read_optional
  test rax, rax
  jnz  .have
  lea  rcx, [rel e_s_nocfg]
  call rt_err_str              ; (clobbers rax: the default is loaded after it)
  lea  rax, [rel e_default_args]
  mov  qword [rsp+32], E_DEFAULT_ARGS_LEN
.have:
  mov  [rsp+48], rax
  lea  rcx, [rel e_s_args]
  call rt_err_str
  mov  rcx, [rsp+48]
  mov  rdx, [rsp+32]
  call rt_err
  lea  rcx, [rel k_newline]
  mov  edx, 1
  call rt_err
  mov  rsi, [rsp+48]           ; cfg bytes
  mov  rbx, [rsp+32]           ; len
  lea  rcx, [rbx + 16]
  shl  rcx, 1                  ; CHAR16s: "asmrun ", the cfg, a NUL
  call rt_alloc
  mov  [rsp+40], rax
  mov  rdi, rax
  lea  rdx, [rel e_w_prog]     ; L"asmrun "
  xor  ecx, ecx
.pre:
  movzx eax, word [rdx + rcx*2]
  test eax, eax
  jz   .pred
  mov  [rdi], ax
  add  rdi, 2
  inc  ecx
  jmp  .pre
.pred:
  cmp  rbx, 3                  ; skip a UTF-8 BOM
  jb   .body
  cmp  byte [rsi], 0xEF
  jne  .body
  cmp  byte [rsi+1], 0xBB
  jne  .body
  cmp  byte [rsi+2], 0xBF
  jne  .body
  add  rsi, 3
  sub  rbx, 3
.body:
  test rbx, rbx
  jz   .fin
  movzx eax, byte [rsi]
  inc  rsi
  dec  rbx
  cmp  eax, 9
  je   .sp
  cmp  eax, 10
  je   .sp
  cmp  eax, 13
  je   .sp
  cmp  eax, 0x80
  jb   .put
  cmp  eax, 0xC0
  jb   .body                   ; a UTF-8 continuation byte: dropped
  mov  eax, '?'                ; a non-ASCII character becomes '?'
  jmp  .put
.sp:
  mov  eax, ' '
.put:
  mov  [rdi], ax
  add  rdi, 2
  jmp  .body
.fin:
  mov  word [rdi], 0
  mov  rcx, [rsp+40]
  call rt_parse_cmdline        ; -> rax = argc
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; Clock and processors
; ===========================================================================

; rt_now_ns() -> rax = nanoseconds: rdtsc * 1e9 / g_tsc_hz in 128-bit.
; Clobbers rax, rcx, rdx.
rt_now_ns:
  rdtsc
  shl  rdx, 32
  or   rax, rdx
  mov  rcx, 1000000000
  mul  rcx
  mov  rcx, [rel g_tsc_hz]
  div  rcx
  ret

; efi_mp() -> rax = EFI_MP_SERVICES_PROTOCOL* or 0 (looked up once).
efi_mp:
  WINPROLOGUE
  cmp  qword [rel g_mp_tried], 0
  jne  .cached
  mov  qword [rel g_mp_tried], 1
  mov  rax, [rel g_bs]
  lea  rcx, [rel e_guid_mp]
  xor  edx, edx
  lea  r8, [rel g_mp]
  call [rax + BS_LocateProtocol]
  test rax, rax
  jz   .cached
  mov  qword [rel g_mp], 0
.cached:
  mov  rax, [rel g_mp]
  WINEPILOGUE
  ret

; rt_cpu_count() -> rax = enabled processors per EFI_MP_SERVICES_PROTOCOL, or 1.
rt_cpu_count:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = total, [rsp+40] = enabled
  call efi_mp
  test rax, rax
  jz   .one
  mov  rcx, rax
  lea  rdx, [rsp+32]
  lea  r8, [rsp+40]
  call [rcx + MP_GetNumberOfProcessors]
  test rax, rax
  jnz  .one
  mov  rax, [rsp+40]
  test rax, rax
  jz   .one
  WINEPILOGUE
  ret
.one:
  mov  eax, 1
  WINEPILOGUE
  ret

; pool_start(rcx=nthreads) — g_nthreads = nthreads (at least 1, at most
; MAX_THREADS, no more than the enabled processors), then for tid 1.. start
; pool_ap_entry(tid) on an application processor through StartupThisAP in
; non-blocking mode (a wait event, no timeout), each with its own 1 MiB stack,
; and wait until every one has checked in. Without EFI_MP_SERVICES_PROTOCOL the
; pool is the boot processor alone. A processor the firmware will not start is
; reported and the pool is the ones started so far; fatal only if a started
; processor does not check in within 5 s.
pool_start:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  sub  rsp, 160                ; [32]=Timeout [40]=Argument [48]=&Finished (StartupThisAP args 5-7)
                               ; [56]=total [64]=enabled [72]=bsp [80]=event [96..160)=EFI_PROCESSOR_INFORMATION
  mov  rbx, rcx                ; threads wanted
  test rbx, rbx
  jnz  .nz
  mov  ebx, 1                  ; -t 0 would leave pool_run waiting for -1 workers
.nz:
  cmp  rbx, MAX_THREADS
  jbe  .cap
  mov  ebx, MAX_THREADS        ; the per-tid tables have this many slots
.cap:
  mov  [rel g_nthreads], rbx
  mov  qword [rel g_pool_gen], 0
  mov  qword [rel g_pool_done], 0
  mov  qword [rel g_pool_quit], 0
  cmp  rbx, 1
  jbe  .done
  call efi_mp
  test rax, rax
  jz   .nomp
  mov  r12, rax                ; the protocol
  mov  rcx, r12
  lea  rdx, [rsp+56]
  lea  r8, [rsp+64]
  call [r12 + MP_GetNumberOfProcessors]
  EFI_CHECK e_err_mpcount
  mov  rcx, r12
  lea  rdx, [rsp+72]
  call [r12 + MP_WhoAmI]
  EFI_CHECK e_err_whoami
  mov  rax, [rsp+64]
  cmp  rbx, rax
  jbe  .ncap
  mov  rbx, rax                ; no more threads than enabled processors
  mov  [rel g_nthreads], rbx
  cmp  rbx, 1
  jbe  .done
.ncap:
  mov  rsi, 1                  ; next tid
  xor  edi, edi                ; candidate processor number
.ap:
  cmp  rsi, rbx
  jae  .started
  cmp  rdi, [rsp+56]
  jae  .fewer                  ; out of processors
  cmp  rdi, [rsp+72]
  je   .skip                   ; the boot processor runs tid 0 itself
  mov  rcx, r12
  mov  rdx, rdi
  lea  r8, [rsp+96]
  call [r12 + MP_GetProcessorInfo]
  test rax, rax
  jnz  .skip
  mov  eax, [rsp+96 + PI_StatusFlag]
  and  eax, PROCESSOR_ENABLED_BIT | PROCESSOR_HEALTH_STATUS_BIT
  cmp  eax, PROCESSOR_ENABLED_BIT | PROCESSOR_HEALTH_STATUS_BIT
  jne  .skip
  mov  rcx, AP_STACK_BYTES     ; this worker's stack
  call efi_pages
  add  rax, AP_STACK_BYTES
  lea  rcx, [rel g_ap_stack_top]
  mov  [rcx + rsi*8], rax
  lea  rcx, [rel g_ap_proc]
  mov  [rcx + rsi*8], rdi
  mov  rax, [rel g_bs]         ; the event non-blocking StartupThisAP requires
  xor  ecx, ecx
  xor  edx, edx
  xor  r8d, r8d
  xor  r9d, r9d
  lea  r10, [rsp+80]
  mov  [rsp+32], r10           ; &Event
  call [rax + BS_CreateEvent]
  test rax, rax
  jnz  .apfail
  mov  rcx, r12
  lea  rdx, [rel pool_ap_entry]
  mov  r8, rdi                 ; ProcessorNumber
  mov  r9, [rsp+80]            ; WaitEvent
  mov  qword [rsp+32], 0       ; TimeoutInMicroseconds: none
  mov  [rsp+40], rsi           ; ProcedureArgument = tid
  mov  qword [rsp+48], 0       ; Finished: not wanted
  call [r12 + MP_StartupThisAP]
  test rax, rax
  jnz  .apfail
  inc  qword [rel g_ap_started] ; rt_exit waits for this many to come back
  inc  rsi
.skip:
  inc  rdi
  jmp  .ap
.apfail:
  ; the firmware would not start this processor: say so and run with the
  ; workers started so far (nothing was started for this tid)
  mov  rdx, rax
  lea  rcx, [rel e_s_apfail]
  call efi_warn_status
.fewer:
  mov  rbx, rsi
  mov  [rel g_nthreads], rbx
.started:
  mov  r13d, 5000              ; wait up to 5 s for the check-ins
.waitap:
  mov  rax, [rel g_ap_ready]
  lea  rcx, [rbx - 1]
  cmp  rax, rcx
  jae  .ready
  mov  rax, [rel g_bs]
  mov  ecx, 1000
  call [rax + BS_Stall]
  dec  r13d
  jnz  .waitap
  lea  rcx, [rel e_err_apstart]
  cmp  qword [rel g_ap_noavx], 0
  je   .apfatal
  lea  rcx, [rel e_err_apnoavx]
.apfatal:
  call rt_fatal
.ready:
  lea  rcx, [rel e_s_cpus1]
  call rt_err_str
  mov  rcx, rbx
  call rt_err_u64
  lea  rcx, [rel e_s_cpus2]
  call rt_err_str
  lea  rcx, [rbx - 1]
  call rt_err_u64
  lea  rcx, [rel e_s_cpus3]
  call rt_err_str
  jmp  .done
.nomp:
  mov  qword [rel g_nthreads], 1
  lea  rcx, [rel e_s_nomp]
  call rt_err_str
.done:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; pool_ap_entry(rcx=tid) — EFI_AP_PROCEDURE run by the firmware on an
; application processor. Saves the firmware's nonvolatile registers, MXCSR, CR4
; and XCR0, enables AVX state on this core, asks it for its highest clock
; (efi_cpu_perf), moves to the worker's own stack, checks in and runs
; pool_worker until g_pool_quit; then puts everything back and returns to the
; firmware on its stack, so the processor is the firmware's again when we leave.
; A core that cannot enable AVX state (not on a homogeneous machine) records it
; in g_ap_noavx and returns at once without checking in; pool_start reports it.
pool_ap_entry:
  push rbx
  push rbp
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  sub  rsp, 168                ; xmm6-15 (160 bytes) + MXCSR (4 bytes) + alignment
  movups [rsp+0],   xmm6
  movups [rsp+16],  xmm7
  movups [rsp+32],  xmm8
  movups [rsp+48],  xmm9
  movups [rsp+64],  xmm10
  movups [rsp+80],  xmm11
  movups [rsp+96],  xmm12
  movups [rsp+112], xmm13
  movups [rsp+128], xmm14
  movups [rsp+144], xmm15
  stmxcsr [rsp+160]
  mov  r12, rcx                ; tid
  lea  rax, [rel g_ap_fw_rsp]
  mov  [rax + r12*8], rsp
  call rt_enable_avx           ; eax = 1 if enabled; rdx = CR4 before, r8 = XCR0 before
  test eax, eax
  jz   .noavx
  lea  rax, [rel g_ap_fw_cr4]
  mov  [rax + r12*8], rdx
  lea  rax, [rel g_ap_fw_xcr0]
  mov  [rax + r12*8], r8
  ldmxcsr [rel k_mxcsr_init]
  call efi_cpu_perf            ; this core at its highest performance level
  lea  rax, [rel g_ap_stack_top]
  mov  rsp, [rax + r12*8]      ; own stack (16-byte aligned top)
  lock inc qword [rel g_ap_ready]
  mov  rcx, r12
  call pool_worker             ; returns when g_pool_quit is set
  vzeroupper
  lea  rax, [rel g_ap_fw_rsp]
  mov  rsp, [rax + r12*8]
  ldmxcsr [rsp+160]
  movups xmm6,  [rsp+0]
  movups xmm7,  [rsp+16]
  movups xmm8,  [rsp+32]
  movups xmm9,  [rsp+48]
  movups xmm10, [rsp+64]
  movups xmm11, [rsp+80]
  movups xmm12, [rsp+96]
  movups xmm13, [rsp+112]
  movups xmm14, [rsp+128]
  movups xmm15, [rsp+144]
  lea  rax, [rel g_ap_fw_xcr0] ; XCR0, then CR4, as the firmware had them
  mov  rax, [rax + r12*8]
  mov  rdx, rax
  shr  rdx, 32
  xor  ecx, ecx
  xsetbv
  lea  rax, [rel g_ap_fw_cr4]
  mov  rax, [rax + r12*8]
  mov  cr4, rax
.out:
  add  rsp, 168
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbp
  pop  rbx
  lock inc qword [rel g_ap_exited]
  ret
.noavx:
  mov  qword [rel g_ap_noavx], 1
  jmp  .out                    ; nothing changed on this core; still on the firmware's stack

; ===========================================================================
; Globals, GUIDs and strings of the UEFI half
; ===========================================================================
section .data
  align 8

g_image_handle: dq 0
g_systab:       dq 0           ; EFI_SYSTEM_TABLE*
g_bs:           dq 0           ; EFI_BOOT_SERVICES*
g_rs:           dq 0           ; EFI_RUNTIME_SERVICES*
g_conout:       dq 0           ; EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL*
g_conin:        dq 0           ; EFI_SIMPLE_TEXT_INPUT_PROTOCOL*
g_root:         dq 0           ; EFI_FILE_PROTOCOL* of the boot volume's root
g_mp:           dq 0           ; EFI_MP_SERVICES_PROTOCOL* or 0
g_mp_tried:     dq 0           ; 1 once LocateProtocol has been asked
g_tsc_hz:       dq 1           ; TSC ticks per second (measured at start)
g_last_status:  dq 0           ; status of the last efi_open
g_exit_code:    dq 0
g_avx_on:       dq 0           ; 1 once AVX state is enabled on the boot processor
g_stdin_drained: dq 0          ; 1 once rt_read_stdin has dropped the keys queued before its first prompt
g_utf_need:     dd 0           ; rt_write_h decoder: continuation bytes expected
g_utf_cp:       dd 0           ;                     code point under construction
k_mxcsr_init:   dd 0x1F80      ; MXCSR for our code: round to nearest, all exceptions masked

g_fw_rsp:       dq 0           ; the firmware's registers at _entry
g_fw_rbx:       dq 0
g_fw_rbp:       dq 0
g_fw_rsi:       dq 0
g_fw_rdi:       dq 0
g_fw_r12:       dq 0
g_fw_r13:       dq 0
g_fw_r14:       dq 0
g_fw_r15:       dq 0
g_fw_cr4:       dq 0           ; the firmware's CR4 and XCR0 on the boot processor
g_fw_xcr0:      dq 0
g_fw_mxcsr:     dd 0
  align 16
g_fw_xmm:       times 20 dq 0  ; xmm6-15

  align 64                     ; the processors' check-in counters, on a line of their own
g_ap_ready:     dq 0           ; APs that reached pool_worker
g_ap_exited:    dq 0           ; APs that returned to the firmware
g_ap_started:   dq 0           ; APs StartupThisAP accepted (counted on the boot processor)
g_ap_noavx:     dq 0           ; 1 if an AP could not enable AVX state (it returned at once)

  align 64
g_ap_stack_top: times MAX_THREADS dq 0  ; per tid: top of the worker's stack
g_ap_fw_rsp:    times MAX_THREADS dq 0  ; per tid: the firmware's rsp inside pool_ap_entry
g_ap_proc:      times MAX_THREADS dq 0  ; per tid: processor number
g_ap_fw_cr4:    times MAX_THREADS dq 0  ; per tid: the firmware's CR4 on that processor
g_ap_fw_xcr0:   times MAX_THREADS dq 0  ; per tid: its XCR0

g_path:         times EFI_PATH_CHARS dw 0
g_finfo:        times EFI_FINFO_BYTES db 0
g_outbuf:       times (EFI_OUT_CHARS + 2) dw 0
g_line_buf:     times EFI_LINE_BYTES db 0  ; rt_read_stdin's line

  align 8
e_guid_loaded_image:           ; EFI_LOADED_IMAGE_PROTOCOL 5B1B31A1-9562-11D2-8E3F-00A0C969723B
  dd 0x5B1B31A1
  dw 0x9562, 0x11D2
  db 0x8E, 0x3F, 0x00, 0xA0, 0xC9, 0x69, 0x72, 0x3B
e_guid_sfs:                    ; EFI_SIMPLE_FILE_SYSTEM_PROTOCOL 964E5B22-6459-11D2-8E39-00A0C969723B
  dd 0x964E5B22
  dw 0x6459, 0x11D2
  db 0x8E, 0x39, 0x00, 0xA0, 0xC9, 0x69, 0x72, 0x3B
e_guid_file_info:              ; EFI_FILE_INFO_ID 09576E92-6D3F-11D2-8E39-00A0C969723B
  dd 0x09576E92
  dw 0x6D3F, 0x11D2
  db 0x8E, 0x39, 0x00, 0xA0, 0xC9, 0x69, 0x72, 0x3B
e_guid_mp:                     ; EFI_MP_SERVICES_PROTOCOL 3FDDA605-A76E-4F46-AD29-12F4531B3D08
  dd 0x3FDDA605
  dw 0xA76E, 0x4F46
  db 0xAD, 0x29, 0x12, 0xF4, 0x53, 0x1B, 0x3D, 0x08

e_w_cfg:        dw __utf16__("asmrun.cfg"), 0
e_w_prog:       dw __utf16__("asmrun "), 0
e_default_args: db "model.gguf -i"
E_DEFAULT_ARGS_LEN equ $ - e_default_args

e_s_banner:     db "asmrun: UEFI build, no operating system (firmware boot services only)", 10, 0
e_s_avx:        db "asmrun: AVX state enabled on the boot processor", 10, 0
e_s_tsc:        db "asmrun: tsc_mhz=", 0
e_s_tsc_cpuid:  db " (cpuid says ", 0
e_s_tsc_bad:    db "asmrun: the TSC measured against Stall is implausible (", 0
e_s_tsc_bad2:   db " MHz): the CPUID.15H value is used", 10, 0
e_s_clock1:     db "asmrun: cpu clock: ", 0
e_s_clock2:     db " MHz before, ", 0
e_s_clock3:     db " MHz now (boot processor, APERF/MPERF over 50 ms)", 10, 0
e_s_hwp_enabled: db "asmrun: hwp enabled here", 0
e_s_hwp_on:     db "asmrun: hwp was already on", 0
e_s_hwp1:       db "; performance levels lowest ", 0
e_s_hwp2:       db ", guaranteed ", 0
e_s_hwp3:       db ", highest ", 0
e_s_hwp4:       db "; every processor asks for the highest (EPP 0)", 10, 0
e_s_nohwp:      db "asmrun: no hardware P-states (HWP) on this CPU: the clock stays as the firmware set it", 10, 0
e_s_volume:     db "asmrun: boot volume opened", 10, 0
e_s_nocfg:      db "asmrun: no asmrun.cfg on the boot volume: using \model.gguf -i", 10, 0
e_s_args:       db "asmrun: args: ", 0
e_s_reading:    db "asmrun: reading ", 0
e_s_mb:         db " MB from the boot volume ", 0
e_s_dot:        db "."
e_s_done:       db " done, ", 0
e_s_mbs_nl:     db " MB/s", 10, 0
e_s_cpus1:      db "asmrun: processors=", 0
e_s_cpus2:      db " (the boot processor and ", 0
e_s_cpus3:      db " application processors through EFI_MP_SERVICES_PROTOCOL)", 10, 0
e_s_nomp:       db "asmrun: EFI_MP_SERVICES_PROTOCOL not found: running on the boot processor only", 10, 0
e_s_exit1:      db 10, "asmrun: exit code ", 0
e_s_exit2:      db ". Press any key to return to the firmware.", 10, 0
e_s_stuck1:     db ": ", 0
e_s_stuck2:     db " worker processor(s) still run asmrun code and cannot be given back to the firmware. Press any key to reset the machine.", 10, 0
e_s_status:     db " (EFI status ", 0
e_s_paren_nl:   db ")", 10, 0
e_s_cannot_open: db "cannot open ", 0
e_s_bs:         db 8, ' ', 8

e_err_xsave:        db "this CPU has no XSAVE or no AVX: AVX state cannot be enabled", 0
e_err_tsc:          db "the TSC frequency could not be determined (Stall returned early and CPUID.15H is absent)", 0
e_err_loaded_image: db "EFI_LOADED_IMAGE_PROTOCOL not found on our image handle", 0
e_err_sfs:          db "no simple file system on the boot device", 0
e_err_openvol:      db "OpenVolume failed", 0
e_err_getinfo:      db "GetInfo(EFI_FILE_INFO) failed", 0
e_err_alloc:        db "AllocatePages failed (out of memory)", 0
e_err_read:         db "file read failed", 0
e_err_short:        db "file read ended before the end of the file", 0
e_err_mpcount:      db "GetNumberOfProcessors failed", 0
e_err_whoami:       db "WhoAmI failed", 0
e_s_apfail:         db "the firmware would not start an application processor; running with the processors started so far", 0
e_err_apstart:      db "the application processors did not start running the pool", 0
e_err_apnoavx:      db "an application processor could not enable AVX state (XSAVE/AVX missing on that core)", 0

section .text

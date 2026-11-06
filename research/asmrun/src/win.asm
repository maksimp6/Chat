; win.asm — the Windows half of the runtime (runtime.asm has the shared half and
; the list of names both halves implement; efi.asm is the other implementation).
;
; %included into asmrun.asm after runtime.asm (bits 64, default rel, .text
; current). Code stays in .text; the globals at the end switch to .data.
;
;   _entry         PE entry: console handles, QPC frequency, GetCommandLineW ->
;                  g_argv, main(argc, argv), rt_exit
;   rt_write_h     WriteFile(handle, ptr, len)
;   rt_exit        ExitProcess(code)
;   rt_alloc       zeroed VirtualAlloc
;   rt_map_file    CreateFileW + CreateFileMappingW + MapViewOfFile (read-only view)
;   rt_read_file   the same (a read-only mapping serves a whole-file read)
;   rt_read_stdin  ReadFile(STD_INPUT_HANDLE) into a doubling buffer until EOF
;   rt_now_ns      QueryPerformanceCounter scaled by the cached frequency
;   rt_cpu_count   GetSystemInfo.dwNumberOfProcessors
;   pool_start     CreateThread x (nthreads-1) running pool_worker
;
; Imports used (all from inc/pe.inc's kernel32 IAT): GetStdHandle, WriteFile,
; ReadFile, ExitProcess, VirtualAlloc, CreateFileW, CreateFileMappingW,
; MapViewOfFile, CloseHandle, GetFileSizeEx, QueryPerformanceCounter,
; QueryPerformanceFrequency, GetCommandLineW, GetSystemInfo, CreateThread.

; ===========================================================================
; Entry point
; ===========================================================================

; _entry — PE entry. Caches stdout/stderr/stdin handles and the QPC frequency,
; parses GetCommandLineW into g_argv, calls main(rcx=argc, rdx=argv), and exits
; with its eax. The OS calls us with rsp%16 == 8 (return address on the stack).
_entry:
  push rbp
  mov  rbp, rsp
  sub  rsp, 32                 ; 32-byte call shadow, keeps rsp 16-aligned

  mov  ecx, STD_OUTPUT_HANDLE
  call winimp(GetStdHandle)
  mov  [rel g_stdout], rax
  mov  ecx, STD_ERROR_HANDLE
  call winimp(GetStdHandle)
  mov  [rel g_stderr], rax
  mov  ecx, STD_INPUT_HANDLE
  call winimp(GetStdHandle)
  mov  [rel g_stdin], rax

  lea  rcx, [rel g_qpc_freq]
  call winimp(QueryPerformanceFrequency)

  call winimp(GetCommandLineW) ; rax -> UTF-16 command line (writable)
  mov  rcx, rax
  call rt_parse_cmdline        ; -> rax = argc, fills g_argv

  mov  rcx, rax                ; argc
  lea  rdx, [rel g_argv]       ; argv
  call main                    ; defined in model.asm

  mov  ecx, eax                ; exit code = main's return
  call rt_exit
  hlt                          ; unreachable

; rt_exit(rcx=code) — ExitProcess. Never returns.
rt_exit:
  WINPROLOGUE
  call winimp(ExitProcess)
  hlt

; ===========================================================================
; Console output
; ===========================================================================

; rt_write_h(rcx=handle, rdx=ptr, r8=len) — WriteFile all len bytes once.
; Internal; callers supply the handle. Clobbers rax,rcx,rdx,r8-r11.
rt_write_h:
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32]=lpOverlapped(0), [rsp+40]=bytesWritten
  lea  r9, [rsp+40]            ; lpNumberOfBytesWritten
  mov  qword [rsp+32], 0       ; lpOverlapped = NULL
  call winimp(WriteFile)       ; rcx=handle, rdx=buf, r8=len already in place
  WINEPILOGUE
  ret

; ===========================================================================
; Memory
; ===========================================================================

; rt_alloc(rcx=bytes) -> rax = base. VirtualAlloc(NULL, bytes, COMMIT|RESERVE,
; PAGE_RW); memory is zero-filled by the OS. rt_fatal on failure.
rt_alloc:
  WINPROLOGUE
  mov  rdx, rcx                ; dwSize
  xor  rcx, rcx                ; lpAddress = NULL
  mov  r8d, MEM_COMMIT | MEM_RESERVE
  mov  r9d, PAGE_RW
  call winimp(VirtualAlloc)
  test rax, rax
  jz   .fail
  WINEPILOGUE
  ret
.fail:
  lea  rcx, [rel k_err_alloc]
  call rt_fatal

; ===========================================================================
; File mapping and whole-file reads
; ===========================================================================

; rt_map_file(rcx=path utf16, rdx=&size) -> rax = view base. Opens the file
; read-only, writes its 64-bit size to *size, maps the whole file read-only and
; returns the base. The file and mapping handles are closed (the view survives).
; rt_fatal on any failure. Clobbers caller-saved registers.
rt_map_file:
  WINPROLOGUE
  sub  rsp, 96                 ; args at [rsp+32..56); [rsp+56]=hFile,[+64]=&size,[+72]=hMap,[+80]=base
  mov  [rsp+64], rdx           ; save &size
  ; CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, NORMAL, NULL)
  mov  edx, GENERIC_READ
  mov  r8d, FILE_SHARE_READ
  xor  r9d, r9d
  mov  qword [rsp+32], OPEN_EXISTING
  mov  qword [rsp+40], FILE_ATTRIBUTE_NORMAL
  mov  qword [rsp+48], 0
  call winimp(CreateFileW)
  cmp  rax, INVALID_HANDLE
  je   .fail
  mov  [rsp+56], rax           ; hFile
  ; GetFileSizeEx(hFile, &size)
  mov  rcx, rax
  mov  rdx, [rsp+64]
  call winimp(GetFileSizeEx)
  test eax, eax
  jz   .fail
  ; CreateFileMappingW(hFile, NULL, PAGE_READONLY, 0, 0, NULL)
  mov  rcx, [rsp+56]
  xor  edx, edx
  mov  r8d, PAGE_READONLY
  xor  r9d, r9d
  mov  qword [rsp+32], 0
  mov  qword [rsp+40], 0
  call winimp(CreateFileMappingW)
  test rax, rax
  jz   .fail
  mov  [rsp+72], rax           ; hMapping
  ; MapViewOfFile(hMapping, FILE_MAP_READ, 0, 0, 0)
  mov  rcx, rax
  mov  edx, FILE_MAP_READ
  xor  r8d, r8d
  xor  r9d, r9d
  mov  qword [rsp+32], 0
  call winimp(MapViewOfFile)
  test rax, rax
  jz   .fail
  mov  [rsp+80], rax           ; view base
  mov  rcx, [rsp+72]           ; close mapping handle (view persists)
  call winimp(CloseHandle)
  mov  rcx, [rsp+56]           ; close file handle (view persists)
  call winimp(CloseHandle)
  mov  rax, [rsp+80]
  WINEPILOGUE
  ret
.fail:
  lea  rcx, [rel k_err_map]
  call rt_fatal

; rt_read_file(rcx=path utf16, rdx=&len) -> rax = ptr. A whole file in memory for
; -p; a read-only mapping is enough, so this is rt_map_file with the same ABI.
rt_read_file:
  jmp  rt_map_file

; rt_read_stdin(rcx=&len) -> rax = ptr, *len = bytes. Reads STD_INPUT_HANDLE into
; a growing rt_alloc buffer (doubling, old buffer left to the OS) until EOF or a
; zero-byte read. Clobbers caller-saved registers.
rt_read_stdin:
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32]=overlapped; [+40]buf [+48]cap [+56]used [+64]read [+72]&len [+80]newbuf [+88]newcap
  mov  [rsp+72], rcx           ; &len
  mov  rcx, 65536
  call rt_alloc
  mov  [rsp+40], rax           ; buf
  mov  qword [rsp+48], 65536   ; cap
  mov  qword [rsp+56], 0        ; used
.rloop:
  mov  rax, [rsp+56]
  cmp  rax, [rsp+48]
  jb   .room
  ; grow: newcap = cap*2, copy used bytes
  mov  rcx, [rsp+48]
  shl  rcx, 1
  mov  [rsp+88], rcx
  call rt_alloc                ; rcx = newcap
  mov  [rsp+80], rax
  mov  rcx, rax
  mov  rdx, [rsp+40]
  mov  r8, [rsp+56]
  call rt_memcpy
  mov  rax, [rsp+80]
  mov  [rsp+40], rax           ; buf = newbuf
  mov  rcx, [rsp+88]
  mov  [rsp+48], rcx           ; cap = newcap
.room:
  mov  rcx, [rel g_stdin]
  mov  rdx, [rsp+40]
  add  rdx, [rsp+56]           ; buf + used
  mov  r8, [rsp+48]
  sub  r8, [rsp+56]            ; cap - used
  lea  r9, [rsp+64]            ; &bytesRead
  mov  qword [rsp+32], 0       ; lpOverlapped
  call winimp(ReadFile)
  test eax, eax
  jz   .eof                    ; read failed -> treat as EOF
  mov  eax, [rsp+64]           ; bytesRead
  test eax, eax
  jz   .eof                    ; 0 bytes -> EOF
  add  [rsp+56], rax           ; used += bytesRead
  jmp  .rloop
.eof:
  mov  rax, [rsp+72]
  mov  rcx, [rsp+56]
  mov  [rax], rcx              ; *len = used
  mov  rax, [rsp+40]           ; return buffer
  WINEPILOGUE
  ret

; ===========================================================================
; Timing and cores
; ===========================================================================

; rt_now_ns() -> rax = nanoseconds. QueryPerformanceCounter scaled by the cached
; frequency as counter*1e9/freq in 128-bit to avoid overflow. Clobbers rax,rcx,rdx.
rt_now_ns:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = counter
  lea  rcx, [rsp+32]
  call winimp(QueryPerformanceCounter)
  mov  rax, [rsp+32]
  mov  rcx, 1000000000
  mul  rcx                     ; rdx:rax = counter * 1e9
  mov  rcx, [rel g_qpc_freq]
  div  rcx                     ; rax = ns
  WINEPILOGUE
  ret

; rt_cpu_count() -> rax = dwNumberOfProcessors from GetSystemInfo.
rt_cpu_count:
  WINPROLOGUE
  sub  rsp, 48                 ; SYSTEM_INFO (48 bytes) at [rsp+32]
  lea  rcx, [rsp+32]
  call winimp(GetSystemInfo)
  mov  eax, [rsp+32 + 0x20]    ; dwNumberOfProcessors
  WINEPILOGUE
  ret

; ===========================================================================
; Thread pool start (pool_worker / pool_run are shared, in runtime.asm)
; ===========================================================================

; pool_start(rcx=nthreads) — store nthreads and create nthreads-1 worker threads
; with ids 1..nthreads-1 (tid 0 is the calling thread). rt_fatal if CreateThread
; fails. Clobbers caller-saved registers.
pool_start:
  WINPROLOGUE
  sub  rsp, 48                 ; args at [rsp+32..48); [rsp+56] = tid counter
  mov  [rel g_nthreads], rcx
  mov  qword [rel g_pool_gen], 0
  mov  qword [rel g_pool_done], 0
  mov  qword [rsp+56], 1
.cloop:
  mov  rax, [rel g_nthreads]
  cmp  [rsp+56], rax
  jae  .done
  ; CreateThread(NULL, 0, pool_worker, tid, 0, &g_thread_ids[tid])
  xor  rcx, rcx
  xor  rdx, rdx
  lea  r8, [rel pool_worker]
  mov  r9, [rsp+56]            ; lpParameter = tid
  mov  qword [rsp+32], 0       ; dwCreationFlags
  mov  rax, [rsp+56]
  lea  r10, [rel g_thread_ids]
  lea  r10, [r10 + rax*8]
  mov  [rsp+40], r10           ; lpThreadId
  call winimp(CreateThread)
  test rax, rax
  jz   .fail
  inc  qword [rsp+56]
  jmp  .cloop
.done:
  WINEPILOGUE
  ret
.fail:
  lea  rcx, [rel k_err_thread]
  call rt_fatal

; ===========================================================================
; Globals and strings of the Windows half
; ===========================================================================
section .data

g_qpc_freq:     dq 0           ; QueryPerformanceFrequency ticks per second
g_thread_ids:   times 64 dq 0  ; OS thread ids returned by CreateThread

k_err_alloc:    db "out of memory", 0
k_err_map:      db "cannot open or map file", 0
k_err_thread:   db "CreateThread failed", 0

section .text

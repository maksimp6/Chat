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
;   rt_view_show   a window with a picture (view.asm), until it is closed
;
; Imports used (all from inc/pe.inc's IAT): kernel32 GetStdHandle, WriteFile,
; ReadFile, ExitProcess, VirtualAlloc, CreateFileW, CreateFileMappingW,
; MapViewOfFile, CloseHandle, GetFileSizeEx, QueryPerformanceCounter,
; QueryPerformanceFrequency, GetCommandLineW, GetSystemInfo, CreateThread;
; user32 RegisterClassExW, CreateWindowExW, DefWindowProcW, GetMessageW,
; DispatchMessageW, PostQuitMessage, DestroyWindow, BeginPaint, EndPaint,
; GetClientRect, AdjustWindowRect, GetSystemMetrics, LoadCursorW; gdi32
; StretchDIBits, SetStretchBltMode, GetStockObject.

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
  cmp  qword [rel g_http_cap_on], 0
  je   .nocap
  cmp  rcx, [rel g_stdout]
  jne  .nocap
  push rcx                     ; stdout also goes to the HTTP answer (http.asm)
  push rdx
  push r8
  sub  rsp, 32
  mov  rcx, rdx
  mov  rdx, r8
  call http_capture
  add  rsp, 32
  pop  r8
  pop  rdx
  pop  rcx
.nocap:
  cmp  qword [rel g_log_tee], 0
  je   .body
  push rcx                     ; the screen output also goes to the dialog buffer
  push rdx
  push r8
  sub  rsp, 32
  mov  rcx, rdx
  mov  rdx, r8
  call rt_log_add
  add  rsp, 32
  pop  r8
  pop  rdx
  pop  rcx
.body:
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

; rt_model_open(rcx=path utf16, rdx=&size) -> rax = base: the model file mapped
; whole (the header is read from it; rt_tensor_data points into it).
rt_model_open:
  WINPROLOGUE
  call rt_map_file
  mov  [rel g_model_base], rax
  WINEPILOGUE
  ret

; rt_tensor_data(rcx=file offset, rdx=bytes) -> rax = the tensor's bytes: inside
; the mapping (Windows pages them in on first touch). Leaf.
rt_tensor_data:
  mov  rax, [rel g_model_base]
  add  rax, rcx
  ret

; rt_model_done() — nothing to finish on Windows. Leaf.
rt_model_done:
  ret

; rt_write_file(rcx=path utf16, rdx=ptr, r8=len) -> rax = 1 when the whole
; buffer was written to a new (or replaced) file, else 0.
rt_write_file:
  WINPROLOGUE
  sub  rsp, 80                 ; [rsp+32..56) CreateFileW's stack arguments, [+56] ptr, [+64] len, [+72] written
  mov  [rsp+56], rdx
  mov  [rsp+64], r8
  mov  edx, GENERIC_WRITE
  xor  r8d, r8d                ; no sharing
  xor  r9d, r9d
  mov  qword [rsp+32], CREATE_ALWAYS
  mov  qword [rsp+40], FILE_ATTRIBUTE_NORMAL
  mov  qword [rsp+48], 0
  call winimp(CreateFileW)
  cmp  rax, INVALID_HANDLE
  je   .fail
  mov  [rsp+48], rax           ; the handle
  mov  rcx, rax
  mov  rdx, [rsp+56]
  mov  r8d, [rsp+64]
  lea  r9, [rsp+72]
  mov  qword [rsp+32], 0
  call winimp(WriteFile)
  mov  rcx, [rsp+48]
  mov  [rsp+40], rax           ; WriteFile's result
  call winimp(CloseHandle)
  mov  rax, [rsp+40]
  test eax, eax
  jz   .fail
  mov  eax, [rsp+72]
  cmp  rax, [rsp+64]
  jne  .fail
  mov  eax, 1
  WINEPILOGUE
  ret
.fail:
  xor  eax, eax
  WINEPILOGUE
  ret

; rt_pick_model() -> rax = 0: Windows has no boot volume to list; the path is an
; argument (the USB build offers a menu).
rt_pick_model:
  xor  eax, eax
  ret

; rt_list_root(rcx=buf, rdx=cap) -> rax = bytes: on Windows the tool's listing
; is a note (the files live on the stick's root only on the USB build).
rt_list_root:
  lea  r8, [rel s_nolist]
  xor  eax, eax
.c:
  mov  r9b, [r8 + rax]
  test r9b, r9b
  jz   .d
  cmp  rax, rdx
  jae  .d
  mov  [rcx + rax], r9b
  inc  rax
  jmp  .c
.d:
  ret

; rt_read_optional(rcx=path utf16, rdx=&len) -> rax = the file's bytes in a
; zero-terminated buffer, or 0 when it cannot be opened (no rt_fatal).
rt_read_optional:
  push rbx
  push rsi
  WINPROLOGUE
  sub  rsp, 80                 ; [rsp+32..56) CreateFileW's stack args; [+56] size; [+64] read; [+72] buffer
  mov  rsi, rdx                ; &len
  mov  edx, GENERIC_READ
  mov  r8d, FILE_SHARE_READ
  xor  r9d, r9d
  mov  qword [rsp+32], OPEN_EXISTING
  mov  qword [rsp+40], FILE_ATTRIBUTE_NORMAL
  mov  qword [rsp+48], 0
  call winimp(CreateFileW)
  cmp  rax, INVALID_HANDLE
  je   .none
  mov  rbx, rax
  mov  rcx, rbx
  lea  rdx, [rsp+56]
  call winimp(GetFileSizeEx)
  mov  rcx, [rsp+56]
  inc  rcx                     ; the terminator
  call rt_alloc
  mov  [rsp+72], rax
  mov  rcx, rbx
  mov  rdx, rax
  mov  r8d, [rsp+56]
  lea  r9, [rsp+64]
  mov  qword [rsp+32], 0
  call winimp(ReadFile)
  mov  rcx, rbx
  call winimp(CloseHandle)
  mov  rax, [rsp+56]
  mov  [rsi], rax
  mov  rax, [rsp+72]
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret
.none:
  mov  qword [rsi], 0
  xor  eax, eax
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; rt_delete_file(rcx=path utf16) -> rax = 1 when the file is gone (deleted, or
; it was not there), 0 when Windows refused.
rt_delete_file:
  WINPROLOGUE
  call winimp(DeleteFileW)
  test eax, eax
  jnz  .ok
  call winimp(GetLastError)
  cmp  eax, 2                  ; ERROR_FILE_NOT_FOUND
  jne  .no
.ok:
  mov  eax, 1
  WINEPILOGUE
  ret
.no:
  xor  eax, eax
  WINEPILOGUE
  ret

; rt_find_task(rcx=out utf16 buffer, rdx=cap in chars) -> rax = 1 when the
; working directory holds a task file: task-*.txt that is not *.answer.txt; the
; first one found is copied to the buffer (the --auto work queue; the stick's
; root on the USB build). Preserves the nonvolatile registers.
rt_find_task:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  sub  rsp, 608                ; [rsp+32..624) WIN32_FIND_DATAW (592 bytes): attributes at +0, the name at +44
  mov  rbx, rcx
  mov  rsi, rdx
  lea  rcx, [rel w_task_glob]
  lea  rdx, [rsp+32]
  call winimp(FindFirstFileW)
  cmp  rax, INVALID_HANDLE
  je   .none
  mov  rdi, rax
.entry:
  test dword [rsp+32], 0x10    ; a directory
  jnz  .next
  lea  rcx, [rsp+76]           ; the name
  xor  r12d, r12d
.len:
  cmp  word [rcx + r12*2], 0
  je   .lend
  inc  r12
  jmp  .len
.lend:
  cmp  r12, 16                 ; room for ".answer.txt"
  jb   .take
  lea  rcx, [rel w_answer_txt]
  lea  rdx, [rsp+76 + r12*2 - 22]
  mov  r8d, 11
  call rt_wprefix_ieq
  test rax, rax
  jnz  .next                   ; an answer, not a task
.take:
  lea  rax, [r12 + 1]
  cmp  rax, rsi
  ja   .next                   ; longer than the buffer
  mov  rcx, rbx
  lea  rdx, [rsp+76]
  lea  r8, [r12*2 + 2]
  call rt_memcpy
  mov  rcx, rdi
  call winimp(FindClose)
  mov  eax, 1
  jmp  .ret
.next:
  mov  rcx, rdi
  lea  rdx, [rsp+32]
  call winimp(FindNextFileW)
  test eax, eax
  jnz  .entry
  mov  rcx, rdi
  call winimp(FindClose)
.none:
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; rt_kick() — the --auto heartbeat re-arms the firmware's watchdog on the USB
; build; Windows has no watchdog to arm.
rt_kick:
  ret

; rt_key_ready() -> rax = 1 when a keystroke is waiting. On Windows the
; console is a pipe or a terminal the test bench drives; nothing is polled.
rt_key_ready:
  xor  eax, eax
  ret

w_task_glob:    dw __utf16__("task-*.txt"), 0
w_answer_txt:   dw __utf16__(".answer.txt")

; rt_append_file(rcx=path utf16, rdx=ptr, r8=len) -> rax = 1 when the bytes were
; appended to the file (created when absent), else 0.
rt_append_file:
  WINPROLOGUE
  sub  rsp, 80                 ; [rsp+32..56) CreateFileW's stack arguments, [+56] ptr, [+64] len, [+72] written
  mov  [rsp+56], rdx
  mov  [rsp+64], r8
  mov  edx, FILE_APPEND_DATA
  mov  r8d, FILE_SHARE_READ
  xor  r9d, r9d
  mov  qword [rsp+32], OPEN_ALWAYS
  mov  qword [rsp+40], FILE_ATTRIBUTE_NORMAL
  mov  qword [rsp+48], 0
  call winimp(CreateFileW)
  cmp  rax, INVALID_HANDLE
  je   .fail
  mov  [rsp+48], rax           ; the handle
  mov  rcx, rax
  mov  rdx, [rsp+56]
  mov  r8d, [rsp+64]
  lea  r9, [rsp+72]
  mov  qword [rsp+32], 0
  call winimp(WriteFile)
  mov  rcx, [rsp+48]
  mov  [rsp+40], rax           ; WriteFile's result
  call winimp(CloseHandle)
  mov  rax, [rsp+40]
  test eax, eax
  jz   .fail
  mov  eax, [rsp+72]
  cmp  rax, [rsp+64]
  jne  .fail
  mov  eax, 1
  WINEPILOGUE
  ret
.fail:
  xor  eax, eax
  WINEPILOGUE
  ret

; rt_read_stdin(rcx=&len) -> rax = ptr, *len = bytes. Reads STD_INPUT_HANDLE into
; a growing rt_alloc buffer (doubling, old buffer left to the OS) until EOF or a
; zero-byte read; with g_stdin_line set (-i) a read that ends in LF ends the
; prompt, since a console in line mode returns one line per Enter. Clobbers
; caller-saved registers.
rt_read_stdin:
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32]=overlapped; [+40]buf [+48]cap [+56]used [+64]read [+72]&len [+80]newbuf [+88]newcap
  mov  [rsp+72], rcx           ; &len
  mov  rcx, 65536
  call rt_alloc
  mov  [rsp+40], rax           ; buf
  mov  qword [rsp+48], 65536   ; cap
  mov  qword [rsp+56], 0        ; used
  ; -i: bytes a previous read took past its line end (a pipe hands several lines
  ; in one ReadFile) come first
  mov  r8, [rel g_stdin_carry_len]
  test r8, r8
  jz   .rloop
  mov  rcx, rax
  mov  rdx, [rel g_stdin_carry]
  mov  [rsp+56], r8
  mov  qword [rel g_stdin_carry_len], 0
  call rt_memcpy
  jmp  .check
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
.check:
  cmp  qword [rel g_stdin_line], 0
  je   .rloop
  ; -i: the line ends at the first LF; what follows it waits for the next call
  mov  rcx, [rsp+40]
  xor  edx, edx
.lf:
  cmp  rdx, [rsp+56]
  jae  .rloop                  ; no LF yet: read more
  cmp  byte [rcx + rdx], 10
  je   .lf_found
  inc  rdx
  jmp  .lf
.lf_found:
  inc  rdx                     ; the line's bytes, LF included
  mov  rax, [rsp+56]
  sub  rax, rdx                ; the surplus
  jz   .line_only
  mov  [rel g_stdin_carry_len], rax
  add  rcx, rdx
  mov  [rel g_stdin_carry], rcx ; the buffer is never freed: the carry can live in it
.line_only:
  mov  [rsp+56], rdx
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

; pool_start(rcx=nthreads) — store nthreads (at least 1, at most MAX_THREADS) and
; create nthreads-1 worker threads with ids 1..nthreads-1 (tid 0 is the calling
; thread). rt_fatal if CreateThread fails. Clobbers caller-saved registers.
pool_start:
  WINPROLOGUE
  sub  rsp, 64                 ; args at [rsp+32..48); [rsp+56] = tid counter; [rsp+64] = a thread handle
  test rcx, rcx
  jnz  .nz
  mov  ecx, 1                  ; -t 0 would leave pool_run waiting for -1 workers
.nz:
  cmp  rcx, MAX_THREADS
  jbe  .cap
  mov  ecx, MAX_THREADS        ; g_thread_ids has this many slots
.cap:
  mov  [rel g_nthreads], rcx
  mov  qword [rel g_pool_gen], 0
  mov  qword [rel g_pool_done], 0
  ; this thread (tid 0) on core 0 at the highest priority: the pool's cores are
  ; never given to other processes while a job runs
  mov  rcx, -2                 ; GetCurrentThread()
  mov  edx, 1
  call winimp(SetThreadAffinityMask)
  mov  rcx, -2
  mov  edx, THREAD_PRIORITY_HIGHEST
  call winimp(SetThreadPriority)
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
  mov  [rsp+64], rax           ; the worker's handle: pin it to core tid, highest priority
  mov  rcx, rax
  mov  rdx, [rsp+56]
  mov  eax, 1
  xchg rcx, rdx                ; cl = tid for the shift
  shl  rax, cl
  mov  rcx, rdx
  mov  rdx, rax
  call winimp(SetThreadAffinityMask)
  mov  rcx, [rsp+64]
  mov  edx, THREAD_PRIORITY_HIGHEST
  call winimp(SetThreadPriority)
  inc  qword [rsp+56]
  jmp  .cloop
.done:
  WINEPILOGUE
  ret
.fail:
  lea  rcx, [rel k_err_thread]
  call rt_fatal

; ===========================================================================
; Picture viewer (view.asm has the shared half)
; ===========================================================================

VIEW_EDIT_H        equ 36      ; the prompt box's height in pixels (--image)
VIEW_BTN_W         equ 140     ; the Generate button's width, right of the prompt box
VIEW_BTN_ID        equ 2       ; its control id (WM_COMMAND)
VIEW_FONT_H        equ 20      ; text height in pixels (Segoe UI) for the box and the messages
VIEW_BG            equ 0x00202020 ; COLORREF of the window's background: dark grey
VIEW_TEXT_RGB      equ 0x00D0D0D0 ; COLORREF of the messages: light grey
VIEW_PROMPT_CHARS  equ 2048    ; UTF-16 characters read from the prompt box

; rt_view_show(rcx=pixels BGRX top-down, rdx=w, r8=h) — open a window with the
; picture and return when it is closed (the close button or Esc). The window
; starts at view_fit's size for 7/8 of the screen (1:1, or a whole-number
; enlargement of a small picture) and can be resized or maximised; the picture
; is redrawn fitted and centred on black. Preserves the nonvolatile registers.
rt_view_show:
  mov  qword [rel g_view_ui], 0
  jmp  view_run

; rt_image_ui() — asmrun --image: the same window, empty, with a prompt box
; along the bottom. Type a prompt and press Enter: img_generate makes the
; picture and it is shown above the box; the title bar says what is happening.
; Esc or the close button returns. Preserves the nonvolatile registers.
rt_image_ui:
  mov  qword [rel g_view_ui], 1
  xor  ecx, ecx                ; no picture yet; the window is sized for one
  mov  edx, IMG_SIDE
  mov  r8d, IMG_SIDE
view_run:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  sub  rsp, 240                ; [rsp+32..96) call arguments, [+96..176) WNDCLASSEXW,
                               ; [+176..192) RECT, [+192..240) MSG
  call view_set_picture
  lea  rax, [rel w_status_start] ; what the empty picture area says (--image)
  mov  [rel g_view_status], rax

  cmp  qword [rel g_view_font], 0
  jne  .have_font
  ; CreateFontW(-VIEW_FONT_H, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, "Segoe UI")
  mov  ecx, -VIEW_FONT_H
  xor  edx, edx
  xor  r8d, r8d
  xor  r9d, r9d
  mov  qword [rsp+32], FW_NORMAL
  mov  qword [rsp+40], 0
  mov  qword [rsp+48], 0
  mov  qword [rsp+56], 0
  mov  qword [rsp+64], DEFAULT_CHARSET
  mov  qword [rsp+72], 0
  mov  qword [rsp+80], 0
  mov  qword [rsp+88], CLEARTYPE_QUALITY
  mov  qword [rsp+96], 0
  lea  rax, [rel w_font_face]
  mov  [rsp+104], rax          ; (the WNDCLASSEXW below reuses these bytes afterwards)
  call winimp(CreateFontW)
  mov  [rel g_view_font], rax
.have_font:

  cmp  qword [rel g_view_class], 0
  jne  .have_class
  lea  rdi, [rsp+96]
  mov  dword [rdi], 80         ; cbSize
  mov  dword [rdi+4], CS_HREDRAW | CS_VREDRAW
  lea  rax, [rel view_wndproc]
  mov  [rdi+8], rax            ; lpfnWndProc
  mov  qword [rdi+16], 0       ; cbClsExtra, cbWndExtra
  lea  rax, [rel dos_header]   ; hInstance: the image base
  mov  [rdi+24], rax
  mov  qword [rdi+32], 0       ; hIcon
  xor  ecx, ecx
  mov  edx, IDC_ARROW
  call winimp(LoadCursorW)
  mov  [rdi+40], rax           ; hCursor
  mov  ecx, VIEW_BG
  call winimp(CreateSolidBrush)
  mov  [rdi+48], rax           ; hbrBackground: dark grey around the picture
  mov  qword [rdi+56], 0       ; lpszMenuName
  lea  rax, [rel w_view_class]
  mov  [rdi+64], rax           ; lpszClassName
  mov  qword [rdi+72], 0       ; hIconSm
  mov  rcx, rdi
  call winimp(RegisterClassExW)
  test ax, ax
  jz   .fail
  mov  qword [rel g_view_class], 1
.have_class:
  mov  ecx, SM_CXSCREEN
  call winimp(GetSystemMetrics)
  mov  ebx, eax
  mov  ecx, SM_CYSCREEN
  call winimp(GetSystemMetrics)
  mov  esi, eax
  lea  r8, [rbx*8]             ; 7/8 of the screen, at least one pixel
  sub  r8, rbx
  shr  r8, 3
  lea  r9, [rsi*8]
  sub  r9, rsi
  shr  r9, 3
  test r8, r8
  jnz  .sw
  mov  r8d, 1
.sw:
  test r9, r9
  jnz  .sh
  mov  r9d, 1
.sh:
  mov  rcx, [rel g_view_w]
  mov  rdx, [rel g_view_h]
  call view_fit                ; rax = client width, rdx = client height
  cmp  qword [rel g_view_ui], 0
  je   .noedit_h
  add  edx, VIEW_EDIT_H        ; room for the prompt box under the picture
.noedit_h:
  mov  dword [rsp+176], 0      ; RECT left
  mov  dword [rsp+180], 0      ;      top
  mov  [rsp+184], eax          ;      right
  mov  [rsp+188], edx          ;      bottom
  lea  rcx, [rsp+176]
  mov  edx, WS_OVERLAPPEDWINDOW
  xor  r8d, r8d                ; no menu
  call winimp(AdjustWindowRect) ; the RECT grows by the frame and the title bar
  ; CreateWindowExW(0, class, title, style, x, y, width, height, NULL, NULL, hInstance, NULL)
  xor  ecx, ecx
  lea  rdx, [rel w_view_class]
  lea  r8, [rel w_view_title]
  cmp  qword [rel g_view_ui], 0
  je   .title
  lea  r8, [rel w_title_ui]
.title:
  mov  r9d, WS_OVERLAPPEDWINDOW | WS_VISIBLE
  mov  dword [rsp+32], CW_USEDEFAULT ; x (an int: the upper half is not read)
  mov  dword [rsp+40], CW_USEDEFAULT ; y
  mov  eax, [rsp+184]
  sub  eax, [rsp+176]
  mov  [rsp+48], rax           ; width
  mov  eax, [rsp+188]
  sub  eax, [rsp+180]
  mov  [rsp+56], rax           ; height
  mov  qword [rsp+64], 0       ; hWndParent
  mov  qword [rsp+72], 0       ; hMenu
  lea  rax, [rel dos_header]
  mov  [rsp+80], rax           ; hInstance
  mov  qword [rsp+88], 0       ; lpParam
  call winimp(CreateWindowExW)
  test rax, rax
  jz   .fail
  mov  [rel g_view_hwnd], rax
  cmp  qword [rel g_view_ui], 0
  je   .msg
  ; the prompt box: a single-line EDIT child, laid out by view_layout
  mov  ecx, WS_EX_CLIENTEDGE
  lea  rdx, [rel w_edit_class]
  xor  r8d, r8d                ; no text
  mov  r9d, WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL
  mov  qword [rsp+32], 0
  mov  qword [rsp+40], 0
  mov  qword [rsp+48], 16
  mov  qword [rsp+56], VIEW_EDIT_H
  mov  rax, [rel g_view_hwnd]
  mov  [rsp+64], rax           ; parent: the picture window
  mov  qword [rsp+72], 1       ; child id
  lea  rax, [rel dos_header]
  mov  [rsp+80], rax
  mov  qword [rsp+88], 0
  call winimp(CreateWindowExW)
  test rax, rax
  jz   .fail
  mov  [rel g_view_edit], rax
  mov  rcx, rax
  mov  edx, WM_SETFONT
  mov  r8, [rel g_view_font]
  xor  r9d, r9d
  call winimp(SendMessageW)
  ; the Generate button right of the box: a click does what Enter does
  xor  ecx, ecx
  lea  rdx, [rel w_button_class]
  lea  r8, [rel w_button_text]
  mov  r9d, WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON
  mov  qword [rsp+32], 0
  mov  qword [rsp+40], 0
  mov  qword [rsp+48], VIEW_BTN_W
  mov  qword [rsp+56], VIEW_EDIT_H
  mov  rax, [rel g_view_hwnd]
  mov  [rsp+64], rax
  mov  qword [rsp+72], VIEW_BTN_ID
  lea  rax, [rel dos_header]
  mov  [rsp+80], rax
  mov  qword [rsp+88], 0
  call winimp(CreateWindowExW)
  test rax, rax
  jz   .fail
  mov  [rel g_view_button], rax
  mov  rcx, rax
  mov  edx, WM_SETFONT
  mov  r8, [rel g_view_font]
  xor  r9d, r9d
  call winimp(SendMessageW)
  mov  rcx, [rel g_view_hwnd]
  call view_layout
  mov  rcx, [rel g_view_edit]
  call winimp(SetFocus)        ; typing goes straight into the box
.msg:
  lea  rcx, [rsp+192]
  xor  edx, edx
  xor  r8d, r8d
  xor  r9d, r9d
  call winimp(GetMessageW)     ; 0 = WM_QUIT (the window was destroyed), -1 = error
  test eax, eax
  jz   .quit
  cmp  eax, -1
  je   .quit
  cmp  dword [rsp+192+8], WM_KEYDOWN ; MSG.message: keys are seen here, whichever window has the focus
  jne  .dispatch
  mov  rax, [rsp+192+16]       ; MSG.wParam: the virtual key
  cmp  rax, VK_ESCAPE
  je   .esc
  cmp  rax, VK_RETURN
  jne  .dispatch
  cmp  qword [rel g_view_ui], 0
  je   .dispatch
  call view_on_enter           ; Enter in the prompt box: generate (the key is not passed on)
  jmp  .msg
.esc:
  mov  rcx, [rel g_view_hwnd]
  call winimp(DestroyWindow)
  jmp  .msg
.dispatch:
  lea  rcx, [rsp+192]
  call winimp(TranslateMessage) ; key presses -> characters for the prompt box
  lea  rcx, [rsp+192]
  call winimp(DispatchMessageW)
  jmp  .msg
.quit:
  mov  qword [rel g_view_hwnd], 0
  mov  qword [rel g_view_edit], 0
  mov  qword [rel g_view_button], 0
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.fail:
  lea  rcx, [rel k_err_window]
  call rt_fatal

; view_set_picture(rcx=pixels or 0, rdx=w, r8=h) — the picture the window
; draws, and the top-down 32-bit BI_RGB BITMAPINFOHEADER StretchDIBits reads it
; with. Leaf; clobbers rax, rcx.
view_set_picture:
  mov  [rel g_view_px], rcx
  mov  [rel g_view_w], rdx
  mov  [rel g_view_h], r8
  lea  rax, [rel g_view_bmi]
  mov  dword [rax], 40         ; biSize
  mov  [rax+4], edx            ; biWidth
  mov  ecx, r8d
  neg  ecx
  mov  [rax+8], ecx            ; biHeight < 0: the top row first
  mov  word [rax+12], 1        ; biPlanes
  mov  word [rax+14], 32       ; biBitCount (biCompression and the rest stay 0)
  ret

; view_layout(rcx=hwnd of the picture window) — put the prompt box (if there
; is one) along the bottom of the client area and the Generate button at its
; right end. Preserves the nonvolatile registers.
view_layout:
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32..48) MoveWindow's stack arguments, [+48..64) RECT
  cmp  qword [rel g_view_edit], 0
  je   .ret
  lea  rdx, [rsp+48]
  call winimp(GetClientRect)
  ; MoveWindow(edit, 0, height - VIEW_EDIT_H, width - VIEW_BTN_W, VIEW_EDIT_H, TRUE)
  mov  rcx, [rel g_view_edit]
  xor  edx, edx
  mov  r8d, [rsp+60]
  sub  r8d, VIEW_EDIT_H
  mov  r9d, [rsp+56]
  sub  r9d, VIEW_BTN_W
  mov  qword [rsp+32], VIEW_EDIT_H
  mov  qword [rsp+40], 1
  call winimp(MoveWindow)
  cmp  qword [rel g_view_button], 0
  je   .ret
  ; MoveWindow(button, width - VIEW_BTN_W, height - VIEW_EDIT_H, VIEW_BTN_W, VIEW_EDIT_H, TRUE)
  mov  rcx, [rel g_view_button]
  mov  edx, [rsp+56]
  sub  edx, VIEW_BTN_W
  mov  r8d, [rsp+60]
  sub  r8d, VIEW_EDIT_H
  mov  r9d, VIEW_BTN_W
  mov  qword [rsp+32], VIEW_EDIT_H
  mov  qword [rsp+40], 1
  call winimp(MoveWindow)
.ret:
  WINEPILOGUE
  ret

; view_on_enter() — Enter in the prompt box: read it (UTF-16 -> UTF-8), say
; "generating" in the title bar, img_generate; show the picture, or say in the
; title bar why there is none. The prompt stays in the box. Preserves the
; nonvolatile registers.
view_on_enter:
  push rbx
  WINPROLOGUE
  mov  rcx, [rel g_view_edit]
  lea  rdx, [rel g_view_wtext]
  mov  r8d, VIEW_PROMPT_CHARS
  call winimp(GetWindowTextW)  ; eax = characters copied, NUL-terminated
  test eax, eax
  jz   .ret                    ; an empty box: nothing to do
  lea  rcx, [rel g_view_wtext]
  lea  rdx, [rel g_view_utf8]
  mov  r8d, VIEW_PROMPT_CHARS * 3
  call rt_utf16_to_utf8        ; rax = UTF-8 bytes
  mov  rbx, rax
  mov  rcx, [rel g_view_hwnd]
  lea  rdx, [rel w_title_busy]
  call winimp(SetWindowTextW)
  lea  rcx, [rel g_view_utf8]
  mov  rdx, rbx
  call img_generate            ; rax = IMG_SIDE x IMG_SIDE BGRX picture, or 0
  test rax, rax
  jz   .none
  mov  rcx, rax
  mov  edx, IMG_SIDE
  mov  r8d, IMG_SIDE
  call view_set_picture
  mov  rcx, [rel g_view_hwnd]
  xor  edx, edx                ; the whole client area
  mov  r8d, 1                  ; erase: black behind the new picture
  call winimp(InvalidateRect)
  jmp  .title
.none:
  lea  rax, [rel w_status_nogen] ; the picture area says why there is no picture
  mov  [rel g_view_status], rax
  mov  rcx, [rel g_view_hwnd]
  xor  edx, edx
  mov  r8d, 1
  call winimp(InvalidateRect)
.title:
  mov  rcx, [rel g_view_hwnd]
  lea  rdx, [rel w_view_title]
  call winimp(SetWindowTextW)
.ret:
  WINEPILOGUE
  pop  rbx
  ret

; view_wndproc(rcx=hwnd, edx=msg, r8=wParam, r9=lParam) -> rax. WM_PAINT draws
; the picture fitted and centred in the area above the prompt box
; (StretchDIBits, nearest neighbour), WM_SIZE lays the prompt box out again,
; WM_DESTROY ends view_run's message loop; everything else (including erasing
; the background black) goes to DefWindowProcW. Esc and Enter are handled in
; the message loop.
view_wndproc:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  sub  rsp, 208                ; [rsp+32..104) StretchDIBits stack arguments,
                               ; [+112..184) PAINTSTRUCT, [+184..200) client RECT
  mov  rbx, rcx                ; hwnd
  cmp  edx, WM_PAINT
  je   .paint
  cmp  edx, WM_SIZE
  je   .size
  cmp  edx, WM_COMMAND
  je   .command
  cmp  edx, WM_DESTROY
  je   .destroy
.def:
  mov  rcx, rbx                ; rdx, r8, r9 are still the message's
  call winimp(DefWindowProcW)
  jmp  .ret
.size:
  mov  rcx, rbx
  call view_layout
  jmp  .ret0
.command:
  cmp  r8d, VIEW_BTN_ID        ; wParam: BN_CLICKED (0) << 16 | the button's id
  jne  .def
  call view_on_enter           ; the Generate button: the same as Enter
  mov  rcx, [rel g_view_edit]
  call winimp(SetFocus)        ; back to the box for the next prompt
  jmp  .ret0
.destroy:
  xor  ecx, ecx
  call winimp(PostQuitMessage)
  jmp  .ret0
.paint:
  mov  rcx, rbx
  lea  rdx, [rsp+112]
  call winimp(BeginPaint)
  mov  rsi, rax                ; hdc
  mov  rcx, rbx
  lea  rdx, [rsp+184]
  call winimp(GetClientRect)   ; left = top = 0
  cmp  qword [rel g_view_edit], 0
  je   .area
  sub  dword [rsp+196], VIEW_EDIT_H ; the picture area ends at the prompt box
.area:
  cmp  qword [rel g_view_px], 0
  je   .text                   ; --image with no picture yet: say what to do
  mov  r8d, [rsp+192]          ; picture area width
  mov  r9d, [rsp+196]          ; picture area height
  test r8d, r8d
  jle  .endpaint               ; minimised, or no room above the box
  test r9d, r9d
  jle  .endpaint
  mov  rcx, [rel g_view_w]
  mov  rdx, [rel g_view_h]
  call view_fit
  mov  rdi, rax                ; drawn width
  mov  r12, rdx                ; drawn height
  mov  rcx, rsi
  mov  edx, COLORONCOLOR       ; nearest neighbour, the same pixels as the bare-metal screen
  call winimp(SetStretchBltMode)
  ; StretchDIBits(hdc, x, y, dw, dh, 0, 0, w, h, pixels, &bmi, DIB_RGB_COLORS, SRCCOPY)
  mov  r8d, [rsp+196]
  sub  r8, r12
  shr  r8, 1                   ; y = (client height - dh) / 2
  mov  edx, [rsp+192]
  sub  rdx, rdi
  shr  rdx, 1                  ; x = (client width - dw) / 2
  mov  rcx, rsi
  mov  r9, rdi
  mov  [rsp+32], r12
  mov  qword [rsp+40], 0
  mov  qword [rsp+48], 0
  mov  rax, [rel g_view_w]
  mov  [rsp+56], rax
  mov  rax, [rel g_view_h]
  mov  [rsp+64], rax
  mov  rax, [rel g_view_px]
  mov  [rsp+72], rax
  lea  rax, [rel g_view_bmi]
  mov  [rsp+80], rax
  mov  qword [rsp+88], DIB_RGB_COLORS
  mov  qword [rsp+96], SRCCOPY
  call winimp(StretchDIBits)
  jmp  .endpaint
.text:
  ; the message, centred in the picture area, light grey on the background
  mov  rcx, rsi
  mov  rdx, [rel g_view_font]
  call winimp(SelectObject)
  mov  rcx, rsi
  mov  edx, VIEW_TEXT_RGB
  call winimp(SetTextColor)
  mov  rcx, rsi
  mov  edx, TRANSPARENT
  call winimp(SetBkMode)
  mov  eax, [rsp+196]          ; start two lines above the middle
  shr  eax, 1
  sub  eax, VIEW_FONT_H * 2
  jns  .ttop
  xor  eax, eax
.ttop:
  mov  [rsp+188], eax          ; RECT top
  add  dword [rsp+184], VIEW_FONT_H ; side margins
  sub  dword [rsp+192], VIEW_FONT_H
  ; DrawTextW(hdc, text, -1, &rect, DT_CENTER | DT_WORDBREAK)
  mov  rcx, rsi
  mov  rdx, [rel g_view_status]
  mov  r8d, -1
  lea  r9, [rsp+184]
  mov  qword [rsp+32], DT_CENTER | DT_WORDBREAK
  call winimp(DrawTextW)
.endpaint:
  mov  rcx, rbx
  lea  rdx, [rsp+112]
  call winimp(EndPaint)
.ret0:
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ===========================================================================
; Globals and strings of the Windows half
; ===========================================================================
section .data
  align 8

g_qpc_freq:     dq 0           ; QueryPerformanceFrequency ticks per second
g_model_base:   dq 0           ; the model file's mapping (rt_model_open)
g_thread_ids:   times MAX_THREADS dq 0  ; OS thread ids returned by CreateThread

g_view_px:      dq 0           ; the picture rt_view_show shows (BGRX, top row first)
g_view_w:       dq 0
g_view_h:       dq 0
g_view_class:   dq 0           ; 1 once the window class is registered
g_view_ui:      dq 0           ; 1 for --image: the window has a prompt box
g_view_hwnd:    dq 0           ; the picture window while it is open
g_view_edit:    dq 0           ; its prompt box (--image), else 0
g_view_button:  dq 0           ; the Generate button (--image), else 0
g_view_font:    dq 0           ; Segoe UI for the box and the messages
g_view_status:  dq 0           ; the message drawn while there is no picture (UTF-16)
g_view_bmi:     times 40 db 0  ; BITMAPINFOHEADER for StretchDIBits (no colour table at 32 bits)
g_view_utf8:    times VIEW_PROMPT_CHARS * 3 db 0 ; the prompt as UTF-8 for img_generate

  align 2
g_view_wtext:   times VIEW_PROMPT_CHARS dw 0 ; the prompt box's text
w_view_class:   dw __utf16__("asmrun.view"), 0
w_edit_class:   dw __utf16__("EDIT"), 0
w_button_class: dw __utf16__("BUTTON"), 0
w_button_text:  dw __utf16__("Generate"), 0
w_view_title:   dw __utf16__("asmrun"), 0
w_title_ui:     dw __utf16__("asmrun — text to image"), 0
w_title_busy:   dw __utf16__("asmrun — generating…"), 0
w_font_face:    dw __utf16__("Segoe UI"), 0
w_status_start: dw __utf16__("Type what you want to see in the box below, then press Enter or Generate."), 13, 10
                dw __utf16__("The picture will appear here."), 0
w_status_nogen: dw __utf16__("No picture was made. The console says why"), 13, 10
                dw __utf16__("(asmrun --image needs --sd, --taesd, --vocab and --merges)."), 0

k_err_alloc:    db "out of memory", 0
s_nolist:       db "(the file list is only available on the USB build; read_file/write_file use the current directory here)", 10, 0
k_err_map:      db "cannot open or map file", 0
k_err_thread:   db "CreateThread failed", 0
k_err_window:   db "the picture window could not be created", 0

section .text

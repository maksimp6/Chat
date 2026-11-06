; runtime.asm — the services every other module leans on, independent of what
; runs underneath. The platform half lives in win.asm (Windows) or efi.asm (a
; machine with no operating system, booted from a USB stick); both implement
; the same names, which this file and the modules call:
;
;   _entry        program entry (PE entry point / UEFI efi_main)
;   rt_write_h    write bytes to the console "handle" g_stdout / g_stderr
;   rt_exit       leave the program with an exit code (never returns)
;   rt_alloc      zeroed memory, page granular, never freed
;   rt_map_file   whole file in memory (a mapping on Windows, a read on UEFI)
;   rt_read_file  whole file in memory, for small files such as the prompt
;   rt_read_stdin the prompt from standard input (to its end, or to the end of
;                 the line when g_stdin_line is set: -i) / one line typed on the console
;   rt_now_ns     monotonic nanoseconds
;   rt_cpu_count  processors available to the pool
;   pool_start    start nthreads-1 workers running pool_worker
;
; %included into asmrun.asm (bits 64, default rel set; .text current). Code stays
; in .text; the globals and strings at the end switch to .data (no `resb`: a
; zero-initialised byte is written to the file, so the loader's work is a copy).
;
; Public surface (Windows x64 ABI everywhere, also between modules):
;   rt_parse_cmdline       UTF-16 command line -> g_argv, returns argc
;   rt_out/rt_err          write bytes to stdout / stderr
;   rt_out_str/rt_err_str  zero-terminated text
;   rt_out_u64/rt_err_u64  unsigned decimal
;   rt_err_i64/rt_err_f32  signed decimal / fixed-point (3 fraction digits)
;   rt_err_hex64           0x + 16 hex digits
;   rt_fatal               "asmrun: <msg>\n" to stderr, rt_exit(1)
;   rt_cpuid_check         ISA gate: AVX2, FMA, F16C, OS-enabled YMM state
;   pool_worker/pool_run   fixed thread pool over a generation counter
;   rt_bench_bandwidth     --bw memory-bandwidth probe
;   rt_utf16_to_utf8/rt_watoi/rt_wcmp_ascii   UTF-16 helpers
;   rt_parse_u64/rt_streq/rt_memcmp/rt_memcpy/rt_memset   byte helpers

MAX_ARGS equ 256             ; capacity of the static argv pointer array

; pool_run gives up after this many pause-spins waiting for the workers (tens
; of seconds): a worker that never finishes is a bug, and on a machine with no
; operating system it would otherwise be a silent hang.
POOL_WAIT_SPINS equ (1 << 31)

; ===========================================================================
; Command line
; ===========================================================================

; rt_parse_cmdline(rcx=cmdline wchar*) -> rax = argc. Splits the UTF-16 command
; line into g_argv in place: tokens are separated by spaces/tabs, a double quote
; toggles "inside quotes" and is removed so quoted paths with spaces stay whole.
; The de-quoted, NUL-terminated tokens are written back over the same buffer
; (the write cursor never passes the read cursor). Clobbers rax,rcx,rdx,r8-r11.
rt_parse_cmdline:
  mov  r8, rcx                 ; read cursor
  mov  r9, rcx                 ; write cursor (in-place compaction)
  xor  r10d, r10d              ; argc
  lea  r11, [rel g_argv]       ; argv base
.skip:                          ; skip run of spaces/tabs before a token
  movzx eax, word [r8]
  cmp  eax, ' '
  je   .adv
  cmp  eax, 9
  je   .adv
  test eax, eax
  jz   .fin
  jmp  .tok
.adv:
  add  r8, 2
  jmp  .skip
.tok:                           ; start a token: record its (de-quoted) start
  cmp  r10, MAX_ARGS
  jae  .fin                    ; argv full: stop collecting
  mov  [r11 + r10*8], r9
  inc  r10
  xor  ecx, ecx                ; in_quote flag
.ch:
  movzx eax, word [r8]
  test eax, eax
  jz   .end
  test ecx, ecx
  jnz  .qchk                   ; inside quotes: spaces are literal
  cmp  eax, ' '
  je   .end
  cmp  eax, 9
  je   .end
.qchk:
  cmp  eax, '"'
  jne  .put
  xor  ecx, 1                  ; toggle quote state, drop the quote char
  add  r8, 2
  jmp  .ch
.put:
  mov  [r9], ax
  add  r9, 2
  add  r8, 2
  jmp  .ch
.end:                           ; terminate token; step read cursor past separator
  movzx eax, word [r8]         ; the char that ended the token (0 or space/tab)
  test eax, eax
  jz   .endz
  add  r8, 2                   ; consume the separator before overwriting it
.endz:
  mov  word [r9], 0
  add  r9, 2
  test eax, eax
  jz   .fin
  jmp  .skip
.fin:
  mov  rax, r10
  ret

; ===========================================================================
; Console output (rt_write_h is the platform's)
; ===========================================================================

; rt_out(rcx=ptr, rdx=len) — write len bytes to stdout. Tail-calls rt_write_h.
rt_out:
  mov  r8, rdx
  mov  rdx, rcx
  mov  rcx, [rel g_stdout]
  jmp  rt_write_h

; rt_err(rcx=ptr, rdx=len) — write len bytes to stderr. Tail-calls rt_write_h.
rt_err:
  mov  r8, rdx
  mov  rdx, rcx
  mov  rcx, [rel g_stderr]
  jmp  rt_write_h

; rt_out_str(rcx=zstr) — strlen then rt_out. Clobbers rax,rdx (via rt_out).
rt_out_str:
  xor  rdx, rdx
.sl:
  cmp  byte [rcx+rdx], 0
  je   .done
  inc  rdx
  jmp  .sl
.done:
  jmp  rt_out

; rt_err_str(rcx=zstr) — strlen then rt_err.
rt_err_str:
  xor  rdx, rdx
.sl:
  cmp  byte [rcx+rdx], 0
  je   .done
  inc  rdx
  jmp  .sl
.done:
  jmp  rt_err

; ===========================================================================
; Numbers
; ===========================================================================

; rt_u64_digits(rcx=value, rdx=bufend_exclusive) -> rax = start pointer. Writes
; the decimal digits of value backwards from bufend; at least one digit ('0').
; Leaf; clobbers rax,rdx,r9,r10 (needs up to 20 bytes below bufend).
rt_u64_digits:
  mov  rax, rcx
  mov  r9, rdx                 ; write cursor (moves down)
  mov  r10, 10
.loop:
  xor  rdx, rdx
  div  r10                     ; rax = q, rdx = digit
  add  dl, '0'
  dec  r9
  mov  [r9], dl
  test rax, rax
  jnz  .loop
  mov  rax, r9
  ret

; rt_emit_u64(rcx=value, r8=handle) — format value as unsigned decimal and write
; it to handle. Internal; WINPROLOGUE frame. Clobbers rax,rcx,rdx,r8-r11.
rt_emit_u64:
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32]=handle, [rsp+40..64)=24-byte digit buffer
  mov  [rsp+32], r8            ; save handle across rt_u64_digits/rt_write_h
  lea  rdx, [rsp+64]           ; bufend (exclusive)
  call rt_u64_digits           ; rcx=value -> rax=start
  mov  rcx, [rsp+32]           ; handle
  mov  rdx, rax                ; buf = start
  lea  r8, [rsp+64]
  sub  r8, rax                 ; len = bufend - start
  call rt_write_h
  WINEPILOGUE
  ret

; rt_out_u64(rcx=value) — unsigned decimal to stdout.
rt_out_u64:
  mov  r8, [rel g_stdout]
  jmp  rt_emit_u64

; rt_err_u64(rcx=value) — unsigned decimal to stderr.
rt_err_u64:
  mov  r8, [rel g_stderr]
  jmp  rt_emit_u64

; rt_err_i64(rcx=value) — signed decimal to stderr (leading '-' when negative;
; INT64_MIN is printed as its unsigned magnitude). Clobbers rax,rcx,rdx,r8-r11.
rt_err_i64:
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32]=isneg, [rsp+40..64)=digit buffer
  xor  r8d, r8d
  test rcx, rcx
  jns  .nonneg
  mov  r8d, 1
  neg  rcx
.nonneg:
  mov  [rsp+32], r8            ; isneg
  lea  rdx, [rsp+64]
  call rt_u64_digits           ; rax = start
  cmp  qword [rsp+32], 0
  je   .emit
  dec  rax
  mov  byte [rax], '-'
.emit:
  mov  rcx, [rel g_stderr]
  mov  rdx, rax
  lea  r8, [rsp+64]
  sub  r8, rax
  call rt_write_h
  WINEPILOGUE
  ret

; rt_err_hex64(rcx=value) — "0x" and 16 hex digits to stderr (status codes).
; Clobbers rax,rcx,rdx,r8-r11.
rt_err_hex64:
  WINPROLOGUE
  sub  rsp, 32                 ; [rsp+32..50) = "0x" + 16 digits
  mov  word [rsp+32], '0x'
  lea  r8, [rsp+34]
  mov  r9d, 16
.dig:
  mov  rax, rcx
  shr  rax, 60                 ; top nibble first
  shl  rcx, 4
  lea  rdx, [rel k_hex_digits]
  mov  al, [rdx + rax]
  mov  [r8], al
  inc  r8
  dec  r9d
  jnz  .dig
  mov  rcx, [rel g_stderr]
  lea  rdx, [rsp+32]
  mov  r8d, 18
  call rt_write_h
  WINEPILOGUE
  ret

; rt_err_f32(xmm0=value) — fixed-point decimal with exactly 3 fraction digits to
; stderr: scale by 1000, round half-up, split into integer and 3-digit fraction,
; emit sign if negative. Clobbers rax,rcx,rdx,r8-r11,xmm0,xmm1.
rt_err_f32:
  WINPROLOGUE
  sub  rsp, 64                 ; [rsp+32]=isneg, buffer grows down from [rsp+96]
  xor  r8d, r8d
  movd eax, xmm0
  test eax, eax                ; SF = float sign bit
  jns  .abs
  mov  r8d, 1
  and  eax, 0x7fffffff         ; clear sign -> magnitude
  movd xmm0, eax
.abs:
  mov  [rsp+32], r8            ; isneg
  mov  eax, 0x447a0000         ; 1000.0f
  movd xmm1, eax
  mulss xmm0, xmm1
  mov  eax, 0x3f000000         ; 0.5f
  movd xmm1, eax
  addss xmm0, xmm1             ; round half-up (value is non-negative)
  cvttss2si rax, xmm0          ; total = floor(value*1000 + 0.5)
  mov  rcx, 1000
  xor  rdx, rdx
  div  rcx                     ; rax = integer part, rdx = fraction (0..999)
  mov  r11, rax                ; integer part
  mov  rax, rdx                ; fraction
  mov  r10, 10
  lea  r8, [rsp+96]            ; write cursor (exclusive top of buffer)
  mov  ecx, 3                  ; 3 fraction digits, zero-padded
.frac:
  xor  rdx, rdx
  div  r10
  add  dl, '0'
  dec  r8
  mov  [r8], dl
  dec  ecx
  jnz  .frac
  dec  r8
  mov  byte [r8], '.'
  mov  rax, r11                ; integer part (at least one digit)
.int:
  xor  rdx, rdx
  div  r10
  add  dl, '0'
  dec  r8
  mov  [r8], dl
  test rax, rax
  jnz  .int
  cmp  qword [rsp+32], 0
  je   .emit
  dec  r8
  mov  byte [r8], '-'
.emit:
  mov  r9, r8                  ; start
  lea  r10, [rsp+96]
  sub  r10, r9                 ; len
  mov  rcx, [rel g_stderr]
  mov  rdx, r9
  mov  r8, r10
  call rt_write_h
  WINEPILOGUE
  ret

; ===========================================================================
; Fatal error
; ===========================================================================

; rt_fatal(rcx=zstr) — write "asmrun: <msg>\n" to stderr and rt_exit(1).
; Never returns. Clobbers everything.
rt_fatal:
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = saved message pointer
  mov  [rsp+32], rcx
  lea  rcx, [rel k_fatal_prefix]
  mov  rdx, FATAL_PREFIX_LEN
  call rt_err
  mov  rcx, [rsp+32]
  call rt_err_str
  lea  rcx, [rel k_newline]
  mov  rdx, 1
  call rt_err
  mov  ecx, 1
  call rt_exit
  hlt

; ===========================================================================
; ISA gate
; ===========================================================================

; rt_cpuid_check() — require AVX2, FMA, F16C and OS-enabled XMM/YMM state; set
; g_has_vnni to 1 when AVX-VNNI (leaf 7.1 eax bit4) is present. rt_fatal if a
; required feature is missing. Preserves rbx (clobbered by cpuid).
rt_cpuid_check:
  push rbp
  mov  rbp, rsp
  push rbx
  sub  rsp, 40                 ; 32-byte shadow, keeps rsp 16-aligned for rt_fatal
  mov  eax, 1                  ; leaf 1
  xor  ecx, ecx
  cpuid
  mov  r8d, ecx                ; leaf1.ecx feature bits
  bt   r8d, 27                 ; OSXSAVE
  jnc  .missing
  bt   r8d, 28                 ; AVX
  jnc  .missing
  bt   r8d, 12                 ; FMA
  jnc  .missing
  bt   r8d, 29                 ; F16C
  jnc  .missing
  xor  ecx, ecx                ; XCR0 via xgetbv
  xgetbv
  and  eax, 0x6                ; XMM (bit1) + YMM (bit2) state saved by OS
  cmp  eax, 0x6
  jne  .missing
  mov  eax, 7                  ; leaf 7, subleaf 0
  xor  ecx, ecx
  cpuid
  bt   ebx, 5                  ; AVX2
  jnc  .missing
  mov  eax, 7                  ; leaf 7, subleaf 1
  mov  ecx, 1
  cpuid
  xor  r10d, r10d
  bt   eax, 4                  ; AVX-VNNI
  jnc  .novnni
  mov  r10d, 1
.novnni:
  mov  [rel g_has_vnni], r10
  add  rsp, 40
  pop  rbx
  pop  rbp
  ret
.missing:
  lea  rcx, [rel k_err_cpu]
  call rt_fatal

; ===========================================================================
; Thread pool (pool_start is the platform's)
; ===========================================================================
;
; A fixed set of workers spins on g_pool_gen. pool_run publishes the job
; (func/arg), resets g_pool_done, bumps the generation (a locked increment, a
; full barrier so workers see func/arg too), runs tid 0 itself, then spins until
; every worker has incremented g_pool_done. Each job splits its own work by tid.
; g_pool_quit set to 1 makes an idle worker return (efi.asm uses it to give the
; processors back to the firmware before leaving).

; pool_worker(rcx=tid) — worker body. Spins (pause) until the generation
; advances, runs g_pool_func(rcx=g_pool_arg, rdx=tid, r8=g_nthreads), atomically
; bumps g_pool_done, and repeats until g_pool_quit is set between jobs.
pool_worker:
  push rbp
  mov  rbp, rsp
  push rsi
  push rdi
  sub  rsp, 32                 ; 32-byte shadow, rsp 16-aligned
  mov  rsi, rcx                ; tid
  xor  edi, edi                ; last generation seen (initial gen is 0)
.wloop:
.spin:
  mov  rax, [rel g_pool_gen]
  cmp  rax, rdi
  jne  .go
  cmp  byte [rel g_pool_quit], 0
  jne  .quit
  pause
  jmp  .spin
.go:
  mov  rdi, rax                ; remember this generation
  mov  rcx, [rel g_pool_arg]
  mov  rdx, rsi                ; tid
  mov  r8, [rel g_nthreads]
  call [rel g_pool_func]
  lock inc qword [rel g_pool_done]
  jmp  .wloop
.quit:
  add  rsp, 32
  pop  rdi
  pop  rsi
  pop  rbp
  ret

; pool_run(rcx=func, rdx=arg) — run func(rcx=arg, rdx=tid, r8=nthreads) on every
; thread (tid 0 on the caller) and return when all have finished. rt_fatal if the
; workers have not reported back after POOL_WAIT_SPINS pauses. Clobbers
; caller-saved registers.
pool_run:
  WINPROLOGUE
  mov  [rel g_pool_func], rcx
  mov  [rel g_pool_arg], rdx
  mov  qword [rel g_pool_done], 0        ; reset before publishing the generation
  lock inc qword [rel g_pool_gen]        ; release workers (barrier orders func/arg first)
  mov  rcx, [rel g_pool_arg]
  xor  edx, edx                          ; tid 0
  mov  r8, [rel g_nthreads]
  call [rel g_pool_func]
  mov  rax, [rel g_nthreads]
  dec  rax                               ; workers expected to report done
  jz   .done                             ; single-threaded: no workers
  mov  r9, POOL_WAIT_SPINS
.wait:
  mov  rcx, [rel g_pool_done]
  cmp  rcx, rax
  jae  .done
  pause
  dec  r9
  jnz  .wait
  lea  rcx, [rel k_err_hang]
  call rt_fatal
.done:
  WINEPILOGUE
  ret

; ===========================================================================
; Memory-bandwidth probe (--bw)
; ===========================================================================

; bw_job(rcx=buf, rdx=tid, r8=nthreads) — pool body: sum this thread's 32-byte
; aligned stripe of g_bw_bytes with vpaddq into four ymm accumulators and store
; the reduced sum to g_bw_partials[tid] (forcing the reads). Leaf; uses ymm0-3.
bw_job:
  mov  r11, rdx                ; tid
  mov  r9, [rel g_bw_bytes]
  mov  rax, r9
  xor  rdx, rdx
  div  r8                      ; rax = bytes / nthreads
  and  rax, -32                ; stripe, 32-aligned
  mov  r10, rax                ; stripe
  mov  rax, r11
  imul rax, r10                ; tid * stripe
  add  rax, rcx                ; start = buf + tid*stripe
  mov  rdx, r8
  dec  rdx                     ; nthreads - 1
  cmp  r11, rdx
  jne  .notlast
  mov  rdx, rcx                ; last thread takes the remainder
  add  rdx, [rel g_bw_bytes]   ; end = buf + bytes
  jmp  .haveend
.notlast:
  mov  rdx, rax
  add  rdx, r10                ; end = start + stripe
.haveend:
  vpxor ymm0, ymm0, ymm0
  vpxor ymm1, ymm1, ymm1
  vpxor ymm2, ymm2, ymm2
  vpxor ymm3, ymm3, ymm3
.sum:
  mov  r9, rdx
  sub  r9, rax                 ; bytes remaining
  cmp  r9, 128
  jb   .tail
  vpaddq ymm0, ymm0, [rax]
  vpaddq ymm1, ymm1, [rax+32]
  vpaddq ymm2, ymm2, [rax+64]
  vpaddq ymm3, ymm3, [rax+96]
  add  rax, 128
  jmp  .sum
.tail:
  mov  r9, rdx
  sub  r9, rax                 ; recompute bytes remaining each pass
  cmp  r9, 32
  jb   .reduce
  vpaddq ymm0, ymm0, [rax]
  add  rax, 32
  jmp  .tail
.reduce:
  vpaddq ymm0, ymm0, ymm1
  vpaddq ymm2, ymm2, ymm3
  vpaddq ymm0, ymm0, ymm2
  vextracti128 xmm1, ymm0, 1
  vpaddq xmm0, xmm0, xmm1
  vmovq rax, xmm0
  vpextrq r9, xmm0, 1
  add  rax, r9
  lea  rcx, [rel g_bw_partials]
  mov  [rcx + r11*8], rax
  vzeroupper
  ret

; rt_bench_bandwidth() — allocate 512 MiB, page it in once, then read it three
; times with the pool and print "asmrun: bw_gb_s=<x>\n" to stderr (bytes moved /
; elapsed ns == GB/s with GB = 1e9). Clobbers caller-saved registers.
rt_bench_bandwidth:
  WINPROLOGUE
  sub  rsp, 48                 ; [rsp+32]=buf [+40]=t0ns [+48]=pass [+56]=gbps(f32)
  mov  rax, [rel g_nthreads]   ; tolerate being called before pool_start
  test rax, rax
  jnz  .haven
  mov  qword [rel g_nthreads], 1
.haven:
  mov  rcx, 512*1024*1024
  call rt_alloc
  mov  [rsp+32], rax
  mov  qword [rel g_bw_bytes], 512*1024*1024
  mov  rcx, rax                ; fill once to fault the pages in
  xor  rdx, rdx
  mov  r8, 512*1024*1024
  call rt_memset
  call rt_now_ns
  mov  [rsp+40], rax           ; t0
  mov  qword [rsp+48], 0
.pass:
  cmp  qword [rsp+48], 3
  jae  .timed
  lea  rcx, [rel bw_job]
  mov  rdx, [rsp+32]           ; arg = buf
  call pool_run
  inc  qword [rsp+48]
  jmp  .pass
.timed:
  call rt_now_ns
  sub  rax, [rsp+40]           ; elapsed ns
  mov  rcx, rax
  mov  rax, 512*1024*1024
  imul rax, 3                  ; bytes moved
  cvtsi2ss xmm0, rax
  cvtsi2ss xmm1, rcx
  divss xmm0, xmm1             ; GB/s = bytes / ns
  movss [rsp+56], xmm0         ; save across the prefix write
  lea  rcx, [rel k_bw_prefix]
  mov  rdx, K_BW_PREFIX_LEN
  call rt_err
  movss xmm0, [rsp+56]
  call rt_err_f32
  lea  rcx, [rel k_newline]
  mov  rdx, 1
  call rt_err
  WINEPILOGUE
  ret

; ===========================================================================
; UTF-16 helpers
; ===========================================================================

; rt_utf16_to_utf8(rcx=wptr, rdx=out, r8=max) -> rax = bytes written. Decodes the
; NUL-terminated UTF-16 at wptr (surrogate pairs combined) to UTF-8 in out,
; stopping before it would exceed max bytes. Leaf; clobbers rax,rcx,rdx,r8-r11.
rt_utf16_to_utf8:
  mov  r9, rcx                 ; read cursor
  mov  r10, rdx                ; write cursor
  mov  r11, r8                 ; max bytes
  xor  eax, eax                ; count
.loop:
  movzx ecx, word [r9]
  test ecx, ecx
  jz   .done
  add  r9, 2
  mov  edx, ecx
  and  edx, 0xFC00
  cmp  edx, 0xD800             ; high surrogate?
  jne  .enc
  movzx edx, word [r9]
  mov  r8d, edx
  and  r8d, 0xFC00
  cmp  r8d, 0xDC00             ; followed by a low surrogate?
  jne  .enc                    ; lone high surrogate: encode as-is
  add  r9, 2
  sub  ecx, 0xD800
  shl  ecx, 10
  sub  edx, 0xDC00
  add  ecx, edx
  add  ecx, 0x10000            ; combined code point
.enc:
  cmp  ecx, 0x80
  jae  .e2
  lea  rdx, [rax+1]
  cmp  rdx, r11
  ja   .done
  mov  [r10], cl
  inc  r10
  mov  rax, rdx
  jmp  .loop
.e2:
  cmp  ecx, 0x800
  jae  .e3
  lea  rdx, [rax+2]
  cmp  rdx, r11
  ja   .done
  mov  r8d, ecx
  shr  r8d, 6
  or   r8d, 0xC0
  mov  [r10], r8b
  mov  r8d, ecx
  and  r8d, 0x3F
  or   r8d, 0x80
  mov  [r10+1], r8b
  add  r10, 2
  mov  rax, rdx
  jmp  .loop
.e3:
  cmp  ecx, 0x10000
  jae  .e4
  lea  rdx, [rax+3]
  cmp  rdx, r11
  ja   .done
  mov  r8d, ecx
  shr  r8d, 12
  or   r8d, 0xE0
  mov  [r10], r8b
  mov  r8d, ecx
  shr  r8d, 6
  and  r8d, 0x3F
  or   r8d, 0x80
  mov  [r10+1], r8b
  mov  r8d, ecx
  and  r8d, 0x3F
  or   r8d, 0x80
  mov  [r10+2], r8b
  add  r10, 3
  mov  rax, rdx
  jmp  .loop
.e4:
  lea  rdx, [rax+4]
  cmp  rdx, r11
  ja   .done
  mov  r8d, ecx
  shr  r8d, 18
  or   r8d, 0xF0
  mov  [r10], r8b
  mov  r8d, ecx
  shr  r8d, 12
  and  r8d, 0x3F
  or   r8d, 0x80
  mov  [r10+1], r8b
  mov  r8d, ecx
  shr  r8d, 6
  and  r8d, 0x3F
  or   r8d, 0x80
  mov  [r10+2], r8b
  mov  r8d, ecx
  and  r8d, 0x3F
  or   r8d, 0x80
  mov  [r10+3], r8b
  add  r10, 4
  mov  rax, rdx
  jmp  .loop
.done:
  ret

; rt_watoi(rcx=wstr) -> rax. Parse the leading ASCII decimal digits of a UTF-16
; string to u64 (stops at the first non-digit). Leaf; clobbers rax,rcx,rdx.
rt_watoi:
  xor  eax, eax
.loop:
  movzx edx, word [rcx]
  sub  edx, '0'
  cmp  edx, 9
  ja   .done
  imul rax, rax, 10
  add  rax, rdx
  add  rcx, 2
  jmp  .loop
.done:
  ret

; rt_wcmp_ascii(rcx=wstr, rdx=ascii_zstr) -> rax = 0 if the UTF-16 string equals
; the ASCII zstr (code point == byte at every position, same length), else 1.
; Leaf; clobbers rax,rcx,rdx,r8.
rt_wcmp_ascii:
.loop:
  movzx eax, word [rcx]
  movzx r8d, byte [rdx]
  cmp  eax, r8d
  jne  .ne
  test eax, eax
  jz   .eq                     ; both terminated together
  add  rcx, 2
  inc  rdx
  jmp  .loop
.eq:
  xor  eax, eax
  ret
.ne:
  mov  eax, 1
  ret

; ===========================================================================
; Byte helpers
; ===========================================================================

; rt_parse_u64(rcx=ptr, rdx=len) -> rax. Parse up to len bytes of ASCII decimal
; to u64, stopping at the first non-digit or at len. Leaf; clobbers rax,rcx,rdx,r8.
rt_parse_u64:
  xor  eax, eax
.loop:
  test rdx, rdx
  jz   .done
  movzx r8d, byte [rcx]
  sub  r8d, '0'
  cmp  r8d, 9
  ja   .done
  imul rax, rax, 10
  add  rax, r8
  inc  rcx
  dec  rdx
  jmp  .loop
.done:
  ret

; rt_streq(rcx=a, rdx=b) -> rax = 1 if the two zero-terminated byte strings are
; equal, else 0. Leaf; clobbers rax,rcx,rdx,r8.
rt_streq:
.loop:
  mov  al, [rcx]
  mov  r8b, [rdx]
  cmp  al, r8b
  jne  .ne
  test al, al
  jz   .eq
  inc  rcx
  inc  rdx
  jmp  .loop
.eq:
  mov  eax, 1
  ret
.ne:
  xor  eax, eax
  ret

; rt_memcmp(rcx=a, rdx=b, r8=n) -> rax = 0 if the n bytes are equal, else the
; signed difference a[i]-b[i] at the first mismatch. Leaf; clobbers rax,rcx,rdx,r8-r10.
rt_memcmp:
  test r8, r8
  jz   .eq
.loop:
  movzx r9d, byte [rcx]
  movzx r10d, byte [rdx]
  sub  r9d, r10d
  jnz  .diff
  inc  rcx
  inc  rdx
  dec  r8
  jnz  .loop
.eq:
  xor  eax, eax
  ret
.diff:
  movsxd rax, r9d
  ret

; rt_memcpy(rcx=dst, rdx=src, r8=n) -> rax = dst. Forward copy (no overlap),
; qwords then a byte tail. Leaf; clobbers rax,rcx,rdx,r8-r10.
rt_memcpy:
  mov  rax, rcx                ; return dst
  mov  r9, r8
  shr  r9, 3
  test r9, r9
  jz   .bytes
.qloop:
  mov  r10, [rdx]
  mov  [rcx], r10
  add  rdx, 8
  add  rcx, 8
  dec  r9
  jnz  .qloop
.bytes:
  and  r8, 7
  jz   .done
.bloop:
  mov  r10b, [rdx]
  mov  [rcx], r10b
  inc  rdx
  inc  rcx
  dec  r8
  jnz  .bloop
.done:
  ret

; rt_memset(rcx=dst, rdx=val, r8=n) -> rax = dst. Fill n bytes with the low byte
; of val, qwords then a byte tail. Leaf; clobbers rax,rcx,rdx,r8-r10.
rt_memset:
  mov  rax, rcx                ; return dst
  movzx r9, dl
  mov  r10, 0x0101010101010101
  imul r10, r9                 ; broadcast the byte to all 8 lanes
  mov  r9, r8
  shr  r9, 3
  test r9, r9
  jz   .bytes
.qloop:
  mov  [rcx], r10
  add  rcx, 8
  dec  r9
  jnz  .qloop
.bytes:
  and  r8, 7
  jz   .done
.bloop:
  mov  [rcx], dl
  inc  rcx
  dec  r8
  jnz  .bloop
.done:
  ret

; ===========================================================================
; Mutable globals and constants shared by both platform halves
; ===========================================================================
section .data

g_stdout:       dq 0           ; Windows: STD_OUTPUT_HANDLE; UEFI: tag 1 (the screen)
g_stderr:       dq 0           ; Windows: STD_ERROR_HANDLE;  UEFI: tag 2 (the screen)
g_stdin:        dq 0           ; Windows: STD_INPUT_HANDLE;  UEFI: unused
g_stdin_line:   dq 0           ; 1 (-i): rt_read_stdin returns at the end of a line; 0: reads to the end of input
g_has_vnni:     dq 0           ; 1 if AVX-VNNI is available, else 0
g_nthreads:     dq 0           ; pool size (including the calling thread, tid 0)
g_pool_func:    dq 0           ; current job function pointer
g_pool_arg:     dq 0           ; current job argument
g_pool_gen:     dq 0           ; generation counter workers spin on
g_pool_done:    dq 0           ; count of workers that finished the current job
g_pool_quit:    dq 0           ; 1: idle workers return from pool_worker
g_argv:         times MAX_ARGS dq 0   ; parsed argument pointers (into the command line)

g_bw_bytes:     dq 0           ; --bw buffer size in bytes
g_bw_partials:  times MAX_THREADS dq 0  ; --bw per-thread checksums (keep the reads live)

k_fatal_prefix: db "asmrun: "
FATAL_PREFIX_LEN equ $ - k_fatal_prefix
k_newline:      db 10
k_hex_digits:   db "0123456789abcdef"
k_err_cpu:      db "CPU lacks required AVX2/FMA/F16C support", 0
k_err_hang:     db "a worker thread did not finish its job (thread pool hang)", 0
k_bw_prefix:    db "asmrun: bw_gb_s="
K_BW_PREFIX_LEN equ $ - k_bw_prefix

section .text

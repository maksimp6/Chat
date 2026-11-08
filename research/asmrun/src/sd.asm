; sd.asm — the SD-Turbo weights: a GGUF file indexed by tensor name, for the
; image generator (image.asm). Unlike gguf_open, which fills the Qwen3 Model,
; this reads any GGUF: every tensor's name, type, shape and data address, in a
; table that sd_find searches by name.
;
;   sd_index(rcx=path utf16)    map the file, build the table (globals g_sd_*)
;   sd_find(rcx=zname) -> rax   Tensor* or 0
;   sd_list()                   --sdinfo: every tensor (name type ne0 ne1 ne2 ne3)
;                               and the per-type totals, on stdout
;
; The file is mapped read-only (rt_map_file) and never written. A truncated or
; malformed file is refused with rt_fatal.
;
; %included into asmrun.asm (bits 64, default rel) after gguf.asm (it uses
; gguf_skip_value, gguf_read_int, gguf_keyeq and the Tensor struct).

section .text

SD_TYPES equ 40                ; ggml type ids counted by sd_list (0..39)

; sd_index(rcx=path utf16) — map the GGUF and build g_sd_tensors (Tensor[g_sd_n]).
; Preserves the nonvolatile registers.
sd_index:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 16                 ; [rsp+32] = file size
  lea  rdx, [rsp+32]
  call rt_map_file
  mov  [rel g_sd_base], rax
  mov  rbx, [rsp+32]
  mov  [rel g_sd_size], rbx
  add  rbx, rax                ; rbx = end of the file
  mov  rsi, rax
  lea  rcx, [rel sd_err_magic]
  cmp  dword [rsi], 0x46554747 ; 'GGUF'
  jne  .bad
  cmp  dword [rsi+4], 3
  jne  .bad
  mov  r12, [rsi+8]            ; n_tensors
  mov  r13, [rsi+16]           ; n_kv
  mov  [rel g_sd_n], r12
  mov  qword [rel g_sd_align], 32
  add  rsi, 24
.kv:                           ; key = gguf_string, u32 value type, value
  test r13, r13
  jz   .kvdone
  mov  r15, [rsi]              ; key length (gguf_keyeq reads r14/r15)
  lea  r14, [rsi+8]
  lea  rsi, [rsi + 8 + r15]
  mov  edi, [rsi]              ; value type
  add  rsi, 4
  lea  rcx, [rel sd_key_align]
  call gguf_keyeq
  test rax, rax
  jz   .skip
  mov  ecx, edi
  call gguf_read_int
  test rax, rax
  jz   .kvnext                 ; an alignment of 0 would divide by zero: keep 32
  mov  [rel g_sd_align], rax
  jmp  .kvnext
.skip:
  mov  ecx, edi
  call gguf_skip_value
.kvnext:
  lea  rcx, [rel sd_err_short]
  cmp  rsi, rbx
  ja   .bad
  dec  r13
  jmp  .kv
.kvdone:
  mov  rcx, r12
  imul rcx, rcx, Tensor_size
  call rt_alloc
  mov  [rel g_sd_tensors], rax
  mov  rdi, rax
  xor  r13d, r13d
.t:                            ; name, u32 ndims, u64 ne[ndims], u32 type, u64 offset
  cmp  r13, r12
  jae  .tdone
  mov  rax, [rsi]
  lea  rcx, [rsi+8]
  mov  [rdi + Tensor.name], rcx
  mov  [rdi + Tensor.namelen], rax
  lea  rsi, [rsi + 8 + rax]
  mov  ecx, [rsi]
  add  rsi, 4
  mov  [rdi + Tensor.ndims], ecx
  mov  qword [rdi + Tensor.ne0], 1
  mov  qword [rdi + Tensor.ne1], 1
  mov  qword [rdi + Tensor.ne2], 1
  mov  qword [rdi + Tensor.ne3], 1
  lea  rax, [rel sd_err_dims]
  cmp  ecx, 4
  ja   .bad_rax
  xor  edx, edx
.d:
  cmp  edx, ecx
  jae  .ddone
  mov  rax, [rsi]
  mov  [rdi + Tensor.ne0 + rdx*8], rax
  add  rsi, 8
  inc  edx
  jmp  .d
.ddone:
  mov  eax, [rsi]
  mov  [rdi + Tensor.type], eax
  mov  rax, [rsi+4]
  mov  [rdi + Tensor.data], rax ; the offset for now; made absolute below
  add  rsi, 12
  lea  rcx, [rel sd_err_short]
  cmp  rsi, rbx
  ja   .bad
  add  rdi, Tensor_size
  inc  r13
  jmp  .t
.tdone:
  mov  rax, rsi                ; the data starts at the next multiple of the alignment
  sub  rax, [rel g_sd_base]
  mov  rcx, [rel g_sd_align]
  add  rax, rcx
  dec  rax
  xor  edx, edx
  div  rcx
  imul rax, rcx
  add  rax, [rel g_sd_base]
  mov  [rel g_sd_data], rax
  mov  rdi, [rel g_sd_tensors]
  xor  r13d, r13d
.fix:                          ; absolute data address, size, and inside the file
  cmp  r13, r12
  jae  .ok
  mov  rax, [rel g_sd_data]
  add  [rdi + Tensor.data], rax
  mov  rcx, rdi
  call sd_tensor_bytes
  mov  [rdi + Tensor.nbytes], rax
  add  rax, [rdi + Tensor.data]
  lea  rcx, [rel sd_err_short]
  cmp  rax, rbx
  ja   .bad
  add  rdi, Tensor_size
  inc  r13
  jmp  .fix
.ok:
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret
.bad_rax:
  mov  rcx, rax
.bad:
  call rt_fatal

; sd_tensor_bytes(rcx=Tensor*) -> rax = the tensor's data size in bytes from
; its type and shape (blocks of 32 or 256 values for the quantised types), 0
; for a type this table does not know. Leaf; clobbers rax, rdx, r8, r9.
sd_tensor_bytes:
  mov  rax, [rcx + Tensor.ne0]
  imul rax, [rcx + Tensor.ne1]
  imul rax, [rcx + Tensor.ne2]
  imul rax, [rcx + Tensor.ne3] ; values
  mov  r8d, [rcx + Tensor.type]
  cmp  r8d, SD_TYPES
  jae  .unknown
  lea  r9, [rel sd_type_geom]
  movzx edx, word [r9 + r8*4]  ; bytes per block
  test edx, edx
  jz   .unknown
  movzx r8d, word [r9 + r8*4 + 2] ; values per block
  imul rax, rdx
  xor  edx, edx
  div  r8
  ret
.unknown:
  xor  eax, eax
  ret

; sd_find(rcx=zname) -> rax = the Tensor* whose name equals zname, or 0.
; Preserves the nonvolatile registers.
sd_find:
  push rbx
  push rsi
  push rdi
  mov  rsi, rcx
  mov  rdi, [rel g_sd_tensors]
  mov  rbx, [rel g_sd_n]
.l:
  test rbx, rbx
  jz   .none
  mov  rcx, [rdi + Tensor.name]
  mov  rdx, [rdi + Tensor.namelen]
  mov  r8, rsi
  call gguf_zeq
  test rax, rax
  jnz  .hit
  add  rdi, Tensor_size
  dec  rbx
  jmp  .l
.hit:
  mov  rax, rdi
  jmp  .ret
.none:
  xor  eax, eax
.ret:
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; sd_typename(rcx=ggml type) -> rax = zero-terminated name, or 0 if unknown. Leaf.
sd_typename:
  xor  eax, eax
  cmp  ecx, SD_TYPES
  jae  .ret
  lea  rax, [rel sd_type_names]
  mov  eax, [rax + rcx*4]      ; offset of the name in sd_type_names, 0 = none
  test eax, eax
  jz   .ret
  lea  rcx, [rel sd_type_names]
  add  rax, rcx
.ret:
  ret

; sd_list() — --sdinfo: "tensors=N alignment=A data_offset=D", then one line
; per tensor "name type ne0 ne1 ne2 ne3", then per type "type: count tensors,
; bytes", on stdout. Preserves the nonvolatile registers.
sd_list:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  lea  rcx, [rel sd_s_tensors]
  call rt_out_str
  mov  rcx, [rel g_sd_n]
  call rt_out_u64
  lea  rcx, [rel sd_s_align]
  call rt_out_str
  mov  rcx, [rel g_sd_align]
  call rt_out_u64
  lea  rcx, [rel sd_s_dataoff]
  call rt_out_str
  mov  rcx, [rel g_sd_data]
  sub  rcx, [rel g_sd_base]
  call rt_out_u64
  lea  rcx, [rel sd_s_nl]
  call rt_out_str
  mov  rdi, [rel g_sd_tensors]
  mov  rbx, [rel g_sd_n]
.l:
  test rbx, rbx
  jz   .totals
  mov  rcx, [rdi + Tensor.name]
  mov  rdx, [rdi + Tensor.namelen]
  call rt_out
  lea  rcx, [rel sd_s_sp]
  call rt_out_str
  mov  ecx, [rdi + Tensor.type]
  call sd_put_type
  xor  esi, esi
.dim:
  lea  rcx, [rel sd_s_sp]
  call rt_out_str
  mov  rcx, [rdi + Tensor.ne0 + rsi*8]
  call rt_out_u64
  inc  esi
  cmp  esi, 4
  jb   .dim
  lea  rcx, [rel sd_s_nl]
  call rt_out_str
  mov  eax, [rdi + Tensor.type] ; per-type count and bytes
  cmp  eax, SD_TYPES
  jae  .next
  lea  rcx, [rel g_sd_count]
  inc  qword [rcx + rax*8]
  mov  rdx, [rdi + Tensor.nbytes]
  lea  rcx, [rel g_sd_bytes]
  add  [rcx + rax*8], rdx
.next:
  add  rdi, Tensor_size
  dec  rbx
  jmp  .l
.totals:
  xor  ebx, ebx
.tl:
  cmp  ebx, SD_TYPES
  jae  .done
  lea  rax, [rel g_sd_count]
  cmp  qword [rax + rbx*8], 0
  je   .tn
  mov  ecx, ebx
  call sd_put_type
  lea  rcx, [rel sd_s_colon]
  call rt_out_str
  lea  rax, [rel g_sd_count]
  mov  rcx, [rax + rbx*8]
  call rt_out_u64
  lea  rcx, [rel sd_s_tens]
  call rt_out_str
  lea  rax, [rel g_sd_bytes]
  mov  rcx, [rax + rbx*8]
  call rt_out_u64
  lea  rcx, [rel sd_s_bytes]
  call rt_out_str
.tn:
  inc  ebx
  jmp  .tl
.done:
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; sd_kv() — --kv: every metadata key with its scalar value (integers, floats as
; fixed-point, strings cut at 200 bytes; arrays as "[type x count]") on stdout.
; Walks the header again from g_sd_base. Preserves the nonvolatile registers.
sd_kv:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 16
  mov  rsi, [rel g_sd_base]
  mov  r13, [rsi+16]           ; n_kv
  add  rsi, 24
.kv:
  test r13, r13
  jz   .done
  mov  r15, [rsi]              ; key length
  lea  r14, [rsi+8]
  lea  rsi, [rsi + 8 + r15]
  mov  edi, [rsi]              ; value type
  add  rsi, 4
  mov  rcx, r14
  mov  rdx, r15
  call rt_out
  lea  rcx, [rel sd_s_eq]
  call rt_out_str
  cmp  edi, GGUF_STRING
  je   .str
  cmp  edi, GGUF_ARRAY
  je   .arr
  cmp  edi, GGUF_F32
  je   .f32
  cmp  edi, GGUF_F64
  je   .f32
  mov  ecx, edi
  call gguf_read_int           ; rax, rsi advanced
  mov  rcx, rax
  call rt_out_u64
  jmp  .nl
.f32:
  mov  ecx, edi
  call gguf_read_f32           ; eax = f32 bits
  vmovd xmm0, eax
  call rt_err_f32              ; fixed-point (to stderr; the rest to stdout)
  jmp  .nl
.str:
  mov  rax, [rsi]
  add  rsi, 8
  mov  rdx, rax
  push rax
  push rax
  mov  rcx, r14                ; the chat template is printed whole
  mov  rdx, r15
  lea  r8, [rel sd_key_template]
  call gguf_zeq
  pop  rdx
  pop  rdx
  test rax, rax
  jnz  .sl
  cmp  rdx, 200
  jbe  .sl
  mov  edx, 200
.sl:
  mov  rax, rdx
  mov  rax, [rsi - 8]          ; the full length again (to advance)
  mov  rcx, rsi
  mov  [rsp+32], rax
  call rt_out
  add  rsi, [rsp+32]
  jmp  .nl
.arr:
  mov  eax, [rsi]              ; element type
  mov  rdx, [rsi+4]            ; count
  mov  [rsp+32], rdx
  lea  rcx, [rel sd_s_arr]
  mov  [rsp+40], rax
  call rt_out_str
  mov  rcx, [rsp+40]
  call rt_out_u64
  lea  rcx, [rel sd_s_x]
  call rt_out_str
  mov  rcx, [rsp+32]
  call rt_out_u64
  lea  rcx, [rel sd_s_arrend]
  call rt_out_str
  mov  ecx, GGUF_ARRAY
  call gguf_skip_value
.nl:
  lea  rcx, [rel sd_s_nl]
  call rt_out_str
  dec  r13
  jmp  .kv
.done:
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; sd_tensor_dump(rcx=zname) — --tensor NAME: the tensor's type, shape and its
; first 16 values (dequantised) on stdout. Preserves the nonvolatile registers.
sd_tensor_dump:
  push rbx
  push rsi
  WINPROLOGUE
  sub  rsp, 160                ; [rsp+32..160) 32 f32
  call sd_find
  test rax, rax
  jz   .none
  mov  rbx, rax
  mov  rcx, [rbx + Tensor.name]
  mov  rdx, [rbx + Tensor.namelen]
  call rt_out
  lea  rcx, [rel sd_s_sp]
  call rt_out_str
  mov  ecx, [rbx + Tensor.type]
  call sd_put_type
  xor  esi, esi
.dim:
  lea  rcx, [rel sd_s_sp]
  call rt_out_str
  mov  rcx, [rbx + Tensor.ne0 + rsi*8]
  call rt_out_u64
  inc  esi
  cmp  esi, 4
  jb   .dim
  lea  rcx, [rel sd_s_colon]
  call rt_out_str
  ; the first 32 values of row 0 (the block of a quantised type), 16 printed
  lea  rcx, [rsp+32]
  mov  rdx, [rbx + Tensor.data]
  mov  r8d, 32
  mov  r9d, [rbx + Tensor.type]
  cmp  r9d, GGML_F32
  je   .f32
  cmp  r9d, GGML_F16
  je   .f16
  cmp  r9d, 8                  ; q8_0
  je   .q8
  lea  rcx, [rel sd_s_noval]
  call rt_out_str
  jmp  .nl
.f32:
.f16:
.q8:
  call sg_deq_row
  xor  esi, esi
.v:
  cmp  esi, 16
  jae  .nl
  vmovss xmm0, [rsp+32 + rsi*4]
  call rt_err_f32
  lea  rcx, [rel sd_s_sp]
  call rt_out_str
  inc  esi
  jmp  .v
.nl:
  lea  rcx, [rel sd_s_nl]
  call rt_out_str
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret
.none:
  lea  rcx, [rel sd_s_notensor]
  call rt_out_str
  jmp  .nl

; sd_put_type(ecx=ggml type) — the type's name on stdout, or "type<N>".
sd_put_type:
  push rbx
  WINPROLOGUE
  mov  ebx, ecx
  call sd_typename
  test rax, rax
  jz   .num
  mov  rcx, rax
  call rt_out_str
  jmp  .ret
.num:
  lea  rcx, [rel sd_s_type]
  call rt_out_str
  mov  ecx, ebx
  call rt_out_u64
.ret:
  WINEPILOGUE
  pop  rbx
  ret

section .data
  align 8
g_sd_base:     dq 0            ; the mapped file
g_sd_size:     dq 0
g_sd_data:     dq 0            ; first byte of the tensor data
g_sd_align:    dq 32           ; general.alignment
g_sd_n:        dq 0            ; tensors
g_sd_tensors:  dq 0            ; -> Tensor[g_sd_n]
g_sd_count:    times SD_TYPES dq 0 ; sd_list: tensors per type
g_sd_bytes:    times SD_TYPES dq 0 ; sd_list: bytes per type

; ggml types (ggml.h): bytes per block, values per block (0 = not known here)
  align 4
sd_type_geom:
  dw 4, 1          ; 0  F32
  dw 2, 1          ; 1  F16
  dw 18, 32        ; 2  Q4_0
  dw 20, 32        ; 3  Q4_1
  dw 0, 0          ; 4
  dw 0, 0          ; 5
  dw 22, 32        ; 6  Q5_0
  dw 24, 32        ; 7  Q5_1
  dw 34, 32        ; 8  Q8_0
  dw 36, 32        ; 9  Q8_1
  dw 84, 256       ; 10 Q2_K
  dw 110, 256      ; 11 Q3_K
  dw 144, 256      ; 12 Q4_K
  dw 176, 256      ; 13 Q5_K
  dw 210, 256      ; 14 Q6_K
  dw 292, 256      ; 15 Q8_K
  times 14 dd 0    ; 16..29 (IQ types: not used by SD-Turbo files)
  dw 2, 1          ; 30 BF16
  times 9 dd 0     ; 31..39

; offsets of the type names in sd_type_names (0 = none)
sd_type_names:
  dd tn0 - sd_type_names, tn1 - sd_type_names, tn2 - sd_type_names, tn3 - sd_type_names
  dd 0, 0, tn6 - sd_type_names, tn7 - sd_type_names
  dd tn8 - sd_type_names, tn9 - sd_type_names, tn10 - sd_type_names, tn11 - sd_type_names
  dd tn12 - sd_type_names, tn13 - sd_type_names, tn14 - sd_type_names, tn15 - sd_type_names
  times 14 dd 0
  dd tn30 - sd_type_names
  times 9 dd 0
tn0:  db "f32", 0
tn1:  db "f16", 0
tn2:  db "q4_0", 0
tn3:  db "q4_1", 0
tn6:  db "q5_0", 0
tn7:  db "q5_1", 0
tn8:  db "q8_0", 0
tn9:  db "q8_1", 0
tn10: db "q2_K", 0
tn11: db "q3_K", 0
tn12: db "q4_K", 0
tn13: db "q5_K", 0
tn14: db "q6_K", 0
tn15: db "q8_K", 0
tn30: db "bf16", 0

sd_key_align:  db "general.alignment", 0
sd_key_template: db "tokenizer.chat_template", 0
sd_err_magic:  db "not a GGUF v3 file", 0
sd_err_short:  db "the GGUF file is truncated or malformed", 0
sd_err_dims:   db "a GGUF tensor has more than 4 dimensions", 0
sd_s_tensors:  db "sd: tensors=", 0
sd_s_align:    db " alignment=", 0
sd_s_dataoff:  db " data_offset=", 0
sd_s_sp:       db " ", 0
sd_s_colon:    db ": ", 0
sd_s_tens:     db " tensors, ", 0
sd_s_bytes:    db " bytes", 10, 0
sd_s_type:     db "type", 0
sd_s_nl:       db 10, 0
sd_s_eq:       db " = ", 0
sd_s_arr:      db "[type ", 0
sd_s_x:        db " x ", 0
sd_s_arrend:   db "]", 0
sd_s_noval:    db "(values of this type are not dumped)", 0
sd_s_notensor: db "no tensor of that name", 0
section .text

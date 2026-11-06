; gguf.asm — parse a mapped GGUF v3 file into the Model struct.
;
; %included into asmrun.asm (bits 64, default rel already set). The caller sets
; Model.file_base and Model.file_size (from rt_map_file) before gguf_open.
; Public: gguf_open(rcx=Model*), gguf_cli(rcx=Model*).
; Calls into runtime: rt_alloc, rt_fatal, rt_err, rt_err_str, rt_err_u64, rt_err_f32.
;
; GGUF v3 layout (little-endian):
;   u32 magic 'GGUF' | u32 version=3 | u64 n_tensors | u64 n_kv
;   n_kv  * [ gguf_string key | u32 value_type | value ]
;   n_tensors * [ gguf_string name | u32 ndims | u64 dims[ndims] | u32 type | u64 offset ]
;   pad to general.alignment (default 32) | tensor data
; gguf_string = u64 length + that many bytes. An array value = u32 elem_type +
; u64 count + packed elements.

; Stack-local offsets inside gguf_open's frame (above the 32-byte call shadow).
%define GO_VT     32          ; dword: current KV value type
%define GO_ALIGN  40          ; qword: general.alignment (default 32)
%define GO_NAME   48          ; 64 bytes: scratch for building "blk.N.suffix" names

; ---------------------------------------------------------------------------
; gguf_zeq(rcx=ptr, rdx=len, r8=zstr) -> rax = 1 if the len-byte string at ptr
; equals the zero-terminated string at zstr (same length and bytes), else 0.
; Leaf; clobbers rax, r9, r10 only; preserves rsi/rdi/rbx/r12-r15.
gguf_zeq:
  xor rax, rax                ; index into both strings
.cmp:
  mov r9b, [r8+rax]           ; next zstr byte
  test r9b, r9b
  jz .zend                    ; zstr terminated
  cmp rax, rdx
  jae .ne                     ; ptr exhausted but zstr continues
  mov r10b, [rcx+rax]
  cmp r9b, r10b
  jne .ne
  inc rax
  jmp .cmp
.zend:
  cmp rax, rdx                ; both must end at the same length
  jne .ne
  mov eax, 1
  ret
.ne:
  xor eax, eax
  ret

; ---------------------------------------------------------------------------
; gguf_keyeq(rcx=zstr) -> rax = 1 if the current KV key (r14=ptr, r15=len)
; equals zstr. Thin wrapper around gguf_zeq; preserves r14/r15.
gguf_keyeq:
  mov r8, rcx
  mov rcx, r14
  mov rdx, r15
  jmp gguf_zeq                ; tail call: returns rax to our caller

; ---------------------------------------------------------------------------
; gguf_read_int(rcx=gguf_value_type) -> rax, advancing the cursor rsi past the
; scalar. Integers and bools, width/sign by type. Leaf; clobbers rax, rsi.
gguf_read_int:
  cmp ecx, GGUF_U8
  je .u8
  cmp ecx, GGUF_I8
  je .i8
  cmp ecx, GGUF_U16
  je .u16
  cmp ecx, GGUF_I16
  je .i16
  cmp ecx, GGUF_U32
  je .u32
  cmp ecx, GGUF_I32
  je .i32
  cmp ecx, GGUF_BOOL
  je .u8
  cmp ecx, GGUF_U64
  je .u64
  cmp ecx, GGUF_I64
  je .u64
  jmp .u32                    ; default: treat as a 4-byte value
.u8:
  movzx eax, byte [rsi]
  add rsi, 1
  ret
.i8:
  movsx rax, byte [rsi]
  add rsi, 1
  ret
.u16:
  movzx eax, word [rsi]
  add rsi, 2
  ret
.i16:
  movsx rax, word [rsi]
  add rsi, 2
  ret
.u32:
  mov eax, [rsi]              ; zero-extends into rax
  add rsi, 4
  ret
.i32:
  movsxd rax, dword [rsi]
  add rsi, 4
  ret
.u64:
  mov rax, [rsi]
  add rsi, 8
  ret

; ---------------------------------------------------------------------------
; gguf_read_f32(rcx=gguf_value_type) -> eax = f32 bits, advancing rsi. F32 read
; directly; F64 narrowed with cvtsd2ss. Leaf; clobbers rax, rsi, xmm0.
gguf_read_f32:
  cmp ecx, GGUF_F64
  je .f64
  mov eax, [rsi]
  add rsi, 4
  ret
.f64:
  movsd xmm0, [rsi]
  add rsi, 8
  cvtsd2ss xmm0, xmm0
  movd eax, xmm0
  ret

; ---------------------------------------------------------------------------
; gguf_skip_value(rcx=gguf_value_type) advances rsi past one value whose body
; begins at rsi (scalar, string, or array of scalars/strings). Leaf; clobbers
; rax, rcx, r8, r9, rsi. Nested arrays do not occur in this model.
gguf_skip_value:
  cmp ecx, GGUF_STRING
  je .str
  cmp ecx, GGUF_ARRAY
  je .arr
  ; scalar: size by type
  mov rax, 1
  cmp ecx, GGUF_U8
  je .add
  cmp ecx, GGUF_I8
  je .add
  cmp ecx, GGUF_BOOL
  je .add
  mov rax, 2
  cmp ecx, GGUF_U16
  je .add
  cmp ecx, GGUF_I16
  je .add
  mov rax, 4
  cmp ecx, GGUF_U32
  je .add
  cmp ecx, GGUF_I32
  je .add
  cmp ecx, GGUF_F32
  je .add
  mov rax, 8                  ; U64 / I64 / F64
.add:
  add rsi, rax
  ret
.str:
  mov rax, [rsi]
  add rsi, 8
  add rsi, rax
  ret
.arr:
  mov r8d, [rsi]              ; element type
  mov r9,  [rsi+4]           ; element count
  add rsi, 12
  cmp r8d, GGUF_STRING
  je .arr_str
  ; fixed-size element: size by type
  mov rax, 1
  cmp r8d, GGUF_U8
  je .arr_fixed
  cmp r8d, GGUF_I8
  je .arr_fixed
  cmp r8d, GGUF_BOOL
  je .arr_fixed
  mov rax, 2
  cmp r8d, GGUF_U16
  je .arr_fixed
  cmp r8d, GGUF_I16
  je .arr_fixed
  mov rax, 4
  cmp r8d, GGUF_U32
  je .arr_fixed
  cmp r8d, GGUF_I32
  je .arr_fixed
  cmp r8d, GGUF_F32
  je .arr_fixed
  mov rax, 8
.arr_fixed:
  imul rax, r9
  add rsi, rax
  ret
.arr_str:
  test r9, r9
  jz .arr_done
.as_loop:
  mov rax, [rsi]
  add rsi, 8
  add rsi, rax
  dec r9
  jnz .as_loop
.arr_done:
  ret

; ---------------------------------------------------------------------------
; gguf_blk_prefix(rcx=outbuf, rdx=i) writes "blk.<i>." to outbuf and returns
; rax = number of bytes written (no NUL). Leaf; clobbers rax, rcx, rdx, r8-r11,
; and rbx (saved/restored). Used to build a layer tensor name to look up.
gguf_blk_prefix:
  push rbx
  sub rsp, 32                 ; scratch for the reversed decimal digits
  mov byte [rcx],   'b'
  mov byte [rcx+1], 'l'
  mov byte [rcx+2], 'k'
  mov byte [rcx+3], '.'
  lea r8, [rcx+4]             ; write pointer
  lea r9, [rsp+31]           ; digit buffer, filled backwards
  mov rax, rdx                ; i
  mov r10, 10
  test rax, rax
  jnz .conv
  mov byte [r9], '0'
  dec r9
.conv:
  test rax, rax
  jz .emit
  xor rdx, rdx
  div r10                     ; rax = quot, rdx = rem
  add dl, '0'
  mov [r9], dl
  dec r9
  jmp .conv
.emit:
  inc r9                      ; first digit
  lea r11, [rsp+31]          ; last digit
.copy:
  cmp r9, r11
  ja .after
  mov bl, [r9]
  mov [r8], bl
  inc r8
  inc r9
  jmp .copy
.after:
  mov byte [r8], '.'
  inc r8
  mov rax, r8
  sub rax, rcx                ; length written
  add rsp, 32
  pop rbx
  ret

; ---------------------------------------------------------------------------
; gguf_find_tensor(rcx=Model*, rdx=zname) -> rax = Tensor* whose name equals the
; zero-terminated zname, or 0 if none. Non-leaf (calls gguf_zeq).
gguf_find_tensor:
  push rbx
  push rsi
  push rdi
  push r12
  sub rsp, 40                 ; 32 shadow + 8 local slot, keeps rsp 16-aligned
  mov rsi, rdx                ; zname
  mov r12, [rcx + Model.n_tensors]
  mov rdi, [rcx + Model.tensors]
  xor rbx, rbx                ; index
.loop:
  cmp rbx, r12
  jae .notfound
  mov rax, rbx
  imul rax, rax, Tensor_size
  lea rax, [rdi + rax]        ; Tensor*
  mov rcx, [rax + Tensor.name]
  mov rdx, [rax + Tensor.namelen]
  mov r8, rsi
  mov [rsp+32], rax           ; save Tensor* across the call
  call gguf_zeq
  mov rcx, [rsp+32]
  test rax, rax
  jnz .found
  inc rbx
  jmp .loop
.found:
  mov rax, rcx
  jmp .done
.notfound:
  xor rax, rax
.done:
  add rsp, 40
  pop r12
  pop rdi
  pop rsi
  pop rbx
  ret

; ---------------------------------------------------------------------------
; gguf_typename(rcx=ggml_type) -> rax = zero-terminated type name. Leaf.
gguf_typename:
  cmp ecx, GGML_F32
  je .f32
  cmp ecx, GGML_F16
  je .f16
  cmp ecx, GGML_Q4_K
  je .q4
  cmp ecx, GGML_Q6_K
  je .q6
  lea rax, [rel tn_unk]
  ret
.f32:
  lea rax, [rel tn_f32]
  ret
.f16:
  lea rax, [rel tn_f16]
  ret
.q4:
  lea rax, [rel tn_q4]
  ret
.q6:
  lea rax, [rel tn_q6]
  ret

; ---------------------------------------------------------------------------
; gguf_open(rcx=Model*) parses the mapped GGUF into the Model: header, every KV
; pair (needed scalars/arrays stored, the rest skipped), the tensor table, the
; aligned data_base, resolves named tensors and the per-layer Layer records, and
; fatals on a bad magic/version/architecture, an unsupported tensor type, or a
; missing required tensor. Non-leaf (Win32 x64; WINPROLOGUE frame).
gguf_open:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub rsp, 96                 ; call shadow (low 32) + locals (GO_VT/GO_ALIGN/GO_NAME)

  mov rdi, rcx                ; Model*
  mov rsi, [rdi + Model.file_base]   ; parse cursor

  ; --- header ---
  mov eax, [rsi]
  cmp eax, 0x46554747         ; 'GGUF'
  je .magic_ok
  lea rcx, [rel m_magic]
  call rt_fatal
.magic_ok:
  mov eax, [rsi+4]
  cmp eax, 3
  je .ver_ok
  lea rcx, [rel m_ver]
  call rt_fatal
.ver_ok:
  mov rax, [rsi+8]            ; tensor count
  mov [rdi + Model.n_tensors], rax
  mov r13, [rsi+16]          ; kv count
  add rsi, 24
  mov qword [rsp+GO_ALIGN], 32

  ; --- metadata KV pairs ---
.kv_loop:
  test r13, r13
  jz .kv_done
  mov r15, [rsi]             ; key length
  add rsi, 8
  mov r14, rsi               ; key pointer
  add rsi, r15               ; past key bytes
  mov eax, [rsi]             ; value type
  add rsi, 4
  mov [rsp+GO_VT], eax

  lea rcx, [rel k_arch]
  call gguf_keyeq
  test rax, rax
  jnz .kv_arch
  lea rcx, [rel k_align]
  call gguf_keyeq
  test rax, rax
  jnz .kv_align
  lea rcx, [rel k_block]
  call gguf_keyeq
  test rax, rax
  jnz .kv_block
  lea rcx, [rel k_embd]
  call gguf_keyeq
  test rax, rax
  jnz .kv_embd
  lea rcx, [rel k_ff]
  call gguf_keyeq
  test rax, rax
  jnz .kv_ff
  lea rcx, [rel k_nhead]
  call gguf_keyeq
  test rax, rax
  jnz .kv_nhead
  lea rcx, [rel k_nheadkv]
  call gguf_keyeq
  test rax, rax
  jnz .kv_nheadkv
  lea rcx, [rel k_keylen]
  call gguf_keyeq
  test rax, rax
  jnz .kv_keylen
  lea rcx, [rel k_ctx]
  call gguf_keyeq
  test rax, rax
  jnz .kv_ctx
  lea rcx, [rel k_rope_key]
  call gguf_keyeq
  test rax, rax
  jnz .kv_rope
  lea rcx, [rel k_eps]
  call gguf_keyeq
  test rax, rax
  jnz .kv_eps
  lea rcx, [rel k_tokens]
  call gguf_keyeq
  test rax, rax
  jnz .kv_tokens
  lea rcx, [rel k_merges]
  call gguf_keyeq
  test rax, rax
  jnz .kv_merges
  lea rcx, [rel k_toktype]
  call gguf_keyeq
  test rax, rax
  jnz .kv_toktype
  lea rcx, [rel k_eos]
  call gguf_keyeq
  test rax, rax
  jnz .kv_eos
  lea rcx, [rel k_bos]
  call gguf_keyeq
  test rax, rax
  jnz .kv_bos
  lea rcx, [rel k_pad]
  call gguf_keyeq
  test rax, rax
  jnz .kv_pad
  ; unknown key: advance past its value
  mov ecx, [rsp+GO_VT]
  call gguf_skip_value
  jmp .kv_next

.kv_arch:
  mov r8, [rsi]              ; value string length
  add rsi, 8
  mov rcx, rsi               ; value pointer
  mov rdx, r8
  add rsi, r8                ; past value
  lea r8, [rel v_qwen3]
  call gguf_zeq
  test rax, rax
  jnz .kv_next
  lea rcx, [rel m_arch]
  call rt_fatal
.kv_align:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rsp+GO_ALIGN], rax
  jmp .kv_next
.kv_block:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.n_layer], rax
  jmp .kv_next
.kv_embd:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.n_embd], rax
  jmp .kv_next
.kv_ff:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.n_ff], rax
  jmp .kv_next
.kv_nhead:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.n_head], rax
  jmp .kv_next
.kv_nheadkv:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.n_head_kv], rax
  jmp .kv_next
.kv_keylen:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.head_dim], rax
  jmp .kv_next
.kv_ctx:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.n_ctx_train], rax
  jmp .kv_next
.kv_rope:
  mov ecx, [rsp+GO_VT]
  call gguf_read_f32
  mov [rdi + Model.rope_theta], eax
  jmp .kv_next
.kv_eps:
  mov ecx, [rsp+GO_VT]
  call gguf_read_f32
  mov [rdi + Model.rms_eps], eax
  jmp .kv_next
.kv_tokens:
  mov rdx, [rsi+4]           ; count
  lea r8, [rsi+12]           ; first token's u64 length field
  mov [rdi + Model.tokens_arr], r8
  mov [rdi + Model.n_tokens], rdx
  mov ecx, GGUF_ARRAY
  call gguf_skip_value
  jmp .kv_next
.kv_merges:
  mov rdx, [rsi+4]
  lea r8, [rsi+12]
  mov [rdi + Model.merges_arr], r8
  mov [rdi + Model.n_merges], rdx
  mov ecx, GGUF_ARRAY
  call gguf_skip_value
  jmp .kv_next
.kv_toktype:
  lea r8, [rsi+12]           ; first i32 element
  mov [rdi + Model.token_type_arr], r8
  mov ecx, GGUF_ARRAY
  call gguf_skip_value
  jmp .kv_next
.kv_eos:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.eos_id], rax
  jmp .kv_next
.kv_bos:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.bos_id], rax
  jmp .kv_next
.kv_pad:
  mov ecx, [rsp+GO_VT]
  call gguf_read_int
  mov [rdi + Model.pad_id], rax
  jmp .kv_next
.kv_next:
  dec r13
  jmp .kv_loop
.kv_done:

  ; --- tensor info table ---
  mov rax, [rdi + Model.n_tensors]
  imul rax, rax, Tensor_size
  mov rcx, rax
  call rt_alloc
  mov [rdi + Model.tensors], rax

  xor rbx, rbx                ; tensor index
.tloop:
  cmp rbx, [rdi + Model.n_tensors]
  jae .tdone
  mov r12, [rdi + Model.tensors]
  mov rax, rbx
  imul rax, rax, Tensor_size
  add r12, rax               ; Tensor*
  ; name
  mov rax, [rsi]             ; name length
  add rsi, 8
  mov [r12 + Tensor.name], rsi
  mov [r12 + Tensor.namelen], rax
  add rsi, rax
  ; ndims
  mov eax, [rsi]
  add rsi, 4
  mov [r12 + Tensor.ndims], eax
  ; default dims = 1
  mov qword [r12 + Tensor.ne0], 1
  mov qword [r12 + Tensor.ne1], 1
  mov qword [r12 + Tensor.ne2], 1
  mov qword [r12 + Tensor.ne3], 1
  mov ecx, eax               ; ndims (<= 4)
  lea r8, [r12 + Tensor.ne0]
.dloop:
  test ecx, ecx
  jz .ddone
  mov rax, [rsi]
  add rsi, 8
  mov [r8], rax
  add r8, 8
  dec ecx
  jmp .dloop
.ddone:
  ; type
  mov eax, [rsi]
  add rsi, 4
  mov [r12 + Tensor.type], eax
  ; offset (stored in .data for now; data_base added below)
  mov rax, [rsi]
  add rsi, 8
  mov [r12 + Tensor.data], rax
  ; validate type and compute nbytes = elements * per-type size
  mov eax, [r12 + Tensor.type]
  mov rcx, [r12 + Tensor.ne0]
  imul rcx, [r12 + Tensor.ne1]
  imul rcx, [r12 + Tensor.ne2]
  imul rcx, [r12 + Tensor.ne3]
  cmp eax, GGML_F32
  je .nb_f32
  cmp eax, GGML_F16
  je .nb_f16
  cmp eax, GGML_Q4_K
  je .nb_q4k
  cmp eax, GGML_Q6_K
  je .nb_q6k
  mov rcx, [r12 + Tensor.name]
  mov rdx, [r12 + Tensor.namelen]
  call rt_err                 ; name the offending tensor, then fatal
  lea rcx, [rel m_type]
  call rt_fatal
.nb_f32:
  shl rcx, 2
  jmp .nb_store
.nb_f16:
  shl rcx, 1
  jmp .nb_store
.nb_q4k:
  shr rcx, 8                  ; / QK_K blocks
  imul rcx, rcx, Q4K_BYTES
  jmp .nb_store
.nb_q6k:
  shr rcx, 8
  imul rcx, rcx, Q6K_BYTES
.nb_store:
  mov [r12 + Tensor.nbytes], rcx
  inc rbx
  jmp .tloop
.tdone:

  ; --- data_base = file_base + align_up(header_end_offset, alignment) ---
  mov rax, rsi
  sub rax, [rdi + Model.file_base]   ; header end offset
  mov rcx, [rsp+GO_ALIGN]
  dec rcx
  add rax, rcx                ; + (align - 1)
  not rcx                     ; ~(align - 1)
  and rax, rcx
  add rax, [rdi + Model.file_base]
  mov [rdi + Model.data_base], rax

  ; resolve each tensor's .data = data_base + stored offset
  xor rbx, rbx
.fix:
  cmp rbx, [rdi + Model.n_tensors]
  jae .fixdone
  mov r12, [rdi + Model.tensors]
  mov rcx, rbx
  imul rcx, rcx, Tensor_size
  add r12, rcx
  mov rcx, [r12 + Tensor.data]       ; offset
  add rcx, rax                       ; + data_base (rax preserved, no calls here)
  mov [r12 + Tensor.data], rcx
  inc rbx
  jmp .fix
.fixdone:

  ; --- resolve named tensors ---
  mov rcx, rdi
  lea rdx, [rel t_tok_embd]
  call gguf_find_tensor
  mov [rdi + Model.tok_embd], rax
  mov rcx, rdi
  lea rdx, [rel t_output]
  call gguf_find_tensor
  mov [rdi + Model.output], rax
  mov rcx, rdi
  lea rdx, [rel t_output_norm]
  call gguf_find_tensor
  mov [rdi + Model.output_norm], rax

  ; allocate Layer[n_layer]
  mov rax, [rdi + Model.n_layer]
  imul rax, rax, Layer_size
  mov rcx, rax
  call rt_alloc
  mov [rdi + Model.layers], rax

  xor rbx, rbx                ; layer index i
.layer_loop:
  cmp rbx, [rdi + Model.n_layer]
  jae .layers_done
  lea rsi, [rel blk_tab]      ; suffix/field-offset table
.suf_loop:
  mov r12, [rsi]             ; suffix offset from blk_tab (0 terminates)
  test r12, r12
  jz .suf_done
  lea rax, [rel blk_tab]
  add r12, rax               ; suffix zstr (the table holds offsets: the image is position-independent)
  mov r13, [rsi+8]           ; Layer field offset
  ; build "blk.<i>." then append the suffix into GO_NAME
  lea rcx, [rsp+GO_NAME]
  mov rdx, rbx
  call gguf_blk_prefix        ; rax = prefix length
  lea r8, [rsp+GO_NAME]
  add r8, rax                 ; append point
  mov r9, r12                 ; suffix source
.app:
  mov r10b, [r9]
  mov [r8], r10b
  test r10b, r10b
  jz .app_done
  inc r8
  inc r9
  jmp .app
.app_done:
  mov rcx, rdi
  lea rdx, [rsp+GO_NAME]
  call gguf_find_tensor       ; rax = Tensor* or 0
  mov r8, [rdi + Model.layers]
  mov r9, rbx
  imul r9, r9, Layer_size
  add r8, r9
  add r8, r13                 ; field slot
  mov [r8], rax
  add rsi, 16
  jmp .suf_loop
.suf_done:
  inc rbx
  jmp .layer_loop
.layers_done:

  ; --- required tensors present ---
  cmp qword [rdi + Model.tok_embd], 0
  je .missing
  cmp qword [rdi + Model.output], 0
  je .missing
  cmp qword [rdi + Model.output_norm], 0
  je .missing
  xor rbx, rbx
.chk_layer:
  cmp rbx, [rdi + Model.n_layer]
  jae .chk_done
  mov r8, [rdi + Model.layers]
  mov r9, rbx
  imul r9, r9, Layer_size
  add r8, r9
  xor rcx, rcx
.chk_field:
  cmp rcx, Layer_size
  jae .chk_next
  mov rax, [r8 + rcx]
  test rax, rax
  jz .missing
  add rcx, 8
  jmp .chk_field
.chk_next:
  inc rbx
  jmp .chk_layer
.missing:
  lea rcx, [rel m_miss]
  call rt_fatal
.chk_done:

  ; --- derived scalars ---
  mov rax, [rdi + Model.n_tokens]
  mov [rdi + Model.n_vocab], rax
  mov rax, [rdi + Model.head_dim]
  test rax, rax
  jnz .hd_ok
  mov rax, [rdi + Model.n_embd]      ; default head_dim = n_embd / n_head
  xor rdx, rdx
  mov rcx, [rdi + Model.n_head]
  div rcx
  mov [rdi + Model.head_dim], rax
.hd_ok:
  mov rax, [rdi + Model.n_head_kv]   ; kv_dim = n_head_kv * head_dim
  imul rax, [rdi + Model.head_dim]
  mov [rdi + Model.kv_dim], rax

  WINEPILOGUE
  pop r15
  pop r14
  pop r13
  pop r12
  pop rdi
  pop rsi
  pop rbx
  ret

; ---------------------------------------------------------------------------
; gguf_cli(rcx=Model*) prints the scalar metadata, the per-type tensor counts,
; and the first 20 tensors (name, type, dims) to stderr. Non-leaf (WINPROLOGUE;
; calls rt_err_*). Counts live in [rsp+32 .. rsp+64).
gguf_cli:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub rsp, 64                 ; low 32 = call shadow, next 32 = four count slots
  mov rdi, rcx                ; Model*

  ; line 1: dimensions
  lea rcx, [rel c_l1]
  call rt_err_str
  mov rcx, [rdi + Model.n_layer]
  call rt_err_u64
  lea rcx, [rel c_embd]
  call rt_err_str
  mov rcx, [rdi + Model.n_embd]
  call rt_err_u64
  lea rcx, [rel c_ff]
  call rt_err_str
  mov rcx, [rdi + Model.n_ff]
  call rt_err_u64
  lea rcx, [rel c_heads]
  call rt_err_str
  mov rcx, [rdi + Model.n_head]
  call rt_err_u64
  lea rcx, [rel c_slash]
  call rt_err_str
  mov rcx, [rdi + Model.n_head_kv]
  call rt_err_u64
  lea rcx, [rel c_hdim]
  call rt_err_str
  mov rcx, [rdi + Model.head_dim]
  call rt_err_u64
  lea rcx, [rel c_vocab]
  call rt_err_str
  mov rcx, [rdi + Model.n_vocab]
  call rt_err_u64
  lea rcx, [rel c_ctx]
  call rt_err_str
  mov rcx, [rdi + Model.n_ctx_train]
  call rt_err_u64
  lea rcx, [rel c_nl]
  call rt_err_str

  ; line 2: rope/eps/token ids/merges
  lea rcx, [rel c_rope]
  call rt_err_str
  movss xmm0, [rdi + Model.rope_theta]
  call rt_err_f32
  lea rcx, [rel c_eps]
  call rt_err_str
  movss xmm0, [rdi + Model.rms_eps]
  call rt_err_f32
  lea rcx, [rel c_eos]
  call rt_err_str
  mov rcx, [rdi + Model.eos_id]
  call rt_err_u64
  lea rcx, [rel c_bos]
  call rt_err_str
  mov rcx, [rdi + Model.bos_id]
  call rt_err_u64
  lea rcx, [rel c_pad]
  call rt_err_str
  mov rcx, [rdi + Model.pad_id]
  call rt_err_u64
  lea rcx, [rel c_merges]
  call rt_err_str
  mov rcx, [rdi + Model.n_merges]
  call rt_err_u64
  lea rcx, [rel c_nl]
  call rt_err_str

  ; count tensors per type
  mov qword [rsp+32], 0
  mov qword [rsp+40], 0
  mov qword [rsp+48], 0
  mov qword [rsp+56], 0
  xor rbx, rbx
.cloop:
  cmp rbx, [rdi + Model.n_tensors]
  jae .cdone
  mov r12, [rdi + Model.tensors]
  mov rax, rbx
  imul rax, rax, Tensor_size
  add r12, rax
  mov eax, [r12 + Tensor.type]
  cmp eax, GGML_F32
  je .c0
  cmp eax, GGML_F16
  je .c1
  cmp eax, GGML_Q4_K
  je .c2
  cmp eax, GGML_Q6_K
  je .c3
  jmp .cnext
.c0:
  inc qword [rsp+32]
  jmp .cnext
.c1:
  inc qword [rsp+40]
  jmp .cnext
.c2:
  inc qword [rsp+48]
  jmp .cnext
.c3:
  inc qword [rsp+56]
.cnext:
  inc rbx
  jmp .cloop
.cdone:

  ; line 3: totals
  lea rcx, [rel c_tn]
  call rt_err_str
  mov rcx, [rdi + Model.n_tensors]
  call rt_err_u64
  lea rcx, [rel c_tf32]
  call rt_err_str
  mov rcx, [rsp+32]
  call rt_err_u64
  lea rcx, [rel c_tf16]
  call rt_err_str
  mov rcx, [rsp+40]
  call rt_err_u64
  lea rcx, [rel c_tq4]
  call rt_err_str
  mov rcx, [rsp+48]
  call rt_err_u64
  lea rcx, [rel c_tq6]
  call rt_err_str
  mov rcx, [rsp+56]
  call rt_err_u64
  lea rcx, [rel c_nl]
  call rt_err_str

  ; first 20 tensors
  xor rbx, rbx
.ploop:
  cmp rbx, 20
  jae .pdone
  cmp rbx, [rdi + Model.n_tensors]
  jae .pdone
  mov r12, [rdi + Model.tensors]
  mov rax, rbx
  imul rax, rax, Tensor_size
  add r12, rax
  lea rcx, [rel c_ind]
  call rt_err_str
  mov rcx, [r12 + Tensor.name]
  mov rdx, [r12 + Tensor.namelen]
  call rt_err
  lea rcx, [rel c_type]
  call rt_err_str
  mov ecx, [r12 + Tensor.type]
  call gguf_typename
  mov rcx, rax
  call rt_err_str
  lea rcx, [rel c_dims]
  call rt_err_str
  mov rcx, [r12 + Tensor.ne0]
  call rt_err_u64
  lea rcx, [rel c_x]
  call rt_err_str
  mov rcx, [r12 + Tensor.ne1]
  call rt_err_u64
  lea rcx, [rel c_nl]
  call rt_err_str
  inc rbx
  jmp .ploop
.pdone:

  WINEPILOGUE
  pop r13
  pop r12
  pop rdi
  pop rsi
  pop rbx
  ret

; ---------------------------------------------------------------------------
; gguf.asm read-only data: metadata keys, tensor names, suffix table, messages.
section .data

k_arch:     db "general.architecture", 0
k_align:    db "general.alignment", 0
k_block:    db "qwen3.block_count", 0
k_embd:     db "qwen3.embedding_length", 0
k_ff:       db "qwen3.feed_forward_length", 0
k_nhead:    db "qwen3.attention.head_count", 0
k_nheadkv:  db "qwen3.attention.head_count_kv", 0
k_keylen:   db "qwen3.attention.key_length", 0
k_ctx:      db "qwen3.context_length", 0
k_rope_key: db "qwen3.rope.freq_base", 0
k_eps:      db "qwen3.attention.layer_norm_rms_epsilon", 0
k_tokens:   db "tokenizer.ggml.tokens", 0
k_merges:   db "tokenizer.ggml.merges", 0
k_toktype:  db "tokenizer.ggml.token_type", 0
k_eos:      db "tokenizer.ggml.eos_token_id", 0
k_bos:      db "tokenizer.ggml.bos_token_id", 0
k_pad:      db "tokenizer.ggml.padding_token_id", 0
v_qwen3:    db "qwen3", 0

t_tok_embd:    db "token_embd.weight", 0
t_output:      db "output.weight", 0
t_output_norm: db "output_norm.weight", 0

s_attn_norm:   db "attn_norm.weight", 0
s_attn_q:      db "attn_q.weight", 0
s_attn_k:      db "attn_k.weight", 0
s_attn_v:      db "attn_v.weight", 0
s_attn_output: db "attn_output.weight", 0
s_attn_q_norm: db "attn_q_norm.weight", 0
s_attn_k_norm: db "attn_k_norm.weight", 0
s_ffn_norm:    db "ffn_norm.weight", 0
s_ffn_gate:    db "ffn_gate.weight", 0
s_ffn_up:      db "ffn_up.weight", 0
s_ffn_down:    db "ffn_down.weight", 0

; Each entry: the suffix string's offset from blk_tab (never an absolute
; address: the UEFI build is loaded wherever the firmware likes), then the
; Layer field that receives the tensor.
  align 8
blk_tab:
  dq s_attn_norm - blk_tab,   Layer.attn_norm
  dq s_attn_q - blk_tab,      Layer.attn_q
  dq s_attn_k - blk_tab,      Layer.attn_k
  dq s_attn_v - blk_tab,      Layer.attn_v
  dq s_attn_output - blk_tab, Layer.attn_output
  dq s_attn_q_norm - blk_tab, Layer.attn_q_norm
  dq s_attn_k_norm - blk_tab, Layer.attn_k_norm
  dq s_ffn_norm - blk_tab,    Layer.ffn_norm
  dq s_ffn_gate - blk_tab,    Layer.ffn_gate
  dq s_ffn_up - blk_tab,      Layer.ffn_up
  dq s_ffn_down - blk_tab,    Layer.ffn_down
  dq 0, 0

tn_f32: db "F32", 0
tn_f16: db "F16", 0
tn_q4:  db "Q4_K", 0
tn_q6:  db "Q6_K", 0
tn_unk: db "?", 0

m_magic: db "gguf: not a GGUF file (bad magic)", 0
m_ver:   db "gguf: unsupported GGUF version (need 3)", 0
m_arch:  db "gguf: unsupported architecture (need qwen3)", 0
m_type:  db " : unsupported tensor type", 0
m_miss:  db "gguf: missing required tensor", 0

c_l1:     db "asmrun: arch=qwen3 layers=", 0
c_embd:   db " embd=", 0
c_ff:     db " ff=", 0
c_heads:  db " heads=", 0
c_slash:  db "/", 0
c_hdim:   db " head_dim=", 0
c_vocab:  db " vocab=", 0
c_ctx:    db " ctx=", 0
c_rope:   db "asmrun: rope_theta=", 0
c_eps:    db " rms_eps=", 0
c_eos:    db " eos=", 0
c_bos:    db " bos=", 0
c_pad:    db " pad=", 0
c_merges: db " merges=", 0
c_tn:     db "asmrun: tensors=", 0
c_tf32:   db " F32=", 0
c_tf16:   db " F16=", 0
c_tq4:    db " Q4_K=", 0
c_tq6:    db " Q6_K=", 0
c_ind:    db "  ", 0
c_type:   db " type=", 0
c_dims:   db " dims=", 0
c_x:      db "x", 0
c_nl:     db 10, 0

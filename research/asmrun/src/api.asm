; api.asm — Ollama's API, served by the runner, so Logos on the phone keeps its
; client and only its settings change (owner, 2026-11-08: no Ollama any more,
; replace it). Routes, dispatched from http.asm:
;
;   POST /api/chat     {"model","messages":[{role,content,tool_calls,tool_name}],
;                       "tools":[...],"stream",...}  -> one NDJSON line:
;                      {"model","created_at","message":{"role":"assistant",
;                       "content","tool_calls":[{"function":{"name","arguments"}}]},
;                       "done":true,"done_reason":"stop"|"length",
;                       "prompt_eval_count","eval_count"}
;   GET  /api/version  {"version":"0.1.0-asmrun"}
;   GET  /api/tags     {"models":[{...the loaded model...}]}
;   GET  /api/ps       {"models":[{...}]}
;   POST /api/show     {"parameters":"","details":{...}}
;   POST /api/embed    501: no embedding model is loaded (the next step)
;
; The dialog is Ollama's: stateless, the whole message list every time. The
; model keeps one context from boot (owner, 2026-11-08: "just keep one context
; since boot"): the first request renders the system turn (the system
; messages, then the request's tools verbatim in the <tools> block: the entries
; are already the lines Qwen's template writes) and every message; a later
; request renders only the messages after the ones already fed, the model's
; own answer counted as fed. Nothing is ever reset: a new dialog in the client
; simply continues the model's context, until the context is full. The
; model's <tool_call> blocks come back as Ollama tool_calls. The runner's own
; file tools are off in API mode: the caller runs its tools.
;
; Only the Qwen3.5 path has a continuing state; a Qwen3-family model answers
; 501 here.

section .text

API_RENDER_CAP equ 1048576           ; the rendered prompt text
API_TEXT_CAP   equ 1048576           ; the text the model has processed
API_IDS_CAP    equ 262144            ; ids of the fed tail
API_OUT_CAP    equ 2097152           ; the JSON answer (escaped)
API_STR_CAP    equ 65536             ; one unescaped JSON string

MK_IM_START equ 1
MK_IM_END   equ 2
MK_THINK    equ 3
MK_THINK_END equ 4

; ---------------------------------------------------------------------------
; api_route(rcx=conn, rdx=method/path start (the request line), r8=body,
; r9=body length) -> rax = 0: handled and closed by the caller; 1: a chat
; request was accepted (g_api_mode = 1, the ids are ready, the connection is
; kept for the answer); 2: not an API path (http.asm goes on with its own).
; ---------------------------------------------------------------------------
api_route:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  sub  rsp, 32
  mov  rbx, rcx                ; conn
  mov  rsi, rdx                ; request line
  mov  r12, r8                 ; body
  mov  r13, r9                 ; body length
  cmp  qword [rel g_api_render], 0
  jne  .bufs
  mov  rcx, API_RENDER_CAP
  call rt_alloc
  mov  [rel g_api_render], rax
  mov  rcx, API_TEXT_CAP
  call rt_alloc
  mov  [rel g_api_text], rax
  mov  rcx, API_IDS_CAP * 4
  call rt_alloc
  mov  [rel g_api_ids], rax
  mov  rcx, API_OUT_CAP
  call rt_alloc
  mov  [rel g_api_out], rax
  mov  rcx, API_STR_CAP
  call rt_alloc
  mov  [rel g_api_str], rax
  lea  rax, [rel api_s_defname]
  mov  [rel g_model_name], rax
  mov  qword [rel g_model_namelen], API_S_DEFNAME_LEN
.bufs:
  cmp  dword [rsi], 'GET '
  jne  .post
  lea  rdi, [rsi + 4]
  lea  rdx, [rel api_p_version]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .version
  lea  rdx, [rel api_p_tags]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .models
  lea  rdx, [rel api_p_ps]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .models
  lea  rdx, [rel api_p_status]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .status
  jmp  .notapi
.status:
  call api_status_json
  mov  r9, [rel g_api_out]
  mov  [rsp+32], rax
  jmp  .json200
.post:
  cmp  dword [rsi], 'POST'
  jne  .notapi
  lea  rdi, [rsi + 5]
  lea  rdx, [rel api_p_turn]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .turn
  lea  rdx, [rel api_p_chat]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .chat
  lea  rdx, [rel api_p_show]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .show
  lea  rdx, [rel api_p_embed]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .embed
  lea  rdx, [rel api_p_embeddings]
  mov  rcx, rdi
  call api_path_is
  test rax, rax
  jnz  .embed
  jmp  .notapi
.version:
  lea  r9, [rel api_j_version]
  mov  qword [rsp+32], API_J_VERSION_LEN
  jmp  .json200
.show:
  lea  r9, [rel api_j_show]
  mov  qword [rsp+32], API_J_SHOW_LEN
  jmp  .json200
.models:
  call api_models_json         ; rax = length in g_api_out
  mov  r9, [rel g_api_out]
  mov  [rsp+32], rax
.json200:
  mov  rcx, rbx
  lea  rdx, [rel http_s_200]
  lea  r8, [rel api_s_ct_json]
  call http_respond
  xor  eax, eax
  jmp  .ret
.embed:
  cmp  qword [rel g_embed_model], 0
  je   .noembed
  mov  rcx, r12
  mov  rdx, r13
  call api_embed_json          ; rax = length in g_api_out, or 0 when the body has no "input"
  test rax, rax
  jz   .embed_bad
  mov  r9, [rel g_api_out]
  mov  [rsp+32], rax
  jmp  .json200
.embed_bad:
  mov  rcx, rbx
  lea  rdx, [rel http_s_400]
  lea  r8, [rel api_s_ct_json]
  lea  r9, [rel api_j_noinput]
  mov  qword [rsp+32], API_J_NOINPUT_LEN
  call http_respond
  xor  eax, eax
  jmp  .ret
.noembed:
  mov  rcx, rbx
  lea  rdx, [rel api_s_501]
  lea  r8, [rel api_s_ct_json]
  lea  r9, [rel api_j_noembed]
  mov  qword [rsp+32], API_J_NOEMBED_LEN
  call http_respond
  xor  eax, eax
  jmp  .ret
.chat:
  ; a JSON body is Ollama's chat; a plain body stays http.asm's own API
  mov  rcx, r12
  mov  rdx, r13
  call js_ws                   ; rax = first non-blank or end
  lea  rdx, [r12 + r13]
  cmp  rax, rdx
  jae  .notapi
  cmp  byte [rax], '{'
  jne  .notapi
  mov  rax, [rel g35_model]
  test rax, rax
  jz   .noq35
  call rt_now_ns
  mov  [rel g_api_created], rax
  mov  rcx, r12
  mov  rdx, r13
  call api_chat_prepare        ; rax = 0 ok, else an error text in g_api_err
  test rax, rax
  jnz  .bad
  mov  qword [rel g_api_mode], 1
<<<<<<< ours
  mov  qword [rel g_tools_off], 1 ; the caller runs the tools
=======
>>>>>>> theirs
  mov  eax, 1
  jmp  .ret
.turn:
  mov  rax, [rel g35_model]
  test rax, rax
  jz   .noq35
  mov  rcx, r12
  mov  rdx, r13
  call api_turn_prepare        ; rax = 0 ok, else g_api_err
  test rax, rax
  jnz  .bad
  mov  qword [rel g_api_mode], 2
<<<<<<< ours
  mov  qword [rel g_tools_off], 1
=======
>>>>>>> theirs
  mov  eax, 1
  jmp  .ret
.noq35:
  mov  rcx, rbx
  lea  rdx, [rel api_s_501]
  lea  r8, [rel api_s_ct_json]
  lea  r9, [rel api_j_noq35]
  mov  qword [rsp+32], API_J_NOQ35_LEN
  call http_respond
  xor  eax, eax
  jmp  .ret
.bad:
  mov  rcx, rbx
  lea  rdx, [rel http_s_400]
  lea  r8, [rel api_s_ct_json]
  mov  r9, [rel g_api_err]
  mov  rax, [rel g_api_errlen]
  mov  [rsp+32], rax
  call http_respond
  xor  eax, eax
  jmp  .ret
.notapi:
  mov  eax, 2
.ret:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; api_path_is(rcx=path start, rdx=zstr) -> rax = 1 when the path equals zstr
; and is followed by ' ' or '?'.
api_path_is:
  xor  eax, eax
.c:
  mov  r8b, [rdx]
  test r8b, r8b
  jz   .end
  cmp  r8b, [rcx]
  jne  .no
  inc  rcx
  inc  rdx
  jmp  .c
.end:
  mov  r8b, [rcx]
  cmp  r8b, ' '
  je   .yes
  cmp  r8b, '?'
  jne  .no
.yes:
  mov  eax, 1
.no:
  ret

; ---------------------------------------------------------------------------
; The chat request: render the messages, compare with the processed text,
; encode the tail. Sets g_api_nids / g_api_reset; the log line is the last user
; message (g_api_user / g_api_userlen).
; ---------------------------------------------------------------------------
; api_chat_prepare(rcx=body, rdx=len) -> rax = 0 ok, 1 error (g_api_err set).
api_chat_prepare:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 48
  mov  rbx, rcx                ; body
  lea  r12, [rcx + rdx]        ; end
  mov  rdi, [rel g_api_render] ; write cursor
  mov  [rsp+32], rdi           ; render start
  mov  qword [rel g_api_userlen], 0
  mov  qword [rel g_api_ntools], 0
  mov  rcx, rbx
  mov  rdx, r12
  call js_ws
  mov  rbx, rax                ; the object
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_messages]
  call js_find_key
  test rax, rax
  jz   .nomsgs
  mov  r13, rax                ; the messages array
  cmp  byte [r13], '['
  jne  .nomsgs
  ; how many messages; which is the first new one. One context since boot
  ; (owner): the first request renders everything, a later one only the
  ; messages after the ones already fed, counting the model's own answer as fed.
  mov  rcx, r13
  mov  rdx, r12
  call js_arr_first
  xor  ecx, ecx
.count:
  test rax, rax
  jz   .counted
  inc  rcx
  mov  [rsp+64], rcx
  mov  rdx, r12
  mov  rcx, rax
  call js_arr_next
  mov  rcx, [rsp+64]
  jmp  .count
.counted:
  mov  [rsp+64], rcx           ; N
  mov  rax, [rel g_api_nmsgs]
  cmp  qword [rel g35_pos], 0
  jne  .start_known
  xor  eax, eax                ; a fresh state: everything, system turn first
.start_known:
  cmp  rax, rcx
  jb   .start_ok
  lea  rax, [rcx - 1]          ; a shorter dialog than seen: its last message only
  test rcx, rcx
  jnz  .start_ok
  xor  eax, eax
.start_ok:
  mov  [rsp+56], rax           ; start index
  lea  rax, [rcx + 1]
  mov  [rel g_api_nmsgs], rax  ; next time: after these and the answer
  mov  qword [rsp+48], 0       ; index
  cmp  qword [rel g35_pos], 0
  jne  .turns                  ; the system turn is in the state already
  ; --- the system turn: the system messages' content (Logos sends its
  ;     instructions as one), then the tools block from "tools" ---
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_system]
  call api_cat
  mov  qword [rsp+40], 0       ; 1 once a system content was written
  mov  rcx, r13
  mov  rdx, r12
  call js_arr_first
  mov  r14, rax
.sys_scan:
  test r14, r14
  jz   .sys_done
  mov  rcx, r14
  mov  rdx, r12
  lea  r8, [rel api_k_role]
  call js_find_key
  test rax, rax
  jz   .sys_next
  mov  rcx, rax
  mov  rdx, r12
  lea  r8, [rel api_v_system]
  call js_str_is
  test rax, rax
  jz   .sys_next
  mov  rcx, r14
  mov  rdx, r12
  lea  r8, [rel api_k_content]
  call js_find_key
  test rax, rax
  jz   .sys_next
  cmp  qword [rsp+40], 0
  je   .sys_first
  lea  rsi, [rel api_t_lflf]
  call api_cat
.sys_first:
  mov  qword [rsp+40], 1
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr            ; the unescaped string at the cursor
.sys_next:
  mov  rcx, r14
  mov  rdx, r12
  call js_arr_next
  mov  r14, rax
  jmp  .sys_scan
.sys_done:
  ; tools
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_tools]
  call js_find_key
  test rax, rax
  jz   .tools_done
  cmp  byte [rax], '['
  jne  .tools_done
  mov  rcx, rax
  mov  rdx, r12
  call js_arr_first
  mov  r14, rax
  test r14, r14
  jz   .tools_done
  cmp  qword [rsp+40], 0
  je   .tools_head
  lea  rsi, [rel api_t_lflf]
  call api_cat
.tools_head:
  mov  qword [rsp+40], 1
  mov  qword [rel g_api_tools_sent], 1
  lea  rsi, [rel api_t_tools1]
  call api_cat
.tool_each:
  test r14, r14
  jz   .tools_tail
  mov  rcx, r14
  mov  rdx, r12
  call js_skip                 ; rax = end of this element
  mov  r8, rax
  sub  r8, r14
<<<<<<< ours
<<<<<<< ours
  mov  rcx, rdi
  mov  rdx, r14
  call rt_memcpy               ; the element verbatim
  mov  rdi, rax
  add  rdi, r8
=======
=======
>>>>>>> theirs
  mov  [rsp+72], r8            ; the element's length (rt_memcpy clobbers r8)
  mov  rcx, rdi
  mov  rdx, r14
  call rt_memcpy               ; the element verbatim
  add  rdi, [rsp+72]
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
  mov  byte [rdi], 10
  inc  rdi
  inc  qword [rel g_api_ntools]
  mov  rcx, r14
  mov  rdx, r12
  call js_arr_next
  mov  r14, rax
  jmp  .tool_each
.tools_tail:
  lea  rsi, [rel api_t_tools2]
  call api_cat
.tools_done:
  lea  rsi, [rel api_t_im_end]
  call api_cat
.turns:
  ; --- the turns, from the start index on ---
  mov  rcx, r13
  mov  rdx, r12
  call js_arr_first
  mov  r14, rax
  mov  qword [rsp+40], 0       ; 1 while inside a grouped tool-response user turn
.turn:
  test r14, r14
  jz   .turns_done
  mov  rax, [rsp+48]
  inc  qword [rsp+48]
  cmp  rax, [rsp+56]
  jb   .turn_next              ; already in the state
  mov  rcx, r14
  mov  rdx, r12
  lea  r8, [rel api_k_role]
  call js_find_key
  test rax, rax
  jz   .turn_next
  mov  r15, rax                ; the role string
  mov  rcx, r15
  mov  rdx, r12
  lea  r8, [rel api_v_tool]
  call js_str_is
  test rax, rax
  jnz  .turn_tool
  ; a non-tool turn closes an open tool-response group
  cmp  qword [rsp+40], 0
  je   .grp_closed
  lea  rsi, [rel api_t_im_end]
  call api_cat
  mov  qword [rsp+40], 0
.grp_closed:
  mov  rcx, r15
  mov  rdx, r12
  lea  r8, [rel api_v_user]
  call js_str_is
  test rax, rax
  jnz  .turn_user
  mov  rcx, r15
  mov  rdx, r12
  lea  r8, [rel api_v_assistant]
  call js_str_is
  test rax, rax
  jnz  .turn_asst
  jmp  .turn_next              ; system turns were folded into the first
.turn_user:
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_user]
  call api_cat
  mov  rcx, r14
  mov  rdx, r12
  lea  r8, [rel api_k_content]
  call js_find_key
  test rax, rax
  jz   .user_end
  ; remember the last user text for the log line
  mov  [rel g_api_user], rdi
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr
  mov  rax, rdi
  sub  rax, [rel g_api_user]
  mov  [rel g_api_userlen], rax
.user_end:
  lea  rsi, [rel api_t_im_end]
  call api_cat
  jmp  .turn_next
.turn_asst:
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_asst]
  call api_cat
  lea  rsi, [rel api_t_nothink]
  call api_cat
  mov  rcx, r14
  mov  rdx, r12
  lea  r8, [rel api_k_content]
  call js_find_key
  test rax, rax
  jz   .asst_calls
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr
.asst_calls:
  mov  rcx, r14
  mov  rdx, r12
  lea  r8, [rel api_k_tool_calls]
  call js_find_key
  test rax, rax
  jz   .asst_end
  cmp  byte [rax], '['
  jne  .asst_end
  mov  rcx, rax
  mov  rdx, r12
  call js_arr_first
  mov  r15, rax
.call_each:
  test r15, r15
  jz   .asst_end
  mov  rcx, r15
  mov  rdx, r12
  call api_cat_call            ; <tool_call>...</tool_call> of one call object
  mov  rcx, r15
  mov  rdx, r12
  call js_arr_next
  mov  r15, rax
  jmp  .call_each
.asst_end:
  lea  rsi, [rel api_t_im_end]
  call api_cat
  jmp  .turn_next
.turn_tool:
  cmp  qword [rsp+40], 0
  jne  .tool_resp
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_user]
  call api_cat
  mov  qword [rsp+40], 1
  jmp  .tool_resp0
.tool_resp:
  mov  byte [rdi], 10
  inc  rdi
.tool_resp0:
  lea  rsi, [rel api_t_resp1]
  call api_cat
  mov  rcx, r14
  mov  rdx, r12
  lea  r8, [rel api_k_content]
  call js_find_key
  test rax, rax
  jz   .tool_resp_end
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr
.tool_resp_end:
  lea  rsi, [rel api_t_resp2]
  call api_cat
.turn_next:
  mov  rcx, r14
  mov  rdx, r12
  call js_arr_next
  mov  r14, rax
  jmp  .turn
.turns_done:
  cmp  qword [rsp+40], 0
  je   .gen_prompt
  lea  rsi, [rel api_t_im_end]
  call api_cat
.gen_prompt:
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_asst]
  call api_cat
  lea  rsi, [rel api_t_nothink]
  call api_cat
  ; --- the whole render is the tail: it continues the one context ---
  mov  rsi, rdi
  sub  rsi, [rsp+32]           ; rendered length
  mov  [rel g_api_renderlen], rsi
  mov  qword [rel g_api_reset], 0
  mov  rcx, [rsp+32]
  mov  rdx, rsi
  mov  [rel g_api_tail], rcx
  mov  [rel g_api_taillen], rdx
  mov  r8, [rel g_api_ids]
  mov  r9d, API_IDS_CAP
  call api_encode              ; rax = ids
  mov  [rel g_api_nids], rax
  xor  eax, eax
  jmp  .ret
.nomsgs:
  lea  rax, [rel api_j_nomsgs]
  mov  [rel g_api_err], rax
  mov  qword [rel g_api_errlen], API_J_NOMSGS_LEN
  mov  eax, 1
.ret:
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; api_cat — append the zero-terminated string at rsi to the cursor rdi.
api_cat:
  mov  al, [rsi]
  test al, al
  jz   .done
  mov  [rdi], al
  inc  rsi
  inc  rdi
  jmp  api_cat
.done:
  ret

; api_cat_jstr(rcx=ptr at the JSON value, rdx=end) — a string value, unescaped,
; appended at rdi; a non-string value (an array of content parts, null) adds
; nothing. Preserves rbx, rsi, r12-r15; advances rdi.
api_cat_jstr:
  cmp  byte [rcx], '"'
  jne  .skip
  mov  r8, rdi
  mov  r9d, API_STR_CAP
  call js_str                  ; rax = bytes written at r8
  add  rdi, rax
.skip:
  ret

; api_cat_call(rcx=a tool_calls element, rdx=end) — Qwen's call block for an
; Ollama call {"function":{"name","arguments":{...}}}:
;   \n<tool_call>\n<function=NAME>\n<parameter=K>\nV\n</parameter>...\n</function>\n</tool_call>
; A string argument is written unescaped; any other value verbatim (JSON).
api_cat_call:
  push rbx
  push rsi
  push r12
  push r13
  push r14
  WINPROLOGUE
  mov  rbx, rcx
  mov  r12, rdx
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_function]
  call js_find_key
  test rax, rax
  jz   .done
  mov  rbx, rax                ; the function object
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_name]
  call js_find_key
  test rax, rax
  jz   .done
  lea  rsi, [rel api_t_call1]
  call api_cat
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr
  mov  byte [rdi], '>'
  inc  rdi
  mov  byte [rdi], 10
  inc  rdi
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_arguments]
  call js_find_key
  test rax, rax
  jz   .close
  cmp  byte [rax], '{'
  jne  .close
  ; the members of the arguments object
  mov  r13, rax
  mov  rcx, r13
  mov  rdx, r12
  call js_obj_first            ; rax = ptr at the first key string, or 0
  mov  r14, rax
.arg:
  test r14, r14
  jz   .close
  lea  rsi, [rel api_t_param1]
  call api_cat
  mov  rcx, r14
  mov  rdx, r12
  call api_cat_jstr            ; the key
  mov  byte [rdi], '>'
  inc  rdi
  mov  byte [rdi], 10
  inc  rdi
  mov  rcx, r14
  mov  rdx, r12
  call js_obj_value            ; rax = ptr at the value
  mov  r14, rax
  cmp  byte [rax], '"'
  jne  .arg_raw
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr
  jmp  .arg_end
.arg_raw:
  mov  rcx, rax
  mov  rdx, r12
  call js_skip
  mov  r8, rax
  sub  r8, r14
<<<<<<< ours
<<<<<<< ours
  mov  rcx, rdi
  mov  rdx, r14
  call rt_memcpy
=======
=======
>>>>>>> theirs
  push r8
  push r8                      ; the value's length (rt_memcpy clobbers r8)
  mov  rcx, rdi
  mov  rdx, r14
  call rt_memcpy
  pop  r8
  pop  r8
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
  add  rdi, r8
.arg_end:
  lea  rsi, [rel api_t_param2]
  call api_cat
  mov  rcx, r14
  mov  rdx, r12
  call js_obj_next             ; rax = the next key, or 0 (r14 is at the value)
  mov  r14, rax
  jmp  .arg
.close:
  lea  rsi, [rel api_t_call2]
  call api_cat
.done:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rsi
  pop  rbx
  ret

; api_memeq(rcx=a, rdx=b, r8=n) -> rax = 1 when equal. Leaf.
api_memeq:
  xor  eax, eax
.c:
  test r8, r8
  jz   .eq
  mov  r9b, [rcx]
  cmp  r9b, [rdx]
  jne  .ne
  inc  rcx
  inc  rdx
  dec  r8
  jmp  .c
.eq:
  mov  eax, 1
.ne:
  ret

; ---------------------------------------------------------------------------
; api_encode(rcx=text, rdx=len, r8=ids, r9=cap) -> rax = ids: the template
; text to ids, the markers <|im_start|> <|im_end|> <think> </think> as their
; special ids, the spans between through tok_encode.
; ---------------------------------------------------------------------------
api_encode:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 32
  mov  rbx, r8                 ; ids
  mov  r13, r9                 ; cap
  mov  r12, rcx                ; text
  lea  r14, [rcx + rdx]        ; end
  xor  esi, esi                ; ids written
  mov  r15, r12                ; span start
  mov  rdi, r12                ; scan
.scan:
  cmp  rdi, r14
  jae  .flush_end
  cmp  byte [rdi], '<'
  jne  .adv
  mov  rcx, rdi
  mov  rdx, r14
  call api_marker              ; rax = marker kind or 0, rdx = marker length
  test rax, rax
  jz   .adv
  mov  [rsp+32], rax
  mov  [rsp+40], rdx
  ; the span before the marker
  mov  rcx, r15
  mov  rdx, rdi
  sub  rdx, r15
  jz   .mark
  lea  r8, [rbx + rsi*4]
  mov  r9, r13
  sub  r9, rsi
  call tok_encode
  add  rsi, rax
.mark:
  mov  rax, [rsp+32]
  cmp  rax, MK_IM_START
  jne  .m2
  mov  rax, [rel g35_im_start]
  jmp  .put
.m2:
  cmp  rax, MK_IM_END
  jne  .m3
  mov  rax, [rel g35_im_end]
  jmp  .put
.m3:
  cmp  rax, MK_THINK
  jne  .m4
  mov  rax, [rel g35_think]
  jmp  .put
.m4:
  mov  rax, [rel g35_think_end]
.put:
  test rax, rax
  jz   .skip_put               ; a vocabulary without the token: the marker is dropped
  cmp  rsi, r13
  jae  .skip_put
  mov  [rbx + rsi*4], eax
  inc  rsi
.skip_put:
  add  rdi, [rsp+40]
  mov  r15, rdi
  jmp  .scan
.adv:
  inc  rdi
  jmp  .scan
.flush_end:
  mov  rcx, r15
  mov  rdx, r14
  sub  rdx, r15
  jz   .done
  lea  r8, [rbx + rsi*4]
  mov  r9, r13
  sub  r9, rsi
  call tok_encode
  add  rsi, rax
.done:
  mov  rax, rsi
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; api_marker(rcx=ptr at '<', rdx=end) -> rax = MK_* or 0, rdx = its length.
api_marker:
  mov  r8, rdx
  sub  r8, rcx                 ; bytes left
  cmp  r8, 12
  jb   .m_end
  cmp  dword [rcx], '<|im'
  jne  .m_end
  cmp  dword [rcx+4], '_sta'
  jne  .m_end
  cmp  dword [rcx+8], 'rt|>'
  jne  .m_end
  mov  eax, MK_IM_START
  mov  edx, 12
  ret
.m_end:
  cmp  r8, 10
  jb   .m_think
  cmp  dword [rcx], '<|im'
  jne  .m_think
  cmp  dword [rcx+4], '_end'
  jne  .m_think
  cmp  word [rcx+8], '|>'
  jne  .m_think
  mov  eax, MK_IM_END
  mov  edx, 10
  ret
.m_think:
  cmp  r8, 7
  jb   .m_tend
  cmp  dword [rcx], '<thi'
  jne  .m_tend
  cmp  word [rcx+4], 'nk'
  jne  .m_tend
  cmp  byte [rcx+6], '>'
  jne  .m_tend
  mov  eax, MK_THINK
  mov  edx, 7
  ret
.m_tend:
  cmp  r8, 8
  jb   .none
  cmp  dword [rcx], '</th'
  jne  .none
  cmp  dword [rcx+4], 'ink>'
  jne  .none
  mov  eax, MK_THINK_END
  mov  edx, 8
  ret
.none:
  xor  eax, eax
  xor  edx, edx
  ret

; ---------------------------------------------------------------------------
; After the answer: the processed text grows, the JSON line goes out.
; ---------------------------------------------------------------------------

; api_cat_answer — at rdi: "content":"<the captured answer up to the first
; <tool_call>, trimmed>" and, when there are calls, ,"tool_calls":[...] with every
; <tool_call> block as one. Preserves rbx, rsi, r12-r14; advances rdi.
api_cat_answer:
  push rbx
  push rsi
  push r12
  push r13
  push r14
  WINPROLOGUE
  lea  rsi, [rel api_j_content]
  call api_cat
  mov  rbx, [rel g_http_cap]
  mov  r12, [rel g_http_cap_len]
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_t_tc_open]
  call api_find                ; rax = offset or -1
  mov  r13, rax
  cmp  rax, -1
  jne  .content
  mov  r13, r12
.content:
  mov  rcx, rbx
  mov  rdx, r13
  call api_cat_esc_trim        ; without trailing blank lines
  mov  byte [rdi], '"'
  inc  rdi
  cmp  r13, r12
  jae  .done
  lea  rsi, [rel api_j_a4]     ; ,"tool_calls":[
  call api_cat
  xor  r14d, r14d              ; calls written
.call:
  lea  rcx, [rbx + r13]
  mov  rdx, r12
  sub  rdx, r13
  lea  r8, [rel api_t_tc_open]
  call api_find
  cmp  rax, -1
  je   .calls_end
  add  r13, rax
  add  r13, 11                 ; past "<tool_call>"
  lea  rcx, [rbx + r13]
  mov  rdx, r12
  sub  rdx, r13
  lea  r8, [rel api_t_tc_close]
  call api_find
  cmp  rax, -1
  je   .calls_end
  test r14d, r14d
  jz   .first_call
  mov  byte [rdi], ','
  inc  rdi
.first_call:
  inc  r14d
  lea  rcx, [rbx + r13]
  mov  rdx, rax                ; the block's length
  call api_call_json           ; {"function":{"name":..,"arguments":{..}}}
  add  r13, rax
  jmp  .call
.calls_end:
  mov  byte [rdi], ']'
  inc  rdi
.done:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rsi
  pop  rbx
  ret

; api_cat_temps — at rdi: "temp_c":[..]|null,"pkg_temp_c":N|null (after rt_temp_read).
api_cat_temps:
  push rbx
  push rsi
  push r12
  WINPROLOGUE
  lea  rsi, [rel api_j_temp]
  call api_cat
  cmp  qword [rel g_temp_n], 0
  je   .none
  mov  byte [rdi], '['
  inc  rdi
  xor  r12d, r12d
.c:
  cmp  r12, [rel g_temp_n]
  jae  .close
  test r12, r12
  jz   .first
  mov  byte [rdi], ','
  inc  rdi
.first:
  lea  rax, [rel g_temp_c]
  mov  rcx, [rax + r12*8]
  test rcx, rcx
  js   .null_c
  call api_cat_u64
  jmp  .nc
.null_c:
  lea  rsi, [rel api_v_null]
  call api_cat
.nc:
  inc  r12
  jmp  .c
.close:
  mov  byte [rdi], ']'
  inc  rdi
  lea  rsi, [rel api_j_pkg]
  call api_cat
  mov  rcx, [rel g_temp_pkg]
  test rcx, rcx
  js   .pkg_null
  call api_cat_u64
  jmp  .done
.pkg_null:
  lea  rsi, [rel api_v_null]
  call api_cat
  jmp  .done
.none:
  lea  rsi, [rel api_v_null]
  call api_cat
  lea  rsi, [rel api_j_pkg]
  call api_cat
  lea  rsi, [rel api_v_null]
  call api_cat
.done:
  WINEPILOGUE
  pop  r12
  pop  rsi
  pop  rbx
  ret

; api_cat_ms(rcx=nanoseconds) — the milliseconds, integer, at rdi.
api_cat_ms:
  mov  rax, rcx
  xor  edx, edx
  mov  rcx, 1000000
  div  rcx
  mov  rcx, rax
  jmp  api_cat_u64

; api_cat_turn_stats — at rdi: the numbers every status and final line share:
; "prompt_tokens":N,"gen_tokens":M,"prompt_ms":x,"gen_ms":y,"mhz":m,<temps>,"ctx_used":p,"ctx":c
api_cat_turn_stats:
  push rsi
  WINPROLOGUE
  lea  rsi, [rel api_j_s1]
  call api_cat
  mov  rcx, [rel g35_last_prompt_n]
  call api_cat_u64
  lea  rsi, [rel api_j_s2]
  call api_cat
  mov  rcx, [rel g35_gen_total]
  call api_cat_u64
  lea  rsi, [rel api_j_s3]
  call api_cat
  mov  rcx, [rel g35_last_prompt_ns]
  call api_cat_ms
  lea  rsi, [rel api_j_s4]
  call api_cat
  mov  rcx, [rel g35_last_gen_ns]
  call api_cat_ms
  lea  rsi, [rel api_j_s5]
  call api_cat
  mov  rcx, [rel g35_last_ghz_mhz]
  call api_cat_u64
  mov  byte [rdi], ','
  inc  rdi
  call api_cat_temps
  lea  rsi, [rel api_j_s6]
  call api_cat
  mov  rcx, [rel g35_pos]
  call api_cat_u64
  lea  rsi, [rel api_j_s7]
  call api_cat
  mov  rcx, [rel g35_ctx]
  call api_cat_u64
  WINEPILOGUE
  pop  rsi
  ret

; api_turn_json() -> rax = length in g_api_out of the turn API's final line:
; {"done":true,"content":..,["tool_calls":..,]"stop":"stop|length",<stats>}\n
api_turn_json:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  call rt_temp_read
  mov  rdi, [rel g_api_out]
  mov  rbx, rdi
  lea  rsi, [rel api_j_t1]     ; {"done":true,
  call api_cat
  call api_cat_answer
  lea  rsi, [rel api_j_t2]     ; ,"stop":"
  call api_cat
  lea  rsi, [rel api_j_stop]
  cmp  qword [rel g35_stop_len], 0
  je   .reason
  lea  rsi, [rel api_j_length]
.reason:
  call api_cat
  mov  byte [rdi], '"'
  inc  rdi
  mov  byte [rdi], ','
  inc  rdi
  call api_cat_turn_stats
  lea  rsi, [rel api_j_a8]     ; }\n
  call api_cat
  mov  rax, rdi
  sub  rax, rbx
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; api_status_json() -> rax = length in g_api_out:
; {"model":"..","pos_max":M,<stats>,"model_bytes":B}
api_status_json:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  call rt_temp_read
  mov  rdi, [rel g_api_out]
  mov  rbx, rdi
  lea  rsi, [rel api_j_a1]     ; {"model":"
  call api_cat
  mov  rcx, [rel g_model_name]
  mov  rdx, [rel g_model_namelen]
  call api_cat_esc
  lea  rsi, [rel api_j_st1]    ; ","pos_max":
  call api_cat
  mov  rcx, [rel g35_posmax]
  call api_cat_u64
  mov  byte [rdi], ','
  inc  rdi
  call api_cat_turn_stats
  lea  rsi, [rel api_j_st2]    ; ,"model_bytes":
  call api_cat
  mov  rcx, [rel g_weight_bytes]
  call api_cat_u64
  mov  byte [rdi], '}'
  inc  rdi
  mov  rax, rdi
  sub  rax, rbx
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; api_delta_chunk(rcx=conn, rdx=text, r8=len) — one streamed line {"delta":"..."}
; (the turn API; called from http_capture for every stdout write).
api_delta_chunk:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  WINPROLOGUE
  mov  rbx, rcx                ; conn
  mov  r12, rdx                ; the text
  mov  r13, r8                 ; its length
  cmp  r13, 16384
  ja   .done                   ; a single write this large is not a token
  lea  rdi, [rel g_api_delta]
  lea  rsi, [rel api_j_d1]     ; {"delta":"
  call api_cat
  mov  rcx, r12
  mov  rdx, r13
  call api_cat_esc
  mov  word [rdi], '"}'
  mov  byte [rdi+2], 10
  add  rdi, 3
  mov  rcx, rbx
  lea  rdx, [rel g_api_delta]
  mov  r8, rdi
  sub  r8, rdx
  call http_chunk
.done:
  WINEPILOGUE
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ---------------------------------------------------------------------------
; The turn API: POST /v1/turn {"message"|"tool_results":[{"name","content"}],
; "system","tools"}. The system turn (system text + tools block) only when the
; state is fresh; then one user turn (the message, or the tool responses); then
; the assistant prompt. The answer streams back as {"delta":..} lines and ends
; with api_turn_json.
; ---------------------------------------------------------------------------
; api_turn_prepare(rcx=body, rdx=len) -> rax = 0 ok, 1 error (g_api_err set).
api_turn_prepare:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  sub  rsp, 32
  lea  r12, [rcx + rdx]
  mov  rdx, r12
  call js_ws
  mov  rbx, rax                ; the object
  mov  rdi, [rel g_api_render]
  mov  [rsp+32], rdi
  mov  qword [rel g_api_userlen], 0
  cmp  qword [rel g35_pos], 0
  je   .sys_turn
  ; a running context: a system turn only when tools arrive for the first time
  cmp  qword [rel g_api_tools_sent], 0
  jne  .user
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_tools]
  call js_find_key
  test rax, rax
  jz   .user
  cmp  byte [rax], '['
  jne  .user
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_system]
  call api_cat
  mov  qword [rsp+40], 0
  jmp  .tools
.sys_turn:
  ; the system turn
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_system]
  call api_cat
  mov  qword [rsp+40], 0
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_system]
  call js_find_key
  test rax, rax
  jz   .tools
  cmp  byte [rax], '"'
  jne  .tools
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr
  mov  qword [rsp+40], 1
.tools:
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_tools]
  call js_find_key
  test rax, rax
  jz   .sys_end
  cmp  byte [rax], '['
  jne  .sys_end
  mov  rcx, rax
  mov  rdx, r12
  call js_arr_first
  mov  r14, rax
  test r14, r14
  jz   .sys_end
  cmp  qword [rsp+40], 0
  je   .tools_head
  lea  rsi, [rel api_t_lflf]
  call api_cat
.tools_head:
  mov  qword [rel g_api_tools_sent], 1
  lea  rsi, [rel api_t_tools1]
  call api_cat
.tool_each:
  test r14, r14
  jz   .tools_tail
  mov  rcx, r14
  mov  rdx, r12
  call js_skip
<<<<<<< ours
<<<<<<< ours
  mov  r8, rax
  sub  r8, r14
  mov  rcx, rdi
  mov  rdx, r14
  call rt_memcpy
  mov  rdi, rax
  add  rdi, r8
=======
=======
>>>>>>> theirs
  mov  r13, rax
  sub  r13, r14                ; the element's length (rt_memcpy clobbers r8)
  mov  rcx, rdi
  mov  rdx, r14
  mov  r8, r13
  call rt_memcpy
  add  rdi, r13
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
  mov  byte [rdi], 10
  inc  rdi
  mov  rcx, r14
  mov  rdx, r12
  call js_arr_next
  mov  r14, rax
  jmp  .tool_each
.tools_tail:
  lea  rsi, [rel api_t_tools2]
  call api_cat
.sys_end:
  lea  rsi, [rel api_t_im_end]
  call api_cat
.user:
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_user]
  call api_cat
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_tool_results]
  call js_find_key
  test rax, rax
  jz   .message
  cmp  byte [rax], '['
  jne  .message
  mov  rcx, rax
  mov  rdx, r12
  call js_arr_first
  mov  r14, rax
  test r14, r14
  jz   .message
  xor  r13d, r13d
.res_each:
  test r14, r14
  jz   .user_end
  test r13d, r13d
  jz   .res_first
  mov  byte [rdi], 10
  inc  rdi
.res_first:
  inc  r13d
  lea  rsi, [rel api_t_resp1]
  call api_cat
  mov  rcx, r14
  mov  rdx, r12
  lea  r8, [rel api_k_content]
  call js_find_key
  test rax, rax
  jz   .res_close
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr
.res_close:
  lea  rsi, [rel api_t_resp2]
  call api_cat
  mov  rcx, r14
  mov  rdx, r12
  call js_arr_next
  mov  r14, rax
  jmp  .res_each
.message:
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_k_message]
  call js_find_key
  test rax, rax
  jz   .nomsg
  cmp  byte [rax], '"'
  jne  .nomsg
  mov  [rel g_api_user], rdi
  mov  rcx, rax
  mov  rdx, r12
  call api_cat_jstr
  mov  rax, rdi
  sub  rax, [rel g_api_user]
  mov  [rel g_api_userlen], rax
.user_end:
  lea  rsi, [rel api_t_im_end]
  call api_cat
  lea  rsi, [rel api_t_im_start]
  call api_cat
  lea  rsi, [rel api_t_asst]
  call api_cat
  lea  rsi, [rel api_t_nothink]
  call api_cat
  mov  rsi, rdi
  sub  rsi, [rsp+32]
  mov  [rel g_api_renderlen], rsi
  mov  qword [rel g_api_reset], 0
  mov  rcx, [rsp+32]
  mov  rdx, rsi
  mov  [rel g_api_tail], rcx
  mov  [rel g_api_taillen], rdx
  mov  r8, [rel g_api_ids]
  mov  r9d, API_IDS_CAP
  call api_encode
  mov  [rel g_api_nids], rax
<<<<<<< ours
<<<<<<< ours
=======
=======
>>>>>>> theirs
  ; one line per turn request: what was rendered and fed (into dialog.txt too)
  lea  rcx, [rel api_s_log1]
  call rt_err_str
  mov  rcx, [rel g_api_renderlen]
  call rt_err_u64
  lea  rcx, [rel api_s_log2]
  call rt_err_str
  mov  rcx, [rel g_api_nids]
  call rt_err_u64
  lea  rcx, [rel api_s_log3]
  call rt_err_str
  mov  rcx, [rel g_api_tools_sent]
  call rt_err_u64
  lea  rcx, [rel api_s_log4]
  call rt_err_str
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
  xor  eax, eax
  jmp  .ret
.nomsg:
  lea  rax, [rel api_j_nomessage]
  mov  [rel g_api_err], rax
  mov  qword [rel g_api_errlen], API_J_NOMESSAGE_LEN
  mov  eax, 1
.ret:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; ---------------------------------------------------------------------------
; Embeddings: POST /api/embed {"input": "text" | ["text", ...]} ->
; {"model":"qwen3-embedding:0.6b","embeddings":[[...],...],"prompt_eval_count":N}
; Each text goes through the embedding model's own tokenizer (its tables
; swapped in), gets its end-of-text token appended, runs token by token through
; model_forward with g_embed_only set, and the last token's normed state,
; scaled to unit length, is the vector (Qwen3-Embedding's last-token pooling,
; the same as Ollama gave, so the stored vectors stay comparable).
; ---------------------------------------------------------------------------

; api_tok_use(rcx=saved tables) — the tokenizer's globals from a saved copy.
api_tok_use:
  WINPROLOGUE
  mov  rdx, rcx
  lea  rcx, [rel g_tok_ready]
  mov  r8d, TOK_STATE_BYTES
  call rt_memcpy
  WINEPILOGUE
  ret

; api_embed_json(rcx=body, rdx=len) -> rax = length in g_api_out, 0 without "input".
api_embed_json:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  sub  rsp, 32
  lea  r12, [rcx + rdx]
  mov  rdx, r12
  call js_ws
  mov  rcx, rax
  mov  rdx, r12
  lea  r8, [rel api_k_input]
  call js_find_key
  test rax, rax
  jz   .none
  mov  r13, rax                ; the input value
  mov  rdi, [rel g_api_out]
  mov  [rsp+32], rdi
  mov  qword [rsp+40], 0       ; tokens
  lea  rsi, [rel api_j_e1]
  call api_cat
  lea  rcx, [rel g_tok_embed]
  call api_tok_use
  cmp  byte [r13], '"'
  je   .one
  cmp  byte [r13], '['
  jne  .restore_none
  mov  rcx, r13
  mov  rdx, r12
  call js_arr_first
  mov  r14, rax
  xor  ebx, ebx                ; texts done
.each:
  test r14, r14
  jz   .done
  cmp  byte [r14], '"'
  jne  .next
  test ebx, ebx
  jz   .first
  mov  byte [rdi], ','
  inc  rdi
.first:
  inc  ebx
  mov  rcx, r14
  mov  rdx, r12
  call api_embed_one           ; rax = tokens; the vector at rdi
  add  [rsp+40], rax
.next:
  mov  rcx, r14
  mov  rdx, r12
  call js_arr_next
  mov  r14, rax
  jmp  .each
.one:
  mov  rcx, r13
  mov  rdx, r12
  call api_embed_one
  add  [rsp+40], rax
.done:
  lea  rcx, [rel g_tok_main]
  call api_tok_use
  lea  rsi, [rel api_j_e2]
  call api_cat
  mov  rcx, [rsp+40]
  call api_cat_u64
  mov  byte [rdi], '}'
  inc  rdi
  mov  rax, rdi
  sub  rax, [rsp+32]
  jmp  .ret
.restore_none:
  lea  rcx, [rel g_tok_main]
  call api_tok_use
.none:
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; api_embed_one(rcx=JSON string at '"', rdx=end) -> rax = tokens fed; the unit
; vector as a JSON array at rdi (advanced). The embedder's tokenizer tables are
; in place.
api_embed_one:
  push rbx
  push rsi
  push r12
  push r13
  push r14
  push r15
  WINPROLOGUE
  sub  rsp, 32
  mov  r8, [rel g_api_str]
  mov  r9d, API_STR_CAP
  call js_str                  ; rax = bytes of text
  mov  rcx, [rel g_api_str]
  mov  rdx, rax
  mov  r8, [rel g_api_ids]
  mov  r9d, API_IDS_CAP - 1
  call tok_encode              ; rax = ids
  mov  r12, rax
  mov  rbx, [rel g_embed_model]
  mov  rcx, [rbx + Model.n_ctx]
  dec  rcx
  cmp  r12, rcx
  jbe  .fits
  mov  r12, rcx                ; a text longer than the context: its head
.fits:
  mov  rax, [rel g_api_ids]
  mov  rcx, [rbx + Model.eos_id]
  mov  [rax + r12*4], ecx      ; <|endoftext|> closes the text: its state is the vector
  inc  r12
  mov  qword [rel g_embed_only], 1
  xor  r13d, r13d
  xor  r14d, r14d              ; the last state
<<<<<<< ours
<<<<<<< ours
<<<<<<< ours
.tok:
  cmp  r13, r12
  jae  .fed
  mov  rax, [rel g_api_ids]
  mov  edx, [rax + r13*4]
  mov  rcx, rbx
  mov  r8, r13
  call model_forward           ; rax = the normed state (g_embed_only)
  mov  r14, rax
  inc  r13
  jmp  .tok
=======
=======
>>>>>>> theirs
=======
>>>>>>> theirs
.slice:
  cmp  r13, r12
  jae  .fed
  mov  r8, r12
  sub  r8, r13
  cmp  r8, ACT_SLOTS
  jbe  .sl
  mov  r8d, ACT_SLOTS
.sl:
  mov  rax, [rel g_api_ids]
  lea  rdx, [rax + r13*4]
  mov  rcx, rbx
  mov  r9, r13
  mov  [rsp+40], r8
  call model_forward_batch     ; rax = the last token's normed state (g_embed_only)
  mov  r14, rax
  add  r13, [rsp+40]
  jmp  .slice
<<<<<<< ours
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
=======
>>>>>>> theirs
.fed:
  mov  qword [rel g_embed_only], 0
  ; unit length
  mov  r15, [rbx + Model.n_embd]
  vxorps xmm2, xmm2, xmm2
  xor  ecx, ecx
.ss:
  cmp  rcx, r15
  jae  .ssd
  vmovss xmm0, [r14 + rcx*4]
  vfmadd231ss xmm2, xmm0, xmm0
  inc  rcx
  jmp  .ss
.ssd:
  vsqrtss xmm2, xmm2, xmm2
  vmovss xmm3, [rel api_f_one]
  vucomiss xmm2, xmm3
  vdivss xmm3, xmm3, xmm2      ; 1/norm
  vmovss [rsp+32], xmm3
  mov  byte [rdi], '['
  inc  rdi
  xor  r13d, r13d
.out:
  cmp  r13, r15
  jae  .outd
  test r13, r13
  jz   .o1
  mov  byte [rdi], ','
  inc  rdi
.o1:
  vmovss xmm0, [r14 + r13*4]
  vmulss xmm0, xmm0, [rsp+32]
  call api_cat_f32
  inc  r13
  jmp  .out
.outd:
  mov  byte [rdi], ']'
  inc  rdi
  mov  rax, r12
  WINEPILOGUE
  pop  r15
  pop  r14
  pop  r13
  pop  r12
  pop  rsi
  pop  rbx
  ret

; api_cat_f32(xmm0) — the value with six decimals at rdi (|value| < 2^31/1e6).
; Clobbers rax, rcx, rdx, r8, xmm0-xmm1.
api_cat_f32:
  WINPROLOGUE
  sub  rsp, 32
  vxorps xmm1, xmm1, xmm1
  vucomiss xmm0, xmm1
  jae  .pos
  mov  byte [rdi], '-'
  inc  rdi
  vxorps xmm0, xmm0, [rel api_f_sign]
.pos:
  vmulss xmm0, xmm0, [rel api_f_1e6]
  vcvtss2si rax, xmm0          ; rounded to the nearest
  xor  edx, edx
  mov  ecx, 1000000
  div  rcx                     ; rax = whole, rdx = millionths
  mov  r8, rdx
  mov  rcx, rax
  call api_cat_u64
  mov  byte [rdi], '.'
  inc  rdi
  lea  rcx, [rsp+38]           ; six digits backwards into [rsp+32..38)
  mov  rax, r8
  mov  r8d, 6
.dig:
  dec  rcx
  xor  edx, edx
  mov  r9d, 10
  div  r9
  add  dl, '0'
  mov  [rcx], dl
  dec  r8d
  jnz  .dig
  mov  rcx, rdi
  lea  rdx, [rsp+32]
  mov  r8d, 6
  call rt_memcpy
  add  rdi, 6
  WINEPILOGUE
  ret

  align 16
api_f_sign:  dd 0x80000000, 0, 0, 0
api_f_1e6:   dd 1000000.0
api_f_one:   dd 1.0

; api_answer_json() -> rax = length of the NDJSON line in g_api_out, built from
; the captured answer: the content before the first <tool_call>, and every
; <tool_call> block as a tool call.
api_answer_json:
  push rbx
  push rsi
  push rdi
  push r12
  push r13
  push r14
  WINPROLOGUE
  sub  rsp, 32
  mov  rdi, [rel g_api_out]
  mov  [rsp+32], rdi
  lea  rsi, [rel api_j_a1]     ; {"model":"
  call api_cat
  mov  rcx, [rel g_model_name]
  mov  rdx, [rel g_model_namelen]
  call api_cat_esc
  lea  rsi, [rel api_j_a2]     ; ","created_at":"
  call api_cat
  mov  rcx, [rel g_api_created]
  call api_cat_u64
  lea  rsi, [rel api_j_a3]     ; ","message":{"role":"assistant",
  call api_cat
  call api_cat_answer          ; "content":"...",["tool_calls":[...]]
  lea  rsi, [rel api_j_a5]     ; },"done":true,"done_reason":"
  call api_cat
  lea  rsi, [rel api_j_stop]
  cmp  qword [rel g35_stop_len], 0
  je   .reason
  lea  rsi, [rel api_j_length]
.reason:
  call api_cat
  lea  rsi, [rel api_j_a6]     ; ","prompt_eval_count":
  call api_cat
  mov  rcx, [rel g_api_nids]
  call api_cat_u64
  lea  rsi, [rel api_j_a7]     ; ,"eval_count":
  call api_cat
  mov  rcx, [rel g35_gen_total]
  call api_cat_u64
  lea  rsi, [rel api_j_a8]     ; }\n
  call api_cat
  mov  rax, rdi
  sub  rax, [rsp+32]
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; api_call_json(rcx=block text (after <tool_call>), rdx=length) -> rax = rdx;
; writes {"function":{"name":"N","arguments":{"k":v,...}}} at rdi. A value
; that reads as JSON (object, array, number, true, false, null) goes verbatim,
; anything else as a string.
api_call_json:
  push rbx
  push rsi
  push r12
  push r13
  push r14
  WINPROLOGUE
  mov  rbx, rcx
  mov  r12, rdx
  mov  [rel g_api_tmp], rdx
  lea  rsi, [rel api_j_c1]     ; {"function":{"name":"
  call api_cat
  ; <function=NAME>
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_t_fn_open]
  call api_find
  cmp  rax, -1
  je   .noname
  lea  r13, [rbx + rax + 10]   ; past "<function="
  mov  rcx, r13
  lea  rdx, [rbx + r12]
  sub  rdx, r13
  mov  r8d, '>'
  call api_find_byte           ; rax = offset of '>' or -1
  cmp  rax, -1
  je   .noname
  push rax
  push rax
  mov  rcx, r13
  mov  rdx, rax
  call api_cat_esc             ; (clobbers rax)
  pop  rax
  pop  rax
  lea  r13, [r13 + rax + 1]    ; after '>'
  jmp  .params
.noname:
  mov  r13, rbx
.params:
  lea  rsi, [rel api_j_c2]     ; ","arguments":{
  call api_cat
  xor  r14d, r14d
.param:
  mov  rcx, r13
  lea  rdx, [rbx + r12]
  sub  rdx, r13
  jle  .params_end
  lea  r8, [rel api_t_par_open]
  call api_find
  cmp  rax, -1
  je   .params_end
  lea  r13, [r13 + rax + 11]   ; past "<parameter="
  mov  rcx, r13
  lea  rdx, [rbx + r12]
  sub  rdx, r13
  mov  r8d, '>'
  call api_find_byte
  cmp  rax, -1
  je   .params_end
  test r14d, r14d
  jz   .first_param
  mov  byte [rdi], ','
  inc  rdi
.first_param:
  inc  r14d
  mov  byte [rdi], '"'
  inc  rdi
  push rax
  push rax
  mov  rcx, r13
  mov  rdx, rax
  call api_cat_esc             ; the key (clobbers rax)
  pop  rax
  pop  rax
  mov  byte [rdi], '"'
  inc  rdi
  mov  byte [rdi], ':'
  inc  rdi
  lea  r13, [r13 + rax + 1]    ; after '>'
  cmp  byte [r13], 10
  jne  .val
  inc  r13                     ; the newline after the tag
.val:
  mov  rcx, r13
  lea  rdx, [rbx + r12]
  sub  rdx, r13
  lea  r8, [rel api_t_par_close]
  call api_find
  cmp  rax, -1
  je   .params_end
  mov  rdx, rax                ; value length (up to </parameter>)
  ; drop one trailing newline
  test rdx, rdx
  jz   .v_emit
  cmp  byte [r13 + rdx - 1], 10
  jne  .v_emit
  dec  rdx
.v_emit:
  mov  rcx, r13
  push rax
  push rax
  call api_cat_value
  pop  rax
  pop  rax
  lea  r13, [r13 + rax + 12]   ; past "</parameter>"
  jmp  .param
.params_end:
  lea  rsi, [rel api_j_c3]     ; }}}
  call api_cat
  mov  rax, [rel g_api_tmp]
  WINEPILOGUE
  pop  r14
  pop  r13
  pop  r12
  pop  rsi
  pop  rbx
  ret

; api_cat_value(rcx=text, rdx=len) — a parameter value as JSON: verbatim when
; it is JSON itself, else a quoted string.
api_cat_value:
  push rbx
  push r12
  WINPROLOGUE
  mov  rbx, rcx
  mov  r12, rdx
  test rdx, rdx
  jz   .str
  mov  al, [rbx]
  cmp  al, '{'
  je   .raw_chk
  cmp  al, '['
  je   .raw_chk
  cmp  al, '-'
  je   .num
  cmp  al, '0'
  jb   .lit
  cmp  al, '9'
  jbe  .num
.lit:
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_v_true]
  call api_text_is
  test rax, rax
  jnz  .raw
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_v_false]
  call api_text_is
  test rax, rax
  jnz  .raw
  mov  rcx, rbx
  mov  rdx, r12
  lea  r8, [rel api_v_null]
  call api_text_is
  test rax, rax
  jnz  .raw
  jmp  .str
.num:
  ; digits, one dot at most, nothing else
  mov  rcx, rbx
  lea  rdx, [rbx + r12]
  xor  r8d, r8d                ; dots
  cmp  byte [rcx], '-'
  jne  .nd
  inc  rcx
  cmp  rcx, rdx
  jae  .str
.nd:
  cmp  rcx, rdx
  jae  .raw
  mov  al, [rcx]
  cmp  al, '.'
  jne  .ndig
  inc  r8d
  cmp  r8d, 1
  ja   .str
  inc  rcx
  jmp  .nd
.ndig:
  sub  al, '0'
  cmp  al, 9
  ja   .str
  inc  rcx
  jmp  .nd
.raw_chk:
  ; a JSON object or array: it must parse to exactly its length
  mov  rcx, rbx
  lea  rdx, [rbx + r12]
  call js_skip
  lea  rdx, [rbx + r12]
  cmp  rax, rdx
  jne  .str
.raw:
  mov  rcx, rdi
  mov  rdx, rbx
  mov  r8, r12
  call rt_memcpy
  add  rdi, r12
  jmp  .done
.str:
  mov  byte [rdi], '"'
  inc  rdi
  mov  rcx, rbx
  mov  rdx, r12
  call api_cat_esc
  mov  byte [rdi], '"'
  inc  rdi
.done:
  WINEPILOGUE
  pop  r12
  pop  rbx
  ret

; api_text_is(rcx=text, rdx=len, r8=zstr) -> rax = 1 when equal.
api_text_is:
  xor  eax, eax
.c:
  mov  r9b, [r8]
  test r9b, r9b
  jz   .end
  test rdx, rdx
  jz   .no
  cmp  r9b, [rcx]
  jne  .no
  inc  rcx
  inc  r8
  dec  rdx
  jmp  .c
.end:
  test rdx, rdx
  jnz  .no
  mov  eax, 1
.no:
  ret

; api_models_json() -> rax = length in g_api_out: {"models":[{...}]}
api_models_json:
  push rbx
  push rsi
  push rdi
  WINPROLOGUE
  mov  rdi, [rel g_api_out]
  mov  rbx, rdi
  lea  rsi, [rel api_j_m1]
  call api_cat
  mov  rcx, [rel g_model_name]
  mov  rdx, [rel g_model_namelen]
  call api_cat_esc
  lea  rsi, [rel api_j_m2]
  call api_cat
  mov  rcx, [rel g_model_name]
  mov  rdx, [rel g_model_namelen]
  call api_cat_esc
  lea  rsi, [rel api_j_m3]
  call api_cat
  mov  rcx, [rel g_weight_bytes]
  call api_cat_u64
  lea  rsi, [rel api_j_m4]
  call api_cat
  mov  rcx, [rel g35_ctx]
  call api_cat_u64
  lea  rsi, [rel api_j_m5]
  call api_cat
  mov  rax, rdi
  sub  rax, rbx
  WINEPILOGUE
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; api_cat_esc(rcx=text, rdx=len) — JSON-escaped at rdi. Preserves rbx, rsi, r12-r15.
api_cat_esc:
  lea  r8, [rcx + rdx]
.c:
  cmp  rcx, r8
  jae  .done
  movzx eax, byte [rcx]
  inc  rcx
  cmp  al, '"'
  je   .bs
  cmp  al, '\'
  je   .bs
  cmp  al, 10
  je   .n
  cmp  al, 13
  je   .r
  cmp  al, 9
  je   .t
  cmp  al, 0x20
  jb   .u
  mov  [rdi], al
  inc  rdi
  jmp  .c
.bs:
  mov  byte [rdi], '\'
  mov  [rdi+1], al
  add  rdi, 2
  jmp  .c
.n:
  mov  word [rdi], '\n'        ; two bytes, in memory order: '\' then 'n'
  add  rdi, 2
  jmp  .c
.r:
  mov  word [rdi], '\r'
  add  rdi, 2
  jmp  .c
.t:
  mov  word [rdi], '\t'
  add  rdi, 2
  jmp  .c
.u:
  mov  dword [rdi], '\u00'
  lea  r9, [rel k_hex_digits]
  mov  r10d, eax
  shr  r10d, 4
  mov  r10b, [r9 + r10]
  mov  [rdi+4], r10b
  and  eax, 15
  mov  al, [r9 + rax]
  mov  [rdi+5], al
  add  rdi, 6
  jmp  .c
.done:
  ret

; api_cat_esc_trim(rcx=text, rdx=len) — as api_cat_esc, without trailing
; whitespace.
api_cat_esc_trim:
.t:
  test rdx, rdx
  jz   .go
  mov  al, [rcx + rdx - 1]
  cmp  al, ' '
  ja   .go
  dec  rdx
  jmp  .t
.go:
  jmp  api_cat_esc

; api_cat_u64(rcx=value) — decimal at rdi. Preserves rbx, rsi, r12-r15.
api_cat_u64:
  WINPROLOGUE
  sub  rsp, 32
  lea  rdx, [rsp+56]
  call rt_u64_digits           ; rax = first digit, end at [rsp+56]
.d:
  lea  rcx, [rsp+56]
  cmp  rax, rcx
  jae  .done
  mov  cl, [rax]
  mov  [rdi], cl
  inc  rdi
  inc  rax
  jmp  .d
.done:
  WINEPILOGUE
  ret

; api_strlen(rcx=zstr) -> rax
api_strlen:
  xor  eax, eax
.c:
  cmp  byte [rcx + rax], 0
  je   .done
  inc  rax
  jmp  .c
.done:
  ret

; api_find(rcx=hay, rdx=len, r8=needle zstr) -> rax = offset or -1. Leaf;
; clobbers r9-r11.
api_find:
  xor  eax, eax
.pos:
  mov  r9, rax
  xor  r10d, r10d
.c:
  mov  r11b, [r8 + r10]
  test r11b, r11b
  jz   .found
  lea  r9, [rax + r10]
  cmp  r9, rdx
  jae  .none
  cmp  r11b, [rcx + r9]
  jne  .next
  inc  r10
  jmp  .c
.next:
  inc  rax
  cmp  rax, rdx
  jb   .pos
.none:
  mov  rax, -1
.found:
  ret

; api_find_byte(rcx=hay, rdx=len, r8=byte) -> rax = offset or -1.
api_find_byte:
  xor  eax, eax
.c:
  cmp  rax, rdx
  jae  .none
  cmp  [rcx + rax], r8b
  je   .done
  inc  rax
  jmp  .c
.none:
  mov  rax, -1
.done:
  ret

; ---------------------------------------------------------------------------
; A small JSON reader over the request bytes. Every routine takes rcx = a
; position, rdx = the end, and never reads past the end.
; ---------------------------------------------------------------------------

; js_ws(rcx, rdx) -> rax = the first non-blank position (or the end).
js_ws:
  mov  rax, rcx
.c:
  cmp  rax, rdx
  jae  .done
  mov  r8b, [rax]
  cmp  r8b, ' '
  je   .n
  cmp  r8b, 9
  je   .n
  cmp  r8b, 10
  je   .n
  cmp  r8b, 13
  jne  .done
.n:
  inc  rax
  jmp  .c
.done:
  ret

; js_skip(rcx=value start, rdx=end) -> rax = the position after the value.
js_skip:
  push rbx
  push rsi
  WINPROLOGUE
  mov  rsi, rdx
  call js_ws
  mov  rbx, rax
  cmp  rbx, rsi
  jae  .done
  mov  al, [rbx]
  cmp  al, '"'
  je   .string
  cmp  al, '{'
  je   .nested
  cmp  al, '['
  je   .nested
  ; a literal or number: up to a delimiter
.lit:
  inc  rbx
  cmp  rbx, rsi
  jae  .done
  mov  al, [rbx]
  cmp  al, ','
  je   .done
  cmp  al, '}'
  je   .done
  cmp  al, ']'
  je   .done
  cmp  al, ' '
  je   .done
  cmp  al, 10
  je   .done
  cmp  al, 13
  je   .done
  cmp  al, 9
  je   .done
  jmp  .lit
.string:
  mov  rcx, rbx
  mov  rdx, rsi
  call js_str_end              ; rax = after the closing quote
  mov  rbx, rax
  jmp  .done
.nested:
  xor  ecx, ecx                ; depth
.nest:
  cmp  rbx, rsi
  jae  .done
  mov  al, [rbx]
  cmp  al, '"'
  jne  .nq
  push rcx
  push rcx
  mov  rcx, rbx
  mov  rdx, rsi
  call js_str_end
  pop  rcx
  pop  rcx
  mov  rbx, rax
  jmp  .nest
.nq:
  cmp  al, '{'
  je   .open
  cmp  al, '['
  je   .open
  cmp  al, '}'
  je   .close
  cmp  al, ']'
  je   .close
  inc  rbx
  jmp  .nest
.open:
  inc  ecx
  inc  rbx
  jmp  .nest
.close:
  dec  ecx
  inc  rbx
  test ecx, ecx
  jnz  .nest
.done:
  mov  rax, rbx
  WINEPILOGUE
  pop  rsi
  pop  rbx
  ret

; js_str_end(rcx=at '"', rdx=end) -> rax = the position after the closing quote.
js_str_end:
  mov  rax, rcx
  inc  rax
.c:
  cmp  rax, rdx
  jae  .done
  mov  r8b, [rax]
  inc  rax
  cmp  r8b, '\'
  jne  .nb
  inc  rax                     ; the escaped byte
  jmp  .c
.nb:
  cmp  r8b, '"'
  jne  .c
.done:
  ret

; js_str(rcx=at '"', rdx=end, r8=out, r9=cap) -> rax = bytes written (UTF-8,
; escapes resolved; \uXXXX and surrogate pairs encoded). Preserves rbx, rsi,
; rdi, r12-r15.
js_str:
  push rbx
  push rsi
  push rdi
  push r12
  mov  rsi, rcx
  inc  rsi                     ; past the quote
  mov  rdi, r8                 ; out cursor
  mov  rbx, r8
  lea  r12, [r8 + r9 - 8]      ; room for one escape
.c:
  cmp  rsi, rdx
  jae  .done
  cmp  rdi, r12
  jae  .done
  movzx eax, byte [rsi]
  inc  rsi
  cmp  al, '"'
  je   .done
  cmp  al, '\'
  jne  .plain
  cmp  rsi, rdx
  jae  .done
  movzx eax, byte [rsi]
  inc  rsi
  cmp  al, 'n'
  jne  .e1
  mov  al, 10
  jmp  .plain
.e1:
  cmp  al, 't'
  jne  .e2
  mov  al, 9
  jmp  .plain
.e2:
  cmp  al, 'r'
  jne  .e3
  mov  al, 13
  jmp  .plain
.e3:
  cmp  al, 'b'
  jne  .e4
  mov  al, 8
  jmp  .plain
.e4:
  cmp  al, 'f'
  jne  .e5
  mov  al, 12
  jmp  .plain
.e5:
  cmp  al, 'u'
  jne  .plain                  ; \" \\ \/ and anything else: the byte itself
  mov  rcx, rsi
  call js_hex4                 ; rax = code unit, rsi += 4 (or 0 units on a short read)
  mov  r8d, eax
  and  r8d, 0xFC00
  cmp  r8d, 0xD800
  jne  .cp
  ; a high surrogate: the low one follows as \uXXXX
  lea  rcx, [rsi + 2]
  cmp  rcx, rdx
  ja   .cp
  cmp  word [rsi], 'u\'
  jne  .cp
  mov  r9d, eax
  mov  rcx, rsi
  add  rcx, 2
  mov  rsi, rcx
  call js_hex4
  and  r9d, 0x3FF
  shl  r9d, 10
  and  eax, 0x3FF
  or   eax, r9d
  add  eax, 0x10000
.cp:
  ; UTF-8 of eax
  cmp  eax, 0x80
  jb   .plain
  cmp  eax, 0x800
  jb   .two
  cmp  eax, 0x10000
  jb   .three
  mov  ecx, eax
  shr  ecx, 18
  or   cl, 0xF0
  mov  [rdi], cl
  mov  ecx, eax
  shr  ecx, 12
  and  cl, 0x3F
  or   cl, 0x80
  mov  [rdi+1], cl
  mov  ecx, eax
  shr  ecx, 6
  and  cl, 0x3F
  or   cl, 0x80
  mov  [rdi+2], cl
  and  al, 0x3F
  or   al, 0x80
  mov  [rdi+3], al
  add  rdi, 4
  jmp  .c
.three:
  mov  ecx, eax
  shr  ecx, 12
  or   cl, 0xE0
  mov  [rdi], cl
  mov  ecx, eax
  shr  ecx, 6
  and  cl, 0x3F
  or   cl, 0x80
  mov  [rdi+1], cl
  and  al, 0x3F
  or   al, 0x80
  mov  [rdi+2], al
  add  rdi, 3
  jmp  .c
.two:
  mov  ecx, eax
  shr  ecx, 6
  or   cl, 0xC0
  mov  [rdi], cl
  and  al, 0x3F
  or   al, 0x80
  mov  [rdi+1], al
  add  rdi, 2
  jmp  .c
.plain:
  mov  [rdi], al
  inc  rdi
  jmp  .c
.done:
  mov  rax, rdi
  sub  rax, rbx
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; js_hex4(rcx=4 hex digits (rsi points there too), rdx=end) -> eax = value;
; rsi advanced past them. Clobbers r8, r9.
js_hex4:
  xor  eax, eax
  xor  r9d, r9d
.d:
  cmp  r9d, 4
  jae  .done
  cmp  rsi, rdx
  jae  .done
  movzx r8d, byte [rsi]
  sub  r8b, '0'
  cmp  r8b, 9
  jbe  .acc
  sub  r8b, 'a' - '0' - 10
  cmp  r8b, 15
  jbe  .acc
  sub  r8b, 'A' - 'a'
  cmp  r8b, 15
  ja   .done
.acc:
  shl  eax, 4
  or   eax, r8d
  inc  rsi
  inc  r9d
  jmp  .d
.done:
  ret

; js_str_is(rcx=at '"', rdx=end, r8=zstr) -> rax = 1 when the string equals
; zstr (no escapes expected in roles).
js_str_is:
  xor  eax, eax
  cmp  byte [rcx], '"'
  jne  .no
  inc  rcx
.c:
  cmp  rcx, rdx
  jae  .no
  mov  r9b, [r8]
  test r9b, r9b
  jz   .end
  cmp  r9b, [rcx]
  jne  .no
  inc  rcx
  inc  r8
  jmp  .c
.end:
  cmp  byte [rcx], '"'
  jne  .no
  mov  eax, 1
.no:
  ret

; js_find_key(rcx=at '{', rdx=end, r8=key zstr) -> rax = the value's position
; or 0. Members at this level only.
js_find_key:
  push rbx
  push rsi
  push rdi
  push r12
  WINPROLOGUE
  mov  rsi, rdx
  mov  r12, r8
  call js_ws
  mov  rbx, rax
  cmp  rbx, rsi
  jae  .none
  cmp  byte [rbx], '{'
  jne  .none
  inc  rbx
.member:
  mov  rcx, rbx
  mov  rdx, rsi
  call js_ws
  mov  rbx, rax
  cmp  rbx, rsi
  jae  .none
  mov  al, [rbx]
  cmp  al, '}'
  je   .none
  cmp  al, ','
  jne  .key
  inc  rbx
  jmp  .member
.key:
  cmp  al, '"'
  jne  .none
  mov  rcx, rbx
  mov  rdx, rsi
  mov  r8, r12
  call js_str_is
  mov  rdi, rax                ; 1 when this is the key
  mov  rcx, rbx
  mov  rdx, rsi
  call js_str_end
  mov  rcx, rax
  mov  rdx, rsi
  call js_ws
  mov  rbx, rax
  cmp  rbx, rsi
  jae  .none
  cmp  byte [rbx], ':'
  jne  .none
  inc  rbx
  mov  rcx, rbx
  mov  rdx, rsi
  call js_ws
  mov  rbx, rax
  test rdi, rdi
  jnz  .found
  mov  rcx, rbx
  mov  rdx, rsi
  call js_skip
  mov  rbx, rax
  jmp  .member
.found:
  mov  rax, rbx
  jmp  .ret
.none:
  xor  eax, eax
.ret:
  WINEPILOGUE
  pop  r12
  pop  rdi
  pop  rsi
  pop  rbx
  ret

; js_arr_first(rcx=at '[', rdx=end) -> rax = the first element or 0.
js_arr_first:
  WINPROLOGUE
  call js_ws
  cmp  rax, rdx
  jae  .none
  cmp  byte [rax], '['
  jne  .none
  lea  rcx, [rax + 1]
  call js_ws
  cmp  rax, rdx
  jae  .none
  cmp  byte [rax], ']'
  je   .none
  WINEPILOGUE
  ret
.none:
  xor  eax, eax
  WINEPILOGUE
  ret

; js_arr_next(rcx=at an element, rdx=end) -> rax = the next element or 0.
js_arr_next:
  push rbx
  WINPROLOGUE
  mov  rbx, rdx
  call js_skip
  mov  rcx, rax
  mov  rdx, rbx
  call js_ws
  cmp  rax, rbx
  jae  .none
  cmp  byte [rax], ','
  jne  .none
  lea  rcx, [rax + 1]
  mov  rdx, rbx
  call js_ws
  cmp  rax, rbx
  jae  .none
  cmp  byte [rax], ']'
  je   .none
  WINEPILOGUE
  pop  rbx
  ret
.none:
  xor  eax, eax
  WINEPILOGUE
  pop  rbx
  ret

; js_obj_first(rcx=at '{', rdx=end) -> rax = the first key (at its '"') or 0.
js_obj_first:
  WINPROLOGUE
  call js_ws
  cmp  rax, rdx
  jae  .none
  cmp  byte [rax], '{'
  jne  .none
  lea  rcx, [rax + 1]
  call js_ws
  cmp  rax, rdx
  jae  .none
  cmp  byte [rax], '"'
  jne  .none
  WINEPILOGUE
  ret
.none:
  xor  eax, eax
  WINEPILOGUE
  ret

; js_obj_value(rcx=at a key's '"', rdx=end) -> rax = its value's position.
js_obj_value:
  push rbx
  WINPROLOGUE
  mov  rbx, rdx
  call js_str_end
  mov  rcx, rax
  mov  rdx, rbx
  call js_ws
  cmp  rax, rbx
  jae  .done
  cmp  byte [rax], ':'
  jne  .done
  lea  rcx, [rax + 1]
  mov  rdx, rbx
  call js_ws
.done:
  WINEPILOGUE
  pop  rbx
  ret

; js_obj_next(rcx=at a value, rdx=end) -> rax = the next key or 0.
js_obj_next:
  push rbx
  WINPROLOGUE
  mov  rbx, rdx
  call js_skip
  mov  rcx, rax
  mov  rdx, rbx
  call js_ws
  cmp  rax, rbx
  jae  .none
  cmp  byte [rax], ','
  jne  .none
  lea  rcx, [rax + 1]
  mov  rdx, rbx
  call js_ws
  cmp  rax, rbx
  jae  .none
  cmp  byte [rax], '"'
  jne  .none
  WINEPILOGUE
  pop  rbx
  ret
.none:
  xor  eax, eax
  WINEPILOGUE
  pop  rbx
  ret

%ifndef ASMRUN_EFI
; Windows has no ring 0 for the runner: no temperatures (the fields stay null).
rt_temp_read:
  ret
section .data
  align 8
g_temp_n:       dq 0
g_temp_tjmax:   dq 0
g_temp_pkg:     dq -1
g_temp_c:       dq -1, -1, -1, -1, -1, -1, -1, -1
section .text
%endif

; --- paths, keys, template pieces, JSON pieces ---
api_p_version:    db "/api/version", 0
api_p_tags:       db "/api/tags", 0
api_p_ps:         db "/api/ps", 0
api_p_chat:       db "/api/chat", 0
api_p_show:       db "/api/show", 0
api_p_embed:      db "/api/embed", 0
api_p_embeddings: db "/api/embeddings", 0
api_p_status:     db "/v1/status", 0
api_p_turn:       db "/v1/turn", 0
api_k_system:     db "system", 0
api_k_message:    db "message", 0
api_k_tool_results: db "tool_results", 0
api_s_ct_ndjson:  db "application/x-ndjson", 0
<<<<<<< ours
<<<<<<< ours
=======
=======
>>>>>>> theirs
api_s_log1:       db "asmrun: turn: render_bytes=", 0
api_s_log2:       db " ids=", 0
api_s_log3:       db " tools_sent=", 0
api_s_log4:       db 10, 0
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
api_k_input:      db "input", 0
api_j_noinput:    db `{"error":"input missing: a string or an array of strings"}`
API_J_NOINPUT_LEN equ $ - api_j_noinput
api_j_e1:         db `{"model":"qwen3-embedding:0.6b","embeddings":[`, 0
api_j_e2:         db `],"prompt_eval_count":`, 0
api_j_nomessage:  db `{"error":"message (or tool_results) missing"}`
API_J_NOMESSAGE_LEN equ $ - api_j_nomessage
api_j_content:    db `"content":"`, 0
api_j_temp:       db `"temp_c":`, 0
api_j_pkg:        db `,"pkg_temp_c":`, 0
api_j_s1:         db `"prompt_tokens":`, 0
api_j_s2:         db `,"gen_tokens":`, 0
api_j_s3:         db `,"prompt_ms":`, 0
api_j_s4:         db `,"gen_ms":`, 0
api_j_s5:         db `,"mhz":`, 0
api_j_s6:         db `,"ctx_used":`, 0
api_j_s7:         db `,"ctx":`, 0
api_j_t1:         db `{"done":true,`, 0
api_j_t2:         db `,"stop":"`, 0
api_j_st1:        db `","pos_max":`, 0
api_j_st2:        db `,"model_bytes":`, 0
api_j_d1:         db `{"delta":"`, 0
api_k_messages:   db "messages", 0
api_k_tools:      db "tools", 0
api_k_role:       db "role", 0
api_k_content:    db "content", 0
api_k_tool_calls: db "tool_calls", 0
api_k_function:   db "function", 0
api_k_name:       db "name", 0
api_k_arguments:  db "arguments", 0
api_v_system:     db "system", 0
api_v_user:       db "user", 0
api_v_assistant:  db "assistant", 0
api_v_tool:       db "tool", 0
api_v_true:       db "true", 0
api_v_false:      db "false", 0
api_v_null:       db "null", 0
api_t_im_start:   db "<|im_start|>", 0
api_t_im_end:     db "<|im_end|>", 10, 0
api_t_system:     db "system", 10, 0
api_t_user:       db "user", 10, 0
api_t_asst:       db "assistant", 10, 0
api_t_nothink:    db "<think>", 10, 10, "</think>", 10, 10, 0
api_t_lflf:       db 10, 10, 0
api_t_tools1:     db `# Tools\n\nYou have access to the following functions:\n\n<tools>\n`, 0
api_t_tools2:     db `</tools>\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:\n\n<tool_call>\n<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n</parameter>\n<parameter=example_parameter_2>\nThis is the value for the second parameter\nthat can span\nmultiple lines\n</parameter>\n</function>\n</tool_call>\n\n<IMPORTANT>\nReminder:\n- Function calls MUST follow the specified format: an inner <function=...></function> block must be nested within <tool_call></tool_call> XML tags\n- Required parameters MUST be specified\n- You may provide optional reasoning for your function call in natural language BEFORE the function call, but NOT after\n- If there is no function call available, answer the question like normal with your current knowledge and do not tell the user about function calls\n</IMPORTANT>`, 0
api_t_resp1:      db "<tool_response>", 10, 0
api_t_resp2:      db 10, "</tool_response>", 0
api_t_call1:      db 10, "<tool_call>", 10, "<function=", 0
api_t_param1:     db "<parameter=", 0
api_t_param2:     db 10, "</parameter>", 10, 0
api_t_call2:      db "</function>", 10, "</tool_call>", 0
api_t_tc_open:    db "<tool_call>", 0
api_t_tc_close:   db "</tool_call>", 0
api_t_fn_open:    db "<function=", 0
api_t_par_open:   db "<parameter=", 0
api_t_par_close:  db "</parameter>", 0
api_s_ct_json:    db "application/json", 0
api_s_defname:    db "asmrun"
API_S_DEFNAME_LEN equ $ - api_s_defname
api_s_501:        db "501 Not Implemented", 0
api_j_version:    db `{"version":"0.1.0-asmrun"}`
API_J_VERSION_LEN equ $ - api_j_version
api_j_show:       db `{"parameters":"","details":{"family":"qwen35","format":"gguf"},"capabilities":["completion","tools"]}`
API_J_SHOW_LEN equ $ - api_j_show
api_j_noembed:    db `{"error":"asmrun serves no embedding model yet"}`
API_J_NOEMBED_LEN equ $ - api_j_noembed
api_j_noq35:      db `{"error":"the loaded model has no API path (Qwen3.5 only)"}`
API_J_NOQ35_LEN equ $ - api_j_noq35
api_j_nomsgs:     db `{"error":"messages missing"}`
API_J_NOMSGS_LEN equ $ - api_j_nomsgs
api_j_a1:         db `{"model":"`, 0
api_j_a2:         db `","created_at":"`, 0
api_j_a3:         db `","message":{"role":"assistant",`, 0
api_j_a4:         db `,"tool_calls":[`, 0
api_j_a5:         db `},"done":true,"done_reason":"`, 0
api_j_stop:       db "stop", 0
api_j_length:     db "length", 0
api_j_a6:         db `","prompt_eval_count":`, 0
api_j_a7:         db `,"eval_count":`, 0
api_j_a8:         db `}`, 10, 0
api_j_c1:         db `{"function":{"name":"`, 0
api_j_c2:         db `","arguments":{`, 0
api_j_c3:         db `}}}`, 0
api_j_m1:         db `{"models":[{"name":"`, 0
api_j_m2:         db `","model":"`, 0
api_j_m3:         db `","size":`, 0
api_j_m4:         db `,"size_vram":0,"context_length":`, 0
api_j_m5:         db `,"expires_at":"","details":{"family":"qwen35","format":"gguf"}}]}`, 0

section .data
  align 8
g_api_mode:      dq 0          ; 1 while an Ollama chat request is being answered, 2 a turn-API request
g_api_stream:    dq 0          ; 1 while every stdout write also goes out as a {"delta"} chunk
g_api_mute:      dq 0          ; 1 from the <tool_call> token of a turn on: no more deltas
g_api_delta:     times 20000 db 0
g_api_render:    dq 0
g_api_renderlen: dq 0
g_api_nmsgs:     dq 0          ; messages of the dialog already in the state (the model's answers counted)
g_api_text:      dq 0          ; (unused since "one context since boot")
g_api_textlen:   dq 0
g_api_tail:      dq 0          ; the part of the render fed this turn
g_api_taillen:   dq 0
g_api_ids:       dq 0
g_api_nids:      dq 0
g_api_reset:     dq 0          ; 1: the state is reset before the ids are fed
g_api_out:       dq 0
g_api_str:       dq 0
g_api_err:       dq 0
g_api_errlen:    dq 0
g_api_user:      dq 0          ; the last user message in the render (for the log)
g_api_userlen:   dq 0
g_api_ntools:    dq 0
g_api_tools_sent: dq 0        ; 1 once a tools block went into the context (one context since boot)
g_api_created:   dq 0
g_api_tmp:       dq 0
g_model_name:    dq 0          ; the loaded model's name for the API (set in main)
g_model_namelen: dq 0
section .text

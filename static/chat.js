// Shown when a tool needs the owner's approval: the approval UI was removed, so the action is not run.
const APPROVAL_OFF_TEXT = "Действие требует подтверждения владельца, поэтому не выполнено. Подтверждение в чате отключено.";

// Output of an approved action, kept per chat: shown now if that chat is open, else when it is opened again.
const pendingApprovalOutputs = {};

function deliverApprovalOutput(convId, render) {
  if (convId === currentConvId) return render();
  (pendingApprovalOutputs[convId] = pendingApprovalOutputs[convId] || []).push(render);
}

function flushApprovalOutputs(convId) {
  if (convId !== currentConvId) return;
  const renders = pendingApprovalOutputs[convId] || [];
  delete pendingApprovalOutputs[convId];
  renders.forEach(function (render) {
    render();
  });
}

// The vendored parsers own Markdown/TeX syntax. Keep Logos-specific presentation
// and the existing image-source policy at the renderer boundary.
var chatMarkdownRenderer = null;

function getChatMarkdownRenderer() {
  if (chatMarkdownRenderer) return chatMarkdownRenderer;
  if (typeof window.markdownit !== "function" || typeof texmath !== "function" || !window.katex) {
    throw new Error("Markdown renderer is unavailable");
  }
  var md = window.markdownit({ html: false, breaks: true, linkify: false });
  md.use(texmath, {
    engine: {
      renderToString: function (source, options) {
        return window.katex.renderToString(source, Object.assign({}, options, {
          output: "mathml",
          trust: false,
          throwOnError: false,
          // A formula cannot redefine macros for another message.
          macros: {},
        }));
      },
    },
    delimiters: ["dollars", "brackets"],
  });
  md.renderer.rules.table_open = function (tokens, index, options, env, renderer) {
    tokens[index].attrJoin("class", "md-table");
    return renderer.renderToken(tokens, index, options);
  };
  var image = md.renderer.rules.image;
  md.renderer.rules.image = function (tokens, index, options, env, renderer) {
    var token = tokens[index];
    var src = token.attrGet("src") || "";
    var allowed = /^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(src) ||
      /^\/api\/images\/files\/[A-Za-z0-9_-]{1,128}\/[A-Za-z0-9_-]{16,128}$/.test(src);
    if (!allowed) {
      return md.utils.escapeHtml(renderer.renderInlineAsText(token.children || [], options, env));
    }
    token.attrJoin("class", "md-image");
    token.attrSet("loading", "lazy");
    token.attrSet("decoding", "async");
    return image(tokens, index, options, env, renderer);
  };
  ["fence", "code_block"].forEach(function (rule) {
    var renderCode = md.renderer.rules[rule];
    md.renderer.rules[rule] = function (tokens, index, options, env, renderer) {
      return '<div class="md-code-block">' + renderCode(tokens, index, options, env, renderer) + "</div>";
    };
  });
  chatMarkdownRenderer = md;
  return md;
}

function parseMarkdown(text) {
  if (text === null || text === undefined || text === "") return "";
  var source = typeof text === "string" ? text : JSON.stringify(text);
  try {
    return getChatMarkdownRenderer().render(source);
  } catch (error) {
    // A missing optional renderer must still leave the model's answer readable.
    var plain = document.createElement("pre");
    plain.className = "md-render-fallback";
    plain.textContent = source;
    console.error("[CHAT] Markdown rendering failed", error && error.name);
    return plain.outerHTML;
  }
}

// Server error codes the chat names in plain Russian: what happened and what to do.
var CHAT_ERROR_TEXTS = {
  conversation_not_found: "Диалог не найден: его удалили. Откройте другой диалог или создайте новый.",
  internal_server_error: "Ошибка на сервере. Повторите запрос позже.",
  json_body_required: "Сервер не получил данные запроса. Повторите отправку.",
  invalid_params: "Параметры запроса не приняты. Проверьте настройки модели и повторите.",
  invalid_settings: "Настройки диалога не приняты. Проверьте настройки и инструменты диалога.",
  invalid_conversation_settings: "Настройки диалога не приняты. Проверьте настройки и инструменты диалога.",
  invalid_retry_message: "Повторить нельзя: сохранённый вопрос не совпадает с этим. Отправьте вопрос заново.",
  invalid_skill_selection: "Выбранные навыки недоступны. Измените выбор навыков и повторите.",
  invalid_attachments: "Фото не приняты: формат, размер или число фото не подходят. Уберите лишние или неподходящие фото и отправьте снова.",
  model_does_not_accept_images: "Эта модель не принимает фото. Выберите модель с поддержкой фото.",
  request_too_large: "Запрос слишком большой: уберите фото или уменьшите их.",
  model_requires_dedicated_api: "Эта модель не отвечает в текстовом чате. Выберите текстовую модель.",
  model_uses_completion_api: "Эта модель не ведёт диалог. Выберите другую модель.",
  provider_credential_unavailable: "Нет действующего ключа провайдера. Добавьте ключ в «Ключи провайдеров» и повторите.",
  not_an_image_model: "Эта модель не создаёт изображения. Выберите модель изображений.",
  invalid_conversation_id: "Диалог не распознан. Откройте его заново и повторите.",
  prompt_too_long: "Запрос для изображения слишком длинный. Сократите его и повторите.",
  invalid_image_size: "Этот размер изображения не поддерживается. Выберите другой размер.",
  image_model_does_not_take_photos: "Модель изображений не принимает фото: уберите фото или выберите другую модель.",
  request_id_conflict: "Этот запрос изображения уже отправлен с другим текстом. Отправьте запрос заново.",
  image_generation_unavailable: "Провайдер изображений не ответил. Повторите позже.",
  image_generation_failed: "Провайдер не создал изображение. Измените запрос и повторите.",
  image_generation_storage_failed: "Изображение создано, но не сохранилось на сервере. Чтобы получить его, отправьте запрос заново.",
  image_generation_outcome_unknown: "Результат генерации не подтверждён. Повторная платная генерация не запускалась: проверьте диалог позже.",
  image_artifact_unavailable: "Файл изображения недоступен. Повторная платная генерация не запускалась.",
  image_request_not_found: "Запрос изображения не найден на сервере. Отправьте его заново.",
  invalid_request_id: "Запрос изображения не распознан. Отправьте его заново.",
  voice_session_cancelled: "Голосовая сессия отменена. Отправьте сообщение ещё раз.",
  voice_session_not_found: "Голосовая сессия закончилась. Отправьте сообщение ещё раз.",
  voice_session_input_closed: "Голосовая сессия уже закрыта. Отправьте сообщение ещё раз.",
  unsupported_voice_transport: "Этот способ передачи голоса не поддерживается. Выберите другой режим голоса.",
  select_an_available_text_model_for_speechkit_pipeline: "Для режима SpeechKit нужна доступная текстовая модель. Выберите её в настройках и отправьте сообщение снова.",
  response_mode_must_be_audio_both_or_text: "Режим ответа не распознан. Выберите в голосовой панели «Голос», «Текст» или «Оба» и отправьте снова.",
  invalid_text_model_uri: "Адрес текстовой модели не составился. Выберите другую текстовую модель и отправьте снова.",
  select_an_available_discovered_text_model: "Эта текстовая модель недоступна. Выберите другую модель и отправьте снова.",
  speechkit_pipeline_requires_microphone_audio: "В режиме SpeechKit модель принимает только запись с микрофона. Скажите фразу голосом вместо текста.",
  text_is_required: "Введите текст сообщения.",
  speech_tts_live_requires_text: "Эта модель озвучивает только текст. Введите текст сообщения.",
  speech_tts_live_requires_audio_output: "Эта модель отвечает только голосом. Выберите режим ответа «Голос» или «Оба».",
  select_a_realtime_voice_model: "Выберите голосовую модель Realtime.",
  voice_is_not_compatible_with_model: "Выбранный голос не подходит этой модели. Выберите другой голос в настройках.",
  role_is_not_compatible_with_voice: "Выбранная роль не подходит этому голосу. Выберите другую роль.",
  instructions_must_be_text: "Инструкции модели должны быть текстом. Проверьте настройки.",
  text_model_catalog_unavailable: "Каталог моделей сейчас недоступен. Повторите позже.",
  provider_quota_exceeded: "Достигнут лимит запросов к провайдеру. Повторите после времени сброса.",
  invalid_role: "Роль не найдена. Выберите роль из списка.",
  model_not_available_for_dialog: "Эта модель недоступна для диалога. Выберите другую модель.",
  model_catalog_unavailable: "Список моделей провайдера сейчас недоступен. Повторите позже.",
  tool_action_failed: "Инструмент не выполнил действие. Причина записана в журнале сервера. Попросите модель повторить действие в чате.",
  tool_execution_failed: "Сервер не завершил подтверждённое действие. Проверьте, выполнилось ли оно, прежде чем повторять.",
  approval_followup_failed: "Действие выполнено, но модель не дописала ответ. Повторять действие не нужно: проверьте результат в диалоге.",
  turn_request_conflict: "Этот вопрос уже отправлен с другим текстом или фото. Отправьте его заново.",
  turn_lost: "Сервер перезапустился, пока модель отвечала, и ответ не сохранился. Вопрос сохранён: нажмите «Повторить», чтобы получить ответ.",
  turn_request_not_found: "Сервер не нашёл этот запрос. Если ответа нет в диалоге, отправьте вопрос ещё раз.",
};

function isRussianText(value) {
  return typeof value === "string" && /[А-Яа-яЁё]/.test(value);
}

// The Russian text for a failed request. The server's Russian explanation is the text when it is present, because the
// server knows what was stored (a known code cannot tell); a known code names the failure without one. A bare code and
// English server text (exception and library messages) go to the console only.
function chatErrorReason(data, status, fallback) {
  var body = data && typeof data === "object" ? data : {};
  var code = body.error && typeof body.error === "object" ? body.error.code : body.error;
  var known = typeof code === "string" && Object.prototype.hasOwnProperty.call(CHAT_ERROR_TEXTS, code)
    ? CHAT_ERROR_TEXTS[code]
    : "";
  var own = !known && isRussianText(code) ? code : "";
  var detail = [body.detail, body.message].find(isRussianText) || "";
  console.warn("[CHAT] Request failed:", status || "no status", code || "", body.detail || body.message || "");
  var text = detail || known || own || (body.type && isRussianText(body.title) ? body.title : "");
  if (!text) {
    text = fallback || (status >= 500
      ? "Сервер не смог ответить. Повторите запрос позже."
      : status >= 400
        ? "Сервер не принял запрос. Проверьте сообщение и настройки модели, затем повторите."
        : "Сервер не прислал ответ. Повторите запрос.");
  }
  return text;
}

var CHAT_UI_ROLES = {
  user: "user",
  human: "user",
  assistant: "bot",
  bot: "bot",
  model: "bot",
  error: "error",
};

function normalizeChatRole(role) {
  return CHAT_UI_ROLES[String(role || "").toLowerCase()] || null;
}

function findRenderedMessage(chatbox, messageId) {
  if (!messageId) return null;
  return (
    Array.from(chatbox.children).find(function (node) {
      return node.dataset && node.dataset.messageId === String(messageId);
    }) || null
  );
}

// Folds the per-message diagnostics into one line, so the answer keeps the focus.
function buildDetails(content, usage, totalDurationMs, cost) {
  const parts = [];
  if (usage && usage.total_tokens) parts.push(`${usage.total_tokens} токенов`);
  if (cost && cost > 0) parts.push(`≈ ${cost.toFixed(2)} ₽`);
  if (totalDurationMs) parts.push(`${(totalDurationMs / 1000).toFixed(1)} с`);
  // The cost is shown once, in this line; with nothing else to unfold it is a plain line.
  if (!content.children.length) {
    const line = document.createElement("div");
    line.className = "msg-details";
    line.textContent = parts.length ? "Детали · " + parts.join(" · ") : "Детали";
    return line;
  }
  const details = document.createElement("details");
  details.className = "msg-details";
  const summary = document.createElement("summary");
  summary.className = "msg-details-summary";
  summary.textContent = parts.length ? "Детали · " + parts.join(" · ") : "Детали";
  details.appendChild(summary);
  details.appendChild(content);
  return details;
}

// The Clipboard API exists only in secure contexts (https or localhost), and the phone reaches the app over plain http on the LAN.
// Without it the text goes through a selected textarea and execCommand, as the trace viewer does.
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).catch(() => copyThroughSelection(text));
  }
  return copyThroughSelection(text);
}

function copyThroughSelection(text) {
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_) {
    copied = false;
  }
  field.remove();
  return copied ? Promise.resolve() : Promise.reject(new Error("Copy was refused"));
}

function addMessage({
  text,
  role,
  cost = 0,
  timings,
  totalDurationMs,
  reasoning,
  usage,
  trace,
  id: messageId,
  onRetry,
  editText,
}) {
  const chatbox = document.getElementById("chatbox");
  if (!chatbox) return null;
  const uiRole = normalizeChatRole(role);
  if (!uiRole) return null;
  const existing = findRenderedMessage(chatbox, messageId);
  if (existing) return existing;
  const emptyState = chatbox.querySelector(".empty-state");
  if (emptyState) emptyState.remove();

  const msg = document.createElement("div");
  msg.className = `msg ${uiRole}`;
  if (messageId) msg.dataset.messageId = String(messageId);
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  let htmlContent = "";
  if (reasoning && reasoning.trim() !== "") {
    htmlContent += `<details class="reasoning-details"><summary class="reasoning-summary">🧠 Ход мыслей модели</summary><div class="reasoning-content">${parseMarkdown(reasoning)}</div></details>`;
  }
  htmlContent += parseMarkdown(text);
  bubble.innerHTML = htmlContent;
  msg.appendChild(bubble);

  if (uiRole === "error" && typeof onRetry === "function") {
    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "logos-btn msg-retry-btn";
    retryBtn.textContent = "Повторить";
    retryBtn.addEventListener("click", async function () {
      retryBtn.disabled = true;
      try {
        await onRetry();
      } finally {
        retryBtn.disabled = false;
      }
    });
    msg.appendChild(retryBtn);
  }

  // A question the server already stored is not put back into the field on its own (a plain Enter would store it twice);
  // «Изменить» puts it there on request, so it can be corrected without typing it again.
  if (uiRole === "error" && typeof editText === "string" && editText.trim()) {
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    // It shares the retry class, so it leaves together with «Повторить» when a newer question is sent.
    editBtn.className = "logos-btn msg-retry-btn msg-edit-btn";
    editBtn.textContent = "Изменить";
    editBtn.addEventListener("click", function () {
      const field = document.getElementById("msg-input");
      if (!field) return;
      if (field.value.trim() && field.value !== editText) {
        window.LogosToast.show("В поле уже есть черновик. Отправьте или очистите его и нажмите «Изменить» ещё раз.");
        return;
      }
      field.value = editText;
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.focus();
    });
    msg.appendChild(editBtn);
  }

  // Copy sits over the bubble corner, so it takes no row of its own.
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "logos-btn copy-btn";
  copyBtn.textContent = "📋";
  copyBtn.title = "Копировать";
  copyBtn.setAttribute("aria-label", "Копировать сообщение");

  copyBtn.addEventListener("click", function () {
    // A denied or missing clipboard must show a result, not an unhandled rejection.
    copyText(text).then(
      () => window.LogosToast.show("Скопировано"),
      () => window.LogosToast.error("Не удалось скопировать"),
    );
  });
  actions.appendChild(copyBtn);
  msg.appendChild(actions);

  const metaWrap = document.createElement("div");
  metaWrap.className = "msg-meta";

  if (usage && (usage.input_tokens || usage.total_tokens)) {
    const usageBadge = document.createElement("details");
    usageBadge.className = "msg-meta-details";
    const usageSummary = document.createElement("summary");
    usageSummary.className = "msg-meta-summary";
    usageSummary.textContent = `📊 Токены: ${usage.total_tokens || 0} (вход: ${usage.input_tokens || 0}, выход: ${usage.output_tokens || 0})`;
    const usageDetails = document.createElement("div");
    usageDetails.className = "msg-meta-detail-list";
    [
      ["💾 Кэшированные:", usage.cached_tokens || 0],
      ["🧰 Инструменты:", usage.tool_tokens || 0],
      ["🧠 Рассуждения:", usage.reasoning_tokens || 0],
    ].forEach(function (item) {
      const row = document.createElement("div");
      row.textContent = item[0] + " " + item[1];
      usageDetails.appendChild(row);
    });
    if (usage.incomplete_details) {
      const row = document.createElement("div");
      row.className = "msg-meta-error";
      row.textContent = "⚠️ Прерывание: " + JSON.stringify(usage.incomplete_details);
      usageDetails.appendChild(row);
    }
    usageBadge.appendChild(usageSummary);
    usageBadge.appendChild(usageDetails);
    metaWrap.appendChild(usageBadge);
  }

  if (timings && timings.length > 0) {
    const total = totalDurationMs ? `${totalDurationMs} мс` : "";
    const badge = document.createElement("details");
    badge.className = "msg-meta-details";
    const stepsSummary = document.createElement("summary");
    stepsSummary.className = "msg-meta-summary";
    stepsSummary.textContent = `⏱️ Выполнение: ${total || timings.length + " этапов"}`;
    const steps = document.createElement("div");
    steps.className = "msg-meta-detail-list";
    timings.forEach((t, idx) => {
      const dur = t.duration_source === "unavailable" ? "—" : `${t.duration_ms} мс`;
      const row = document.createElement("div");
      row.className = "msg-meta-step";
      const label = document.createElement("span");
      label.textContent = `${idx + 1}. ${t.name}`;
      if (t.server_label) {
        const server = document.createElement("span");
        server.className = "msg-meta-server";
        server.textContent = t.server_label;
        label.appendChild(server);
      }
      const duration = document.createElement("strong");
      duration.textContent = dur;
      row.appendChild(label);
      row.appendChild(duration);
      steps.appendChild(row);
    });
    badge.appendChild(stepsSummary);
    badge.appendChild(steps);
    metaWrap.appendChild(badge);
  }

  if (trace) {
    let traceObj = trace;
    if (typeof trace === "string") {
      try {
        traceObj = JSON.parse(trace);
      } catch (_) {
        traceObj = null;
      }
    }
    if (traceObj && typeof traceObj === "object" && Object.keys(traceObj).length > 0) {
      // A stored message keeps only a reference to its trace, which is read on demand; summarising the reference would show the run as unfinished.
      const storedReference = traceObj.storage === "invocation";
      if (window.LogosModelView && !storedReference) {
        const modelView = window.LogosModelView.render(traceObj);
        if (modelView) metaWrap.appendChild(modelView);
      }
      if (window.LogosExecutionSurface && !storedReference) {
        const surface = window.LogosExecutionSurface.project(traceObj);
        if (surface) metaWrap.appendChild(window.LogosExecutionSurface.render(surface));
      }
      if (window.LogosTraceSummary && typeof window.LogosTraceSummary.build === "function" && !storedReference) {
        const technical = window.LogosTraceSummary.build(traceObj);
        if (technical && technical.items && technical.items.length > 0) {
          const technicalEl = document.createElement("details");
          technicalEl.className = "msg-meta-details msg-technical-details";
          const technicalSummary = document.createElement("summary");
          technicalSummary.className = "msg-meta-summary";
          technicalSummary.textContent = technical.title;
          const technicalList = document.createElement("div");
          technicalList.className = "msg-meta-detail-list msg-technical-list";

          technical.items.forEach(function (item) {
            const row = document.createElement("div");
            row.className = "msg-technical-step";
            row.dataset.kind = item.kind || "event";

            const label = document.createElement("span");
            label.className = "msg-technical-label";
            label.textContent = (item.icon ? item.icon + " " : "") + item.label;

            const detail = document.createElement("span");
            detail.className = "msg-technical-detail";
            detail.textContent = [item.detail, item.status].filter(Boolean).join(" · ");

            row.appendChild(label);
            row.appendChild(detail);
            technicalList.appendChild(row);
          });

          technicalEl.appendChild(technicalSummary);
          technicalEl.appendChild(technicalList);
          metaWrap.appendChild(technicalEl);
        }
      }

      const traceEl = document.createElement("details");
      traceEl.className = "msg-meta-details msg-trace-details";
      const summary = document.createElement("summary");
      summary.className = "msg-meta-summary";
      const traceId = traceObj.trace_id ? String(traceObj.trace_id).substring(0, 8) : "local";
      const eventsCount = Array.isArray(traceObj.events) ? traceObj.events.length : 0;
      summary.textContent = Array.isArray(traceObj.events)
        ? `🔍 Трасса [${traceId}...] (${eventsCount} соб.)`
        : `🔍 Трасса [${traceId}...]`;
      traceEl.appendChild(summary);

      // The Trace Viewer is a first-class chat action. Do not rely only on
      // trace_viewer_bridge.js parsing the generated HTML after the fact.
      traceEl.dataset.traceViewerDirect = "1";
      const openTraceBtn = document.createElement("button");
      openTraceBtn.type = "button";
      openTraceBtn.className = "logos-btn logos-trace-open-viewer";
      const openTraceLabel = "🔍 Открыть трассу";
      const traceSummaryText = summary.textContent;
      openTraceBtn.textContent = openTraceLabel;
      openTraceBtn.title = "Открыть Execution Trace viewer";

      openTraceBtn.addEventListener("click", async function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (typeof window.openTraceViewer !== "function") {
          console.error("[CHAT] Execution Trace Viewer is not loaded");
          window.LogosToast.error("Просмотр трассы не загрузился. Обновите страницу.");
          return;
        }
        // A stored message keeps only a reference to its trace; the full trace is read from the invocation.
        if (traceObj.storage === "invocation" && traceObj.invocation_id) {
          let loaded = null;
          openTraceBtn.disabled = true;
          openTraceBtn.textContent = "🔍 Загружаем трассу…";
          try {
            const res = await window.LogosDispatcher.request(
              `/api/invocations/${encodeURIComponent(traceObj.invocation_id)}/trace`,
              {},
              { timeoutMs: 60000 },
            );
            if (!res.ok) {
              const httpError = new Error("HTTP " + res.status);
              httpError.status = res.status;
              throw httpError;
            }
            loaded = await res.json();
          } catch (err) {
            // The reason is named (a missing trace, the server, the connection), and the same button loads it again.
            const reason = err && err.status === 404
              ? "трасса не найдена на сервере"
              : err && err.status
                ? "сервер не отдал трассу"
                : err && err.name === "TimeoutError"
                  ? "сервер долго не отвечает"
                  : err && err.name === "SyntaxError"
                    ? "ответ сервера не удалось прочитать"
                    : "нет связи с сервером";
            summary.textContent = "🔍 Трасса не загрузилась: " + reason;
            openTraceBtn.textContent = "🔍 Повторить загрузку трассы";
            window.LogosToast.error("Трасса не загрузилась: " + reason + ". Нажмите «Повторить загрузку трассы».");
            console.error("[CHAT] Trace load failed:", err && (err.status || err.name), err && err.message);
          } finally {
            openTraceBtn.disabled = false;
          }
          if (loaded) {
            summary.textContent = traceSummaryText;
            openTraceBtn.textContent = openTraceLabel;
            try {
              window.openTraceViewer(loaded);
            } catch (viewerError) {
              console.error("[CHAT] Trace viewer failed:", viewerError);
              window.LogosToast.error("Трасса загружена, но просмотр не открылся. Обновите страницу и нажмите «Открыть трассу» снова.");
            }
          }
          return;
        }
        window.openTraceViewer(traceObj);
      });
      metaWrap.appendChild(traceEl);
      metaWrap.appendChild(openTraceBtn);
    }
  }

  if (metaWrap.children.length > 0 || (cost && cost > 0)) msg.appendChild(buildDetails(metaWrap, usage, totalDurationMs, cost));
  chatbox.appendChild(msg);
  chatbox.scrollTop = chatbox.scrollHeight;
  return msg;
}

// A photo request stored with its question is checked once after a reload: a finished photo is shown,
// a pending or failed one is named as such, and no result is promised before the check.
function checkStoredImageRequest(convId, anchor, imageRequestId) {
  const path = "/api/images/requests/" + encodeURIComponent(imageRequestId) +
    "?conversation_id=" + encodeURIComponent(convId);
  window.LogosDispatcher.request(path, {}, { timeoutMs: 15000 })
    .then(
      async function (response) {
        const data = await response.json().catch(() => ({}));
        if (response.ok && data.status === "done" && data.reply) return { reply: data.reply, id: data.message_id };
        if (response.status === 202 && data.status === "pending") return { text: "Изображение ещё формируется.", retry: true };
        return { text: "⚠️ Ошибка: " + chatErrorReason(data, response.status) };
      },
      function () {
        return { text: "Не удалось проверить изображение. Проверьте соединение.", retry: true };
      },
    )
    .then(function (result) {
      // The chat may have been left, or its history redrawn, while the check was running.
      if (currentConvId !== convId || !anchor.isConnected) return;
      const node = addMessage({
        text: result.reply || result.text,
        role: result.reply ? "bot" : "error",
        id: result.id,
        onRetry: result.retry
          ? function () { return window.logosRecoverImage({ conversationId: convId, imageRequestId: imageRequestId }); }
          : undefined,
      });
      // The answer belongs right after its question, not at the end of the chat.
      if (node) anchor.after(node);
    });
}

// A failed history load names its cause (no connection, slow or failing server) and offers «Повторить».
function historyErrorBlock(convId, message) {
  const error = document.createElement("div");
  error.className = "empty-state error-state";
  const text = document.createElement("p");
  text.textContent = message;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "logos-btn";
  retry.textContent = "Повторить";
  retry.addEventListener("click", function () {
    error.remove();
    loadHistory(convId);
  });
  error.append(text, retry);
  return error;
}

function historyErrorText(err) {
  if (err && err.status) return "Сервер не смог отдать диалог. Нажмите «Повторить» или попробуйте позже.";
  if (err && err.name === "TimeoutError") return "Сервер долго не отвечает, диалог не загрузился. Нажмите «Повторить».";
  if (err && err.name === "SyntaxError") {
    return "Ответ сервера не удалось прочитать, диалог не загрузился. Нажмите «Повторить» или попробуйте позже.";
  }
  return "Нет связи с сервером, диалог не загрузился. Проверьте соединение и нажмите «Повторить».";
}

function historyReloadErrorText(err) {
  if (err && err.status) return "Не удалось обновить диалог: сервер не ответил. Нажмите «Повторить» или попробуйте позже.";
  if (err && err.name === "TimeoutError") return "Не удалось обновить диалог: сервер долго не отвечает. Нажмите «Повторить».";
  if (err && err.name === "SyntaxError") {
    return "Не удалось обновить диалог: ответ сервера не прочитался. Нажмите «Повторить» или попробуйте позже.";
  }
  return "Не удалось обновить диалог: нет связи с сервером. Проверьте соединение и нажмите «Повторить».";
}

function loadHistory(convId) {
  const chatbox = document.getElementById("chatbox");
  if (!chatbox) return;
  // Reloading the open chat keeps its messages on screen until the new copy arrives, so a failed reload hides nothing.
  const shown = chatbox.dataset.conversationId === convId && chatbox.querySelector(".msg")
    ? Array.from(chatbox.children)
    : null;
  if (!shown) chatbox.replaceChildren();
  chatbox.dataset.conversationId = convId;
  return window.LogosDispatcher.request(`/api/conversations/${convId}/messages`)
    .then((r) => {
      if (!r.ok) {
        const error = new Error("HTTP " + r.status);
        error.status = r.status;
        throw error;
      }
      return r.json();
    })
    .then((data) => {
      // The user may have switched conversations while this request was in flight.
      if (convId !== currentConvId) return;
      // Only the copy shown before the reload is replaced; whatever was added while it was read stays.
      if (shown) shown.forEach(function (node) { node.remove(); });
      try {
        let rendered = 0;
        // The question still waiting for an answer, as the messages are read in order.
        let question = null;
        (data.messages || []).forEach((msg, index, all) => {
          // A stored error answers the open question; «Повторить» re-sends it, and only the newest error keeps the button.
          const owner = msg.source === "error" && index === all.length - 1 ? question : null;
          const retryText = owner ? owner.text : null;
          // A user message that carried photos shows their count, as it did when it was sent.
          const photos = msg.role === "user" && msg.attachments ? msg.attachments.images : 0;
          const node = addMessage({
            text: photos ? (msg.text ? msg.text + " · фото: " : "Фото: ") + photos : msg.text,
            role: msg.source === "error" ? "error" : msg.role,
            cost: msg.cost || 0,
            timings: msg.timings,
            totalDurationMs: msg.total_duration_ms || 0,
            reasoning: msg.reasoning,
            usage: msg.usage,
            trace: msg.trace,
            id: msg.id,
            onRetry: retryText === null ? undefined : function () {
              return window.logosRetryTurn({
                text: retryText,
                conversationId: convId,
                userMessageId: owner.id,
                imageCount: owner.attachments ? owner.attachments.images || 0 : 0,
                replaces: node,
              });
            },
            // A question with photos is not offered for editing: the photos would not go with the edited text.
            editText: retryText !== null && !(owner.attachments && owner.attachments.images) ? retryText : undefined,
          });
          if (node) rendered += 1;
          const imageRequest = msg.role === "user" && typeof msg.source === "string"
            ? msg.source.match(/^image-request:([A-Za-z0-9_-]{16,128})$/)
            : null;
          if (imageRequest && !all.some(function (candidate) { return candidate.source === "image:" + imageRequest[1]; })) {
            if (node) checkStoredImageRequest(convId, node, imageRequest[1]);
          }
          if (msg.role === "user") question = msg;
          else if (msg.source !== "error") question = null;
        });
        const stored = data.messages || [];
        const lastStored = stored[stored.length - 1];
        // A plain question with no answer yet is still being answered on the server: wait for it, as after a lost connection.
        if (lastStored && lastStored.role === "user" && !lastStored.source && typeof window.logosResumeWaitingTurn === "function") {
          window.logosResumeWaitingTurn(convId, stored.length - 1, lastStored);
        }
        if (!rendered) {
          const empty = document.createElement("div");
          empty.className = "empty-state";
          empty.textContent = "Начните диалог";
          chatbox.replaceChildren(empty);
        }
      } catch (renderErr) {
        console.error("[CHAT] Render history error:", renderErr);
        chatbox.replaceChildren(historyErrorBlock(convId, "Не удалось показать диалог. Нажмите «Повторить» или обновите страницу."));
      }
      flushApprovalOutputs(convId);
    })
    .catch((err) => {
      if (convId !== currentConvId) return;
      if (err && err.status === 404) {
        // Deleted in another tab or on another device: the chat leaves this list, and no retry is offered for it.
        window.LogosToast.error("Диалог удалён.");
        if (typeof deleteConv === "function") deleteConv(convId);
        return;
      }
      console.error("[CHAT] Fetch history error:", err);
      // The chat already on screen stays: the error and «Повторить» go under it,
      // one block at a time, worded as a failed refresh.
      if (shown && shown.some(function (node) { return node.isConnected; })) {
        chatbox.querySelectorAll(".error-state").forEach(function (node) { node.remove(); });
        chatbox.appendChild(historyErrorBlock(convId, historyReloadErrorText(err)));
        chatbox.scrollTop = chatbox.scrollHeight;
      } else {
        chatbox.replaceChildren(historyErrorBlock(convId, historyErrorText(err)));
      }
    });
}

document.addEventListener("DOMContentLoaded", function () {
  const sendBtn = document.getElementById("send-btn");
  const input = document.getElementById("msg-input");
  const pendingConversations = new Set();
  // The chat request in flight for each conversation; «Стоп» aborts only this tab's connection, the server finishes the turn.
  const activeChatRequests = new Map();
  let creatingConversation = false;
  const turnStarts = new Map();
  function markTurnStart(convId) { turnStarts.set(convId, (turnStarts.get(convId) || 0) + 1); }

  // Every server code is named in Russian (chatErrorReason); the code and English server text go to the console.
  function errorReasonText(data, status) {
    return chatErrorReason(data, status);
  }

  // «Стоп» ends only this tab's stream. Once the server has the question ('turn' event) it goes on with the answer; before
  // that the server may not have the question at all, so the text goes back to the field (see sendTurn).
  function stopNotice(accepted) {
    return accepted
      ? "Показ ответа остановлен. Сервер продолжает ответ: он появится в этом диалоге, когда сохранится."
      : "Показ ответа остановлен до подтверждения сервером. Если вопрос дошёл до сервера, ответ появится в этом диалоге.";
  }

  // Shows the reason in Russian. The browser's own message (for example «The operation was aborted») stays out of the chat.
  function chatErrorText(error) {
    if (error && error.name === "AbortError") {
      return stopNotice(false);
    }
    if (error && error.name === "TimeoutError") {
      return "Ответ не пришёл за 5 минут. Повторите запрос.";
    }
    return "Не удалось получить ответ. Проверьте соединение и повторите.";
  }

  // A voice failure from the browser (no connection, no answer in time, an unreadable answer) is named in Russian;
  // the voice module's own Russian text and server codes pass on unchanged. The original error goes to the console.
  function voiceErrorText(error) {
    const name = error && error.name;
    if (name === "TypeError" || name === "AbortError" || name === "TimeoutError" || name === "SyntaxError") {
      console.warn("[CHAT] Voice request failed:", name, error.message);
      if (name === "TimeoutError") return "Сервер не ответил вовремя. Повторите сообщение.";
      if (name === "SyntaxError") return "Ответ сервера не удалось прочитать. Повторите сообщение позже.";
      return "Нет связи с сервером. Проверьте соединение и повторите.";
    }
    return (error && error.message) || "Не удалось получить голосовой ответ";
  }

  // The send button becomes «Stop» while the answer in the open conversation can be stopped. While a send cannot be made
  // (an answer that cannot be stopped is on its way, photos are still being read), the button looks disabled but still
  // takes the tap, and the tap says why nothing is sent instead of being ignored.
  function syncSendButton() {
    if (!sendBtn) return;
    const running = Boolean(currentConvId && activeChatRequests.has(currentConvId));
    const pending = currentConvId ? pendingConversations.has(currentConvId) : creatingConversation;
    const waiting = !running && (pending || imagesInFlight > 0);
    sendBtn.textContent = running ? "■" : "➤";
    const label = running
      ? "Остановить ответ"
      : pending
        ? "Отправка недоступна: ответ ещё формируется"
        : imagesInFlight > 0
          ? "Отправка недоступна: фото ещё загружаются"
          : "Отправить";
    sendBtn.setAttribute("aria-label", label);
    sendBtn.title = label;
    if (waiting) sendBtn.setAttribute("aria-disabled", "true");
    else sendBtn.removeAttribute("aria-disabled");
    sendBtn.disabled = !running && !waiting && (!input || (!input.value.trim() && !pendingImages.length));
  }

  const attachBtn = document.getElementById("attach-btn");
  const imageInput = document.getElementById("image-input");
  const previewBox = document.getElementById("image-previews");
  // Photos chosen for the next message; each has a file name and a base64 data URL.
  let pendingImages = [];
  let imagesInFlight = 0;
  const MAX_IMAGES = 4;
  const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
  const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

  function renderPendingImages() {
    syncSendButton();
    if (!previewBox) return;
    previewBox.replaceChildren();
    previewBox.hidden = pendingImages.length === 0;
    pendingImages.forEach(function (image, index) {
      const thumb = document.createElement("img");
      thumb.src = image.dataUrl;
      thumb.alt = image.name;
      thumb.className = "image-preview";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Убрать фото");
      remove.addEventListener("click", function () {
        pendingImages.splice(index, 1);
        renderPendingImages();
      });
      const item = document.createElement("span");
      item.className = "image-preview-item";
      item.append(thumb, remove);
      previewBox.appendChild(item);
    });
  }

  function readAsDataUrl(file) {
    return new Promise(function (resolve, reject) {
      const reader = new FileReader();
      reader.onload = function () {
        resolve(reader.result);
      };
      reader.onerror = function () {
        reject(reader.error);
      };
      reader.readAsDataURL(file);
    });
  }

  // Photos are for the chat they were attached in; switching chats drops them.
  window.addEventListener("logos:conversation-selected", function () {
    if (!pendingImages.length) return;
    pendingImages = [];
    renderPendingImages();
  });

  // Photos go only to a model that reads them; switching to another model drops the tray.
  window.addEventListener("logos:model-changed", function () {
    if (!pendingImages.length || window.modelAcceptsPhotos(currentModel)) return;
    pendingImages = [];
    renderPendingImages();
  });

  if (attachBtn && imageInput) {
    attachBtn.addEventListener("click", function () {
      imageInput.click();
    });
    imageInput.addEventListener("change", async function () {
      const files = Array.from(imageInput.files || []);
      imageInput.value = "";
      for (const file of files) {
        // Count photos still being read, so two quick picks cannot pass the limit together.
        if (pendingImages.length + imagesInFlight >= MAX_IMAGES) {
          window.LogosToast.error("Можно прикрепить не больше " + MAX_IMAGES + " фото");
          break;
        }
        if (!IMAGE_TYPES.includes(file.type) || file.size > MAX_IMAGE_BYTES) {
          window.LogosToast.error("Фото «" + file.name + "»: нужен PNG, JPEG или WebP до 5 МБ");
          continue;
        }
        // The photo belongs to the chat and model it was picked in, even if the user switches while it is read.
        const scope = currentConvId + "|" + currentModel;
        imagesInFlight += 1;
        syncSendButton();
        try {
          const dataUrl = await readAsDataUrl(file);
          if (currentConvId + "|" + currentModel !== scope) {
            window.LogosToast.error("Фото «" + file.name + "» не прикреплено: сменился диалог или модель");
          } else {
            pendingImages.push({ name: file.name, dataUrl: dataUrl });
            renderPendingImages();
          }
        } catch (error) {
          window.LogosToast.error("Не удалось прочитать фото «" + file.name + "»");
        } finally {
          imagesInFlight -= 1;
          syncSendButton();
        }
      }
    });
  }

  // Lets the history loader (outside this closure) re-send a failed message.
  window.logosRetryTurn = function (request) {
    return sendTurn(request);
  };

  function isImageModel(modelKey) {
    return Boolean(modelsData && modelsData.image && modelsData.image[modelKey]);
  }

  function imageRequestId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    if (window.crypto && typeof window.crypto.getRandomValues === "function") {
      const bytes = window.crypto.getRandomValues(new Uint8Array(16));
      return Array.from(bytes, function (byte) { return byte.toString(16).padStart(2, "0"); }).join("");
    }
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
  }

  // A refused image request keeps the server's code and texts, so the chat names it in Russian (chatErrorReason).
  function imageFailure(data, response, turn) {
    return {
      error: data.error || "HTTP " + response.status,
      message: data.message,
      detail: data.detail,
      httpStatus: response.status,
      imageRequestId: turn.imageRequestId,
    };
  }

  // «Стоп» ends the wait for a picture. The signal is checked between reads, so a stopped wait stops polling too.
  function throwIfStopped(turn) {
    if (!turn.signal || !turn.signal.aborted) return;
    const stopped = new Error("Image wait stopped");
    stopped.name = "AbortError";
    throw stopped;
  }

  // Recovery reads our durable request receipt, never repeats the paid provider POST.
  async function readImageResult(turn) {
    const path = "/api/images/requests/" + encodeURIComponent(turn.imageRequestId) +
      "?conversation_id=" + encodeURIComponent(turn.conversationId);
    const deadline = Date.now() + 240000;
    while (Date.now() < deadline) {
      throwIfStopped(turn);
      let response;
      try {
        response = await window.LogosDispatcher.request(path, turn.signal ? { signal: turn.signal } : {}, { timeoutMs: 15000 });
      } catch (error) {
        throwIfStopped(turn);
        return { error: "Соединение прервалось. Повторите получение результата.", imageRequestId: turn.imageRequestId };
      }
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.status === "done" && data.reply) return data;
      throwIfStopped(turn);
      if (response.status !== 202 || data.status !== "pending") {
        return imageFailure(data, response, turn);
      }
      // The pause between reads ends at once on «Стоп».
      await new Promise(function (resolve) {
        const signal = turn.signal;
        const done = function () {
          clearTimeout(timer);
          if (signal) signal.removeEventListener("abort", done);
          resolve();
        };
        const timer = setTimeout(done, 2000);
        if (signal) signal.addEventListener("abort", done);
      });
    }
    return { error: "Результат ещё не подтверждён. Повторите получение результата.", imageRequestId: turn.imageRequestId };
  }

  window.logosRecoverImage = async function (turn) {
    const data = await readImageResult(turn);
    if (currentConvId !== turn.conversationId) return;
    if (data.reply) {
      addMessage({ text: data.reply, role: "bot", id: data.message_id });
    } else {
      addMessage({
        text: "⚠️ Ошибка: " + chatErrorReason(data, data.httpStatus),
        role: "error",
        onRetry: function () { return window.logosRecoverImage(turn); },
      });
    }
  };

  async function runImageTurn(turn) {
    if (turn.attachmentCount) {
      return { error: "Alice AI ART не принимает фото: уберите фото или выберите другую модель" };
    }
    let response;
    try {
      response = await window.LogosDispatcher.request("/api/images/generate", {
        method: "POST",
        signal: turn.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: turn.conversationId,
          request_id: turn.imageRequestId,
          message: turn.text,
          model: turn.model,
        }),
      }, { timeoutMs: 300000 });
    } catch (error) {
      throwIfStopped(turn);
      return readImageResult(turn);
    }
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.status === "done" && data.reply) return data;
    throwIfStopped(turn);
    if (response.status === 202 && data.status === "pending") return readImageResult(turn);
    return imageFailure(data, response, turn);
  }

  function isVoiceModel(modelKey) {
    return Boolean(modelsData && modelsData.voice && modelsData.voice[modelKey]);
  }

  function sendMessage() {
    const running = currentConvId ? activeChatRequests.get(currentConvId) : null;
    if (running) {
      running.abort();
      return;
    }
    return sendTurn(null);
  }

  window.addEventListener("logos:conversation-selected", syncSendButton);

  // A lost connection does not lose a turn: the server keeps it and stores the answer. The number of
  // stored messages before the send tells a turn the server kept from one it never received.
  const TURN_RECOVERY_MS = 600000;
  const TURN_RECOVERY_POLL_MS = 3000;
  const TURN_MISSING_POLLS = 3;

  function formatElapsed(ms) {
    const total = Math.floor(ms / 1000);
    const pad = function (n) { return String(n).padStart(2, "0"); };
    return pad(Math.floor(total / 60)) + ":" + pad(total % 60);
  }

  // One line under the waiting dots: what the turn is doing and for how long. The handle sets the label and removes the line.
  // The chat is a live region: the label is read out once and when it changes, the per-second timer is not read out.
  function createTurnStatus(chatbox, startedAt) {
    const el = document.createElement("div");
    el.className = "typing-status";
    const labelEl = document.createElement("span");
    const timeEl = document.createElement("span");
    timeEl.setAttribute("aria-hidden", "true");
    el.append(labelEl, timeEl);
    let label = "Отправляем запрос";
    labelEl.textContent = label;
    function render() {
      timeEl.textContent = " · " + formatElapsed(Date.now() - startedAt);
    }
    render();
    if (chatbox) chatbox.appendChild(el);
    const timer = setInterval(render, 1000);
    return {
      setLabel: function (text) {
        if (text !== label) {
          label = text;
          labelEl.textContent = label;
        }
        render();
      },
      remove: function () { clearInterval(timer); el.remove(); },
      element: el,
    };
  }

  async function fetchMessageCount(convId) {
    try {
      const response = await window.LogosDispatcher.request("/api/conversations/" + convId + "/messages", undefined, { timeoutMs: 15000 });
      if (!response.ok) return null;
      const data = await response.json();
      return Array.isArray(data.messages) ? data.messages.length : null;
    } catch (error) {
      return null;
    }
  }

  // "missing": nothing was stored after the send. "pending": the user message is stored, the answer is not.
  // "answered": the answer (or an error answer) is stored.
  function turnStatus(messages, baseline) {
    if (messages.length <= baseline) return "missing";
    return messages[messages.length - 1].role === "assistant" ? "answered" : "pending";
  }

  // An accepted turn whose count before the send failed (the phone was offline then) takes its baseline from the count
  // once the server names the stored question: the question is the last message of a new turn, and was already counted
  // for a retry of a stored one (the rule logosResumeWaitingTurn uses). Resolves to whether the turn has a baseline; a
  // failed read is tried again on the next call.
  function ensureTurnBaseline(turn) {
    if (typeof turn.baselineCount === "number") return Promise.resolve(true);
    if (!turn.accepted || !turn.conversationId) return Promise.resolve(false);
    if (!turn.baselineRequest) {
      turn.baselineRequest = fetchMessageCount(turn.conversationId).then(function (count) {
        turn.baselineRequest = null;
        if (typeof turn.baselineCount !== "number" && typeof count === "number") {
          turn.baselineCount = turn.userMessageId ? count : Math.max(0, count - 1);
          // A retry adds no question, so a count read after acceptance may already include its stored answer: the
          // turn's receipt decides for it (needsTurnReceipt), and this baseline is only the fallback.
          if (turn.userMessageId) turn.baselineLate = true;
        }
        return typeof turn.baselineCount === "number";
      });
    }
    return turn.baselineRequest;
  }

  // A retry accepted by the server before its count was read cannot be judged by the count; its receipt can.
  function needsTurnReceipt(turn) {
    return Boolean(turn.chatRequestId) && Boolean(turn.userMessageId) && Boolean(turn.accepted) &&
      (turn.baselineLate === true || typeof turn.baselineCount !== "number");
  }

  // The server's receipt of a sent turn: {status: "stored" | "lost" | "running", data}, or null when it cannot tell (no
  // receipt, or no answer); the count rule decides then.
  async function readTurnReceipt(turn, signal) {
    if (!turn.chatRequestId || !turn.conversationId) return null;
    const path = "/api/chat/turns/" + encodeURIComponent(turn.chatRequestId) +
      "/events?conversation_id=" + encodeURIComponent(turn.conversationId);
    const init = { method: "GET", headers: { Accept: "application/json" } };
    if (signal) init.signal = signal;
    try {
      const response = await window.LogosDispatcher.request(path, init, { timeoutMs: 15000 });
      // A turn still live on the server answers with its event stream: it is running, and nothing is read from it.
      if (isEventStream(response)) {
        discardBody(response);
        return { status: "running" };
      }
      const data = await response.json().catch(function () { return null; });
      if (data && data.status === "stored") return { status: "stored", data: data };
      if (data && data.status === "lost") return { status: "lost", data: data };
      if (response.status === 202) return { status: "running", data: data };
      return null;
    } catch (error) {
      return null;
    }
  }

  // The turns this tab is answering, by conversation id, with their request ids. A reload keeps them, so an answer that is
  // still streaming on the server is read again from its first event. sessionStorage can be blocked or full: every access
  // is guarded, and without it the turn still runs, it just is not read again after a reload.
  const LIVE_TURNS_KEY = "logos_live_turns";

  function savedLiveTurns() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(LIVE_TURNS_KEY) || "null");
      return saved && typeof saved === "object" ? saved : {};
    } catch (error) {
      return {};
    }
  }

  function rememberLiveTurn(convId, requestId) {
    try {
      const saved = savedLiveTurns();
      saved[convId] = requestId;
      sessionStorage.setItem(LIVE_TURNS_KEY, JSON.stringify(saved));
    } catch (error) {
      // Not kept: a reload then waits for the answer in the stored history.
    }
  }

  function forgetLiveTurn(convId, requestId) {
    try {
      const saved = savedLiveTurns();
      if (saved[convId] !== requestId) return;
      delete saved[convId];
      sessionStorage.setItem(LIVE_TURNS_KEY, JSON.stringify(saved));
    } catch (error) {
      // Nothing was kept, so there is nothing to clear.
    }
  }

  function releaseTurn(turn, needsConversation) {
    if (turn.conversationId) pendingConversations.delete(turn.conversationId);
    if (turn.conversationId) activeChatRequests.delete(turn.conversationId);
    // The turn is over for this tab, so a reload no longer reads it again.
    if (turn.chatRequestId) forgetLiveTurn(turn.conversationId, turn.chatRequestId);
    if (needsConversation) creatingConversation = false;
    syncSendButton();
  }

  // «Стоп» during the recovery wait ends it at once: abort() also cancels the pause and the poll in flight.
  function newTurnRecovery() {
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const recovery = {
      cancelled: false,
      signal: controller ? controller.signal : undefined,
      abort: function () {
        recovery.cancelled = true;
        if (controller) controller.abort();
      },
    };
    return recovery;
  }

  function recoveryPause(recovery) {
    return new Promise(function (resolve) {
      const timer = setTimeout(done, TURN_RECOVERY_POLL_MS);
      function done() {
        clearTimeout(timer);
        if (recovery.signal) recovery.signal.removeEventListener("abort", done);
        resolve();
      }
      if (recovery.signal) recovery.signal.addEventListener("abort", done);
    });
  }

  async function recoverTurn(turn, recovery, onMissing) {
    const started = Date.now();
    let missing = 0;
    while (!recovery.cancelled && Date.now() - started < TURN_RECOVERY_MS) {
      await recoveryPause(recovery);
      if (recovery.cancelled) break;
      if (needsTurnReceipt(turn)) {
        const receipt = await readTurnReceipt(turn, recovery.signal);
        if (recovery.cancelled) break;
        if (receipt && receipt.status === "stored") {
          if (turn.live) turn.live.remove();
          if (currentConvId === turn.conversationId) loadHistory(turn.conversationId);
          return;
        }
        if (receipt && receipt.status === "lost") {
          if (typeof turn.onLost === "function") turn.onLost(receipt.data);
          return;
        }
        if (receipt && receipt.status === "running") {
          missing = 0;
          if (turn.status) turn.status.setLabel("Вопрос сохранён, модель отвечает");
          continue;
        }
      }
      // An accepted turn sent while offline learns its baseline here; until then nothing can be compared. The count read
      // takes no signal, so «Стоп» ends this wait without it (the shared read goes on for watchStoppedTurn).
      if (typeof turn.baselineCount !== "number") {
        let onStop = null;
        const hasBaseline = await Promise.race([
          ensureTurnBaseline(turn),
          new Promise(function (resolve) {
            onStop = function () { resolve(false); };
            if (recovery.signal) recovery.signal.addEventListener("abort", onStop, { once: true });
          }),
        ]);
        if (recovery.signal) recovery.signal.removeEventListener("abort", onStop);
        if (recovery.cancelled) break;
        if (!hasBaseline) continue;
      }
      if (recovery.cancelled) break;
      let status = null;
      try {
        const response = await window.LogosDispatcher.request(
          "/api/conversations/" + turn.conversationId + "/messages",
          recovery.signal ? { signal: recovery.signal } : undefined,
          { timeoutMs: 15000 },
        );
        if (!response.ok) continue;
        const data = await response.json();
        status = turnStatus(Array.isArray(data.messages) ? data.messages : [], turn.baselineCount);
      } catch (error) {
        // Still offline: keep waiting until the window closes.
        continue;
      }
      if (recovery.cancelled) break;
      // A stored question (the server accepted it, or a retry of a stored one) adds no message while it is answered:
      // an unchanged count means it is still pending, not that the server never received it.
      if (status === "missing" && (turn.accepted || turn.userMessageId)) status = "pending";
      if (turn.status) turn.status.setLabel(status === "pending" ? "Вопрос сохранён, модель отвечает" : "Отправляем запрос");
      if (status === "answered") {
        // The stored reply comes with the history; the live bubble of this turn gives way to it.
        if (turn.live) turn.live.remove();
        if (currentConvId === turn.conversationId) loadHistory(turn.conversationId);
        return;
      }
      if (status === "missing") {
        missing += 1;
        if (missing >= TURN_MISSING_POLLS) {
          onMissing();
          return;
        }
      } else {
        missing = 0;
      }
    }
    if (!recovery.cancelled) {
      deliverApprovalOutput(turn.conversationId, function () {
        addMessage({ text: "Ответ не пришёл вовремя. Откройте диалог заново: если ответ сохранён на сервере, он появится. Если нет, отправьте вопрос ещё раз.", role: "error" });
      });
    }
  }

  // «Стоп» ends only the wait: the server still finishes the turn and stores its answer. While this chat stays open and no
  // newer message is on its way in it, the stored answer is shown as soon as it is saved (the history is read again, so
  // the «stopped» note gives way to what the server stored). Otherwise the answer comes with the next history load.
  async function watchStoppedTurn(turn) {
    // An accepted question is stored, so its baseline can still be read after a send made while offline.
    if (typeof turn.baselineCount !== "number" && !turn.accepted) return;
    const convId = turn.conversationId;
    const startMark = turnStarts.get(convId) || 0;
    const started = Date.now();
    let missing = 0;
    while (Date.now() - started < TURN_RECOVERY_MS) {
      await new Promise(function (resolve) { setTimeout(resolve, TURN_RECOVERY_POLL_MS); });
      if (currentConvId !== convId || pendingConversations.has(convId) || (turnStarts.get(convId) || 0) !== startMark) return;
      if (needsTurnReceipt(turn)) {
        const receipt = await readTurnReceipt(turn);
        if (currentConvId !== convId || pendingConversations.has(convId) || (turnStarts.get(convId) || 0) !== startMark) return;
        if (receipt && receipt.status === "stored") {
          loadHistory(convId);
          return;
        }
        if (receipt && receipt.status === "lost") {
          if (typeof turn.onLost === "function") turn.onLost(receipt.data);
          return;
        }
        if (receipt && receipt.status === "running") {
          missing = 0;
          continue;
        }
      }
      if (typeof turn.baselineCount !== "number" && !(await ensureTurnBaseline(turn))) continue;
      let messages;
      try {
        const response = await window.LogosDispatcher.request("/api/conversations/" + convId + "/messages", undefined, { timeoutMs: 15000 });
        if (!response.ok) continue;
        const data = await response.json();
        messages = Array.isArray(data.messages) ? data.messages : [];
      } catch (error) {
        continue;
      }
      if (currentConvId !== convId || pendingConversations.has(convId) || (turnStarts.get(convId) || 0) !== startMark) return;
      const status = turnStatus(messages, turn.baselineCount);
      if (status === "answered") {
        loadHistory(convId);
        return;
      }
      // A retried or accepted question is already stored and adds no new message, so an unchanged count means it is
      // still pending.
      if (status === "missing" && !turn.userMessageId && !turn.accepted) {
        missing += 1;
        if (missing >= TURN_MISSING_POLLS) {
          // The server never received the question, so no answer will come for it.
          addMessage({ text: "Сервер не получил этот вопрос, ответа на него не будет. Отправьте вопрос ещё раз.", role: "error" });
          return;
        }
      } else {
        missing = 0;
      }
    }
  }

  // After a reload, or in another tab, a question with no answer yet is still being answered: its answer is awaited here.
  // A turn this tab started is read again from its first event, so the text streamed so far comes back; the stored
  // history decides when the server does not know the turn.
  window.logosResumeWaitingTurn = function (convId, baselineCount, question) {
    if (pendingConversations.has(convId) || activeChatRequests.has(convId)) return;
    // The baseline is the count before the open question, so the question itself reads as pending.
    const turn = { conversationId: convId, baselineCount: baselineCount };
    const savedRequestId = savedLiveTurns()[convId];
    if (typeof savedRequestId === "string" && savedRequestId) turn.chatRequestId = savedRequestId;
    const recovery = newTurnRecovery();
    pendingConversations.add(convId);
    markTurnStart(convId);
    activeChatRequests.set(convId, recovery);
    let typingEl = null;
    if (currentConvId === convId) {
      const chatbox = document.getElementById("chatbox");
      typingEl = document.createElement("div");
      typingEl.className = "typing";
      typingEl.innerHTML = "<span></span><span></span><span></span>";
      if (chatbox) chatbox.appendChild(typingEl);
      turn.status = createTurnStatus(chatbox, Date.now());
      turn.typingEl = typingEl;
    }
    // A live answer read again keeps the page busy until it ends, as the send that started it did.
    if (turn.chatRequestId && typeof window.LogosDispatcher.hold === "function") turn.hold = window.LogosDispatcher.hold();
    syncSendButton();
    const resuming = turn.chatRequestId ? resumeSavedLiveTurn(turn, recovery, question) : Promise.resolve(false);
    resuming.then(function (shown) {
      if (shown) return null;
      return recoverTurn(turn, recovery, function () {
        // The question itself has disappeared: there is nothing left to wait for.
      });
    }).finally(function () {
      if (turn.hold) turn.hold();
      if (typingEl) typingEl.remove();
      if (turn.status) turn.status.remove();
      if (recovery.cancelled) {
        deliverApprovalOutput(convId, function () {
          addMessage({ text: stopNotice(true), role: "error" });
        });
        watchStoppedTurn(turn);
      }
      releaseTurn(turn, false);
    });
  };

  // Live answers and automatic re-sends of a text or photo turn.
  // The server sends a keep-alive every 15 s, so three silent periods mean the connection is gone. This ends only the
  // connection, never the turn on the server. It is passed as an argument, where 0 means no bound.
  const LIVE_IDLE_MS = 45000;
  // A photo upload can outlast that window on a slow link: the wait for the headers of a send grows by the time its
  // body needs at this rate (bytes per millisecond, about 32 KB/s).
  const LIVE_UPLOAD_BYTES_PER_MS = 32;
  // The live bubble is redrawn at most this often; a slow redraw (a long Markdown reply on a phone) spaces them further.
  const LIVE_RENDER_MS = 250;
  // Automatic re-sends and re-attachments pause for a random time within a window that doubles from the base up to the
  // cap (full jitter). They go on for TURN_RECOVERY_MS after the last contact with the server.
  const RESEND_BASE_MS = 1000;
  const RESEND_WINDOW_CAP_MS = 15000;

  function retryDelayMs(attempt) {
    const span = Math.min(RESEND_WINDOW_CAP_MS, RESEND_BASE_MS * Math.pow(2, Math.max(attempt, 1) - 1));
    return Math.random() * span;
  }

  // Seconds with a decimal comma, as the status line reads them.
  function formatSeconds(ms) {
    return (ms / 1000).toFixed(1).replace(".", ",");
  }

  function namedError(name, message) {
    const error = new Error(message);
    error.name = name;
    return error;
  }

  function stoppedError() {
    return namedError("AbortError", "Chat turn stopped");
  }

  // A pause that «Стоп» ends at once, with an AbortError.
  function pauseFor(ms, signal) {
    return new Promise(function (resolve, reject) {
      if (signal && signal.aborted) {
        reject(stoppedError());
        return;
      }
      const timer = setTimeout(function () {
        if (signal) signal.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      function onAbort() {
        clearTimeout(timer);
        reject(stoppedError());
      }
      if (signal) signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  function setTurnLabel(turn, text) {
    if (turn.status && typeof text === "string" && text) turn.status.setLabel(text);
  }

  function isPinned(chatbox) {
    return chatbox.scrollHeight - chatbox.scrollTop - chatbox.clientHeight <= 80;
  }

  function responseType(response) {
    try {
      return String((response.headers && response.headers.get("Content-Type")) || "").toLowerCase();
    } catch (error) {
      return "";
    }
  }

  function isEventStream(response) {
    return responseType(response).indexOf("text/event-stream") !== -1;
  }

  // A transient status (408, 425, 429, 503) that is neither the server's JSON nor its event stream is a proxy or gateway
  // page: the turn may not have reached the server, so the same request is sent again. Other 5xx stay the server's error.
  function isGatewayFailure(response) {
    const type = responseType(response);
    return [408, 425, 429, 503].indexOf(response.status) !== -1 &&
      type.indexOf("json") === -1 && type.indexOf("text/event-stream") === -1;
  }

  function discardBody(response) {
    try {
      if (response.body && typeof response.body.cancel === "function") response.body.cancel().catch(function () {});
    } catch (error) {
      // The body is already closed.
    }
  }

  // The live path needs a streamed response body; without one the turn takes the JSON path.
  function liveTurnSupported(params) {
    return Boolean(params) && params.stream === true &&
      typeof ReadableStream === "function" && typeof TextDecoder === "function" &&
      typeof Response === "function" && Boolean(Response.prototype) && "body" in Response.prototype;
  }

  // The chat list takes the conversation title the server stored.
  function applyConversationTitle(convId, title) {
    if (!title || typeof conversations === "undefined" || !Array.isArray(conversations)) return;
    const conversation = conversations.find((item) => item.id === convId);
    if (!conversation) return;
    conversation.title = title;
    localStorage.setItem("conversations", JSON.stringify(conversations));
    if (typeof renderSidebar === "function") renderSidebar();
  }

  // One request of a turn. «Стоп» aborts it, also while its body is read; with idleMs, a request whose headers do not
  // come in time is abandoned. That ends only the connection: the turn on the server goes on.
  async function turnRequest(path, init, stopSignal, idleMs, timeoutMs) {
    if (stopSignal.aborted) throw stoppedError();
    const controller = new AbortController();
    const abort = function () { controller.abort(); };
    stopSignal.addEventListener("abort", abort, { once: true });
    const idleTimer = idleMs > 0 ? setTimeout(abort, idleMs) : null;
    try {
      return await window.LogosDispatcher.request(path, Object.assign({}, init, { signal: controller.signal }), {
        timeoutMs: timeoutMs,
      });
    } catch (error) {
      stopSignal.removeEventListener("abort", abort);
      throw error;
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
    }
  }

  // Sends a text or photo turn and returns the server's answer. A send that got no answer from the server (no
  // connection, a gateway page, no headers in time on the live path) is sent again with the same request_id: the server
  // runs that turn once and answers a repeat with its live replay, «running» or «stored».
  async function postChatTurn(turn, body, live, stopSignal) {
    const started = Date.now();
    const headers = { "Content-Type": "application/json" };
    if (live) headers.Accept = "text/event-stream";
    const idleMs = live && LIVE_IDLE_MS > 0 ? LIVE_IDLE_MS + Math.ceil(body.length / LIVE_UPLOAD_BYTES_PER_MS) : 0;
    for (let attempt = 1; ; attempt += 1) {
      let response = null;
      try {
        response = await turnRequest(
          "/api/chat",
          { method: "POST", headers: headers, body: body },
          stopSignal,
          idleMs,
          live ? 0 : 300000,
        );
      } catch (error) {
        if (stopSignal.aborted) throw stoppedError();
        // The JSON path keeps its 5-minute wait: after it the turn is recovered from the stored history.
        if (!live && error && error.name === "TimeoutError") throw error;
      }
      if (response && !isGatewayFailure(response)) return response;
      if (response) discardBody(response);
      if (Date.now() - started > TURN_RECOVERY_MS) throw new TypeError("No server answer to the chat request");
      const delay = retryDelayMs(attempt);
      setTurnLabel(turn, "Нет связи с сервером: повтор через " + formatSeconds(delay) + " с (попытка " + attempt + ")");
      await pauseFor(delay, stopSignal);
      setTurnLabel(turn, "Отправляем запрос");
    }
  }

  // Server-sent events of a live answer: `data:` lines are joined, `id:` is noted, comments (keep-alive) are skipped,
  // and an unreadable event is skipped. push() returns the `done` event once it has arrived, null before.
  function createSseParser(onEvent) {
    let buffer = "";
    let dataLines = [];
    let eventId = null;
    let done = null;
    function dispatch() {
      const hasData = dataLines.length > 0;
      const raw = dataLines.join("\n");
      const id = eventId;
      dataLines = [];
      eventId = null;
      if (!hasData) return;
      let event;
      try {
        event = JSON.parse(raw);
      } catch (error) {
        return;
      }
      if (!event || typeof event !== "object" || typeof event.type !== "string") return;
      if (event.type === "done") {
        done = event;
        return;
      }
      try {
        onEvent(event, id);
      } catch (error) {
        // A failed redraw does not end the turn: the stored reply replaces the bubble at `done`.
        console.error("[CHAT] Live event failed:", event.type, error);
      }
    }
    function readLine(line) {
      if (line === "") {
        dispatch();
        return;
      }
      if (line.charAt(0) === ":") return;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.charAt(0) === " ") value = value.slice(1);
      if (field === "data") dataLines.push(value);
      else if (field === "id") eventId = value;
    }
    return {
      push: function (text) {
        buffer += text;
        let start = 0;
        let newline;
        while (!done && (newline = buffer.indexOf("\n", start)) !== -1) {
          let line = buffer.slice(start, newline);
          start = newline + 1;
          if (line.charAt(line.length - 1) === "\r") line = line.slice(0, -1);
          readLine(line);
        }
        buffer = buffer.slice(start);
        return done;
      },
    };
  }

  // Reads a live answer. It resolves {done: event} at the `done` frame, and {done: null} when the stream ends without
  // it or nothing (not even a keep-alive) came for idleMs: the connection broke, the turn goes on. «Стоп» throws an
  // AbortError. lastActivity is when the server was last heard from.
  async function readLiveStream(response, options) {
    const signal = options.signal;
    const idleMs = Number(options.idleMs) > 0 ? Number(options.idleMs) : 0;
    const parser = createSseParser(options.onEvent);
    let lastActivity = Date.now();
    if (signal && signal.aborted) throw stoppedError();
    if (!response.body || typeof response.body.getReader !== "function") {
      // Without a streamed body the answer is read whole; it ends with the turn.
      let whole;
      try {
        whole = await response.text();
      } catch (error) {
        if (signal && signal.aborted) throw stoppedError();
        return { done: null, lastActivity: lastActivity };
      }
      if (signal && signal.aborted) throw stoppedError();
      return { done: parser.push(whole), lastActivity: Date.now() };
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let idleTimer = null;
    const cancel = function () {
      try {
        reader.cancel().catch(function () {});
      } catch (error) {
        // The stream is already closed.
      }
    };
    const armIdle = function () {
      if (!idleMs) return;
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(cancel, idleMs);
    };
    if (signal) signal.addEventListener("abort", cancel, { once: true });
    armIdle();
    try {
      for (;;) {
        let chunk;
        try {
          chunk = await reader.read();
        } catch (error) {
          if (signal && signal.aborted) throw stoppedError();
          return { done: null, lastActivity: lastActivity };
        }
        if (signal && signal.aborted) throw stoppedError();
        // A partial event at the end is dropped: without `done` the stream was interrupted.
        if (chunk.done) return { done: parser.push(decoder.decode()), lastActivity: lastActivity };
        lastActivity = Date.now();
        armIdle();
        const done = parser.push(decoder.decode(chunk.value, { stream: true }));
        if (done) {
          cancel();
          return { done: done, lastActivity: lastActivity };
        }
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      if (signal) signal.removeEventListener("abort", cancel);
    }
  }

  // The reply bubble of a live turn: text and reasoning as they arrive, redrawn at most every LIVE_RENDER_MS. It sits
  // above the waiting dots and the status line; at `done` the stored reply takes its place.
  function createLiveReply(turn) {
    const el = document.createElement("div");
    el.className = "msg bot live-reply";
    el.setAttribute("aria-busy", "true");
    // Shown once there is something to read.
    el.hidden = true;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const details = document.createElement("details");
    details.className = "reasoning-details";
    details.hidden = true;
    const summary = document.createElement("summary");
    summary.className = "reasoning-summary";
    summary.textContent = "🧠 Ход мыслей модели";
    const reasoningEl = document.createElement("div");
    reasoningEl.className = "reasoning-content";
    details.append(summary, reasoningEl);
    const textEl = document.createElement("div");
    textEl.className = "live-text";
    bubble.append(details, textEl);
    el.appendChild(bubble);

    // Committed: earlier parts of a reply cut by the provider's output limit. Round: the provider request in progress.
    let committedText = "";
    let roundText = "";
    let committedReasoning = "";
    let roundReasoning = "";
    let shownText = "";
    let shownReasoning = "";
    let renderTimer = null;
    let lastRenderAt = 0;
    let lastRenderMs = 0;
    let closed = false;
    // A history load (switching chats and back) redraws the chat without this bubble: it is put back at the end.
    const attachTimer = setInterval(ensureAttached, 1000);

    function joinReasoning(first, second) {
      return first && second ? first + "\n\n" + second : first || second;
    }

    function fullText() {
      return committedText + roundText;
    }

    function fullReasoning() {
      return joinReasoning(committedReasoning, roundReasoning);
    }

    function ensureAttached() {
      if (closed || currentConvId !== turn.conversationId) return;
      const chatbox = document.getElementById("chatbox");
      if (!chatbox) return;
      if (chatbox.dataset.conversationId && chatbox.dataset.conversationId !== turn.conversationId) return;
      const tail = [el, turn.typingEl, turn.status ? turn.status.element : null].filter(Boolean);
      let node = chatbox.lastElementChild;
      for (let index = tail.length - 1; index >= 0; index -= 1) {
        if (node !== tail[index]) {
          const pinned = isPinned(chatbox);
          tail.forEach(function (item) { chatbox.appendChild(item); });
          if (pinned) chatbox.scrollTop = chatbox.scrollHeight;
          return;
        }
        node = node.previousElementSibling;
      }
    }

    function renderReasoning() {
      const reasoning = fullReasoning();
      details.hidden = !reasoning;
      // A closed block is not redrawn: its text is drawn when it is opened.
      if (details.open && reasoning !== shownReasoning) {
        reasoningEl.innerHTML = parseMarkdown(reasoning);
        shownReasoning = reasoning;
      }
    }

    function render() {
      if (renderTimer) {
        clearTimeout(renderTimer);
        renderTimer = null;
      }
      ensureAttached();
      const started = performance.now();
      const chatbox = el.parentNode;
      // The chat follows the reply only when it was at its end.
      const pinned = Boolean(chatbox) && isPinned(chatbox);
      const text = fullText();
      if (text !== shownText) {
        textEl.innerHTML = parseMarkdown(text);
        shownText = text;
      }
      renderReasoning();
      el.hidden = !text && !fullReasoning();
      if (pinned) chatbox.scrollTop = chatbox.scrollHeight;
      lastRenderMs = performance.now() - started;
      lastRenderAt = Date.now();
    }

    function scheduleRender() {
      if (closed || renderTimer) return;
      const wait = Math.max(0, lastRenderAt + Math.max(LIVE_RENDER_MS, 4 * lastRenderMs) - Date.now());
      renderTimer = setTimeout(function () {
        renderTimer = null;
        if (!closed) render();
      }, wait);
    }

    function close() {
      closed = true;
      clearInterval(attachTimer);
      if (renderTimer) {
        clearTimeout(renderTimer);
        renderTimer = null;
      }
    }

    function apply(event) {
      if (closed) return;
      if (event.type === "round") {
        if (event.kind === "initial") {
          committedText = "";
          committedReasoning = "";
        } else if (event.kind === "output_limit") {
          // A continuation goes on from where the cut part stopped.
          committedText += roundText;
          committedReasoning = joinReasoning(committedReasoning, roundReasoning);
        }
        // The text of a round that called tools is not part of the stored reply.
        roundText = "";
        roundReasoning = "";
      } else if (event.type === "delta") {
        roundText += typeof event.text === "string" ? event.text : "";
      } else if (event.type === "reasoning") {
        roundReasoning += typeof event.text === "string" ? event.text : "";
      } else if (event.type === "round_text") {
        roundText = typeof event.text === "string" ? event.text : "";
        roundReasoning = typeof event.reasoning === "string" ? event.reasoning : "";
      } else {
        return;
      }
      scheduleRender();
    }

    details.addEventListener("toggle", function () {
      if (details.open) renderReasoning();
    });
    if (turn.typingEl && turn.typingEl.parentNode) turn.typingEl.before(el);
    else ensureAttached();

    return {
      el: el,
      // The index of the last event applied; a re-attachment asks for the events after it.
      lastId: 0,
      apply: apply,
      ensureAttached: ensureAttached,
      // The stored reply (or error) takes the bubble's place; without one the bubble leaves.
      finish: function (node) {
        close();
        if (node && node !== el && el.isConnected) {
          // A reasoning block the reader opened stays open in the stored reply, whose block starts closed; otherwise
          // the content above the answer shrinks and the restored scroll position shows other text.
          if (details.open && typeof node.querySelector === "function") {
            const finalDetails = node.querySelector(".bubble > .reasoning-details");
            if (finalDetails) finalDetails.open = true;
          }
          el.replaceWith(node);
        } else el.remove();
      },
      // The stream ended before the stored reply: what came stays, marked as stopped. An empty bubble leaves.
      freeze: function () {
        if (closed) return;
        render();
        close();
        if (!fullText() && !fullReasoning()) {
          el.remove();
          return;
        }
        el.removeAttribute("aria-busy");
        el.classList.add("live-stopped");
      },
      remove: function () {
        close();
        el.remove();
      },
    };
  }

  // A «running» answer that names the stored question: the turn is awaited, never sent again, and its text is not put
  // back for a second send.
  function noteStoredQuestion(turn, data) {
    if (data && Number.isInteger(data.user_message_id) && data.user_message_id > 0) {
      turn.accepted = true;
      turn.serverUserMessageId = data.user_message_id;
      ensureTurnBaseline(turn);
    }
  }

  // One live event: the question is stored, a status for the line under the dots, or reply text for the bubble.
  function applyLiveEvent(turn, live, event) {
    live.ensureAttached();
    if (event.type === "turn") {
      // From now on this turn is never sent again, only re-attached to.
      turn.accepted = true;
      if (Number.isInteger(event.user_message_id) && event.user_message_id > 0) {
        turn.serverUserMessageId = event.user_message_id;
      }
      ensureTurnBaseline(turn);
      applyConversationTitle(turn.conversationId, event.title);
      setTurnLabel(turn, "Вопрос сохранён, модель отвечает");
    } else if (event.type === "status" || event.type === "tool" || event.type === "retry") {
      // Kept for a re-attachment: the server sends a status only when its phase changes, so the replay after a break
      // may hold none.
      if (typeof event.text === "string" && event.text) turn.liveLabel = event.text;
      setTurnLabel(turn, event.text);
    } else {
      live.apply(event);
    }
  }

  // After a broken live stream: reads the turn again from the event after the last one applied. Returns the new stream,
  // or null when the turn's outcome was shown from a JSON answer. Throws LiveStreamLost when the server was not reached
  // for TURN_RECOVERY_MS or no longer knows the request, and TurnRunningError when it runs but cannot be relayed: the
  // stored history is then awaited instead.
  async function reattachLiveTurn(turn, live, stopSignal, lastContact, outcome) {
    const path = "/api/chat/turns/" + encodeURIComponent(turn.chatRequestId) +
      "/events?conversation_id=" + encodeURIComponent(turn.conversationId);
    for (let attempt = 1; ; attempt += 1) {
      if (Date.now() - lastContact > TURN_RECOVERY_MS) throw namedError("LiveStreamLost", "Live answer stream lost");
      const delay = retryDelayMs(attempt);
      setTurnLabel(turn, "Связь с сервером прервалась: переподключаюсь через " + formatSeconds(delay) + " с (попытка " + attempt + ")");
      await pauseFor(delay, stopSignal);
      setTurnLabel(turn, "Подключаюсь к ответу снова");
      let response;
      try {
        response = await turnRequest(
          path,
          { method: "GET", headers: { Accept: "text/event-stream", "Last-Event-ID": String(live.lastId) } },
          stopSignal,
          LIVE_IDLE_MS,
          0,
        );
      } catch (error) {
        if (stopSignal.aborted) throw stoppedError();
        continue;
      }
      if (isEventStream(response)) {
        setTurnLabel(turn, turn.liveLabel || (turn.accepted ? "Связь с сервером восстановлена, модель отвечает" : "Связь с сервером восстановлена"));
        return response;
      }
      const data = await response.json().catch(function () { return null; });
      if (stopSignal.aborted) throw stoppedError();
      if (!data || typeof data !== "object") continue;
      if (data.status === "stored") {
        live.remove();
        if (currentConvId === turn.conversationId) loadHistory(turn.conversationId);
        return null;
      }
      if (data.status === "lost") {
        outcome.lost(data);
        return null;
      }
      if (response.status === 202) {
        noteStoredQuestion(turn, data);
        throw namedError("TurnRunningError", "The turn is still running on the server");
      }
      if (response.status === 404 && data.error === "turn_request_not_found") {
        throw namedError("LiveStreamLost", "The server does not know this request");
      }
      if (response.status === 404 && data.error === "conversation_not_found") {
        live.finish(outcome.render(data, 404));
        return null;
      }
    }
  }

  // Follows a live answer to its `done`, re-attaching after every break, and shows its outcome.
  async function followLiveTurn(turn, firstResponse, stopSignal, outcome) {
    const live = createLiveReply(turn);
    turn.live = live;
    const onEvent = function (event, id) {
      applyLiveEvent(turn, live, event);
      const index = Number(id);
      if (id !== null && Number.isInteger(index) && index > live.lastId) live.lastId = index;
    };
    let response = firstResponse;
    while (response) {
      const result = await readLiveStream(response, { signal: stopSignal, idleMs: LIVE_IDLE_MS, onEvent: onEvent });
      if (result.done) {
        const body = result.done.body && typeof result.done.body === "object" ? result.done.body : {};
        // addMessage scrolls to the end; a reader who scrolled up keeps the same text in view, since the stored reply
        // takes the bubble's place. The chat follows to the end only when it was there.
        const box = live.el.parentNode;
        const keepTop = box && !isPinned(box) ? box.scrollTop : null;
        live.finish(outcome.render(body, Number(result.done.http_status) || 0, { live: true }));
        if (keepTop !== null) box.scrollTop = keepTop;
        return;
      }
      response = await reattachLiveTurn(turn, live, stopSignal, result.lastActivity, outcome);
    }
  }

  // The server lost the turn in a restart. The question can be sent again with «Повторить» unless it had photos, which a
  // reload does not keep. The server's explanation, when it sends one, says whether the question was stored.
  function showReloadLost(turn, question, data) {
    if (turn.live) turn.live.freeze();
    // The lost notice is final for this request: a later history load must not read its saved request id again.
    if (turn.chatRequestId) forgetLiveTurn(turn.conversationId, turn.chatRequestId);
    const retryable = Boolean(question) && !(question.attachments && question.attachments.images);
    deliverApprovalOutput(turn.conversationId, function () {
      const lostNode = addMessage({
        // Without a retry button the server's explanation may tell the user to press «Повторить», so the photo text is used.
        text: retryable
          ? data.detail || CHAT_ERROR_TEXTS.turn_lost
          : "Сервер перезапустился, пока модель отвечала, и ответ не сохранился. Вопрос с фото отправьте заново.",
        role: "error",
        onRetry: retryable
          ? function () {
            return sendTurn({
              text: question.text,
              conversationId: turn.conversationId,
              userMessageId: data.user_message_id || question.id,
              replaces: lostNode,
            });
          }
          : undefined,
      });
    });
  }

  // Reads again the answer of a turn this tab started, from its first event (Last-Event-ID 0). Resolves to true when
  // nothing is left for the history recovery: the turn was shown, or it was stopped. Resolves to false when the server
  // does not know the turn or cannot relay it; the stored history then decides. Never rejects.
  async function resumeSavedLiveTurn(turn, recovery, question) {
    let response;
    try {
      response = await turnRequest(
        "/api/chat/turns/" + encodeURIComponent(turn.chatRequestId) +
          "/events?conversation_id=" + encodeURIComponent(turn.conversationId),
        { method: "GET", headers: { Accept: "text/event-stream", "Last-Event-ID": "0" } },
        recovery.signal,
        LIVE_IDLE_MS,
        0,
      );
    } catch (error) {
      return recovery.cancelled;
    }
    if (!isEventStream(response)) {
      // Not streamed here: the stored reply, a lost turn, or nothing known (the history decides then).
      const data = await response.json().catch(function () { return null; });
      if (data && data.status === "stored") {
        if (currentConvId === turn.conversationId) loadHistory(turn.conversationId);
        return true;
      }
      if (data && data.status === "lost") {
        showReloadLost(turn, question, data);
        return true;
      }
      return recovery.cancelled;
    }
    const outcome = {
      render: function () {
        // The stored reply comes with the history, which is read again.
        if (currentConvId === turn.conversationId) loadHistory(turn.conversationId);
        return null;
      },
      lost: function (data) { showReloadLost(turn, question, data); },
    };
    try {
      await followLiveTurn(turn, response, recovery.signal, outcome);
      return true;
    } catch (error) {
      // A stream that broke for good, or a turn the server cannot relay: the stored history is awaited instead.
      if (turn.live) turn.live.freeze();
      return recovery.cancelled;
    }
  }

  // A live retry keeps its photos; a history retry only knows their count and asks for them again.
  async function sendTurn(retryRequest) {
    if (!input) return;
    const rawText = retryRequest ? "" : input.value;
    const text = retryRequest ? retryRequest.text : rawText.trim();
    if (retryRequest && currentConvId !== retryRequest.conversationId) return;
    const selectedAtSubmit = currentConvId;
    // A send while the chat is busy is not ignored: the hint says why nothing was sent and what to do.
    if (selectedAtSubmit ? pendingConversations.has(selectedAtSubmit) : creatingConversation) {
      window.LogosToast.show(selectedAtSubmit && activeChatRequests.has(selectedAtSubmit)
        ? "Ответ ещё формируется. Дождитесь его или остановите кнопкой ■."
        : "Ответ ещё формируется, дождитесь его окончания.");
      return;
    }
    if (imagesInFlight > 0) {
      window.LogosToast.show("Фото ещё загружаются. Отправьте сообщение, когда появится их превью.");
      return;
    }
    const sentImages = retryRequest
      ? retryRequest.images || (retryRequest.imageCount ? pendingImages.slice() : [])
      : pendingImages.slice();
    if (sentImages.length > MAX_IMAGES) {
      window.LogosToast.error("Можно отправить не больше " + MAX_IMAGES + " фото: уберите лишние из вложений");
      return;
    }
    if (retryRequest && retryRequest.imageCount && sentImages.length !== retryRequest.imageCount) {
      window.LogosToast.error("Для повтора прикрепите фото заново: " + retryRequest.imageCount);
      return;
    }
    if (!text && !sentImages.length) return;

    const turn = {
      conversationId: selectedAtSubmit,
      model: retryRequest && retryRequest.model ? retryRequest.model : currentModel,
      text: text,
      userMessageId: retryRequest && Number.isInteger(retryRequest.userMessageId) && retryRequest.userMessageId > 0
        ? retryRequest.userMessageId : null,
      imageRequestId: retryRequest && retryRequest.imageRequestId ? retryRequest.imageRequestId : imageRequestId(),
    };
    if (isImageModel(turn.model) && Array.from(text).length > 500) {
      window.LogosToast.error("Для Alice AI ART запрос должен содержать не больше 500 символов");
      return;
    }
    // «Повторить» replaces its error: the error keeps its text, but no longer offers a retry.
    if (retryRequest && retryRequest.replaces) {
      retryRequest.replaces.querySelectorAll(".msg-retry-btn").forEach(function (button) { button.remove(); });
    }
    const needsConversation = !selectedAtSubmit;
    if (needsConversation) creatingConversation = true;
    else pendingConversations.add(turn.conversationId);
    markTurnStart(turn.conversationId);
    let typingEl = null;
    // True while a lost connection is being recovered: the turn stays pending until its answer is found.
    let recovering = false;
    // Photos belong to this message only; they return to the tray if the send fails.
    // Sent photos leave the tray. A retry finds them there again after a failed send, so they are removed here too.
    pendingImages = pendingImages.filter(function (image) {
      return !sentImages.includes(image);
    });
    renderPendingImages();
    let imagesRestored = false;
    function restoreImages() {
      if (imagesRestored || !sentImages.length) return;
      imagesRestored = true;
      // Photos added while this send was pending stay in the tray, after the restored ones.
      pendingImages = sentImages.concat(pendingImages.filter(function (image) {
        return !sentImages.includes(image);
      }));
      renderPendingImages();
      if (pendingImages.length > MAX_IMAGES) {
        window.LogosToast.error("Все фото сохранены. Перед отправкой оставьте не больше " + MAX_IMAGES);
      }
    }
    try {
      // Keep the submitted settings and text even if the user edits the next draft while waiting.
      let params;
      try {
        params = typeof window.getResponsesParams === "function" ? window.getResponsesParams() : {};
      } catch (settingsError) {
        // An invalid setting is not a connection problem: nothing was sent, and the message names the setting.
        if (currentConvId === turn.conversationId) {
          restoreImages();
          if (!input.value) input.value = turn.text;
          if (!isRussianText(settingsError && settingsError.message)) console.warn("[CHAT] Settings read failed:", settingsError);
          addMessage({
            text: isRussianText(settingsError && settingsError.message)
              ? settingsError.message
              : "Настройки модели не прочитаны. Проверьте их и повторите.",
            role: "error",
          });
        }
        return;
      }
      turn.params = JSON.parse(JSON.stringify(params || {}));
      if (needsConversation) {
        const createdId = await createConversation(turn.model, { select: false });
        if (!createdId) {
          if (currentConvId === turn.conversationId) restoreImages();
          return;
        }
        turn.conversationId = createdId;
        pendingConversations.add(createdId);
        markTurnStart(createdId);
        // Selecting another chat while creation is pending must not pull the user back.
        if (currentConvId === selectedAtSubmit) await selectConv(createdId);
        // The user moved on while the chat was created: the message is not sent into a chat they are not viewing.
        if (currentConvId !== createdId) {
          if (!input.value) input.value = turn.text;
          window.LogosToast.error("Диалог создан, но сообщение не отправлено. Откройте его и отправьте снова.");
          return;
        }
      }

      if (currentConvId === turn.conversationId) {
        // A retry already has its user message in the chat, so it is not painted twice.
        if (!retryRequest) {
          if (input.value === rawText) input.value = "";
          // A newer question makes the earlier errors stale: only the newest error keeps its «Повторить».
          document.querySelectorAll("#chatbox .msg-retry-btn").forEach(function (button) { button.remove(); });
          addMessage({
            text: sentImages.length
              ? (turn.text ? turn.text + " · фото: " : "Фото: ") + sentImages.length
              : turn.text,
            role: "user",
          });
        }
        const chatbox = document.getElementById("chatbox");
        typingEl = document.createElement("div");
        typingEl.className = "typing";
        typingEl.innerHTML = "<span></span><span></span><span></span>";
        if (chatbox) chatbox.appendChild(typingEl);
        turn.status = createTurnStatus(chatbox, Date.now());
        // A live reply is drawn above the dots and the status line.
        turn.typingEl = typingEl;
      }
      const t0 = performance.now();
      // Shows a finished turn: the JSON answer, the `done` of a live answer, or an image or voice result. Returns the
      // message it added, or null.
      const renderTurnResult = function (data, httpStatus, opts) {
        const options = opts || {};
        const clientTotalMs = Math.round(performance.now() - t0);
        // The chat takes the conversation title the server stored, also when the turn then failed at the provider. A Problem
        // Details body (it has «type») is skipped: its «title» describes the error.
        if (!data.type) applyConversationTitle(turn.conversationId, data.title);
        // A reply is persisted and comes back with the history; an approval request is not, so it waits for its chat.
        if (data.requires_approval) {
          deliverApprovalOutput(turn.conversationId, function () {
            addMessage({ text: APPROVAL_OFF_TEXT, role: "error" });
          });
          return null;
        }
        // The backend persists this turn; never paint its late answer into a different conversation.
        if (currentConvId !== turn.conversationId) return null;
        if (data.error === "conversation_not_found") {
          // The chat was deleted elsewhere: it leaves this list, and the unsent text goes back to the field.
          if (!input.value) input.value = turn.text;
          window.LogosToast.error("Диалог удалён в другом окне или на другом устройстве.");
          if (typeof deleteConv === "function") deleteConv(turn.conversationId);
          return null;
        }
        if (data.error) {
          // A question the server stored is not returned to the field: «Повторить» on its error sends it once.
          let textReturned = false;
          if (!data.user_message_id) {
            restoreImages();
            // The failed text goes back to the field, unless the user already started a new draft.
            if (!input.value) {
              input.value = turn.text;
              textReturned = true;
            }
          }
          const errorNode = addMessage({
            text: data.reply || "⚠️ Ошибка: " + errorReasonText(data, httpStatus),
            role: "error",
            // A text that did not go back to the field can be put there with «Изменить»; photos would not follow it.
            editText: textReturned || sentImages.length ? undefined : turn.text,
            cost: data.cost || 0,
            timings: data.timings,
            totalDurationMs: clientTotalMs,
            reasoning: data.reasoning,
            usage: data.usage,
            trace: data.trace,
            onRetry: function () {
              return sendTurn({
                text: turn.text,
                conversationId: turn.conversationId,
                model: turn.model,
                images: sentImages,
                userMessageId: data.user_message_id || turn.userMessageId,
                imageRequestId: turn.imageRequestId,
                replaces: errorNode,
              });
            },
          });
          return errorNode;
        }
        if (data.reply) {
          // The server's duration is the one stored with the reply, so the details read the same after a reload.
          const replyNode = addMessage({
            text: data.reply,
            role: "bot",
            cost: data.cost || 0,
            timings: data.timings,
            totalDurationMs: data.total_duration_ms || clientTotalMs,
            reasoning: data.reasoning,
            usage: data.usage,
            trace: data.trace,
            id: data.message_id,
          });
          // A reply taller than the chat opens at its first line, not its last. A live reply was read as it came.
          const replyBox = document.getElementById("chatbox");
          if (!options.live && replyNode && replyBox && replyNode.offsetHeight > replyBox.clientHeight) {
            replyBox.scrollTop += replyNode.getBoundingClientRect().top - replyBox.getBoundingClientRect().top;
          }
          return replyNode;
        }
        restoreImages();
        // Neither an answer nor an error came back, and the server may have stored the question:
        // the text is not put back on its own, «Изменить» puts it into the field on request.
        return addMessage({
          text: "⚠️ Ошибка: " + chatErrorReason(data, httpStatus),
          role: "error",
          editText: sentImages.length ? undefined : turn.text,
          cost: data.cost || 0,
          timings: data.timings,
          totalDurationMs: clientTotalMs,
          reasoning: data.reasoning,
          usage: data.usage,
          trace: data.trace,
        });
      };
      // The server restarted while the model answered: the question is stored, its answer is not. «Повторить» asks the
      // stored question again (a new send with a new request id).
      const showLost = function (data) {
        if (turn.live) turn.live.freeze();
        // The lost notice is final for this request: a later history load must not read its saved request id again.
        if (turn.chatRequestId) forgetLiveTurn(turn.conversationId, turn.chatRequestId);
        const storedId = Number.isInteger(data.user_message_id) && data.user_message_id > 0
          ? data.user_message_id
          : turn.serverUserMessageId || turn.userMessageId;
        deliverApprovalOutput(turn.conversationId, function () {
          const lostNode = addMessage({
            text: data.detail || CHAT_ERROR_TEXTS.turn_lost,
            role: "error",
            onRetry: function () {
              return sendTurn({
                text: turn.text,
                conversationId: turn.conversationId,
                model: turn.model,
                images: sentImages,
                userMessageId: storedId,
                replaces: lostNode,
              });
            },
          });
        });
      };
      // The recovery waits learn a lost turn from its receipt and show it the same way.
      turn.onLost = showLost;
      let response;
      let data;
      if (isImageModel(turn.model)) {
        // «Стоп» ends the wait for the picture; the generation goes on and its result stays readable.
        const imageController = new AbortController();
        activeChatRequests.set(turn.conversationId, imageController);
        syncSendButton();
        data = await runImageTurn({ ...turn, attachmentCount: sentImages.length, signal: imageController.signal });
        response = { status: data.error ? 502 : 200, ok: !data.error };
      } else if (isVoiceModel(turn.model)) {
        try {
          if (sentImages.length) throw new Error("Голосовая модель не принимает фото");
          const voiceMethod = turn.model === "speech-realtime-tts-live" ? "synthesizeText" : "sendText";
          if (!window.LogosVoice || typeof window.LogosVoice[voiceMethod] !== "function") {
            throw new Error("Голосовой модуль ещё не загружен");
          }
          data = await window.LogosVoice[voiceMethod]({
            conversationId: turn.conversationId, model: turn.model, text: turn.text,
            instructions: turn.params.instructions,
          });
        } catch (error) {
          data = { error: voiceErrorText(error) };
        }
        response = { status: data.error ? 502 : 200, ok: !data.error };
      } else {
        // Counted before the send, so a lost connection can tell whether the server stored this turn.
        turn.baselineCount = await fetchMessageCount(turn.conversationId);
        // One id per send: the automatic re-sends of this send reuse it, so the server runs the turn once.
        // «Повторить» is a new send with a new id.
        turn.chatRequestId = imageRequestId();
        rememberLiveTurn(turn.conversationId, turn.chatRequestId);
        const chatController = new AbortController();
        activeChatRequests.set(turn.conversationId, chatController);
        // A live answer keeps streaming after its headers: the page stays busy (no automatic reload) until the turn ends.
        if (typeof window.LogosDispatcher.hold === "function") turn.hold = window.LogosDispatcher.hold();
        syncSendButton();
        const live = liveTurnSupported(turn.params);
        response = await postChatTurn(turn, JSON.stringify({
          message: turn.text,
          conversation_id: turn.conversationId,
          // Replaying the local bubble does not prove the server stored its user message.
          retry: turn.userMessageId !== null,
          retry_message_id: turn.userMessageId === null ? undefined : turn.userMessageId,
          model: turn.model,
          params: turn.params,
          attachments: sentImages.map(function (image) {
            return { data_url: image.dataUrl };
          }),
          request_id: turn.chatRequestId,
        }), live, chatController.signal);
        if (isEventStream(response)) {
          await followLiveTurn(turn, response, chatController.signal, { render: renderTurnResult, lost: showLost });
          return;
        }
        // A 413 names its cause; other non-JSON answers are recovered like a lost connection (a gateway page was
        // already sent again).
        const body = await response.json().catch(() => null);
        if (body === null && chatController.signal.aborted) {
          // «Стоп» pressed while the answer was being read is a stop, not a lost connection.
          const stoppedRead = new Error("Chat answer read stopped");
          stoppedRead.name = "AbortError";
          throw stoppedRead;
        }
        if (body !== null) {
          data = body;
        } else if (response.status === 413) {
          data = { error: "request_too_large" };
        } else {
          throw new TypeError("Ответ сервера не распознан: HTTP " + response.status);
        }
        // A repeated send finds its turn on the server: still running, already stored, or lost with a restart.
        if (response.status === 202 && data.status === "running") {
          noteStoredQuestion(turn, data);
          throw namedError("TurnRunningError", "The turn is still running on the server");
        }
        if (data.status === "stored") {
          if (currentConvId === turn.conversationId) loadHistory(turn.conversationId);
          return;
        }
        if (data.status === "lost") {
          showLost(data);
          return;
        }
      }
      renderTurnResult(data, data.httpStatus || response.status);
    } catch (error) {
      const stopped = Boolean(error) && error.name === "AbortError";
      // An accepted turn sent while offline has no count yet; recoverTurn reads it once the server is reached.
      const canRecover = !stopped && !isImageModel(turn.model) && !isVoiceModel(turn.model) &&
        (typeof turn.baselineCount === "number" || Boolean(turn.accepted));
      // The chat may have been left: its outcome is kept for it, and shown when it is opened again.
      if (stopped && turn.accepted) {
        // The server stored the question and finishes the answer: the text is not put back (a send would store it
        // twice). What already came stays in the chat until the stored answer replaces it.
        if (turn.live) turn.live.freeze();
        deliverApprovalOutput(turn.conversationId, function () {
          addMessage({ text: stopNotice(true), role: "error" });
        });
        watchStoppedTurn(turn);
      } else if (canRecover) {
        // The server may still answer: wait for the stored answer before offering the text again.
        recovering = true;
        // Nothing more streams into the live reply; it gives way to the stored answer.
        if (turn.live) turn.live.freeze();
        // The request's controller has nothing left to stop: «Остановить» now cancels the wait instead.
        const recovery = newTurnRecovery();
        activeChatRequests.set(turn.conversationId, recovery);
        syncSendButton();
        // The note names the cause: a slow answer is not a lost connection.
        const waitNote = error && error.name === "TurnRunningError"
          ? "Ответ ещё формируется на сервере. Ждём его."
          : error && error.name === "TimeoutError"
            ? "Ответ идёт дольше 5 минут. Ждём его, он сохраняется на сервере. Нажмите ■, чтобы перестать ждать."
            : "Соединение прервалось. Ждём ответ, он сохраняется на сервере.";
        deliverApprovalOutput(turn.conversationId, function () {
          addMessage({ text: waitNote, role: "error" });
        });
        recoverTurn(turn, recovery, function () {
          if (turn.live) turn.live.remove();
          deliverApprovalOutput(turn.conversationId, function () {
            // A question the server accepted is stored: it is not put back for a second send.
            if (!turn.accepted) {
              restoreImages();
              if (!input.value) input.value = turn.text;
            }
            addMessage({ text: chatErrorText(error), role: "error" });
          });
        }).finally(function () {
          if (turn.status) turn.status.remove();
          // Stopped during the wait: the same outcome as stopping the request itself.
          if (recovery.cancelled) {
            deliverApprovalOutput(turn.conversationId, function () {
              if (!turn.accepted) {
                restoreImages();
                if (!input.value) input.value = turn.text;
              }
              addMessage({ text: stopNotice(turn.accepted), role: "error" });
            });
            watchStoppedTurn(turn);
          }
          releaseTurn(turn, needsConversation);
        });
      } else if (stopped && isImageModel(turn.model)) {
        // The generation goes on after «Стоп», so the prompt is not put back for a second paid run:
        // «Повторить» reads the stored result, it never starts a new generation.
        deliverApprovalOutput(turn.conversationId, function () {
          addMessage({
            text: "Ожидание изображения остановлено. Сервер может его создать: «Повторить» покажет результат без новой генерации.",
            role: "error",
            onRetry: function () {
              return window.logosRecoverImage({ conversationId: turn.conversationId, imageRequestId: turn.imageRequestId });
            },
          });
        });
      } else {
        if (turn.live) {
          if (turn.accepted) turn.live.freeze();
          else turn.live.remove();
        }
        deliverApprovalOutput(turn.conversationId, function () {
          // A question the server accepted is stored: it is not put back for a second send.
          if (!turn.accepted) {
            restoreImages();
            if (!input.value) input.value = turn.text;
          }
          addMessage({ text: stopped ? stopNotice(false) : chatErrorText(error), role: "error" });
        });
        if (stopped) watchStoppedTurn(turn);
      }
    } finally {
      if (turn.hold) turn.hold();
      if (typingEl) typingEl.remove();
      if (!recovering && turn.status) turn.status.remove();
      if (!recovering) releaseTurn(turn, needsConversation);
    }
  }

  if (sendBtn) sendBtn.addEventListener("click", sendMessage);
  if (input) input.addEventListener("input", syncSendButton);
  if (input)
    input.addEventListener("keydown", function (e) {
      // Enter that confirms an IME word is not a send (keyCode 229 is how Android keyboards report a composing key).
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        // Enter only sends: during an answer the send is refused with a hint, and «Стоп» alone stops it.
        sendTurn(null);
      }
    });

  // Single history trigger: it does not depend on the conversation list or model catalog.
  syncSendButton();
  if (currentConvId) loadHistory(currentConvId);
});


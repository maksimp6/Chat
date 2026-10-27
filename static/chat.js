function renderApprovalCard(toolCall, origMsg) {
  const chatbox = document.getElementById("chatbox");
  if (!chatbox) return;

  const card = document.createElement("div");
  card.className = "msg bot approval-card";

  const paramsStr = JSON.stringify(toolCall.arguments, null, 2);
  const heading = document.createElement("div");
  heading.className = "approval-card-title";
  heading.textContent = "⚠️ Требуется подтверждение действия";

  const action = document.createElement("div");
  action.className = "approval-card-action";
  const actionLabel = document.createElement("strong");
  actionLabel.textContent = "Действие:";
  action.appendChild(actionLabel);
  action.appendChild(document.createTextNode(" " + (toolCall.description || toolCall.name)));

  const pre = document.createElement("pre");
  pre.className = "approval-card-params";
  const code = document.createElement("code");
  code.textContent = paramsStr;
  pre.appendChild(code);

  const actions = document.createElement("div");
  actions.className = "approval-card-actions";

  const approveBtn = document.createElement("button");
  approveBtn.type = "button";
  approveBtn.className = "logos-btn approval-btn approval-btn-approve";
  approveBtn.textContent = "✓ Разрешить";

  const rejectBtn = document.createElement("button");
  rejectBtn.type = "button";
  rejectBtn.className = "logos-btn approval-btn approval-btn-reject";
  rejectBtn.textContent = "✗ Отклонить";

  actions.appendChild(approveBtn);
  actions.appendChild(rejectBtn);
  card.appendChild(heading);
  card.appendChild(action);
  card.appendChild(pre);
  card.appendChild(actions);
  chatbox.appendChild(card);
  chatbox.scrollTop = chatbox.scrollHeight;

  approveBtn.addEventListener("click", function () {
    // The result belongs to the chat where the action was approved, not to whichever chat is open later.
    const cardConvId = currentConvId;
    card.classList.add("is-executing");
    card.replaceChildren(document.createTextNode("Выполняется..."));
    var approvalPayload = {
      conversation_id: cardConvId,
      model: currentModel,
      name: toolCall.name,
      arguments: toolCall.arguments,
      approval_token: toolCall.approval_token,
      original_message: origMsg,
    };
    if (toolCall.name === "set_ui_theme" && window.LogosTheme) {
      approvalPayload.current_theme = window.LogosTheme.getStored();
    }
    window.LogosDispatcher.request("/api/mcp/execute-approved", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(approvalPayload),
    })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        return { ok: r.ok, status: r.status, data };
      })
      .then((result) => {
        const data = result.data || {};
        card.remove();
        deliverApprovalOutput(cardConvId, function () {
          if (data.requires_approval) {
            renderApprovalCard(data.tool_call, origMsg);
          } else if (data.reply) {
            if (data.execution_result && typeof window.applyThemeAssistantResult === "function") {
              window.applyThemeAssistantResult(data.execution_result);
            }
            addMessage({
              text: data.reply,
              role: "bot",
              cost: data.cost || 0,
              timings: data.timings,
              reasoning: data.reasoning,
              usage: data.usage,
              trace: data.trace,
            });
          } else {
            addMessage({
              text: "⚠️ Ошибка выполнения: " + (data.error || `HTTP ${result.status}`),
              role: "error",
              trace: data.trace,
            });
          }
        });
      })
      .catch(() => {
        card.remove();
        deliverApprovalOutput(cardConvId, function () {
          addMessage({ text: "Сетевая ошибка при выполнении действия", role: "error" });
        });
      });
  });

  rejectBtn.addEventListener("click", function () {
    card.remove();
    addMessage({ text: `⛔ Действие "${toolCall.name}" отклонено пользователем.`, role: "bot" });
  });
}

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

// Only the outer pipes are dropped: an empty inner cell keeps its column.
function splitTableRow(line) {
  var cells = line.trim().split("|");
  if (cells.length && cells[0].trim() === "") cells.shift();
  if (cells.length && cells[cells.length - 1].trim() === "") cells.pop();
  return cells;
}

var MATH_SYMBOLS = {
  times: "×",
  cdot: "·",
  div: "÷",
  pm: "±",
  le: "≤",
  leq: "≤",
  ge: "≥",
  geq: "≥",
  neq: "≠",
  approx: "≈",
  infty: "∞",
  ldots: "…",
  cdots: "⋯",
  sin: "sin",
  cos: "cos",
  tan: "tan",
  cot: "cot",
  log: "log",
  ln: "ln",
  exp: "exp",
  lim: "lim",
  min: "min",
  max: "max",
};

// Plain-HTML approximation of common LaTeX. The input is already HTML-escaped, so the only tags here are the ones made below.
function mathToHtml(tex) {
  return tex
    .replace(/\\text\{([^{}]*)\}/g, "$1")
    .replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '<span class="md-frac"><span>$1</span><span>$2</span></span>')
    .replace(/\\sqrt\{([^{}]*)\}/g, "√($1)")
    .replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>")
    .replace(/\^(&[a-z]+;|[^{\s\\])/g, "<sup>$1</sup>")
    .replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>")
    .replace(/_(&[a-z]+;|[^{\s\\])/g, "<sub>$1</sub>")
    .replace(/\\%/g, "%")
    .replace(/\\[,;]/g, " ")
    .replace(/\\([a-zA-Z]+)/g, function (m, name) {
      return Object.prototype.hasOwnProperty.call(MATH_SYMBOLS, name) ? MATH_SYMBOLS[name] : m;
    })
    .replace(/[{}]/g, "");
}

function parseMarkdown(text) {
  if (!text) return "";
  var strText = typeof text === "string" ? text : JSON.stringify(text);
  // Drop blank lines at both ends; the indentation of the first real line is content and stays.
  strText = strText.replace(/^(?:[ \t]*\r?\n)+/, "").replace(/\s+$/, "");

  strText = strText.replace(/^\s*```(?:markdown|md|html)?\s*\n([\s\S]*)\n?```\s*$/i, "$1");

  var safe = strText.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  var codeBlocks = [];
  safe = safe.replace(/```([\w\-\+\#]*)\n?([\s\S]*?)```/g, function (m, lang, code) {
    var ph = "\x00CB" + codeBlocks.length + "\x00";
    code = code.replace(/^\n/, "").replace(/\n$/, "");
    codeBlocks.push(
      '<div class="md-code-block"><pre class="md-code"><code' +
        (lang ? ' class="language-' + lang + '"' : "") +
        ">" +
        code +
        "</code></pre></div>",
    );
    return ph;
  });

  // Inline code is taken out first, so a $ inside backticks is never read as a formula.
  var codeSpans = [];
  safe = safe.replace(/`([^`\n]+)`/g, function (m, code) {
    var ph = "\x00IC" + codeSpans.length + "\x00";
    codeSpans.push("<code>" + code + "</code>");
    return ph;
  });

  // Render stored inline PNG/JPEG and only the authenticated generated-image route.
  var images = [];
  safe = safe.replace(/!\[([^\]\n]{0,200})\]\((data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=]+|\/api\/images\/files\/[A-Za-z0-9_-]{1,128}\/[A-Za-z0-9_-]{16,128})\)/g, function (m, alt, src) {
    var ph = "\x00IM" + images.length + "\x00";
    images.push('<img class="md-image" src="' + src + '" alt="' + alt.replace(/"/g, "") + '">');
    return ph;
  });

  // Formulas are taken out before the lines are split, so a $$ block may span several lines.
  var maths = [];
  safe = safe.replace(/\$\$([\s\S]+?)\$\$|\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)/g, function (m, block, inline) {
    var ph = "\x00MT" + maths.length + "\x00";
    if (block !== undefined) {
      maths.push('<div class="md-math md-math-block">' + mathToHtml(block.trim()) + "</div>");
    } else {
      maths.push('<span class="md-math">' + mathToHtml(inline) + "</span>");
    }
    return ph;
  });

  var lines = safe.split("\n");
  var out = [];
  var listType = null;
  var inTable = false;
  var tableHtml = "";

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    var nextLine = lines[i + 1] || "";
    var isTableStart =
      /^\s*\|.*\|\s*$/.test(line) && /^\s*\|?[\s:]*-+[\s:|-]*\|?\s*$/.test(nextLine);

    if (isTableStart && !inTable) {
      if (listType) {
        out.push("</" + listType + ">");
        listType = null;
      }
      inTable = true;
      tableHtml = '<table class="md-table"><thead><tr>';
      var hdr = splitTableRow(line);
      hdr.forEach((c) => {
        tableHtml += "<th>" + formatInline(c.trim()) + "</th>";
      });
      tableHtml += "</tr></thead><tbody>";
      i++;
      continue;
    }

    if (inTable) {
      if (/^\s*\|.*\|\s*$/.test(line)) {
        var cells = splitTableRow(line);
        tableHtml += "<tr>";
        cells.forEach((c) => {
          var cellContent = c
            .trim()
            .replace(/&lt;ul&gt;/g, "<ul>")
            .replace(/&lt;\/ul&gt;/g, "</ul>")
            .replace(/&lt;li&gt;/g, "<li>")
            .replace(/&lt;\/li&gt;/g, "</li>");
          tableHtml += "<td>" + formatInline(cellContent) + "</td>";
        });
        tableHtml += "</tr>";
        continue;
      } else {
        tableHtml += "</tbody></table>";
        out.push(tableHtml);
        tableHtml = "";
        inTable = false;
      }
    }

    if (/^(-{3,}|_{3,}|\*{3,})$/.test(line.trim())) {
      if (listType) {
        out.push("</" + listType + ">");
        listType = null;
      }
      out.push("<hr>");
      continue;
    }

    var hdrMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (hdrMatch) {
      if (listType) {
        out.push("</" + listType + ">");
        listType = null;
      }
      out.push(
        "<h" +
          hdrMatch[1].length +
          ">" +
          formatInline(hdrMatch[2]) +
          "</h" +
          hdrMatch[1].length +
          ">",
      );
      continue;
    }

    var liMatch = line.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
    if (liMatch) {
      var currentListType = /^\d+\.$/.test(liMatch[1]) ? "ol" : "ul";
      if (!listType) {
        out.push("<" + currentListType + ">");
        listType = currentListType;
      } else if (listType !== currentListType) {
        out.push("</" + listType + ">");
        out.push("<" + currentListType + ">");
        listType = currentListType;
      }
      out.push("<li>" + formatInline(liMatch[2]) + "</li>");
      continue;
    }

    if (line.trim() === "") {
      var nextLiMatch = nextLine.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
      if (listType && nextLiMatch) continue;
      if (listType) {
        out.push("</" + listType + ">");
        listType = null;
      }
      out.push("<br>");
      continue;
    }

    if (listType) {
      out.push("</" + listType + ">");
      listType = null;
    }
    if (line.trim().indexOf("\x00CB") === 0) out.push(line);
    else out.push("<p>" + formatInline(line) + "</p>");
  }

  if (listType) out.push("</" + listType + ">");
  if (inTable) {
    tableHtml += "</tbody></table>";
    out.push(tableHtml);
  }

  var result = out.join("\n");
  for (var j = 0; j < codeBlocks.length; j++)
    // A function keeps "$$", "$&" and "$'" in the code literal; a string would read them as patterns.
    result = result.replace("\x00CB" + j + "\x00", function () {
      return codeBlocks[j];
    });
  for (var k = 0; k < maths.length; k++)
    result = result.replace("\x00MT" + k + "\x00", function () {
      return maths[k];
    });
  for (var n = 0; n < images.length; n++)
    result = result.replace("\x00IM" + n + "\x00", function () {
      return images[n];
    });
  for (var c = 0; c < codeSpans.length; c++)
    result = result.replace("\x00IC" + c + "\x00", function () {
      return codeSpans[c];
    });
  return result;
}

function formatInline(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\s)\*(?!\s)(.+?)(?<!\s)\*(?!\s)/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
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
    Promise.resolve()
      .then(() => navigator.clipboard.writeText(text))
      .then(
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
      if (window.LogosExecutionSurface) {
        const surface = window.LogosExecutionSurface.project(traceObj);
        if (surface) metaWrap.appendChild(window.LogosExecutionSurface.render(surface));
      }
      if (window.LogosTraceSummary && typeof window.LogosTraceSummary.build === "function") {
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
      openTraceBtn.textContent = "🔍 Открыть трассу";
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
          try {
            const res = await window.LogosDispatcher.request(
              `/api/invocations/${encodeURIComponent(traceObj.invocation_id)}/trace`,
              {},
              { timeoutMs: 60000 },
            );
            if (!res.ok) throw new Error("HTTP " + res.status);
            window.openTraceViewer(await res.json());
          } catch (err) {
            summary.textContent = "🔍 Трасса недоступна";
            window.LogosToast.error("Не удалось загрузить трассу. Попробуйте позже.");
            console.error("[CHAT] Trace load failed: " + err.message);
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

function loadHistory(convId) {
  const chatbox = document.getElementById("chatbox");
  if (!chatbox) return;
  chatbox.replaceChildren();
  return window.LogosDispatcher.request(`/api/conversations/${convId}/messages`)
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then((data) => {
      // The user may have switched conversations while this request was in flight.
      if (convId !== currentConvId) return;
      try {
        let rendered = 0;
        (data.messages || []).forEach((msg, index, all) => {
          // A stored error answers the user message right before it; «Повторить» re-sends that message.
          const previous = index > 0 ? all[index - 1] : null;
          const retryText = msg.source === "error" && previous && previous.role === "user" ? previous.text : null;
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
                imageCount: previous.attachments ? previous.attachments.images || 0 : 0,
              });
            },
          });
          if (node) rendered += 1;
          const imageRequest = msg.role === "user" && typeof msg.source === "string"
            ? msg.source.match(/^image-request:([A-Za-z0-9_-]{16,128})$/)
            : null;
          if (imageRequest && !all.some(function (candidate) { return candidate.source === "image:" + imageRequest[1]; })) {
            addMessage({
              text: "Результат изображения можно получить без новой генерации.",
              role: "error",
              onRetry: function () {
                return window.logosRecoverImage({ conversationId: convId, imageRequestId: imageRequest[1] });
              },
            });
          }
        });
        if (!rendered) {
          const empty = document.createElement("div");
          empty.className = "empty-state";
          empty.textContent = "Начните диалог";
          chatbox.replaceChildren(empty);
        }
      } catch (renderErr) {
        console.error("[CHAT] Render history error:", renderErr);
        const error = document.createElement("div");
        error.className = "empty-state error-state";
        error.textContent = "Ошибка отрисовки истории: " + renderErr.message;
        chatbox.replaceChildren(error);
      }
      flushApprovalOutputs(convId);
    })
    .catch((err) => {
      if (convId !== currentConvId) return;
      console.error("[CHAT] Fetch history error:", err);
      const error = document.createElement("div");
      error.className = "empty-state error-state";
      const text = document.createElement("p");
      text.textContent = "Не удалось загрузить диалог. Проверьте соединение.";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "logos-btn";
      retry.textContent = "Повторить";
      retry.addEventListener("click", function () {
        loadHistory(convId);
      });
      error.append(text, retry);
      chatbox.replaceChildren(error);
    });
}

document.addEventListener("DOMContentLoaded", function () {
  const sendBtn = document.getElementById("send-btn");
  const input = document.getElementById("msg-input");
  const pendingConversations = new Set();
  // The chat request in flight for each conversation; «Стоп» aborts it from the UI.
  const activeChatRequests = new Map();
  let creatingConversation = false;

  // A photo error names its cause; other server codes are shown as they are.
  function errorReasonText(data) {
    if (data.error === "invalid_attachments") return "Фото не приняты: " + (data.message || "проверьте файлы");
    if (data.error === "model_does_not_accept_images") {
      return "Эта модель не принимает фото. Выберите модель с поддержкой фото.";
    }
    return data.error;
  }

  // Shows the reason in Russian. The browser's own message (for example «The operation was aborted») stays out of the chat.
  function chatErrorText(error) {
    if (error && error.name === "AbortError") {
      return "Ответ остановлен. Сервер мог продолжить работу, проверьте диалог позже.";
    }
    if (error && error.name === "TimeoutError") {
      return "Ответ не пришёл за 5 минут. Повторите запрос.";
    }
    return "Не удалось получить ответ. Проверьте соединение и повторите.";
  }

  // The send button becomes «Stop» while the chat in the open conversation is still answering.
  function syncSendButton() {
    if (!sendBtn) return;
    const running = Boolean(currentConvId && activeChatRequests.has(currentConvId));
    const pending = currentConvId ? pendingConversations.has(currentConvId) : creatingConversation;
    sendBtn.textContent = running ? "■" : "➤";
    sendBtn.setAttribute("aria-label", running ? "Остановить ответ" : "Отправить");
    sendBtn.disabled = !running && (
      pending || imagesInFlight > 0 || !input || (!input.value.trim() && !pendingImages.length)
    );
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
        imagesInFlight += 1;
        syncSendButton();
        try {
          pendingImages.push({ name: file.name, dataUrl: await readAsDataUrl(file) });
          renderPendingImages();
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

  // Recovery reads our durable request receipt, never repeats the paid provider POST.
  async function readImageResult(turn) {
    const path = "/api/images/requests/" + encodeURIComponent(turn.imageRequestId) +
      "?conversation_id=" + encodeURIComponent(turn.conversationId);
    const deadline = Date.now() + 240000;
    while (Date.now() < deadline) {
      let response;
      try {
        response = await window.LogosDispatcher.request(path, {}, { timeoutMs: 15000 });
      } catch (error) {
        return { error: "Соединение прервалось. Повторите получение результата.", imageRequestId: turn.imageRequestId };
      }
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.status === "done" && data.reply) return data;
      if (response.status !== 202 || data.status !== "pending") {
        return { error: data.message || data.error || "HTTP " + response.status, imageRequestId: turn.imageRequestId };
      }
      await new Promise(function (resolve) { setTimeout(resolve, 2000); });
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
        text: "⚠️ Ошибка: " + data.error,
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: turn.conversationId,
          request_id: turn.imageRequestId,
          message: turn.text,
          model: turn.model,
        }),
      }, { timeoutMs: 300000 });
    } catch (error) {
      return readImageResult(turn);
    }
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.status === "done" && data.reply) return data;
    if (response.status === 202 && data.status === "pending") return readImageResult(turn);
    return { error: data.message || data.error || "HTTP " + response.status, imageRequestId: turn.imageRequestId };
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

  function releaseTurn(turn, needsConversation) {
    if (turn.conversationId) pendingConversations.delete(turn.conversationId);
    if (turn.conversationId) activeChatRequests.delete(turn.conversationId);
    if (needsConversation) creatingConversation = false;
    syncSendButton();
  }

  async function recoverTurn(turn, onMissing) {
    const started = Date.now();
    let missing = 0;
    while (Date.now() - started < TURN_RECOVERY_MS) {
      await new Promise(function (resolve) { setTimeout(resolve, TURN_RECOVERY_POLL_MS); });
      let status = null;
      try {
        const response = await window.LogosDispatcher.request("/api/conversations/" + turn.conversationId + "/messages", undefined, { timeoutMs: 15000 });
        if (!response.ok) continue;
        const data = await response.json();
        status = turnStatus(Array.isArray(data.messages) ? data.messages : [], turn.baselineCount);
      } catch (error) {
        // Still offline: keep waiting until the window closes.
        continue;
      }
      if (status === "answered") {
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
    if (currentConvId === turn.conversationId) {
      addMessage({ text: "Ответ ещё не пришёл. Сервер сохранит его: откройте диалог позже.", role: "error" });
    }
  }

  // A live retry keeps its photos; a history retry only knows their count and asks for them again.
  async function sendTurn(retryRequest) {
    if (!input) return;
    const rawText = retryRequest ? "" : input.value;
    const text = retryRequest ? retryRequest.text : rawText.trim();
    if (retryRequest && currentConvId !== retryRequest.conversationId) return;
    if (imagesInFlight > 0) return;
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
    const selectedAtSubmit = currentConvId;
    if (selectedAtSubmit ? pendingConversations.has(selectedAtSubmit) : creatingConversation) {
      window.LogosToast.show("Ответ ещё формируется, дождитесь его окончания.");
      return;
    }

    const turn = {
      conversationId: selectedAtSubmit,
      model: retryRequest && retryRequest.model ? retryRequest.model : currentModel,
      text: text,
      imageRequestId: retryRequest && retryRequest.imageRequestId ? retryRequest.imageRequestId : imageRequestId(),
    };
    if (isImageModel(turn.model) && Array.from(text).length > 500) {
      window.LogosToast.error("Для Alice AI ART запрос должен содержать не больше 500 символов");
      return;
    }
    const needsConversation = !selectedAtSubmit;
    if (needsConversation) creatingConversation = true;
    else pendingConversations.add(turn.conversationId);
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
      const params = typeof window.getResponsesParams === "function" ? window.getResponsesParams() : {};
      turn.params = JSON.parse(JSON.stringify(params || {}));
      if (needsConversation) {
        const createdId = await createConversation(turn.model, { select: false });
        if (!createdId) {
          if (currentConvId === turn.conversationId) restoreImages();
          return;
        }
        turn.conversationId = createdId;
        pendingConversations.add(createdId);
        // Selecting another chat while creation is pending must not pull the user back.
        if (currentConvId === selectedAtSubmit) await selectConv(createdId);
      }

      if (currentConvId === turn.conversationId) {
        // A retry already has its user message in the chat, so it is not painted twice.
        if (!retryRequest) {
          if (input.value === rawText) input.value = "";
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
      }
      const t0 = performance.now();
      let response;
      let data;
      if (isImageModel(turn.model)) {
        data = await runImageTurn({ ...turn, attachmentCount: sentImages.length });
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
          data = { error: error.message || "Не удалось получить голосовой ответ" };
        }
        response = { status: data.error ? 502 : 200, ok: !data.error };
      } else {
        // Counted before the send, so a lost connection can tell whether the server stored this turn.
        turn.baselineCount = await fetchMessageCount(turn.conversationId);
        const chatController = new AbortController();
        activeChatRequests.set(turn.conversationId, chatController);
        syncSendButton();
        response = await window.LogosDispatcher.request("/api/chat", {
          method: "POST",
          signal: chatController.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: turn.text,
            conversation_id: turn.conversationId,
            retry: Boolean(retryRequest),
            model: turn.model,
            params: turn.params,
            attachments: sentImages.map(function (image) {
              return { data_url: image.dataUrl };
            }),
          }),
        }, { timeoutMs: 300000 });
        data = await response.json().catch(() => ({}));
      }
      const clientTotalMs = Math.round(performance.now() - t0);
      if (data.title && typeof conversations !== "undefined" && Array.isArray(conversations)) {
        const conversation = conversations.find((item) => item.id === turn.conversationId);
        if (conversation) {
          conversation.title = data.title;
          localStorage.setItem("conversations", JSON.stringify(conversations));
          if (typeof renderSidebar === "function") renderSidebar();
        }
      }
      // A reply is persisted and comes back with the history; an approval request is not, so it waits for its chat.
      if (data.requires_approval) {
        deliverApprovalOutput(turn.conversationId, function () {
          renderApprovalCard(data.tool_call, data.original_message || turn.text);
        });
        return;
      }
      // The backend persists this turn; never paint its late answer into a different conversation.
      if (currentConvId !== turn.conversationId) return;
      if (data.error) {
        restoreImages();
        // The failed text goes back to the field, unless the user already started a new draft.
        if (!input.value) input.value = turn.text;
        addMessage({
          text: data.reply || "⚠️ Ошибка: " + errorReasonText(data),
          role: "error",
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
              imageRequestId: turn.imageRequestId,
            });
          },
        });
      } else if (data.reply) {
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
        // A reply taller than the chat opens at its first line, not its last.
        const replyBox = document.getElementById("chatbox");
        if (replyNode && replyBox && replyNode.offsetHeight > replyBox.clientHeight) {
          replyBox.scrollTop += replyNode.getBoundingClientRect().top - replyBox.getBoundingClientRect().top;
        }
      } else {
        restoreImages();
        addMessage({
          text: "⚠️ Ошибка: " + (data.detail || data.title || "HTTP " + response.status),
          role: "error",
          cost: data.cost || 0,
          timings: data.timings,
          totalDurationMs: clientTotalMs,
          reasoning: data.reasoning,
          usage: data.usage,
          trace: data.trace,
        });
      }
    } catch (error) {
      const stopped = Boolean(error) && error.name === "AbortError";
      const canRecover = !stopped && !isImageModel(turn.model) && !isVoiceModel(turn.model) && typeof turn.baselineCount === "number";
      if (canRecover && currentConvId === turn.conversationId) {
        // The server may still answer: wait for the stored answer before offering the text again.
        recovering = true;
        addMessage({ text: "Соединение прервалось. Ждём ответ, он сохраняется на сервере.", role: "error" });
        recoverTurn(turn, function () {
          if (currentConvId !== turn.conversationId) return;
          restoreImages();
          if (!input.value) input.value = turn.text;
          addMessage({ text: chatErrorText(error), role: "error" });
        }).finally(function () {
          releaseTurn(turn, needsConversation);
        });
      } else if (currentConvId === turn.conversationId) {
        restoreImages();
        if (!input.value) input.value = turn.text;
        addMessage({ text: chatErrorText(error), role: "error" });
      }
    } finally {
      if (typingEl) typingEl.remove();
      if (!recovering) releaseTurn(turn, needsConversation);
    }
  }

  if (sendBtn) sendBtn.addEventListener("click", sendMessage);
  if (input) input.addEventListener("input", syncSendButton);
  if (input)
    input.addEventListener("keydown", function (e) {
      // Enter that confirms an IME word is not a send.
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        sendMessage();
      }
    });

  // Single history trigger: it does not depend on the conversation list or model catalog.
  syncSendButton();
  if (currentConvId) loadHistory(currentConvId);
});

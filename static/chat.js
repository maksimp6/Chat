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
  approveBtn.className = "alice-btn approval-btn approval-btn-approve";
  approveBtn.textContent = "✓ Разрешить";

  const rejectBtn = document.createElement("button");
  rejectBtn.type = "button";
  rejectBtn.className = "alice-btn approval-btn approval-btn-reject";
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
    card.classList.add("is-executing");
    card.replaceChildren(document.createTextNode("Выполняется..."));
    var approvalPayload = {
      conversation_id: currentConvId,
      model: currentModel,
      name: toolCall.name,
      arguments: toolCall.arguments,
      original_message: origMsg,
    };
    if (toolCall.name === "set_ui_theme" && window.AliceTheme) {
      approvalPayload.current_theme = window.AliceTheme.getStored();
    }
    window.AliceDispatcher.request("/api/mcp/execute-approved", {
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
        if (data.requires_approval) {
          renderApprovalCard(data.tool_call, origMsg);
        } else if (data.reply) {
          if (data.execution_result && typeof window.applyThemeAssistantResult === "function") {
            window.applyThemeAssistantResult(data.execution_result);
          }
          addMessage(
            data.reply,
            "bot",
            false,
            data.cost || 0,
            data.timings,
            null,
            data.reasoning,
            data.usage,
            data.trace,
          );
        } else {
          addMessage(
            "⚠️ Ошибка выполнения: " + (data.error || `HTTP ${result.status}`),
            "bot",
            false,
            0,
            null,
            null,
            null,
            null,
            data.trace,
          );
        }
      })
      .catch(() => {
        card.remove();
        addMessage("Сетевая ошибка при выполнении действия", "bot", false, 0);
      });
  });

  rejectBtn.addEventListener("click", function () {
    card.remove();
    addMessage(`⛔ Действие "${toolCall.name}" отклонено пользователем.`, "bot", true, 0);
  });
}

function parseMarkdown(text) {
  if (!text) return "";
  var strText = typeof text === "string" ? text : JSON.stringify(text);

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
      var hdr = line.split("|").filter((c) => c.trim() !== "");
      hdr.forEach((c) => {
        tableHtml += "<th>" + formatInline(c.trim()) + "</th>";
      });
      tableHtml += "</tr></thead><tbody>";
      i++;
      continue;
    }

    if (inTable) {
      if (/^\s*\|.*\|\s*$/.test(line)) {
        var cells = line.split("|").filter((c) => c.trim() !== "");
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
    result = result.replace("\x00CB" + j + "\x00", codeBlocks[j]);
  return result;
}

function formatInline(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\s)\*(?!\s)(.+?)(?<!\s)\*(?!\s)/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function addMessage(text, role, save, cost, timings, totalDurationMs, reasoning, usage, trace) {
  const chatbox = document.getElementById("chatbox");
  if (!chatbox) return;
  const emptyState = chatbox.querySelector(".empty-state");
  if (emptyState) emptyState.remove();

  const msg = document.createElement("div");
  msg.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  let htmlContent = "";
  if (reasoning && reasoning.trim() !== "") {
    htmlContent += `<details class="reasoning-details"><summary class="reasoning-summary">🧠 Ход мыслей модели (Reasoning)</summary><div class="reasoning-content">${parseMarkdown(reasoning)}</div></details>`;
  }
  htmlContent += parseMarkdown(text);
  bubble.innerHTML = htmlContent;
  msg.appendChild(bubble);

  const copyBtn = document.createElement("button");
  copyBtn.className = "alice-btn copy-btn";
  copyBtn.textContent = "📋";
  copyBtn.title = "Копировать";

  copyBtn.addEventListener("click", function () {
    navigator.clipboard.writeText(text).then(() => {
      copyBtn.textContent = "✅";
      window.AliceCoreAPI.scheduler.defer(() => (copyBtn.textContent = "📋"), 2000);
    });
  });
  msg.appendChild(copyBtn);

  const metaWrap = document.createElement("div");
  metaWrap.className = "msg-meta";

  if (usage && (usage.input_tokens || usage.total_tokens)) {
    const usageBadge = document.createElement("details");
    usageBadge.className = "msg-meta-details";
    const usageSummary = document.createElement("summary");
    usageSummary.className = "msg-meta-summary";
    usageSummary.textContent = `📊 Токены: ${usage.total_tokens || 0} (вход: ${usage.input_tokens}, выход: ${usage.output_tokens})`;
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

  if (cost && cost > 0) {
    const billing = document.createElement("div");
    billing.className = "billing-bubble";
    billing.textContent = `≈ ${cost.toFixed(2)} ₽`;
    metaWrap.appendChild(billing);
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
      const traceEl = document.createElement("details");
      traceEl.className = "msg-meta-details msg-trace-details";
      const summary = document.createElement("summary");
      summary.className = "msg-meta-summary";
      const traceId = traceObj.trace_id ? String(traceObj.trace_id).substring(0, 8) : "local";
      const eventsCount = Array.isArray(traceObj.events) ? traceObj.events.length : 0;
      summary.textContent = `🔍 Trace [${traceId}...] (${eventsCount} соб.)`;
      traceEl.appendChild(summary);

      // The Trace Viewer is a first-class chat action. Do not rely only on
      // trace_viewer_auto.js parsing the generated HTML after the fact.
      traceEl.dataset.traceViewerDirect = "1";
      const openTraceBtn = document.createElement("button");
      openTraceBtn.type = "button";
      openTraceBtn.className = "alice-btn alice-trace-open-viewer";
      openTraceBtn.textContent = "🔍 Открыть Trace Viewer";
      openTraceBtn.title = "Открыть Execution Trace viewer";

      openTraceBtn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (typeof window.openTraceViewer === "function") {
          window.openTraceViewer(traceObj);
        } else {
          console.error("[CHAT] Execution Trace Viewer is not loaded");
        }
      });
      metaWrap.appendChild(traceEl);
      metaWrap.appendChild(openTraceBtn);
    }
  }

  if (metaWrap.children.length > 0) msg.appendChild(metaWrap);
  chatbox.appendChild(msg);
  chatbox.scrollTop = chatbox.scrollHeight;
}

function loadHistory(convId) {
  const chatbox = document.getElementById("chatbox");
  if (!chatbox) return;
  chatbox.replaceChildren();
  window.AliceDispatcher.request(`/api/conversations/${convId}/messages`)
    .then((r) => r.json())
    .then((data) => {
      try {
        if (data.messages && data.messages.length) {
          data.messages.forEach((msg) => {
            const role = msg.role === "assistant" ? "bot" : "user";
            addMessage(
              msg.text,
              role,
              false,
              msg.cost || 0,
              msg.timings,
              0,
              msg.reasoning,
              msg.usage,
              msg.trace,
            );
          });
        } else {
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
    })
    .catch((err) => {
      console.error("[CHAT] Fetch history error:", err);
      const error = document.createElement("div");
      error.className = "empty-state error-state";
      error.textContent = "Ошибка сети: " + err.message;
      chatbox.replaceChildren(error);
    });
}

document.addEventListener("DOMContentLoaded", function () {
  const sendBtn = document.getElementById("send-btn");
  const input = document.getElementById("msg-input");

  function sendMessage() {
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    if (!currentConvId) {
      if (typeof renderModelModal === "function") renderModelModal();
      const modal = document.getElementById("model-modal");
      if (modal) modal.classList.add("visible");
      return;
    }

    input.value = "";
    addMessage(text, "user", false, 0);
    const params =
      typeof window.getResponsesParams === "function" ? window.getResponsesParams() : {};
    const t0 = performance.now();

    window.AliceDispatcher.request("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        conversation_id: currentConvId,
        model: currentModel,
        params: params,
      }),
    })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        return { ok: r.ok, status: r.status, data };
      })
      .then((result) => {
        const data = result.data || {};
        const clientTotalMs = Math.round(performance.now() - t0);
        if (data.title && Array.isArray(conversations)) {
          var conversation = conversations.find(function (item) {
            return item.id === currentConvId;
          });
          if (conversation) {
            conversation.title = data.title;
            localStorage.setItem("conversations", JSON.stringify(conversations));
            if (typeof renderSidebar === "function") renderSidebar();
          }
        }
        if (data.reply) {
          addMessage(
            data.reply,
            "bot",
            false,
            data.cost || 0,
            data.timings,
            clientTotalMs,
            data.reasoning,
            data.usage,
            data.trace,
          );
        } else if (data.error) {
          addMessage(
            "⚠️ Ошибка: " + data.error,
            "bot",
            false,
            data.cost || 0,
            data.timings,
            clientTotalMs,
            data.reasoning,
            data.usage,
            data.trace,
          );
        } else {
          addMessage(
            `⚠️ Ошибка HTTP ${result.status}`,
            "bot",
            false,
            0,
            null,
            clientTotalMs,
            null,
            null,
            data.trace,
          );
        }
      })
      .catch((e) => {
        addMessage("⚠️ Ошибка: " + (e.message || "Сетевой сбой"), "bot", false, 0);
      });
  }

  if (sendBtn) sendBtn.addEventListener("click", sendMessage);
  if (input)
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
});

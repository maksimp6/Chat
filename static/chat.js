function renderApprovalCard(toolCall, origMsg) {
    const chatbox = document.getElementById("chatbox");
    if (!chatbox) return;

    const card = document.createElement("div");
    card.className = "msg bot";
    card.style.cssText = "border: 1px solid var(--accent); background: rgba(0, 122, 255, 0.05); border-radius: 12px; padding: 14px;";

    const paramsStr = JSON.stringify(toolCall.arguments, null, 2);
    card.innerHTML = `
        <div style="font-weight: 600; margin-bottom: 6px; color: var(--accent);">⚠️ Требуется подтверждение действия</div>
        <div style="font-size: 14px; margin-bottom: 8px;"><strong>Действие:</strong> ${toolCall.description || toolCall.name}</div>
        <pre style="font-size: 12px; background: rgba(0,0,0,0.05); padding: 8px; border-radius: 6px; margin-bottom: 10px;"><code>${paramsStr}</code></pre>
        <div style="display: flex; gap: 10px;">
            <button id="btn-approve-${toolCall.call_id}" style="padding: 6px 14px; background: #28a745; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-weight: 500;">✓ Разрешить</button>
            <button id="btn-reject-${toolCall.call_id}" style="padding: 6px 14px; background: #dc3545; color: #fff; border: none; border-radius: 6px; cursor: pointer;">✗ Отклонить</button>
        </div>
    `;

    chatbox.appendChild(card);
    chatbox.scrollTop = chatbox.scrollHeight;

    document.getElementById(`btn-approve-${toolCall.call_id}`).onclick = function() {
        card.innerHTML = "<em>Выполняется...</em>";
        fetch("/api/mcp/execute-approved", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                conversation_id: currentConvId,
                model: currentModel,
                name: toolCall.name,
                arguments: toolCall.arguments,
                original_message: origMsg
            })
        })
        .then(async r => {
            const data = await r.json().catch(() => ({}));
            return {ok: r.ok, status: r.status, data};
        })
        .then(result => {
            const data = result.data || {};
            card.remove();
            if (data.requires_approval) {
                renderApprovalCard(data.tool_call, origMsg);
            } else if (data.reply) {
                addMessage(data.reply, "bot", false, data.cost || 0, data.timings, null, data.reasoning, data.usage, data.trace);
            } else {
                addMessage("⚠️ Ошибка выполнения: " + (data.error || `HTTP ${result.status}`), "bot", false, 0, null, null, null, null, data.trace);
            }
        })
        .catch(() => {
            card.remove();
            addMessage("Сетевая ошибка при выполнении действия", "bot", false, 0);
        });
    };

    document.getElementById(`btn-reject-${toolCall.call_id}`).onclick = function() {
        card.remove();
        addMessage(`⛔ Действие "${toolCall.name}" отклонено пользователем.`, "bot", true, 0);
    };
}

function parseMarkdown(text) {
    if (!text) return "";
    var strText = typeof text === "string" ? text : JSON.stringify(text);

    strText = strText.replace(/^\s*```(?:markdown|md|html)?\s*\n([\s\S]*)\n?```\s*$/i, "$1");

    var safe = strText
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

    var codeBlocks = [];
    safe = safe.replace(/```([\w\-\+\#]*)\n?([\s\S]*?)```/g, function(m, lang, code) {
        var ph = "\x00CB" + codeBlocks.length + "\x00";
        code = code.replace(/^\n/, '').replace(/\n$/, '');
        codeBlocks.push('<div style="margin:8px 0;"><pre style="margin:0;"><code' + (lang ? ' class="language-' + lang + '"' : '') + '>' + code + '</code></pre></div>');
        return ph;
    });

    var lines = safe.split('\n');
    var out = [];
    var listType = null;
    var inTable = false;
    var tableHtml = '';

    for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var nextLine = lines[i + 1] || '';
        var isTableStart = /^\s*\|.*\|\s*$/.test(line) && /^\s*\|?[\s:]*-+[\s:|-]*\|?\s*$/.test(nextLine);

        if (isTableStart && !inTable) {
            if (listType) { out.push('</' + listType + '>'); listType = null; }
            inTable = true;
            tableHtml = '<table class="md-table"><thead><tr>';
            var hdr = line.split('|').filter(c => c.trim() !== '');
            hdr.forEach(c => { tableHtml += '<th>' + formatInline(c.trim()) + '</th>'; });
            tableHtml += '</tr></thead><tbody>';
            i++;
            continue;
        }

        if (inTable) {
            if (/^\s*\|.*\|\s*$/.test(line)) {
                var cells = line.split('|').filter(c => c.trim() !== '');
                tableHtml += '<tr>';
                cells.forEach(c => {
                    var cellContent = c.trim().replace(/&lt;ul&gt;/g, "<ul>").replace(/&lt;\/ul&gt;/g, "</ul>").replace(/&lt;li&gt;/g, "<li>").replace(/&lt;\/li&gt;/g, "</li>");
                    tableHtml += "<td>" + formatInline(cellContent) + "</td>";
                });
                tableHtml += '</tr>';
                continue;
            } else {
                tableHtml += '</tbody></table>';
                out.push(tableHtml);
                tableHtml = '';
                inTable = false;
            }
        }

        if (/^(-{3,}|_{3,}|\*{3,})$/.test(line.trim())) {
            if (listType) { out.push('</' + listType + '>'); listType = null; }
            out.push('<hr>');
            continue;
        }

        var hdrMatch = line.match(/^(#{1,6})\s+(.*)$/);
        if (hdrMatch) {
            if (listType) { out.push('</' + listType + '>'); listType = null; }
            out.push('<h' + hdrMatch[1].length + '>' + formatInline(hdrMatch[2]) + '</h' + hdrMatch[1].length + '>');
            continue;
        }

        var liMatch = line.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
        if (liMatch) {
            var currentListType = /^\d+\.$/.test(liMatch[1]) ? 'ol' : 'ul';
            if (!listType) {
                out.push('<' + currentListType + '>');
                listType = currentListType;
            } else if (listType !== currentListType) {
                out.push('</' + listType + '>');
                out.push('<' + currentListType + '>');
                listType = currentListType;
            }
            out.push('<li>' + formatInline(liMatch[2]) + '</li>');
            continue;
        }

        if (line.trim() === '') {
            var nextLiMatch = nextLine.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
            if (listType && nextLiMatch) continue;
            if (listType) { out.push('</' + listType + '>'); listType = null; }
            out.push('<br>');
            continue;
        }

        if (listType) { out.push('</' + listType + '>'); listType = null; }
        if (line.trim().indexOf('\x00CB') === 0) out.push(line);
        else out.push('<p>' + formatInline(line) + '</p>');
    }

    if (listType) out.push('</' + listType + '>');
    if (inTable) { tableHtml += '</tbody></table>'; out.push(tableHtml); }

    var result = out.join('\n');
    for (var j = 0; j < codeBlocks.length; j++) result = result.replace("\x00CB" + j + "\x00", codeBlocks[j]);
    return result;
}

function formatInline(text) {
    return text
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/(?<!\s)\*(?!\s)(.+?)(?<!\s)\*(?!\s)/g, '<em>$1</em>')
        .replace(/`(.+?)`/g, '<code>$1</code>');
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
        htmlContent += `<details style="margin-bottom:10px;font-size:13px;background:rgba(0,0,0,0.05);border-radius:8px;padding:8px 12px;border:1px dashed var(--border-color);"><summary style="cursor:pointer;font-weight:600;color:var(--accent);">🧠 Ход мыслей модели (Reasoning)</summary><div style="margin-top:8px;white-space:pre-wrap;font-family:monospace;font-size:12px;color:var(--text-main);opacity:0.85;">${parseMarkdown(reasoning)}</div></details>`;
    }
    htmlContent += parseMarkdown(text);
    bubble.innerHTML = htmlContent;
    msg.appendChild(bubble);

    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.textContent = "📋";
    copyBtn.title = "Копировать";
    copyBtn.style.cssText = "margin-left:10px;cursor:pointer;background:none;border:none;";
    copyBtn.onclick = function() {
        navigator.clipboard.writeText(text).then(() => {
            copyBtn.textContent = "✅";
            setTimeout(() => copyBtn.textContent = "📋", 2000);
        });
    };
    msg.appendChild(copyBtn);

    const metaWrap = document.createElement("div");
    metaWrap.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;align-items:center;";

    if (usage && (usage.input_tokens || usage.total_tokens)) {
        const usageBadge = document.createElement("details");
        usageBadge.style.cssText = "font-size:11px;background:rgba(0,0,0,0.04);border-radius:6px;padding:3px 8px;cursor:pointer;color:var(--text-secondary);";
        let detailsHtml = `<summary style="font-weight:500;">📊 Токены: <strong>${usage.total_tokens || 0}</strong> (вход: ${usage.input_tokens}, выход: ${usage.output_tokens})</summary>` +
                          `<div style="margin-top:4px;display:flex;flex-direction:column;gap:2px;font-family:monospace;font-size:11px;">` +
                          `<div>💾 Кэшированные: <strong>${usage.cached_tokens || 0}</strong></div>` +
                          `<div>🧰 Инструменты: <strong>${usage.tool_tokens || 0}</strong></div>` +
                          `<div>🧠 Рассуждения: <strong>${usage.reasoning_tokens || 0}</strong></div>`;
        if (usage.incomplete_details) detailsHtml += `<div style="color:var(--danger);">⚠️ Прерывание: ${JSON.stringify(usage.incomplete_details)}</div>`;
        detailsHtml += `</div>`;
        usageBadge.innerHTML = detailsHtml;
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
        badge.style.cssText = "font-size:11px;background:rgba(0,0,0,0.06);border-radius:6px;padding:3px 8px;cursor:pointer;color:var(--text-secondary);";
        let stepsHtml = `<summary style="font-weight:600;">⏱️ Выполнение: <strong>${total || (timings.length + ' этапов')}</strong></summary><div style="margin-top:4px;display:flex;flex-direction:column;gap:2px;">`;
        timings.forEach((t, idx) => {
            const dur = t.duration_source === "unavailable" ? "—" : `${t.duration_ms} мс`;
            let srv = t.server_label ? `<span style="color:var(--accent);font-size:10px;margin-left:4px;">${t.server_label}</span>` : "";
            stepsHtml += `<div style="display:flex;justify-content:space-between;gap:12px;"><span>${idx + 1}. ${t.name}${srv}</span><strong>${dur}</strong></div>`;
        });
        stepsHtml += "</div>";
        badge.innerHTML = stepsHtml;
        metaWrap.appendChild(badge);
    }

    if (trace) {
        let traceObj = trace;
        if (typeof trace === "string") {
            try { traceObj = JSON.parse(trace); } catch (_) { traceObj = null; }
        }
        if (traceObj && typeof traceObj === "object" && Object.keys(traceObj).length > 0) {
            const traceEl = document.createElement("details");
            traceEl.style.cssText = "font-size:11px;background:rgba(0,0,0,0.06);border-radius:6px;padding:3px 8px;cursor:pointer;color:var(--text-secondary);width:100%;margin-top:4px;";
            const summary = document.createElement("summary");
            summary.style.fontWeight = "600";
            const traceId = traceObj.trace_id ? String(traceObj.trace_id).substring(0, 8) : "local";
            const eventsCount = Array.isArray(traceObj.events) ? traceObj.events.length : 0;
            summary.textContent = `🔍 Trace [${traceId}...] (${eventsCount} соб.)`;
            traceEl.appendChild(summary);
            const pre = document.createElement("pre");
            pre.style.cssText = "margin-top:6px;max-height:250px;overflow-y:auto;background:rgba(0,0,0,0.03);padding:6px;border-radius:4px;font-family:monospace;font-size:10px;white-space:pre-wrap;text-align:left;";
            pre.textContent = JSON.stringify(traceObj, null, 2);
            traceEl.appendChild(pre);
            metaWrap.appendChild(traceEl);
        }
    }

    if (metaWrap.children.length > 0) msg.appendChild(metaWrap);
    chatbox.appendChild(msg);
    chatbox.scrollTop = chatbox.scrollHeight;
}

function loadHistory(convId) {
    const chatbox = document.getElementById("chatbox");
    if (!chatbox) return;
    chatbox.innerHTML = "";
    fetch(`/api/conversations/${convId}/messages`)
        .then(r => r.json())
        .then(data => {
            try {
                if (data.messages && data.messages.length) {
                    data.messages.forEach(msg => {
                        const role = msg.role === "assistant" ? "bot" : "user";
                        addMessage(msg.text, role, false, msg.cost || 0, msg.timings, 0, msg.reasoning, msg.usage, msg.trace);
                    });
                } else {
                    chatbox.innerHTML = '<div class="empty-state">Начните диалог</div>';
                }
            } catch (renderErr) {
                console.error("[CHAT] Render history error:", renderErr);
                chatbox.innerHTML = `<div class="empty-state">Ошибка отрисовки истории: ${renderErr.message}</div>`;
            }
        })
        .catch(err => {
            console.error("[CHAT] Fetch history error:", err);
            chatbox.innerHTML = `<div class="empty-state">Ошибка сети: ${err.message}</div>`;
        });
}

document.addEventListener("DOMContentLoaded", function() {
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
        const params = (typeof window.getResponsesParams === "function") ? window.getResponsesParams() : {};
        const t0 = performance.now();

        fetch("/api/chat", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({message: text, conversation_id: currentConvId, model: currentModel, params: params})
        })
        .then(async r => {
            const data = await r.json().catch(() => ({}));
            return {ok: r.ok, status: r.status, data};
        })
        .then(result => {
            const data = result.data || {};
            const clientTotalMs = Math.round(performance.now() - t0);
            if (data.reply) {
                addMessage(data.reply, "bot", false, data.cost || 0, data.timings, clientTotalMs, data.reasoning, data.usage, data.trace);
            } else if (data.error) {
                addMessage("⚠️ Ошибка: " + data.error, "bot", false, data.cost || 0, data.timings, clientTotalMs, data.reasoning, data.usage, data.trace);
            } else {
                addMessage(`⚠️ Ошибка HTTP ${result.status}`, "bot", false, 0, null, clientTotalMs, null, null, data.trace);
            }
        })
        .catch(e => {
            addMessage("⚠️ Ошибка: " + (e.message || "Сетевой сбой"), "bot", false, 0);
        });
    }

    if (sendBtn) sendBtn.addEventListener("click", sendMessage);
    if (input) input.addEventListener("keydown", function(e) {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
});

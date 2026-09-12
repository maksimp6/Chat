// Простой и безопасный парсер Markdown
function parseMarkdown(text) {
    if (!text) return "";

    // 1. Экранируем HTML во всём тексте (защита от XSS)
    var safe = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

    // 2. Извлекаем блоки кода (уже экранированные, повторно не нужно)
    var codeBlocks = [];
    safe = safe.replace(/```(\w*)\n?([\s\S]*?)```/g, function(m, lang, code) {
        var ph = "\x00CB" + codeBlocks.length + "\x00";
        // Убираем лишний перенос строки в начале блока, если он есть
        code = code.replace(/^\n/, ''); 
        codeBlocks.push('<pre><code' + (lang ? ' class="language-' + lang + '"' : '') + '>' + code + '</code></pre>');
        return ph;
    });

    // 3. Построчная обработка блочных элементов
    var lines = safe.split('\n');
    var out = [];
    var inList = false;
    var inTable = false;
    var tableHtml = '';

    for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var nextLine = lines[i + 1] || '';

        // --- Таблицы ---
        // Проверяем, является ли текущая строка началом таблицы (есть | и следующая строка - разделитель)
        var isTableStart = /^\s*\|.*\|\s*$/.test(line) && /^\s*\|?[\s:]*-+[\s:|-]*\|?\s*$/.test(nextLine);

        if (isTableStart && !inTable) {
            if (inList) { out.push('</ul>'); inList = false; }
            inTable = true;
            tableHtml = '<table class="md-table"><thead><tr>';
            var hdr = line.split('|').filter(function(c) { return c.trim() !== ''; });
            hdr.forEach(function(c) { tableHtml += '<th>' + formatInline(c.trim()) + '</th>'; });
            tableHtml += '</tr></thead><tbody>';
            i++; // пропускаем строку-разделитель
            continue;
        }

        if (inTable) {
            if (/^\s*\|.*\|\s*$/.test(line)) {
                var cells = line.split('|').filter(function(c) { return c.trim() !== ''; });
                tableHtml += '<tr>';
                cells.forEach(function(c) { tableHtml += '<td>' + formatInline(c.trim()) + '</td>'; });
                tableHtml += '</tr>';
                continue;
            } else {
                // Конец таблицы
                tableHtml += '</tbody></table>';
                out.push(tableHtml);
                tableHtml = '';
                inTable = false;
                // Падаем в обработку текущей строки как обычного текста
            }
        }

        // --- Горизонтальная линия ---
        if (/^(-{3,}|_{3,}|\*{3,})$/.test(line.trim())) {
            if (inList) { out.push('</ul>'); inList = false; }
            out.push('<hr>');
            continue;
        }

        // --- Заголовки ---
        var hdr = line.match(/^(#{1,6})\s+(.*)$/);
        if (hdr) {
            if (inList) { out.push('</ul>'); inList = false; }
            out.push('<h' + hdr[1].length + '>' + formatInline(hdr[2]) + '</h' + hdr[1].length + '>');
            continue;
        }

        // --- Списки ---
        var li = line.match(/^\s*[-*+]\s+(.*)$/);
        if (li) {
            if (!inList) { out.push('<ul>'); inList = true; }
            out.push('<li>' + formatInline(li[1]) + '</li>');
            continue;
        } else if (inList) {
            out.push('</ul>');
            inList = false;
        }

        // --- Пустые строки ---
        if (line.trim() === '') {
            out.push('<br>');
            continue;
        }

        // --- Обычный текст ---
        out.push('<p>' + formatInline(line) + '</p>');
    }

    if (inList) out.push('</ul>');
    if (inTable) { tableHtml += '</tbody></table>'; out.push(tableHtml); }

    // 4. Восстанавливаем блоки кода
    var result = out.join('\n');
    for (var j = 0; j < codeBlocks.length; j++) {
        result = result.replace("\x00CB" + j + "\x00", codeBlocks[j]);
    }

    return result;
}

function formatInline(text) {
    return text
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>') // Жирный
        .replace(/(?<!\s)\*(?!\s)(.+?)(?<!\s)\*(?!\s)/g, '<em>$1</em>') // Курсив (без пробелов вокруг)
        .replace(/`(.+?)`/g, '<code>$1</code>'); // Инлайн-код
}

function addMessage(text, role, save, cost, timings, totalDurationMs) {
    const chatbox = document.getElementById("chatbox");
    if (!chatbox) return;

    const emptyState = chatbox.querySelector(".empty-state");
    if (emptyState) emptyState.remove();

    const msg = document.createElement("div");
    msg.className = `msg ${role}`;
    
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    // Используем innerHTML для рендеринга отформатированного текста
    bubble.innerHTML = parseMarkdown(text);
    
    msg.appendChild(bubble);

    // Кнопка копирования
    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.textContent = "📋";
    copyBtn.title = "Копировать";
    copyBtn.style.marginLeft = "10px";
    copyBtn.style.cursor = "pointer";
    copyBtn.style.background = "none";
    copyBtn.style.border = "none";
    copyBtn.onclick = function() {
        navigator.clipboard.writeText(text).then(() => {
            copyBtn.textContent = "✅";
            setTimeout(() => copyBtn.textContent = "📋", 2000);
        });
    };
    msg.appendChild(copyBtn);

    const metaWrap = document.createElement("div");
    metaWrap.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;align-items:center;";

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
        
        let stepsHtml = `<summary style="font-weight:600;">⏱️ Цепочка: ${total} (${timings.length} ${timings.length === 1 ? 'этап' : 'этапа'})</summary><div style="margin-top:4px;display:flex;flex-direction:column;gap:2px;">`;
        timings.forEach((t, idx) => {
            stepsHtml += `<div style="display:flex;justify-content:space-between;gap:12px;"><span>${idx + 1}. ${t.name}</span><strong>${t.duration_ms} мс</strong></div>`;
        });
        stepsHtml += "</div>";
        badge.innerHTML = stepsHtml;
        metaWrap.appendChild(badge);
    }

    if (metaWrap.children.length > 0) {
        msg.appendChild(metaWrap);
    }

    chatbox.appendChild(msg);
    chatbox.scrollTop = chatbox.scrollHeight;

    if (save && currentConvId) {
        const msgs = getMessages(currentConvId);
        msgs.push({ role, text, cost: cost || 0 });
        saveMessages(currentConvId, msgs);
    }
}

function loadHistory(convId) {
    const chatbox = document.getElementById("chatbox");
    if (!chatbox) return;
    
    chatbox.innerHTML = "";
    
    fetch(`/api/conversations/${convId}/messages`)
        .then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
        })
        .then(data => {
            if (data.messages && data.messages.length) {
                data.messages.forEach(msg => {
                    const role = msg.role === "assistant" ? "bot" : "user";
                    addMessage(msg.text, role, false, msg.cost || 0);
                });
            } else {
                chatbox.innerHTML = '<div class="empty-state">Начните диалог</div>';
            }
        })
        .catch(e => {
            console.error("Load history error:", e);
            chatbox.innerHTML = '<div class="empty-state">Ошибка загрузки истории</div>';
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
        addMessage(text, "user", true, 0);

        // Получаем параметры настроек, если они есть
        const params = (typeof window.getResponsesParams === "function") 
            ? window.getResponsesParams() 
            : {};

        fetch("/api/chat", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                message: text, 
                conversation_id: currentConvId, 
                model: currentModel,
                params: params
            })
        })
        .then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
        })
        .then(data => {
            if (data.error) {
                addMessage("Ошибка: " + data.error, "bot", false, 0);
            } else {
                addMessage(data.reply, "bot", true, data.cost || 0, data.timings, data.total_duration_ms);
            }
        })
        .catch(e => {
            addMessage("Ошибка сети при отправке сообщения", "bot", false, 0);
        });
    }

    if (sendBtn) sendBtn.addEventListener("click", sendMessage);
    
    if (input) {
        input.addEventListener("keydown", function(e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    window.uploadImage = async function() {
        const fileInput = document.createElement("input");
        fileInput.type = "file";
        fileInput.accept = "image/*";
        fileInput.capture = "environment";
        
        fileInput.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            addMessage(`📷 Загрузка фото: ${file.name}`, "user", true, 0);

            const formData = new FormData();
            formData.append("file", file);
            formData.append("message", "Проанализируй это изображение.");
            if (currentConvId) formData.append("conversation_id", currentConvId);

            try {
                const res = await fetch("/api/chat-with-image", {
                    method: "POST",
                    body: formData
                });
                const data = await res.json();
                
                if (data.error) {
                    addMessage("Ошибка анализа: " + data.error, "bot", false, 0);
                } else {
                    addMessage(data.reply, "bot", true, data.cost || 0);
                }
            } catch (err) {
                addMessage("Ошибка сети при загрузке фото", "bot", false, 0);
            }
        };
        
        fileInput.click();
    };
});

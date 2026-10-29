// "What the model sees": the hidden part of each provider request of a reply —
// instructions, tools, input items and history — from the trace's model_view events.
(function () {
  "use strict";

  function number(value) {
    var n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function fmt(value) {
    return number(value).toLocaleString("ru-RU");
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function joined(parts) {
    return Array.isArray(parts) ? parts.join("") : "";
  }

  function fold(title, body) {
    var details = el("details", "msg-model-view-fold");
    details.appendChild(el("summary", "msg-model-view-fold-summary", title));
    details.appendChild(body);
    return details;
  }

  function usageByStep(trace) {
    var byStep = {};
    (trace.responses || []).forEach(function (response) {
      var usage = response && response.raw && response.raw.usage;
      if (!usage || response.step === undefined) return;
      var details = usage.input_tokens_details || {};
      byStep[response.step] = {
        input: number(usage.input_tokens),
        cached: number(details.cached_tokens),
        output: number(usage.output_tokens),
        total: number(usage.total_tokens),
      };
    });
    return byStep;
  }

  function views(trace) {
    return (trace.events || [])
      .filter(function (event) {
        return event && event.type === "model_view" && event.payload && event.payload.view;
      })
      .map(function (event) {
        return { step: event.payload.step, view: event.payload.view };
      });
  }

  function historyText(history) {
    if (!history) return "";
    var text = history.provider_conversation
      ? "Беседа хранится у провайдера: он сам добавляет к запросу всю сохранённую историю"
      : history.previous_response
        ? "Продолжение предыдущего ответа провайдера (previous_response_id)"
        : "История передаётся только в элементах ввода выше";
    if (history.truncation) text += " · truncation: " + history.truncation;
    return text;
  }

  function renderTools(tools) {
    var list = el("div", "msg-model-view-list");
    (tools.groups || []).forEach(function (group) {
      var row = el("div", "msg-model-view-row");
      var deferred = number(group.deferred) ? " · отложено " + fmt(group.deferred) : "";
      row.appendChild(
        el("strong", "", group.group + " · " + fmt(group.count) + " шт." + deferred + " · " + fmt(group.chars) + " симв."),
      );
      if (group.names) row.appendChild(el("div", "msg-model-view-names", group.names));
      list.appendChild(row);
    });
    return list;
  }

  function renderInput(input) {
    var list = el("div", "msg-model-view-list");
    (input.items || []).forEach(function (item) {
      var row = el("div", "msg-model-view-row");
      if (item.type === "omitted") {
        row.textContent = "… ещё " + fmt(item.count) + " элементов";
        list.appendChild(row);
        return;
      }
      var head = [item.type, item.role, item.name].filter(Boolean).join(" · ") + " · " + fmt(item.chars) + " симв.";
      row.appendChild(el("strong", "", head));
      var text = joined(item.text_parts);
      if (text) row.appendChild(el("pre", "msg-model-view-pre", text));
      list.appendChild(row);
    });
    return list;
  }

  function render(trace) {
    var all = views(trace);
    if (!all.length) return null;
    var usage = usageByStep(trace);
    var first = all[0].view;
    var root = el("details", "msg-meta-details msg-model-view");
    var tools = first.tools || {};
    var instructions = first.instructions || {};
    root.appendChild(
      el(
        "summary",
        "msg-meta-summary",
        "👁 Что видит модель: " +
          all.length +
          " запр. · инструкции " +
          fmt(instructions.chars) +
          " симв. · инструменты " +
          fmt(tools.count) +
          (number(tools.deferred) ? ", отложено " + fmt(tools.deferred) : "") +
          " (" +
          fmt(tools.chars) +
          " симв.)",
      ),
    );
    var body = el("div", "msg-meta-detail-list msg-model-view-body");
    var seenInstructions = null;
    var seenTools = null;
    all.forEach(function (entry, index) {
      var view = entry.view;
      var block = el("div", "msg-model-view-request");
      var head = ["Запрос " + (index + 1)];
      if (view.tool_choice) head.push("tool_choice: " + view.tool_choice);
      head.push("всего " + fmt(view.payload_chars) + " симв.");
      var tokens = usage[entry.step];
      if (tokens) {
        head.push(
          "токены: вход " + fmt(tokens.input) + " (кэш " + fmt(tokens.cached) + ") · выход " + fmt(tokens.output),
        );
        var hosted = tokens.total - tokens.input - tokens.output;
        if (tokens.total) head.push("всего " + fmt(tokens.total) + (hosted > 0 ? " (инструменты провайдера " + fmt(hosted) + ")" : ""));
      }
      block.appendChild(el("div", "msg-model-view-head", head.join(" · ")));

      var instr = view.instructions || {};
      var instrText = joined(instr.text_parts);
      if (instrText && instrText === seenInstructions) {
        block.appendChild(el("div", "msg-model-view-row", "Инструкции: те же, что в запросе 1 (" + fmt(instr.chars) + " симв.)"));
      } else {
        block.appendChild(
          fold("Инструкции · " + fmt(instr.chars) + " симв.", el("pre", "msg-model-view-pre", instrText || "—")),
        );
        if (seenInstructions === null) seenInstructions = instrText;
      }

      var tl = view.tools || {};
      var toolsKey = fmt(tl.count) + "/" + fmt(tl.deferred) + "/" + fmt(tl.chars);
      if (toolsKey === seenTools) {
        block.appendChild(
          el("div", "msg-model-view-row", "Инструменты: те же, что в запросе 1 (" + fmt(tl.count) + " шт., " + fmt(tl.chars) + " симв.)"),
        );
      } else {
        var tlDeferred = number(tl.deferred) ? " (отложено " + fmt(tl.deferred) + ")" : "";
        block.appendChild(
          fold("Инструменты · " + fmt(tl.count) + " шт." + tlDeferred + " · " + fmt(tl.chars) + " симв.", renderTools(tl)),
        );
        if (seenTools === null) seenTools = toolsKey;
      }

      var inp = view.input || {};
      block.appendChild(fold("Ввод · " + fmt(inp.count) + " эл. · " + fmt(inp.chars) + " симв.", renderInput(inp)));
      var history = historyText(view.history);
      if (history) block.appendChild(el("div", "msg-model-view-row", "История: " + history));
      body.appendChild(block);
    });
    root.appendChild(body);
    return root;
  }

  // A stored message keeps only a reference to its trace: the block loads it when opened.
  function lazy(load) {
    var root = el("details", "msg-meta-details msg-model-view");
    var summary = el("summary", "msg-meta-summary", "👁 Что видит модель");
    root.appendChild(summary);
    var state = "idle";
    root.addEventListener("toggle", function () {
      if (!root.open || state !== "idle") return;
      state = "loading";
      summary.textContent = "👁 Что видит модель · загружаем…";
      Promise.resolve()
        .then(load)
        .then(function (trace) {
          var full = trace && typeof trace === "object" ? render(trace) : null;
          if (!full) {
            state = "done";
            summary.textContent = "👁 Что видит модель · нет данных: ответ получен до этой версии";
            return;
          }
          state = "done";
          summary.textContent = full.firstChild.textContent;
          root.appendChild(full.lastChild);
        })
        .catch(function () {
          state = "idle";
          root.open = false;
          summary.textContent = "👁 Что видит модель · не загрузилось, откройте ещё раз";
        });
    });
    return root;
  }

  window.LogosModelView = { render: render, views: views, lazy: lazy };
})();

/*
 * Keep a failed chat request debuggable without losing text the model
 * already generated before the pipeline failed.
 */
(function () {
    function extractGeneratedText(trace) {
        if (!trace || typeof trace !== "object") return "";
        const responses = Array.isArray(trace.responses) ? trace.responses : [];
        const parts = [];

        responses.forEach(function (response) {
            if (!response || typeof response !== "object") return;
            const raw = response.raw && typeof response.raw === "object"
                ? response.raw : response;
            if (!raw || typeof raw !== "object") return;

            if (typeof raw.output_text === "string" && raw.output_text.trim()) {
                parts.push(raw.output_text.trim());
                return;
            }

            const output = Array.isArray(raw.output) ? raw.output : [];
            output.forEach(function (item) {
                if (!item || typeof item !== "object") return;
                const content = Array.isArray(item.content) ? item.content : [];
                content.forEach(function (part) {
                    if (!part || typeof part !== "object") return;
                    const type = part.type;
                    if ((type === "output_text" || type === "text") && typeof part.text === "string" && part.text.trim()) {
                        parts.push(part.text.trim());
                    }
                });
            });
        });

        return parts.join("\n\n");
    }

    const originalFetch = window.fetch.bind(window);
    window.fetch = function () {
        return originalFetch.apply(null, arguments).then(function (response) {
            const request = arguments[0];
            const url = typeof request === "string" ? request : (request && request.url) || "";
            if (!url.includes("/api/chat") || response.ok) return response;

            return response.clone().json().catch(function () { return {}; }).then(function (data) {
                const trace = data && data.trace;
                if (!trace || typeof trace !== "object") return response;

                const generated = extractGeneratedText(trace);
                const errorText = data.error || ("HTTP " + response.status);
                const reply = generated
                    ? generated + "\n\n⚠️ Ошибка: " + errorText
                    : "⚠️ Ошибка: " + errorText;

                const normalized = Object.assign({}, data, {
                    reply: reply,
                    trace: trace,
                    partial_output: generated || null,
                    error: null
                });

                return new Response(JSON.stringify(normalized), {
                    status: 200,
                    headers: { "Content-Type": "application/json" }
                });
            });
        });
    };
})();

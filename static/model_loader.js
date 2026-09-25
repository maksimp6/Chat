(function() {
    "use strict";

    function logModelEvent(level, event, details) {
        var payload = Object.assign({
            component: "model_loader",
            event: event,
            timestamp: new Date().toISOString()
        }, details || {});
        var logger = console[level] || console.error;
        logger.call(console, "[MODEL_LOAD]", payload);
    }

    function normalize(payload) {
        if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
            throw new Error("Models API returned an invalid payload");
        }
        if (payload.text != null && (typeof payload.text !== "object" || Array.isArray(payload.text))) {
            throw new Error("Models API returned invalid text models");
        }
        if (payload.voice != null && (typeof payload.voice !== "object" || Array.isArray(payload.voice))) {
            throw new Error("Models API returned invalid voice models");
        }
        var text = payload.text || {};
        var voice = payload.voice || {};
        if (Object.keys(text).length === 0 && Object.keys(voice).length === 0) {
            var emptyError = new Error("Models API returned no models");
            emptyError.code = "EMPTY_MODELS";
            throw emptyError;
        }
        return {text: text, voice: voice};
    }

    async function loadModels(options) {
        options = options || {};
        var fetchImpl = options.fetchImpl || window.fetch.bind(window);
        var timeoutMs = options.timeoutMs || 10000;
        logModelEvent("info", "request_started", {timeout_ms: timeoutMs});

        var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
        var timer = controller ? setTimeout(function() { controller.abort(); }, timeoutMs) : null;

        try {
            var response = await fetchImpl("/api/models", {
                credentials: "same-origin",
                cache: "no-store",
                signal: controller ? controller.signal : undefined
            });
            if (!response || !response.ok) {
                var status = response && response.status != null ? response.status : "network";
                var httpError = new Error("Models API request failed: " + status);
                httpError.code = "HTTP_ERROR";
                httpError.status = status;
                logModelEvent("error", "request_failed", {reason: "http", status: status});
                throw httpError;
            }

            var payload;
            try {
                payload = await response.json();
            } catch (error) {
                error.code = "INVALID_JSON";
                logModelEvent("error", "request_failed", {reason: "invalid_json", message: error.message});
                throw error;
            }

            try {
                var models = normalize(payload);
                logModelEvent("info", "request_succeeded", {
                    text_count: Object.keys(models.text).length,
                    voice_count: Object.keys(models.voice).length
                });
                return models;
            } catch (error) {
                logModelEvent("error", "request_failed", {
                    reason: error.code === "EMPTY_MODELS" ? "empty_response" : "invalid_payload",
                    message: error.message
                });
                throw error;
            }
        } catch (error) {
            if (error && error.name === "AbortError") {
                error.code = "TIMEOUT";
                logModelEvent("error", "request_failed", {reason: "timeout"});
            } else if (error && !error.code) {
                error.code = "NETWORK_ERROR";
                logModelEvent("error", "request_failed", {reason: "network", message: error.message});
            }
            throw error;
        } finally {
            if (timer) clearTimeout(timer);
        }
    }

    window.AliceModelLoader = {
        load: loadModels,
        normalize: normalize,
        logEvent: logModelEvent
    };
})();
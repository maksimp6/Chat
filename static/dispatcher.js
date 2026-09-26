"use strict";

(function() {
    const MAX_TIMEOUT_MS = 30000;
    const DEFAULT_TIMEOUT_MS = 10000;

    function request(input, init, options) {
        const settings = options || {};
        const timeoutMs = Math.min(
            Math.max(Number(settings.timeoutMs || DEFAULT_TIMEOUT_MS), 1),
            MAX_TIMEOUT_MS
        );
        const requestInit = Object.assign({}, init || {});
        if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
            requestInit.signal = AbortSignal.timeout(timeoutMs);
        }
        return fetch(input, requestInit);
    }

    function dispatch(action, payload) {
        if (action !== "request") {
            return Promise.reject(new Error("Unknown dispatcher action: " + action));
        }
        const data = payload || {};
        return request(data.input, data.init, data.options);
    }

    window.AliceDispatcher = Object.freeze({
        request,
        dispatch
    });
})();

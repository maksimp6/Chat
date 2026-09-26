"use strict";

(function () {
  const MAX_TIMEOUT_MS = 30000;
  const DEFAULT_TIMEOUT_MS = 10000;
  let activeRequests = 0;

  function requestPath(input) {
    try {
      const value = typeof input === "string" ? input : input && input.url ? input.url : "";
      const url = new URL(value, window.location.href);
      return url.pathname || "/";
    } catch (error) {
      return "<invalid-url>";
    }
  }

  function beginRequestTrace(input) {
    if (!window.AliceCoreAPI || !window.AliceCoreAPI.trace) return null;
    return window.AliceCoreAPI.trace.begin("dispatcher", "http.request", {
      path: requestPath(input),
    });
  }

  function setNetworkStatus(status, message) {
    if (!window.AliceCoreAPI || !window.AliceCoreAPI.status) return;
    window.AliceCoreAPI.status.set("network", status, message, {
      activeRequests: activeRequests,
      revocable: false,
    });
  }

  function request(input, init, options) {
    const settings = options || {};
    const timeoutMs = Math.min(
      Math.max(Number(settings.timeoutMs || DEFAULT_TIMEOUT_MS), 1),
      MAX_TIMEOUT_MS,
    );
    const requestInit = Object.assign({}, init || {});
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      requestInit.signal = AbortSignal.timeout(timeoutMs);
    }

    const trace = beginRequestTrace(input);
    activeRequests += 1;
    setNetworkStatus("running", "Сетевой запрос выполняется");

    return fetch(input, requestInit)
      .then(function (response) {
        if (trace) {
          trace.event("response", {
            path: requestPath(input),
            status: response.status,
            ok: response.ok,
          });
          trace.end(response.ok ? "completed" : "http_error");
        }
        activeRequests = Math.max(activeRequests - 1, 0);
        setNetworkStatus(
          response.ok ? (activeRequests ? "running" : "ready") : "degraded",
          response.ok ? "Соединение активно" : "HTTP " + response.status,
        );
        return response;
      })
      .catch(function (error) {
        if (trace) {
          trace.error(error);
          trace.end("error");
        }
        activeRequests = Math.max(activeRequests - 1, 0);
        setNetworkStatus("error", "Сетевой запрос завершился ошибкой");
        throw error;
      });
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
    dispatch,
  });
})();

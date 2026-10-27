"use strict";

(function () {
  const MAX_TIMEOUT_MS = 300000;
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
    if (!window.LogosCoreAPI || !window.LogosCoreAPI.trace) return null;
    return window.LogosCoreAPI.trace.begin("dispatcher", "http.request", {
      path: requestPath(input),
    });
  }

  function setNetworkStatus(status, message) {
    if (!window.LogosCoreAPI || !window.LogosCoreAPI.status) return;
    window.LogosCoreAPI.status.set("network", status, message, {
      activeRequests: activeRequests,
      revocable: false,
    });
  }

  // Aborts when either signal aborts; keeps the reason of the one that did.
  function combineSignals(first, second) {
    if (typeof AbortSignal.any === "function") return AbortSignal.any([first, second]);
    // Compatibility for browsers with timeout() but without any(). Remove both
    // subscriptions when either signal ends, including the bounded timeout.
    const signals = [first, second];
    const aborted = signals.find(function (signal) { return signal.aborted; });
    if (aborted) return aborted;
    const controller = new AbortController();
    const listeners = signals.map(function (signal) {
      return function () {
        listeners.forEach(function (listener, index) {
          signals[index].removeEventListener("abort", listener);
        });
        controller.abort(signal.reason);
      };
    });
    signals.forEach(function (signal, index) {
      signal.addEventListener("abort", listeners[index], { once: true });
    });
    return controller.signal;
  }

  function request(input, init, options) {
    const settings = options || {};
    const requestedTimeout = Number(settings.timeoutMs);
    const timeoutMs = Math.min(
      Number.isFinite(requestedTimeout) && requestedTimeout > 0
        ? Math.max(Math.trunc(requestedTimeout), 1)
        : DEFAULT_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    );
    const requestInit = Object.assign({}, init || {});
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      // The caller's signal (for example «Stop») and the timeout both end the request.
      requestInit.signal = requestInit.signal
        ? combineSignals(requestInit.signal, AbortSignal.timeout(timeoutMs))
        : AbortSignal.timeout(timeoutMs);
    }

    const trace = beginRequestTrace(input);
    activeRequests += 1;
    setNetworkStatus("running", "Сетевой запрос выполняется");

    // Release the slot once, first: a throw while reporting the response must not
    // release it again, and the error handler must not run for a success path.
    return fetch(input, requestInit).then(
      function (response) {
        activeRequests = Math.max(activeRequests - 1, 0);
        if (trace) {
          trace.event("response", {
            path: requestPath(input),
            status: response.status,
            ok: response.ok,
          });
          trace.end(response.ok ? "completed" : "http_error");
        }
        setNetworkStatus(
          response.ok ? (activeRequests ? "running" : "ready") : "degraded",
          response.ok ? "Соединение активно" : "HTTP " + response.status,
        );
        return response;
      },
      function (error) {
        activeRequests = Math.max(activeRequests - 1, 0);
        if (trace) {
          trace.error(error);
          trace.end("error");
        }
        setNetworkStatus("error", "Сетевой запрос завершился ошибкой");
        throw error;
      },
    );
  }

  function dispatch(action, payload) {
    if (action !== "request") {
      return Promise.reject(new Error("Unknown dispatcher action: " + action));
    }
    const data = payload || {};
    return request(data.input, data.init, data.options);
  }

  window.LogosDispatcher = Object.freeze({
    request,
    dispatch,
    isBusy: () => activeRequests > 0,
  });
})();

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

  function request(input, init, options) {
    const settings = options || {};
    const requested = Number(settings.timeoutMs);
    const timeoutMs =
      Number.isFinite(requested) && requested > 0
        ? Math.min(requested, MAX_TIMEOUT_MS)
        : DEFAULT_TIMEOUT_MS;
    const requestInit = Object.assign({}, init || {});
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      const timeoutSignal = AbortSignal.timeout(timeoutMs);
      // Keep a signal the caller passed in: cancelling it must still cancel the request.
      requestInit.signal =
        init && init.signal && typeof AbortSignal.any === "function"
          ? AbortSignal.any([init.signal, timeoutSignal])
          : timeoutSignal;
    }

    const trace = beginRequestTrace(input);
    activeRequests += 1;
    setNetworkStatus("running", "Сетевой запрос выполняется");

    let pending;
    try {
      pending = fetch(input, requestInit);
    } catch (error) {
      // A synchronous throw never reaches the promise handlers below: release the slot here.
      activeRequests = Math.max(activeRequests - 1, 0);
      setNetworkStatus("error", "Сетевой запрос завершился ошибкой");
      throw error;
    }

    // Release the slot once, first: a throw while reporting the response must not
    // release it again, and the error handler must not run for a success path.
    return pending.then(
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
  });
})();

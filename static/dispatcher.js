"use strict";

(function () {
  const MAX_TIMEOUT_MS = 300000;
  const DEFAULT_TIMEOUT_MS = 10000;
  let activeRequests = 0;
  // Work that outlives its request (a live answer still streaming after its headers) keeps the page busy too.
  let holds = 0;

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

  // The time limit of one request: until the headers it is the whole wait; after them it counts only silence, and
  // every received piece of the body starts it again. A body that keeps flowing is read to its end at any link speed
  // (a long history at 1.25 KB/s), a stalled one ends after the same time as before. abort() ends the request with a
  // TimeoutError, the error a whole-request timeout gave.
  function idleTimeout(timeoutMs) {
    const controller = new AbortController();
    let timer = null;
    const fire = function () {
      timer = null;
      controller.abort(new DOMException("The request stalled for " + timeoutMs + " ms", "TimeoutError"));
    };
    return {
      signal: controller.signal,
      arm: function () {
        if (controller.signal.aborted) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(fire, timeoutMs);
      },
      stop: function () {
        if (timer) clearTimeout(timer);
        timer = null;
      },
    };
  }

  // Statuses whose response may not carry a body: a Response built with one throws.
  const NULL_BODY_STATUSES = [101, 103, 204, 205, 304];

  // The same response, with a body that re-arms the idle timer on every piece read and stops it at the end.
  function withIdleBody(response, limit) {
    if (
      !response.body ||
      NULL_BODY_STATUSES.indexOf(response.status) !== -1 ||
      typeof TransformStream !== "function" ||
      typeof response.body.pipeThrough !== "function"
    ) {
      limit.stop();
      return response;
    }
    let rebuilt;
    let stream;
    try {
      stream = new TransformStream({
        transform: function (chunk, controller) {
          limit.arm();
          controller.enqueue(chunk);
        },
        flush: function () {
          limit.stop();
        },
      });
      rebuilt = new Response(stream.readable, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    } catch (error) {
      // A browser that cannot rebuild the response reads the original body (still unlocked); the limit then keeps
      // counting from the headers, as a whole-request timeout did.
      return response;
    }
    // A failure of the original body (the time limit aborted it) reaches the reader through the rebuilt one.
    response.body.pipeTo(stream.writable).catch(function () {});
    return rebuilt;
  }

  function request(input, init, options) {
    const settings = options || {};
    const requestedTimeout = Number(settings.timeoutMs);
    // An explicit 0 means no time limit: the caller bounds the request itself (a live answer runs as long as
    // the turn does). A missing or invalid value still gets the default.
    const unbounded = settings.timeoutMs === 0;
    const timeoutMs = Math.min(
      Number.isFinite(requestedTimeout) && requestedTimeout > 0
        ? Math.max(Math.trunc(requestedTimeout), 1)
        : DEFAULT_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    );
    const requestInit = Object.assign({}, init || {});
    const limit = !unbounded && typeof AbortController === "function" ? idleTimeout(timeoutMs) : null;
    if (limit) {
      // The caller's signal (for example «Stop») and the time limit both end the request.
      requestInit.signal = requestInit.signal ? combineSignals(requestInit.signal, limit.signal) : limit.signal;
      limit.arm();
    }

    const trace = beginRequestTrace(input);
    activeRequests += 1;
    setNetworkStatus("running", "Сетевой запрос выполняется");

    // Release the slot once, first: a throw while reporting the response must not
    // release it again, and the error handler must not run for a success path.
    return fetch(input, requestInit).then(
      function (received) {
        activeRequests = Math.max(activeRequests - 1, 0);
        // The headers came: from here on only silence ends the request.
        if (limit) limit.arm();
        const response = limit ? withIdleBody(received, limit) : received;
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
        if (limit) limit.stop();
        if (trace) {
          trace.error(error);
          trace.end("error");
        }
        setNetworkStatus("error", "Сетевой запрос завершился ошибкой");
        throw error;
      },
    );
  }

  // Marks the page busy until the returned release() is called; release() may be called more than once.
  function hold() {
    holds += 1;
    let released = false;
    return function release() {
      if (released) return;
      released = true;
      holds = Math.max(holds - 1, 0);
    };
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
    hold,
    isBusy: () => activeRequests + holds > 0,
  });
})();

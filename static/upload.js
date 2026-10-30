"use strict";

// A file or a photo sent in pieces that survives a broken connection (POST /api/uploads, workspace/upload_routes.py).
// On a 10 kb/s link (1.25 KB/s) one request cannot carry a file: every drop would start it over. Here each piece goes
// at the offset the server has; after a drop the server says how much arrived and the upload goes on from there.
// The first piece is 1 KiB (the smallest piece, set by the owner); the next ones are sized to the measured speed, a
// failed one halves the size, never below 1 KiB.
(function () {
  const MIN_PIECE_BYTES = 1024;
  // A piece is sized to take about half of the dispatcher's 10 s wait at the measured speed.
  const PIECE_TARGET_MS = 5000;
  // The slowest link the app must serve, 10 kb/s, in bytes per millisecond: a piece may take this long to go out
  // before its answer, on top of the usual wait.
  const WORST_LINK_BYTES_PER_MS = 1.28;
  const BASE_WAIT_MS = 10000;
  const MAX_WAIT_MS = 300000;
  // Pauses between attempts after a failure: random within a window that doubles from the base to the cap, as the
  // chat's re-sends do.
  const RETRY_BASE_MS = 1000;
  const RETRY_CAP_MS = 15000;

  function retryDelayMs(attempt) {
    const span = Math.min(RETRY_CAP_MS, RETRY_BASE_MS * Math.pow(2, Math.max(attempt, 1) - 1));
    return Math.random() * span;
  }

  function stopped() {
    const error = new Error("The upload was stopped");
    error.name = "AbortError";
    return error;
  }

  function pause(ms, signal) {
    return new Promise(function (resolve, reject) {
      if (signal && signal.aborted) return reject(stopped());
      const done = function () {
        if (signal) signal.removeEventListener("abort", onStop);
        resolve();
      };
      const onStop = function () {
        clearTimeout(timer);
        reject(stopped());
      };
      const timer = setTimeout(done, ms);
      if (signal) signal.addEventListener("abort", onStop, { once: true });
    });
  }

  function statusError(response, body) {
    const error = new Error((body && body.error) || "HTTP " + response.status);
    error.status = response.status;
    error.body = body || {};
    return error;
  }

  function readJson(response) {
    return response.json().catch(function () {
      return {};
    });
  }

  // A failure the next attempt can get past: no answer (a drop, a timeout) or a server or gateway error.
  function retryable(error) {
    if (!error) return false;
    if (error.name === "AbortError" && error.message === "The upload was stopped") return false;
    if (typeof error.status === "number") return error.status >= 500 || error.status === 408 || error.status === 429;
    return error instanceof TypeError || error.name === "TimeoutError" || error.name === "AbortError";
  }

  function request(path, init, timeoutMs, signal) {
    const options = Object.assign({}, init || {});
    if (signal) options.signal = signal;
    return window.LogosDispatcher.request(path, options, { timeoutMs: timeoutMs }).then(function (response) {
      return readJson(response).then(function (body) {
        if (!response.ok) throw statusError(response, body);
        return body;
      });
    });
  }

  // Tries ``attempt`` until it works, a failure is final, or ``signal`` stops it.
  async function persist(attempt, signal) {
    for (let tries = 1; ; tries += 1) {
      try {
        return await attempt();
      } catch (error) {
        if (signal && signal.aborted) throw stopped();
        if (!retryable(error)) throw error;
        await pause(retryDelayMs(tries), signal);
      }
    }
  }

  function pieceWaitMs(bytes) {
    return Math.min(MAX_WAIT_MS, BASE_WAIT_MS + Math.ceil(bytes / WORST_LINK_BYTES_PER_MS));
  }

  // Sends ``blob`` and resolves with the completion answer: for a file what POST /api/files answers, for an image
  // {upload_id, size}. options: name, kind ("file" | "image"), purpose, signal, onProgress(received, size).
  async function send(blob, options) {
    const settings = options || {};
    const signal = settings.signal;
    const size = blob.size;
    const report = function (received) {
      if (typeof settings.onProgress === "function") settings.onProgress(received, size);
    };
    const created = await persist(function () {
      return request(
        "/api/uploads",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: settings.name || blob.name || "file",
            size: size,
            kind: settings.kind || "file",
            purpose: settings.purpose || "assistants",
          }),
        },
        BASE_WAIT_MS,
        signal,
      );
    }, signal);
    const path = "/api/uploads/" + encodeURIComponent(created.id);
    let received = Number(created.received) || 0;
    let pieceBytes = MIN_PIECE_BYTES;
    report(received);
    for (let failures = 0; received < size; ) {
      if (signal && signal.aborted) throw stopped();
      const piece = blob.slice(received, Math.min(size, received + pieceBytes));
      const started = Date.now();
      try {
        const state = await request(
          path,
          {
            method: "PUT",
            headers: { "Content-Type": "application/octet-stream", "Upload-Offset": String(received) },
            body: piece,
          },
          pieceWaitMs(piece.size),
          signal,
        );
        received = Number(state.received) || received + piece.size;
        failures = 0;
        const elapsed = Math.max(Date.now() - started, 1);
        pieceBytes = Math.max(MIN_PIECE_BYTES, Math.floor((piece.size / elapsed) * PIECE_TARGET_MS));
        report(received);
        continue;
      } catch (error) {
        if (signal && signal.aborted) throw stopped();
        if (error && error.status === 409 && error.body && typeof error.body.received === "number") {
          // The server has another size (an earlier copy of this piece got through): go on from there.
          received = error.body.received;
          report(received);
          continue;
        }
        if (!retryable(error)) throw error;
        failures += 1;
        pieceBytes = Math.max(MIN_PIECE_BYTES, Math.floor(pieceBytes / 2));
        await pause(retryDelayMs(failures), signal);
      }
      // After a failure the server says how much arrived; when it cannot be asked now, the next piece asks again.
      try {
        const state = await request(path, { method: "GET" }, BASE_WAIT_MS, signal);
        received = Number(state.received) || 0;
        report(received);
      } catch (error) {
        if (signal && signal.aborted) throw stopped();
        if (!retryable(error)) throw error;
      }
    }
    return persist(function () {
      return request(path + "/complete", { method: "POST" }, MAX_WAIT_MS, signal);
    }, signal);
  }

  // Drops an upload the page no longer needs (a photo removed or already sent); a failure leaves it to the server.
  function discard(uploadId) {
    if (!uploadId) return Promise.resolve();
    return window.LogosDispatcher.request("/api/uploads/" + encodeURIComponent(uploadId), { method: "DELETE" }, {
      timeoutMs: BASE_WAIT_MS,
    }).then(
      function () {},
      function () {},
    );
  }

  window.LogosUpload = Object.freeze({ send: send, discard: discard });
})();

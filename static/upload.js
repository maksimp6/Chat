"use strict";

// A file or a photo sent in pieces that survives a broken connection (POST /api/uploads, workspace/upload_routes.py).
// On a 10 kb/s link (1.25 KB/s) one request cannot carry a file: every drop would start it over. Here each piece goes
// at the offset the server has; after a drop the server says how much arrived and the upload goes on from there.
// The first piece is 1 KiB (the smallest piece, set by the owner); the next ones are sized to the measured speed, at
// most twice the last one and never more than fits its wait on the slowest link; a failed one halves the size, never
// below 1 KiB.
(function () {
  const MIN_PIECE_BYTES = 1024;
  // A piece is sized to take about half of the dispatcher's 10 s wait at the measured speed.
  const PIECE_TARGET_MS = 5000;
  // The slowest link the app must serve, 10 kb/s, in bytes per millisecond: a piece may take this long to go out
  // before its answer, on top of the usual wait.
  const WORST_LINK_BYTES_PER_MS = 1.28;
  const BASE_WAIT_MS = 10000;
  const MAX_WAIT_MS = 300000;
  // The largest piece that still goes out within the longest wait on the slowest link: after the link drops, one
  // timed-out piece costs at most this much.
  const MAX_PIECE_BYTES = Math.floor((MAX_WAIT_MS - BASE_WAIT_MS) * WORST_LINK_BYTES_PER_MS);
  // Pauses between attempts after a failure: random within a window that doubles from the base to the cap, as the
  // chat's re-sends do.
  const RETRY_BASE_MS = 1000;
  const RETRY_CAP_MS = 15000;
  // Answers that a repeat can get past: a gateway or the server busy for a moment. Any other answer is final.
  const PASSING_STATUSES = [408, 429, 502, 503, 504];

  function retryDelayMs(attempt) {
    const span = Math.min(RETRY_CAP_MS, RETRY_BASE_MS * Math.pow(2, Math.max(attempt, 1) - 1));
    return Math.random() * span;
  }

  function stopped() {
    const error = new Error("The upload was stopped");
    error.name = "UploadStoppedError";
    return error;
  }

  function badAnswer() {
    const error = new Error("The server answer has no upload state");
    error.name = "BadAnswerError";
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

  // No usable answer came: a drop, a timeout, or an answer without the fields it must carry.
  function noAnswer(error) {
    return Boolean(error) && typeof error.status !== "number" && error.name !== "UploadStoppedError";
  }

  function passes(error) {
    return noAnswer(error) || (Boolean(error) && PASSING_STATUSES.indexOf(error.status) !== -1);
  }

  function request(path, init, timeoutMs, signal) {
    const options = Object.assign({}, init || {});
    if (signal) options.signal = signal;
    return window.LogosDispatcher.request(path, options, { timeoutMs: timeoutMs }).then(function (response) {
      return response.json().then(
        function (body) {
          if (!response.ok) throw statusError(response, body);
          return body && typeof body === "object" ? body : {};
        },
        function () {
          if (!response.ok) throw statusError(response, {});
          // A success whose body did not arrive whole is no answer.
          throw badAnswer();
        },
      );
    });
  }

  function hasState(body) {
    return Boolean(body) && typeof body.received === "number" && body.received >= 0;
  }

  // Tries ``attempt`` until it works, a failure is final (``retry`` says which pass), or ``signal`` stops it.
  async function persist(attempt, retry, signal) {
    for (let tries = 1; ; tries += 1) {
      try {
        return await attempt();
      } catch (error) {
        if (signal && signal.aborted) throw stopped();
        if (!retry(error)) throw error;
        await pause(retryDelayMs(tries), signal);
      }
    }
  }

  function pieceWaitMs(bytes) {
    return Math.min(MAX_WAIT_MS, BASE_WAIT_MS + Math.ceil(bytes / WORST_LINK_BYTES_PER_MS));
  }

  // An upload id chosen here, so a create sent again (its answer lost) names the same upload on the server.
  function newUploadId() {
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, function (byte) { return byte.toString(16).padStart(2, "0"); }).join("");
  }

  async function upload(blob, settings, uploadId) {
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
            id: uploadId,
            name: settings.name || blob.name || "file",
            size: size,
            kind: settings.kind || "file",
            purpose: settings.purpose || "assistants",
          }),
        },
        BASE_WAIT_MS,
        signal,
      ).then(function (body) {
        if (body.id !== uploadId || !hasState(body)) throw badAnswer();
        return body;
      });
    }, passes, signal);
    if (typeof settings.onCreated === "function") settings.onCreated(uploadId);
    const path = "/api/uploads/" + encodeURIComponent(uploadId);
    let received = created.received;
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
        if (!hasState(state)) throw badAnswer();
        received = state.received;
        failures = 0;
        const elapsed = Math.max(Date.now() - started, 1);
        const measured = Math.floor((piece.size / elapsed) * PIECE_TARGET_MS);
        pieceBytes = Math.max(MIN_PIECE_BYTES, Math.min(measured, pieceBytes * 2, MAX_PIECE_BYTES));
        report(received);
        continue;
      } catch (error) {
        if (signal && signal.aborted) throw stopped();
        if (error && error.status === 409 && hasState(error.body)) {
          // The server has another size (an earlier copy of this piece got through): go on from there.
          received = error.body.received;
          report(received);
          continue;
        }
        if (!passes(error)) throw error;
        failures += 1;
        pieceBytes = Math.max(MIN_PIECE_BYTES, Math.floor(pieceBytes / 2));
        await pause(retryDelayMs(failures), signal);
      }
      // After a failure the server says how much arrived; when it cannot be asked now, the next piece asks again.
      try {
        const state = await request(path, { method: "GET" }, BASE_WAIT_MS, signal);
        if (hasState(state)) {
          received = state.received;
          report(received);
        }
      } catch (error) {
        if (signal && signal.aborted) throw stopped();
        if (!passes(error)) throw error;
      }
    }
    // A completion is sent again only when no answer came: Files may store a file only once, so any answer from the
    // server, an error too, is final (a completion that may have stored the file answers completion_uncertain).
    const image = settings.kind === "image";
    return persist(function () {
      return request(path + "/complete", { method: "POST" }, MAX_WAIT_MS, signal).then(function (body) {
        if (image ? typeof body.upload_id !== "string" : typeof body.id !== "string") throw badAnswer();
        return body;
      });
    }, noAnswer, signal);
  }

  // Sends ``blob`` and resolves with the completion answer: for a file what POST /api/files answers, for an image
  // {upload_id, size}. options: name, kind ("file" | "image"), purpose, signal, onProgress(received, size),
  // onCreated(uploadId). The page stays busy (no automatic reload) until the upload ends. A stopped or failed upload
  // drops its copy on the server; one whose completion may have stored the file (completion_uncertain) keeps it.
  async function send(blob, options) {
    const settings = options || {};
    const uploadId = newUploadId();
    const release = typeof window.LogosDispatcher.hold === "function" ? window.LogosDispatcher.hold() : function () {};
    try {
      return await upload(blob, settings, uploadId);
    } catch (error) {
      if (!(error && error.body && error.body.error === "completion_uncertain")) discard(uploadId);
      throw error;
    } finally {
      release();
    }
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

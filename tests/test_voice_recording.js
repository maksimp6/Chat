"use strict";

const assert = require("assert");
const path = require("path");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const ROOT = path.resolve(__dirname, "..");

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await flush();
}

async function main() {
  const browser = new BrowserShim(
    '<div id="voice-status"></div><button id="mic-btn" type="button"></button>',
  );
  const requests = [];
  const recorders = [];

  class FakeMediaRecorder {
    static isTypeSupported(mime) {
      return mime === "audio/ogg;codecs=opus";
    }

    constructor(stream, options) {
      this.mimeType = options.mimeType;
      this.stopped = false;
      this.listeners = {};
      recorders.push(this);
    }

    start(timeslice) {
      this.timeslice = timeslice;
    }

    addEventListener(type, handler) {
      this.listeners[type] = handler;
    }

    stop() {
      this.stopped = true;
      this.ondataavailable({ data: new Blob(["tail"]) });
      if (this.listeners.stop) this.listeners.stop();
    }
  }

  const dispatcher = {
    request: async (url, init, options) => {
      requests.push({ url, init, options });
      const body = url === "/api/voice/session" ? { session_id: "voice-1" } : { ok: true };
      return { ok: true, status: 200, json: async () => body };
    },
  };

  browser.load([path.join(ROOT, "static", "voice.js")], {
    AliceDispatcher: dispatcher,
    fetch: () => {
      throw new Error("voice.js must use AliceDispatcher instead of fetch");
    },
    Blob,
    MediaRecorder: FakeMediaRecorder,
    AudioContext: class {
      createMediaStreamSource() {
        return { connect() {}, disconnect() {} };
      }

      close() {}
    },
    EventSource: class {
      close() {}
    },
    navigator: {
      mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) },
    },
  });

  browser.document.getElementById("mic-btn").click();
  await settle();

  assert.strictEqual(requests[0].url, "/api/voice/session");
  assert.ok(requests[0].options.timeoutMs > 0, "requests must carry a dispatcher timeout");
  assert.strictEqual(recorders.length, 1);
  const recorder = recorders[0];
  assert.strictEqual(recorder.timeslice, 1000, "recorder emits one-second slices");

  for (let i = 0; i < 28; i += 1) recorder.ondataavailable({ data: new Blob(["x"]) });
  await settle();
  assert.strictEqual(recorder.stopped, false, "recording continues below the slice limit");

  recorder.ondataavailable({ data: new Blob(["x"]) });
  await settle();
  assert.strictEqual(recorder.stopped, true, "recording stops at the slice limit without timers");
  assert.deepStrictEqual(
    requests.map((request) => request.url),
    ["/api/voice/session", "/api/voice/audio?session_id=voice-1", "/api/voice/close"],
  );

  console.log("voice recording tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

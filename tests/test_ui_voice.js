"use strict";

// Voice input on the BrowserShim: real static/voice.js with mocked dispatcher responses and a
// microphone that the user has refused.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function boot() {
  const requests = [];
  const dispatcher = {
    request: (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, body });
      if (url === "/api/voice/session") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ session_id: "session-1" }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    },
  };
  const shim = new BrowserShim('<body><button id="mic-btn" type="button"></button></body>');
  const { window, document } = shim.load(["static/voice.js"], {
    LogosDispatcher: dispatcher,
    navigator: {
      mediaDevices: {
        getUserMedia: () => Promise.reject(new Error("Permission denied")),
      },
    },
    currentConvId: "c1",
    currentModel: "speech-realtime-260528",
  });
  return { window, document, requests };
}

async function settle() {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

test("a refused microphone closes the voice session the server just opened", async () => {
  const { document, requests } = boot();
  document.getElementById("mic-btn").click();
  await settle();

  const close = requests.find((request) => request.url === "/api/voice/close");
  assert.deepEqual(close && close.body, { session_id: "session-1" });
});

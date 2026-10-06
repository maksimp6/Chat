const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { BrowserDom } = require("../browser/emulator/browser_dom");

const ROOT = path.resolve(__dirname, "..");

function response(body) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

function boot(routes = {}) {
  const browser = new BrowserDom();
  const document = browser.parse(
    '<div id="chatbox"></div><textarea id="msg-input"></textarea>' +
      '<button id="send-btn"></button><div id="voice-status"></div>' +
      '<button id="mic-btn" type="button"></button>',
  );
  const window = browser.window;
  window.document = document;
  window.AliceDispatcher = {
    request: (url) => {
      const value = routes[url];
      if (value instanceof Error) return Promise.reject(value);
      return response(value || {});
    },
  };
  window.localStorage = { getItem: () => null, setItem: () => {} };
  window.addEventListener = () => {};
  window.location = { href: "" };

  const context = vm.createContext({
    window,
    document,
    console,
    fetch: window.AliceDispatcher.request,
    localStorage: window.localStorage,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(
    fs.readFileSync(path.join(ROOT, "static", "chat.js"), "utf8"),
    context,
  );
  return { context, document };
}

function rendered(document) {
  return Array.from(document.querySelectorAll(".msg")).map((node) => ({
    role: node.className.split(/\s+/).find((name) =>
      ["user", "bot", "error"].includes(name),
    ),
    text: node.textContent.trim(),
  }));
}

(async () => {
  const { context, document } = boot();

  context.addMessage("human", "user", false, 0, null, 0, null, null, null, {
    id: "m1",
  });
  context.addMessage(
    "assistant",
    "assistant",
    false,
    0,
    null,
    0,
    null,
    null,
    null,
    { id: "m2" },
  );
  context.addMessage("ignored", "system", false, 0, null, 0, null, null, null, {
    id: "m3",
  });

  assert.deepEqual(rendered(document), [
    { role: "user", text: "human" },
    { role: "bot", text: "assistant" },
  ]);

  context.addMessage(
    "assistant",
    "assistant",
    false,
    0,
    null,
    0,
    null,
    null,
    null,
    { id: "m2" },
  );
  assert.equal(rendered(document).length, 2);

  const history = boot({
    "/api/conversations/c1/messages": {
      messages: [
        { id: "u1", role: "user", text: "one" },
        { id: "a1", role: "assistant", text: "two" },
        { id: "s1", role: "system", text: "hidden" },
      ],
    },
  });
  history.context.loadHistory("c1");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(rendered(history.document), [
    { role: "user", text: "one" },
    { role: "bot", text: "two" },
  ]);

  console.log("chat history role tests passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

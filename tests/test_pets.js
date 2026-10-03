"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { BrowserShim } = require("./browser_dom");

const html =
  '<div id="alice-pet" data-static-root="/preview/demo/static" hidden><span class="alice-pet-label"></span><button class="alice-pet-button"><span class="alice-pet-sprite"></span></button></div><div id="chatbox"></div><input id="msg-input"><button id="send-btn"></button>';

function setup(options = {}) {
  const browser = new BrowserShim(html);
  const callbacks = new Set();
  const mediaListeners = new Set();
  const media = {
    matches: false,
    addEventListener: (_, fn) => mediaListeners.add(fn),
    removeEventListener: (_, fn) => mediaListeners.delete(fn),
  };
  if (options.saved) browser.window.localStorage.setItem("alice_pet", options.saved);
  if (options.storageFailure) {
    browser.window.localStorage.getItem = () => {
      throw new Error("denied");
    };
    browser.window.localStorage.setItem = () => {
      throw new Error("denied");
    };
  }
  const fakeAbort = {
    timeout() {
      let callback;
      return {
        addEventListener(_, fn) {
          callback = fn;
          callbacks.add(fn);
        },
        removeEventListener() {
          callbacks.delete(callback);
        },
      };
    },
  };
  const loaded = browser.load(
    ["static/core_api.js", "static/pets.js", ...(options.chat ? ["static/chat.js"] : [])],
    {
      AbortSignal: fakeAbort,
      matchMedia: () => media,
      performance: { now: () => 100 },
      currentConvId: "c1",
      currentModel: "test",
      conversations: [],
    },
  );
  const root = loaded.document.getElementById("alice-pet");
  function tick() {
    const batch = [...callbacks];
    batch.forEach((fn) => {
      callbacks.delete(fn);
      fn();
    });
  }
  return {
    ...loaded,
    browser,
    root,
    callbacks,
    media,
    mediaListeners,
    tick,
    pet: loaded.window.AlicePets,
  };
}

async function settle() {
  for (let i = 0; i < 12; i++) await Promise.resolve();
}

async function main() {
  {
    const t = setup();
    assert.equal(t.root.hidden, false);
    assert.equal(t.root.dataset.state, "idle");
    assert.match(
      t.root.querySelector(".alice-pet-sprite").style.backgroundImage,
      /\/preview\/demo\/static\/pets\/plush.png/,
    );
    assert.equal(t.callbacks.size, 1);
    t.tick();
    assert.equal(t.root.querySelector(".alice-pet-sprite").style.backgroundPosition, "-60px 0px");
    const first = t.pet.begin("running"),
      second = t.pet.begin("running");
    first("success");
    first("failed");
    assert.equal(
      t.root.dataset.state,
      "running",
      "earlier/duplicate completion cannot stop another request",
    );
    second("success");
    assert.equal(t.root.dataset.state, "jumping");
    for (let i = 0; i < 5; i++) t.tick();
    assert.equal(t.root.dataset.state, "idle");
    const approval = t.pet.begin("waiting");
    assert.equal(t.root.dataset.state, "waiting");
    const request = t.pet.begin("running");
    request("success");
    assert.equal(t.root.dataset.state, "waiting");
    approval();
    t.root.querySelector(".alice-pet-button").click();
    assert.equal(t.root.dataset.state, "waving");
    for (let i = 0; i < 4; i++) t.tick();
    assert.equal(t.root.dataset.state, "idle");
    const stale = t.pet.begin("running");
    t.pet.reset();
    stale("failed");
    assert.equal(t.root.dataset.state, "idle");
    t.document.hidden = true;
    t.document.dispatchEvent(new t.window.Event("visibilitychange"));
    assert.equal(t.callbacks.size, 0);
    t.document.hidden = false;
    t.document.dispatchEvent(new t.window.Event("visibilitychange"));
    assert.equal(t.callbacks.size, 1);
    t.media.matches = true;
    t.mediaListeners.forEach((fn) => fn());
    assert.equal(t.callbacks.size, 0, "reduced-motion idle has no animation work");
    t.pet.begin("running")("failed");
    assert.equal(t.root.dataset.state, "failed");
    t.tick();
    assert.equal(t.root.dataset.state, "idle");
    t.pet.select("none");
    assert.equal(t.root.hidden, true);
    assert.equal(t.callbacks.size, 0);
    assert.equal(t.window.localStorage.getItem("alice_pet"), "none");
    t.pet.select("unknown");
    assert.equal(t.pet.getSelected(), "none");
    t.pet.select("plush");
    t.window.AliceCoreAPI.revocation.revokeAll("pets", "user_requested");
    assert.equal(t.root.hidden, true);
    assert.equal(t.callbacks.size, 0);
    t.pet.destroy();
    assert.equal(t.mediaListeners.size, 0);
  }
  {
    const t = setup({ saved: "none" });
    assert.equal(t.root.hidden, true);
    assert.equal(t.callbacks.size, 0);
    t.pet.destroy();
    const denied = setup({ storageFailure: true });
    denied.pet.select("none");
    assert.equal(denied.root.hidden, true);
    denied.pet.destroy();
  }
  {
    const t = setup({ chat: true });
    const responses = [];
    t.window.AliceDispatcher = {
      request: () => new Promise((resolve, reject) => responses.push({ resolve, reject })),
    };
    const input = t.document.getElementById("msg-input");
    const send = () => {
      input.value = "Привет";
      t.document.getElementById("send-btn").click();
    };
    send();
    assert.equal(t.root.dataset.state, "running");
    responses.shift().resolve({ ok: true, status: 200, json: async () => ({ reply: "Привет!" }) });
    await settle();
    assert.equal(t.root.dataset.state, "jumping");
    send();
    responses.shift().reject(new Error("offline"));
    await settle();
    assert.equal(t.root.dataset.state, "failed");
    send();
    responses.shift().resolve({ ok: false, status: 500, json: async () => ({ error: "failed" }) });
    await settle();
    assert.equal(t.root.dataset.state, "failed");
    send();
    responses.shift().resolve({
      ok: true,
      status: 200,
      json: async () => ({ requires_approval: true, tool_call: { name: "demo", arguments: {} } }),
    });
    await settle();
    assert.equal(t.root.dataset.state, "waiting");
    t.document.querySelector(".approval-btn-reject").click();
    assert.equal(t.root.dataset.state, "idle");
    vm.runInContext('renderApprovalCard({ name: "demo", arguments: {} }, "hello")', t.context);
    t.document.querySelector(".approval-btn-approve").click();
    assert.equal(t.root.dataset.state, "running");
    responses.shift().resolve({ ok: true, status: 200, json: async () => ({ reply: "Готово" }) });
    await settle();
    assert.equal(t.root.dataset.state, "jumping");
    send();
    vm.runInContext('currentConvId = "c2"; window.AlicePets.reset();', t.context);
    responses.shift().resolve({
      ok: true,
      status: 200,
      json: async () => ({
        requires_approval: true,
        tool_call: { name: "stale", arguments: {} },
      }),
    });
    await settle();
    assert.equal(t.root.dataset.state, "idle", "old conversation cannot revive approval waiting");
    assert.equal(t.document.querySelector(".approval-card"), null);
    t.pet.destroy();
  }
  {
    const t = setup();
    for (const file of [
      "static/settings/ui_helpers.js",
      "static/settings/settings_storage.js",
      "static/settings/settings_modal.js",
    ])
      vm.runInContext(fs.readFileSync(file, "utf8"), t.context);
    t.window.AliceTheme = { themes: { light: "Светлая" }, getStored: () => "light" };
    t.window.openSettingsModal();
    const select = t.document.getElementById("set-pet");
    assert.equal(select.value, "plush");
    select.value = "none";
    select.dispatchEvent(new t.window.Event("change"));
    assert.equal(t.root.hidden, true);
    assert.equal(t.window.localStorage.getItem("alice_pet"), "none");
    t.pet.destroy();
  }
  const png = fs.readFileSync("static/pets/plush.png");
  assert.equal(png.readUInt32BE(16), 1536);
  assert.equal(png.readUInt32BE(20), 2288);
  assert.equal(png[25], 6, "atlas preserves RGBA transparency");
  console.log(
    "Pet animation, preferences, accessibility, lifecycle and chat integration tests passed",
  );
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

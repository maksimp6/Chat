"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { BrowserShim } = require("./browser_dom");

const ROOT = path.resolve(__dirname, "..");

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

async function approve(toolCall) {
  const browser = new BrowserShim('<div id="chatbox"></div>');
  const bodies = [];
  const { document, context } = browser.load([path.join(ROOT, "static", "chat.js")], {
    AliceDispatcher: {
      request: async (_url, init) => {
        bodies.push(JSON.parse(init.body));
        return { ok: true, status: 200, json: async () => ({ reply: "Готово" }) };
      },
    },
    currentConvId: "conv-1",
    currentModel: "test-model",
  });
  context.renderApprovalCard(toolCall, "Сделай");
  document.querySelector(".approval-btn-approve").click();
  await settle();
  return bodies[0];
}

(async () => {
  const keyed = await approve({ name: "delete_file", arguments: {}, call_id: "call-9" });
  assert.equal(keyed.idempotency_key, "conv-1:call-9");

  const unkeyed = await approve({ name: "delete_file", arguments: {} });
  assert.equal("idempotency_key" in unkeyed, false);

  console.log("chat approval idempotency tests passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

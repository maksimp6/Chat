"use strict";

// Partner relations and departments on the BrowserShim: real static modules with mocked
// dispatcher responses.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

async function settle() {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

function partnerRelationsBoot() {
  let followupStatus = "open";
  const partner = (status) => ({
    id: "p1",
    name: "Анна",
    status: "active",
    organization: "ООО",
    contacts: [],
    messages: [],
    followups: [{ id: "f1", title: "Звонок", status }],
  });
  const dispatcher = {
    request: (url, options = {}) => {
      const method = options.method || "GET";
      const json = (status, body) =>
        Promise.resolve({ ok: status < 300, status, json: async () => body });
      if (method === "POST" && url === "/api/partners/followups/f1/complete") {
        followupStatus = "completed";
        return json(200, { followup: { id: "f1", status: "completed" } });
      }
      if (url === "/api/partners") {
        return json(200, {
          partners: [{ id: "p1", name: "Анна", status: "active", organization: "ООО" }],
        });
      }
      if (url === "/api/partners/p1") {
        return json(200, { partner: partner(followupStatus) });
      }
      return json(404, { error: "not mocked" });
    },
  };
  const shim = new BrowserShim("<body></body>");
  const { window, document } = shim.load(
    ["static/core_api.js", "static/partner_relations.js"],
    { LogosDispatcher: dispatcher },
  );
  window.LogosCoreAPI.ui.events.mountClicks(document.body);
  return { window, document };
}

test("completing a follow-up shows its new status, not the copy taken before", async () => {
  const { window, document } = partnerRelationsBoot();
  window.openPartnerRelationsModal();
  await settle();

  document.querySelector(".logos-dept-row").click();
  await settle();
  const done = Array.from(document.querySelectorAll("button")).find(
    (button) => button.textContent === "Готово",
  );
  done.click();
  await settle();

  const detail = document.getElementById("partner-relations-detail").textContent;
  assert.match(detail, /Звонок · completed/);
  assert.doesNotMatch(detail, /Звонок · open/);
});

test("a network failure starting a department session is reported to the user", async () => {
  const alerts = [];
  const dispatcher = {
    request: (url, options = {}) => {
      if (url === "/api/departments") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            departments: [{ id: "d1", name: "Продажи", description: "отдел" }],
          }),
        });
      }
      return Promise.reject(new Error("network down"));
    },
  };
  const shim = new BrowserShim("<body></body>");
  const { window, document } = shim.load(["static/core_api.js", "static/departments.js"], {
    LogosDispatcher: dispatcher,
    alert: (message) => alerts.push(message),
  });
  window.LogosCoreAPI.ui.events.mountClicks(document.body);
  window.openDepartmentsModal();
  await settle();

  document.querySelector(".logos-dept-row").click();
  await settle();

  assert.deepEqual(alerts, ["Ошибка: network down"]);
});

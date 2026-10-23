import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { chromium } from "playwright-core";
import { createOAuth } from "./oauth.mjs";

const callback = "https://chatgpt.com/connector_platform_oauth_redirect";
const password = "synthetic-mobile-owner-token";
const verifier = "v".repeat(43);

for (const viewport of [{ width: 320, height: 640 }, { width: 393, height: 873 }, { width: 844, height: 393 }]) {
  test(`mobile consent and PKCE roundtrip at ${viewport.width}px`, { skip: process.env.BROWSER_LIVE_SMOKE_TEST !== "1", timeout: 20000 }, async (t) => {
    const directory = await mkdtemp(join(tmpdir(), "mobile-oauth-browser-"));
    const server = createServer();
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    const resource = base + "/browser/v1/mcp";
    const oauth = createOAuth({ env: {}, publicUrl: base, shortToken: password, ownerId: "owner", stateFile: join(directory, "oauth.json") });
    server.on("request", async (request, response) => {
      if (await oauth.handle(request, response, new URL(request.url, base))) return;
      response.writeHead(404).end();
    });
    t.after(async () => { await new Promise((resolve) => server.close(resolve)); await rm(directory, { recursive: true, force: true }); });
    const browser = await chromium.launch({ executablePath: process.env.CHROME_EXECUTABLE_PATH ?? "/usr/bin/google-chrome-stable", headless: true });
    t.after(() => browser.close());
    const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true, serviceWorkers: "block" });
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const errors = [];
    const unexpectedRequests = [];
    let callbackRequests = 0;
    // BrowserContext.route only handles the first request in a redirect chain.
    // Intercept each hop in Chromium so synthetic codes never reach ChatGPT.
    const cdp = await context.newCDPSession(page);
    cdp.on("Fetch.requestPaused", async ({ requestId, request }) => {
      const url = new URL(request.url);
      if (url.origin === base) return cdp.send("Fetch.continueRequest", { requestId });
      if (url.origin + url.pathname === callback) {
        callbackRequests += 1;
        return cdp.send("Fetch.fulfillRequest", {
          requestId, responseCode: 200,
          responseHeaders: [{ name: "content-type", value: "text/html; charset=utf-8" }],
          body: Buffer.from('<link rel="icon" href="data:,"><title>Synthetic callback</title>Authorized').toString("base64"),
        });
      }
      unexpectedRequests.push(url.origin + url.pathname);
      return cdp.send("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" });
    });
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] });
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const location = message.location().url;
      const url = location ? new URL(location) : null;
      errors.push({ message: message.text(), location: url ? url.origin + url.pathname : "" });
    });
    page.on("pageerror", (error) => errors.push({ message: error.message }));
    const registration = await fetch(base + "/browser/oauth/register", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ client_name: "Mobile".repeat(15), redirect_uris: [callback], scope: "browser offline_access" }) });
    assert.equal(registration.status, 201);
    const client = await registration.json();
    const query = new URLSearchParams({ client_id: client.client_id, redirect_uri: callback, response_type: "code", resource, code_challenge: createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256", scope: "browser offline_access", state: "mobile-test" });
    await page.goto(base + "/browser/oauth/authorize?" + query);
    const dimensions = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, fieldHeight: document.querySelector('input[type="password"]').getBoundingClientRect().height, buttonHeight: document.querySelector('button').getBoundingClientRect().height, font: parseFloat(getComputedStyle(document.querySelector('input[type="password"]')).fontSize) }));
    assert.equal(dimensions.viewport, viewport.width);
    assert.ok(dimensions.document <= viewport.width, "horizontal overflow");
    assert.ok(dimensions.fieldHeight >= 48 && dimensions.buttonHeight >= 48, "touch targets");
    assert.ok(dimensions.font >= 16, "readable input");
    const input = page.getByLabel("Код доступа владельца");
    assert.equal(await input.getAttribute("type"), "password");
    if (process.env.MOBILE_OAUTH_SCREENSHOT_DIR) {
      await mkdir(process.env.MOBILE_OAUTH_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.MOBILE_OAUTH_SCREENSHOT_DIR, `consent-${viewport.width}.png`), fullPage: true });
    }
    await input.fill(password);
    await Promise.all([page.waitForURL(callback + "**"), page.getByRole("button", { name: "Разрешить" }).tap()]);
    assert.equal(await page.title(), "Synthetic callback");
    assert.equal(callbackRequests, 1);
    assert.deepEqual(unexpectedRequests, []);
    const redirected = new URL(page.url());
    assert.equal(redirected.searchParams.get("state"), "mobile-test");
    assert.equal(redirected.searchParams.get("iss"), base + "/browser/oauth");
    assert.ok(redirected.searchParams.get("code"));
    const tokenResponse = await fetch(base + "/browser/oauth/token", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ grant_type: "authorization_code", client_id: client.client_id, redirect_uri: callback, resource, code: redirected.searchParams.get("code"), code_verifier: verifier }) });
    assert.equal(tokenResponse.status, 200);
    const tokens = await tokenResponse.json();
    assert.equal(oauth.authorize({ headers: { authorization: `Bearer ${tokens.access_token}` } }), true);
    assert.equal(oauth.authorize({ headers: { authorization: "Bearer invalid" } }), false);
    assert.deepEqual(errors, []);
  });
}

const assert = require("node:assert/strict");
const { PassThrough } = require("node:stream");
const { EmulatorSession, EmulatorError } = require("../browser/emulator/session");
const { serve } = require("../browser/emulator/server");

const PAGES = {
  "https://example.test/": `<!DOCTYPE html><html><head><title>Home</title>
    <script>window.evil = true;</script><style>.x{}</style></head><body>
    <h1 id="greeting">Hello &amp; welcome</h1>
    <a id="about" href="/about">About us</a>
    <span id="plain">tap</span>
    <form id="search" action="/search" method="get">
      <input name="q" type="text"><input name="secret" type="password" value="hunter2">
      <input name="opt" type="checkbox"><button id="go" type="submit">Go</button>
    </form>
    <form id="login" action="/login" method="post">
      <input name="user"><input type="submit" id="send">
    </form></body></html>`,
  "https://example.test/about": "<title>About</title><p>About page</p>",
};

function makeFetch(calls) {
  return async (url, init = {}) => {
    calls.push({ url, init });
    const parsed = new URL(url);
    const key = parsed.origin + parsed.pathname;
    const body =
      PAGES[key] ??
      `<title>Echo</title><p id="echo">${parsed.pathname}?${parsed.search.slice(1)}|${init.body || ""}</p>`;
    return { url, status: 200, text: async () => body };
  };
}

(async () => {
  const calls = [];
  const session = new EmulatorSession({ fetch: makeFetch(calls) });

  await assert.rejects(() => session.execute("inspect", "body"), EmulatorError);
  await assert.rejects(() => session.execute("navigate", "file:///etc/passwd"), /only http/);
  await assert.rejects(() => session.execute("navigate", "http://["), /invalid URL/);
  await assert.rejects(() => session.execute("screenshot", "page"), /not supported/);
  await assert.rejects(() => session.execute("hover", "x"), /unsupported action/);

  const nav = await session.execute("navigate", "https://example.test/");
  assert.deepEqual(nav, { url: "https://example.test/", status: 200, title: "Home" });

  const page = await session.execute("inspect", "page");
  assert.match(page.text, /Hello & welcome/);
  assert.doesNotMatch(page.text, /evil|\.x\{\}/);
  assert.deepEqual(page.links, [{ text: "About us", href: "https://example.test/about" }]);
  assert.equal(page.inputs.find((i) => i.name === "secret").value, null);
  assert.deepEqual(page.buttons, ["Go"]);
  assert.equal((await session.execute("inspect", "#greeting")).text, "Hello & welcome");

  assert.deepEqual(await session.execute("assert_state", "#greeting", "welcome"), {
    ok: true,
    found: true,
    text: "Hello & welcome",
  });
  assert.equal((await session.execute("assert_state", "#greeting", "bye")).ok, false);
  assert.equal((await session.execute("assert_state", "#missing", null)).found, false);

  await assert.rejects(() => session.execute("fill", "#greeting", "x"), /not fillable/);
  await assert.rejects(() => session.execute("click", "#missing"), /element not found/);
  assert.deepEqual(await session.execute("click", "#plain"), {
    url: "https://example.test/",
    clicked: "#plain",
  });

  await session.execute("fill", "input[name='q']", "cats");
  const search = await session.execute("click", "#go");
  assert.equal(search.url, "https://example.test/search?q=cats&secret=hunter2");

  await session.execute("navigate", "https://example.test/");
  await session.execute("fill", "input[name='user']", "max");
  await session.execute("click", "#send");
  const post = calls.at(-1);
  assert.equal(post.init.method, "POST");
  assert.equal(post.init.body, "user=max");

  await session.execute("navigate", "https://example.test/");
  assert.equal((await session.execute("click", "#about")).title, "About");

  const tiny = new EmulatorSession({ fetch: makeFetch([]), maxBytes: 10 });
  await assert.rejects(() => tiny.navigate("https://example.test/"), /size limit/);

  const input = new PassThrough();
  const output = new PassThrough();
  serve(input, output, new EmulatorSession({ fetch: makeFetch([]) }));
  const responses = [];
  output.on("data", (chunk) => responses.push(...String(chunk).trim().split("\n")));
  input.write('{"action":"navigate","target":"https://example.test/about"}\n');
  input.write("not json\n");
  input.write('{"action":"screenshot","target":"page"}\n');
  await new Promise((resolve) => setTimeout(resolve, 50));
  const parsed = responses.map((line) => JSON.parse(line));
  assert.equal(parsed[0].success, true);
  assert.equal(parsed[0].data.title, "About");
  assert.equal(parsed[1].success, false);
  assert.match(parsed[2].error, /not supported/);

  console.log("browser emulator tests passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

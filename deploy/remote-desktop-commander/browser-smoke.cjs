"use strict";

// Container-only acceptance test: no external site, user data, or RDC OAuth.
async function main() {
  const targets = await (await fetch("http://127.0.0.1:9222/json/list", {
    signal: AbortSignal.timeout(3000),
  })).json();
  const target = targets.find((item) => item.type === "page");
  const endpoint = new URL(target.webSocketDebuggerUrl);
  if (endpoint.protocol !== "ws:" || endpoint.hostname !== "127.0.0.1" || endpoint.port !== "9222") throw new Error();
  const socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let id = 0;
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const request = ++id;
    const listener = (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== request) return;
      socket.removeEventListener("message", listener);
      if (message.error) reject(new Error()); else resolve(message.result);
    };
    socket.addEventListener("message", listener);
    socket.send(JSON.stringify({ id: request, method, params }));
  });
  try {
    const result = await send("Runtime.evaluate", {
      expression: "document.body.innerHTML = '<h1>RDC Chromium smoke</h1>'; document.body.textContent",
      returnByValue: true,
    });
    if (result.result.value !== "RDC Chromium smoke") throw new Error();
    console.log("Chromium rendered a synthetic page; private browser endpoint is ready.");
  } finally {
    socket.close();
  }
}
const timeout = setTimeout(() => {
  console.error("Chromium smoke exceeded 30 seconds.");
  process.exit(1);
}, 30000);
main().then(() => clearTimeout(timeout)).catch(() => {
  console.error("Chromium smoke failed.");
  process.exit(1);
});

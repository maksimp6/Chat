// Renders the real chat.js and style.css in Chromium and checks one bot bubble with
// formulas and a generated picture. Runs only inside GitHub Actions.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..", "..");
const out = process.env.VISUAL_OUT || path.join(process.env.RUNNER_TEMP || "/tmp", "visual-chat");
fs.mkdirSync(out, { recursive: true });

const harness = `<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<link rel="stylesheet" href="file://${root}/static/style.css">
</head><body class="logos-pro-app" style="margin:0;background:#fff">
<div id="chatbox" class="chatbox" style="width:420px;padding:12px"></div>
<script>window.LogosDispatcher = { request: function () { return Promise.reject(new Error("offline test")); } };</script>
<script src="file://${root}/static/chat.js"></script>
</body></html>`;
const harnessPath = path.join(out, "harness.html");
fs.writeFileSync(harnessPath, harness, "utf-8");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 460, height: 900 }, deviceScaleFactor: 1 });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("file://" + harnessPath, { waitUntil: "load" });

  const checks = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 320;
    canvas.height = 180;
    const g = canvas.getContext("2d");
    g.fillStyle = "#e8f0fe";
    g.fillRect(0, 0, 320, 180);
    g.fillStyle = "#d93025";
    g.beginPath();
    g.arc(160, 90, 60, 0, Math.PI * 2);
    g.fill();
    const picture = canvas.toDataURL("image/png");

    const text = [
      "Формулы: $y = x^3 - 3x + 1$, $e^{x}$, $\\sqrt{x}$, $\\frac{1}{2}$.",
      "Расчёт: $2340 \\times 0{,}17 = 397{,}8$.",
      "$$x(t) = \\sin(3t), \\; y(t) = \\sin(4t)$$",
      "![Alice AI ART](" + picture + ")",
    ].join("\n\n");
    if (typeof addMessage !== "function") return { addMessageAvailable: false };
    addMessage("Нарисуй круг и покажи формулы", "user", false, 0);
    addMessage(text, "bot", false, 0);
    return { addMessageAvailable: true };
  });

  const imageReady = await page
    .waitForFunction(() => {
      const img = document.querySelector(".md-image");
      return img && img.complete && img.naturalWidth > 0;
    }, null, { timeout: 15000 })
    .then(() => true)
    .catch(() => false);

  const result = await page.evaluate(() => {
    const bubble = document.querySelector(".msg:last-child .bubble") || document.querySelector(".bubble:last-of-type");
    const bubbleText = bubble ? bubble.textContent : "";
    const image = document.querySelector(".md-image");
    return {
      bubbleFound: Boolean(bubble),
      images: document.querySelectorAll(".md-image").length,
      imageWidth: image ? image.naturalWidth : 0,
      fractions: document.querySelectorAll(".md-frac").length,
      mathBlocks: document.querySelectorAll(".md-math").length,
      rawLatex: /\\[a-zA-Z]+|\$\$/.test(bubbleText),
      rawDollar: bubbleText.indexOf("$") !== -1,
    };
  });

  const bubbles = await page.locator("#chatbox").screenshot({ path: path.join(out, "chat-bubbles.png") });
  void bubbles;

  const report = {
    addMessageAvailable: checks.addMessageAvailable,
    imageReady,
    ...result,
    pageErrors: pageErrors.slice(0, 10),
  };
  fs.writeFileSync(path.join(out, "report.json"), JSON.stringify(report, null, 2), "utf-8");
  console.log(JSON.stringify(report));
  await browser.close();

  const failed =
    !report.addMessageAvailable ||
    !report.bubbleFound ||
    !report.imageReady ||
    report.images !== 1 ||
    report.fractions < 1 ||
    report.mathBlocks < 3 ||
    report.rawLatex ||
    report.rawDollar;
  if (failed) process.exit(1);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

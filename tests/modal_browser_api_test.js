"use strict";

const fs = require("fs");
const vm = require("vm");
const { DocumentShim, WindowShim } = require("./browser_dom");

const html = fs.readFileSync("templates/index.html", "utf8");
const document = new DocumentShim(html);
const window = new WindowShim(document);
global.document = document;
global.window = window;

const modal = document.getElementById("model-modal");
if (!modal) throw new Error("model modal missing");
if (!modal.classList.contains("modal")) throw new Error("modal class missing");

const content = modal.querySelector(".modal-content");
if (!content) throw new Error("modal-content missing");

const button = content.querySelector("button");
if (!button) throw new Error("button missing");

let clicked = false;
button.addEventListener("click", () => {
  clicked = true;
});
button.setAttribute("data-test", "ok");
if (button.getAttribute("data-test") !== "ok") throw new Error("attribute API broken");
button.classList.add("browser-test");
if (!button.classList.contains("browser-test")) throw new Error("classList.add broken");
button.classList.remove("browser-test");
if (button.classList.contains("browser-test")) throw new Error("classList.remove broken");
button.click();
if (!clicked) throw new Error("event dispatch/click API broken");

const dynamic = document.createElement("div");
dynamic.id = "dynamic-modal";
dynamic.className = "modal visible";
dynamic.setAttribute("aria-hidden", "false");
const dynamicContent = document.createElement("div");
dynamicContent.className = "modal-content";
dynamicContent.innerHTML = '<button type="button">Close</button><span>Тест UTF-8</span>';
dynamic.appendChild(dynamicContent);
document.body.appendChild(dynamic);

if (document.getElementById("dynamic-modal") !== dynamic)
  throw new Error("dynamic DOM insertion broken");
if (dynamic.querySelectorAll(".modal-content").length !== 1)
  throw new Error("querySelectorAll broken");
if (dynamic.querySelector("button").textContent !== "Close")
  throw new Error("innerHTML parsing broken");
if (dynamic.querySelector("span").textContent !== "Тест UTF-8")
  throw new Error("UTF-8 DOM text broken");
if (dynamic.getAttribute("aria-hidden") !== "false") throw new Error("attribute read broken");

dynamic.remove();
if (document.getElementById("dynamic-modal")) throw new Error("remove API broken");

console.log("modal browser API emulation: ok");

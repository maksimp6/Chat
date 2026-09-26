const {BrowserShim} = require("./browser_dom");

const shim = new BrowserShim();
const {window, document} = shim.load(["static/core_api.js"]);

const UI = window.AliceCoreAPI.ui;
const body = document.createElement("div");
body.className = "modal-body";
body.textContent = "Body";

const modal = UI.modal.create({
    id: "contract-modal",
    title: "Contract modal",
    closeAction: "contract.modal.close",
    body
});

if (!modal.classList.contains("modal")) throw new Error("modal root must use .modal");
if (!modal.hidden) throw new Error("new modal must start hidden");
if (modal.getAttribute("role") !== "dialog") throw new Error("modal must expose dialog role");
if (modal.getAttribute("aria-modal") !== "true") throw new Error("modal must expose aria-modal");
if (modal.getAttribute("aria-hidden") !== "true") throw new Error("hidden modal must expose aria-hidden=true");
if (modal.getAttribute("aria-labelledby") !== "contract-modal-title") throw new Error("modal must be labelled by its title");

const content = modal.querySelector(".modal-content");
if (!content) throw new Error("modal requires one .modal-content shell");
if (modal.querySelectorAll(".modal-content").length !== 1) throw new Error("modal requires exactly one content shell");

const close = modal.querySelector(".modal-close");
if (!close || !close.classList.contains("alice-btn")) throw new Error("modal close must use button contract");
if (close.dataset.action !== "contract.modal.close") throw new Error("close button must use action contract");

UI.modal.open(modal);
if (modal.hidden || !modal.classList.contains("visible")) throw new Error("open must expose modal");
if (modal.getAttribute("aria-hidden") !== "false") throw new Error("open modal must expose aria-hidden=false");

UI.modal.close(modal);
if (!modal.hidden || modal.classList.contains("visible")) throw new Error("close must hide modal");
if (modal.getAttribute("aria-hidden") !== "true") throw new Error("closed modal must expose aria-hidden=true");

let closeCalls = 0;
const unregister = UI.actions.register("contract.modal.close", function() {
    closeCalls += 1;
    UI.modal.close(modal);
});
document.body.appendChild(modal);
const unmount = UI.events.mountClicks(document.body);

UI.modal.open(modal);
close.click();
if (closeCalls !== 1 || !modal.hidden) throw new Error("close click must dispatch exactly once and close modal");

unmount();
unregister();
console.log("UI modal contract tests passed");

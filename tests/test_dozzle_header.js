const fs = require("fs");

const html = fs.readFileSync("templates/index.html", "utf8");
const js = fs.readFileSync("static/dozzle.js", "utf8");

if (!html.includes('id="dozzle-btn"')) throw new Error("Dozzle header button missing");
if (!html.includes('src="{{ static_root }}/dozzle.js')) throw new Error("Dozzle script missing");
if (!html.includes("window.openDozzleLogs()")) throw new Error("Dozzle action missing");
if (!js.includes('"/logs/"')) throw new Error("Dozzle logs path missing");

console.log("Dozzle header regression checks passed");

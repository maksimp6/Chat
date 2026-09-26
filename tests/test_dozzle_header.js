const fs = require("fs");

const html = fs.readFileSync("templates/index.html", "utf8");
const js = fs.readFileSync("static/dozzle.js", "utf8");
const header = fs.readFileSync("static/header_actions.js", "utf8");

if (!html.includes('id="dozzle-btn"')) throw new Error("Dozzle header button missing");
if (!html.includes('src="{{ static_root }}/dozzle.js')) throw new Error("Dozzle script missing");
if (!html.includes('src="{{ static_root }}/header_actions.js'))
  throw new Error("Header actions script missing");
if (!html.includes('id="dozzle-btn"') || !html.includes('data-action="header.dozzle.open"'))
  throw new Error("Dozzle declarative action missing");
if (!header.includes('actions.register("header.dozzle.open", call("openDozzleLogs"))'))
  throw new Error("Dozzle dispatcher registration missing");
if (!js.includes('"/logs/"')) throw new Error("Dozzle logs path missing");

console.log("Dozzle header regression checks passed");

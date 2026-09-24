const fs = require("fs");
const source = fs.readFileSync("static/trace_viewer.js", "utf8");

if (!source.includes('className:"alice-trace-copy"')) {
  throw new Error("tool copy control is missing");
}
if (!source.includes('copyItem(item,copy)')) {
  throw new Error("tool copy handler is missing");
}
if (!source.includes('Скопировать данные инструмента')) {
  throw new Error("tool copy accessibility label is missing");
}
if (!source.includes('button.textContent="✅"')) {
  throw new Error("tool copy success feedback is missing");
}

console.log("Trace viewer tool-copy regression checks passed");

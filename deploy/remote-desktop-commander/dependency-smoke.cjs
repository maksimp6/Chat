// Exercise the dependency APIs used by upstream after the security overrides.
const assert = require("node:assert/strict");
const sharp = require("sharp");
const ExcelJS = require("exceljs");
(async () => {
  const png = await sharp({
    create: { width: 1, height: 1, channels: 4, background: "white" },
  }).png().toBuffer();
  assert.equal((await sharp(png).metadata()).width, 1);
  const workbook = new ExcelJS.Workbook();
  workbook.addWorksheet("Smoke").getCell("A1").value = "ok";
  const restored = new ExcelJS.Workbook();
  await restored.xlsx.load(await workbook.xlsx.writeBuffer());
  assert.equal(restored.getWorksheet("Smoke").getCell("A1").value, "ok");
})().catch((error) => { console.error(error); process.exitCode = 1; });

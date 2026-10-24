'use strict';
// RDC 0.2.52 calls os.hostname() for its displayed device name.
// Override only in the RDC Node process; never change Android hostname.
const os = require('node:os');
const cp = require('node:child_process');
const original = os.hostname.bind(os);
function model() {
  if (process.platform !== 'android' && !process.env.PREFIX?.includes('com.termux')) return null;
  try {
    const name = cp.execFileSync('/system/bin/getprop', ['ro.product.model'], {encoding:'utf8',timeout:1500,stdio:['ignore','pipe','ignore']}).trim();
    if (!name || name.length > 64 || /[\x00-\x1f\x7f]/.test(name)) return null;
    return name;
  } catch { return null; }
}
const name = model();
if (name) os.hostname = () => name;

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

test('handoff contains only verification URL and expiry, never provider secrets', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'rdc-handoff-'));
  const target = path.join(directory, 'handoff.json');
  const original = globalThis.fetch;
  process.env.ALICE_RDC_PAIRING_FILE = target;
  let payload = {
    verification_uri_complete: 'https://mcp.desktopcommander.app/device/verify?code=test',
    expires_in: 900,
    device_code: 'private-device-code',
    code_verifier: 'private-pkce',
  };
  globalThis.fetch = async () => new Response(JSON.stringify(payload));
  try {
    require('./pairing-handoff.cjs');
    const start = 'https://mcp.desktopcommander.app/device/start';
    const response = await fetch(start);
    assert.equal((await response.json()).device_code, 'private-device-code');
    const handoff = JSON.parse(fs.readFileSync(target, 'utf8'));
    assert.deepEqual(Object.keys(handoff).sort(), ['expires_at', 'verification_uri_complete']);
    assert.equal(handoff.verification_uri_complete, payload.verification_uri_complete);
    assert.ok(handoff.expires_at <= Date.now() / 1000 + 600);
    assert.equal(fs.statSync(target).mode & 0o777, 0o640);
    const validStart = { ...payload };
    payload = { verification_uri_complete: 'https://evil.example/', expires_in: 60 };
    await fetch(start);
    assert.equal(fs.existsSync(target), false);
    payload = validStart;
    await fetch(start);
    payload = { error_description: 'Denied' };
    await fetch('https://mcp.desktopcommander.app/device/poll');
    assert.equal(fs.existsSync(target), false);
    payload = validStart;
    await fetch(start);
    payload = { error: 'authorization_pending' };
    await fetch('https://mcp.desktopcommander.app/device/poll');
    assert.ok(fs.existsSync(target));
    payload = { access_token: 'private-access-token', refresh_token: 'private-refresh-token' };
    await fetch('https://mcp.desktopcommander.app/device/poll');
    assert.equal(fs.existsSync(target), false);
    payload = { verification_uri_complete: 'https://evil.example/', expires_in: 60 };
    await fetch(start);
    assert.equal(fs.existsSync(target), false);
  } finally {
    globalThis.fetch = original;
    delete process.env.ALICE_RDC_PAIRING_FILE;
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

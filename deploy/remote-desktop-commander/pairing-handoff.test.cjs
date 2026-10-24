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

test('auth observer reports commit only after atomic rename without reading or sending tokens', async () => {
  const { observeAuthWrites } = require('./pairing-handoff.cjs');
  const events = [];
  const calls = [];
  const api = Object.fromEntries(['mkdir', 'writeFile', 'rename', 'rm'].map((method) => [method, async (...args) => {
    calls.push({ method, args });
  }]));
  observeAuthWrites(api, (event) => events.push(event));
  const file = '/home/node/.desktop-commander-device/device.json';
  await api.mkdir(path.dirname(file), { recursive: true });
  await api.writeFile(file + '.123.tmp', 'synthetic-private-token');
  assert.deepEqual(events, []);
  await api.rename(file + '.123.tmp', file);
  assert.deepEqual(events, [{ type: 'rdc-auth-committed' }]);
  assert.equal(calls.length, 3);
  await api.rename('/workspace/other.tmp', '/workspace/other.json');
  assert.equal(events.length, 1);
  await api.rm(file, { force: true });
  assert.deepEqual(events[1], { type: 'rdc-auth-committed' });
});

test('auth observer surfaces upstream-suppressed mkdir, write and rename errors as fixed events', async () => {
  const { observeAuthWrites } = require('./pairing-handoff.cjs');
  const file = '/home/node/.desktop-commander-device/device.json';
  for (const [method, args] of [
    ['mkdir', [path.dirname(file)]],
    ['writeFile', [file + '.123.tmp', 'synthetic-private-token']],
    ['rename', [file + '.123.tmp', file]],
    ['rm', [file]],
  ]) {
    const events = [];
    const error = new Error('synthetic-private-provider-error');
    const api = Object.fromEntries(['mkdir', 'writeFile', 'rename', 'rm'].map((entry) => [entry, async () => {
      if (entry === method) throw error;
    }]));
    observeAuthWrites(api, (event) => events.push(event));
    await assert.rejects(api[method](...args), error);
    assert.deepEqual(events, [{ type: 'rdc-auth-write-failed' }]);
    assert(!JSON.stringify(events).includes('synthetic-private'));
    await assert.rejects(api[method]('/workspace/other'), error);
    assert.equal(events.length, 1);
  }
});

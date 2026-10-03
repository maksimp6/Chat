// Observe only the public verification handoff; PKCE and tokens stay in RDC.
const fs = require('node:fs');
const path = require('node:path');
const promises = require('node:fs/promises');

const target = process.env.ALICE_RDC_PAIRING_FILE;
const trusted = new Set(['mcp.desktopcommander.app', 'auth.desktopcommander.app']);
function trustedUrl(value) {
  try {
    const url = new URL(value);
    return !/[\s\x00-\x1f\x7f]/.test(value) && url.protocol === 'https:' && trusted.has(url.hostname) &&
      !url.username && !url.password && !url.hash && (!url.port || url.port === '443');
  } catch {
    return false;
  }
}
function clear() {
  if (target) fs.rmSync(target, { force: true });
}
function observeAuthWrites(api, notify, authFile = '/home/node/.desktop-commander-device/device.json') {
  const authDirectory = path.dirname(authFile);
  const relevant = (value) => typeof value === 'string' &&
    (value === authFile || value.startsWith(authFile + '.') || value === authDirectory);
  for (const method of ['mkdir', 'writeFile', 'rename', 'rm']) {
    const original = api[method];
    api[method] = async function (...args) {
      const observed = relevant(args[0]) || (method === 'rename' && relevant(args[1]));
      try {
        const result = await original.apply(this, args);
        if ((method === 'rename' && args[1] === authFile) || (method === 'rm' && args[0] === authFile)) {
          notify({ type: 'rdc-auth-committed' });
        }
        return result;
      } catch (error) {
        if (observed) notify({ type: 'rdc-auth-write-failed' });
        throw error;
      }
    };
  }
}
// The pinned RDC writes through fs/promises and suppresses persistence errors.
// IPC carries fixed event names only; the parent journals the committed file.
if (process.env.ALICE_RDC_MODE === 'cloud-rdc' && typeof process.send === 'function') {
  observeAuthWrites(promises, (message) => {
    if (process.connected) process.send(message, () => {});
  });
}
if (target) {
  clear();
  const upstreamFetch = globalThis.fetch;
  globalThis.fetch = async function (...args) {
    const response = await upstreamFetch.apply(this, args);
    const raw = args[0] instanceof Request ? args[0].url : String(args[0]);
    const endpoint = new URL(raw);
    if (trustedUrl(raw) && ['/device/start', '/device/poll'].includes(endpoint.pathname)) {
      if (endpoint.pathname === '/device/start') clear();
      const data = await response.clone().json().catch(() => null);
      if (endpoint.pathname === '/device/start' && response.ok && data &&
          typeof data.verification_uri_complete === 'string' &&
          trustedUrl(data.verification_uri_complete) &&
          Number.isFinite(data.expires_in) && data.expires_in > 0) {
        fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o750 });
        const temporary = target + '.tmp';
        fs.writeFileSync(temporary, JSON.stringify({
          verification_uri_complete: data.verification_uri_complete,
          expires_at: Date.now() / 1000 + Math.min(data.expires_in, 600),
        }), { mode: 0o640 });
        fs.chmodSync(temporary, 0o640);
        fs.renameSync(temporary, target);
      } else if (endpoint.pathname === '/device/poll' && response.status < 500 && data &&
                 (data.access_token || !['authorization_pending', 'slow_down'].includes(data.error))) {
        clear();
      }
    }
    return response;
  };
}
module.exports = { trustedUrl, observeAuthWrites };

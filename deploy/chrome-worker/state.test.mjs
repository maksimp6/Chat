import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import * as fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { gzipSync } from "node:zlib";
import { createChromeStateStore } from "./state.mjs";

async function fixture(t) {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), "chrome-state-test-"));
  t.after(() => fs.rm(base, { recursive: true, force: true }));
  const stateDir = path.join(base, "durable");
  const profileDir = path.join(base, "profile");
  const authDir = path.join(base, "auth");
  await fs.mkdir(stateDir);
  const options = { stateDir, profileDir, authDir, requireMount: false };
  const store = createChromeStateStore(options);
  await store.restore();
  return { base, stateDir, profileDir, authDir, options, store };
}

async function coldStart(value) {
  await fs.rm(value.profileDir, { recursive: true, force: true });
  await fs.rm(value.authDir, { recursive: true, force: true });
  const store = createChromeStateStore(value.options);
  await store.restore();
  return store;
}

async function maliciousArchive(value, entries) {
  const archive = gzipSync(entries.map((entry) => JSON.stringify(entry)).join("\n") + "\n");
  await fs.writeFile(path.join(value.stateDir, "profile-0.ndjson.gz"), archive);
  await fs.writeFile(path.join(value.stateDir, "profile-0.manifest.json"), JSON.stringify({
    schema: 1, kind: "profile", generation: 1, size: archive.length,
    sha256: createHash("sha256").update(archive).digest("hex"),
  }));
}

test("a closed profile, empty directories and OAuth survive a fresh local filesystem", async (t) => {
  const value = await fixture(t);
  const cookies = Buffer.alloc(180000, 143);
  await fs.mkdir(path.join(value.profileDir, "Default", "Empty"), { recursive: true });
  await fs.writeFile(path.join(value.profileDir, "Default", "Cookies"), cookies);
  await fs.writeFile(path.join(value.profileDir, "Local State"), '{"os_crypt":{}}');
  await fs.writeFile(path.join(value.authDir, "oauth.json"), '{"refresh":"test-only"}');
  assert.equal((await value.store.checkpoint()).generation, 1);
  const restored = await coldStart(value);
  assert.deepEqual(await fs.readFile(path.join(value.profileDir, "Default", "Cookies")), cookies);
  assert.equal(await fs.readFile(path.join(value.authDir, "oauth.json"), "utf8"), '{"refresh":"test-only"}');
  assert.deepEqual(await fs.readdir(path.join(value.profileDir, "Default", "Empty")), []);
  assert.equal((await fs.stat(path.join(value.profileDir, "Default", "Cookies"))).mode & 0o777, 0o600);
  assert.deepEqual(restored.status(), { enabled: true, restored: true, generation: 1, authGeneration: 1, lastError: null });
});

test("OAuth checkpoints never read the live browser profile", async (t) => {
  const value = await fixture(t);
  await fs.symlink("/etc/passwd", path.join(value.profileDir, "live-profile"));
  await fs.writeFile(path.join(value.authDir, "oauth.json"), "durable refresh token");
  const result = await value.store.checkpointAuth();
  assert.equal(result.generation, 0);
  assert.equal(result.authGeneration, 1);
  await coldStart(value);
  assert.equal(await fs.readFile(path.join(value.authDir, "oauth.json"), "utf8"), "durable refresh token");
});

test("Chrome singleton locks are omitted while other symlinks are rejected", async (t) => {
  const value = await fixture(t);
  await fs.symlink("/tmp/nonexistent-socket", path.join(value.profileDir, "SingletonSocket"));
  await fs.writeFile(path.join(value.profileDir, "DevToolsActivePort"), "9222");
  await value.store.checkpoint();
  await coldStart(value);
  assert.deepEqual(await fs.readdir(value.profileDir), []);
  await fs.symlink("/etc/passwd", path.join(value.profileDir, "Cookies"));
  const current = createChromeStateStore({ ...value.options, stateDir: path.join(value.base, "another") });
  await fs.mkdir(path.join(value.base, "another"));
  await current.restore();
  await assert.rejects(current.checkpoint(), /chrome_state_unsafe_local_state/);
});

test("an interrupted overwrite keeps the prior committed snapshot recoverable", async (t) => {
  const value = await fixture(t);
  const cookiePath = path.join(value.profileDir, "Cookies");
  await fs.writeFile(cookiePath, "first checkpoint");
  await value.store.checkpoint();
  await fs.writeFile(cookiePath, "second checkpoint");
  await value.store.checkpoint();
  await fs.writeFile(path.join(value.stateDir, "profile-1.ndjson.gz"), "partial upload");
  const restored = await coldStart(value);
  assert.equal(restored.status().generation, 1);
  assert.equal(await fs.readFile(cookiePath, "utf8"), "first checkpoint");
});

test("no valid committed archive fails closed instead of creating an empty profile", async (t) => {
  const value = await fixture(t);
  await fs.writeFile(path.join(value.stateDir, "profile-0.ndjson.gz"), "uncommitted");
  await assert.rejects(coldStart(value), /chrome_state_no_valid_snapshot/);
});

test("restore rejects path traversal before touching either live state root", async (t) => {
  const value = await fixture(t);
  await maliciousArchive(value, [
    { schema: 1, kind: "profile" },
    { type: "file", path: "../outside", size: 0 },
    { type: "end", count: 1, bytes: 0 },
  ]);
  await assert.rejects(coldStart(value), /chrome_state_unsafe_archive/);
  await assert.rejects(fs.stat(path.join(value.base, "outside")), { code: "ENOENT" });
  await assert.rejects(fs.stat(value.profileDir), { code: "ENOENT" });
});

test("restore rejects duplicate paths and archives without a completion record", async (t) => {
  for (const entries of [
    [{ type: "file", path: "Cookies", size: 0 }, { type: "file", path: "Cookies", size: 0 }, { type: "end", count: 2, bytes: 0 }],
    [{ type: "file", path: "Cookies", size: 0 }],
  ]) {
    const value = await fixture(t);
    await maliciousArchive(value, [{ schema: 1, kind: "profile" }, ...entries]);
    await assert.rejects(coldStart(value), /chrome_state_(unsafe|incomplete)_archive/);
  }
});

test("existing local data is never overwritten during restore", async (t) => {
  const value = await fixture(t);
  await fs.writeFile(path.join(value.profileDir, "Cookies"), "saved");
  await value.store.checkpoint();
  await fs.writeFile(path.join(value.profileDir, "Cookies"), "unsaved local");
  await assert.rejects(createChromeStateStore(value.options).restore(), /chrome_state_local_state_not_empty/);
  assert.equal(await fs.readFile(path.join(value.profileDir, "Cookies"), "utf8"), "unsaved local");
});

test("the live replica adopts a newer generation written by an overlapping one", async (t) => {
  const value = await fixture(t);
  const other = createChromeStateStore({ ...value.options,
    profileDir: path.join(value.base, "other-profile"), authDir: path.join(value.base, "other-auth"),
  });
  await other.restore();
  await value.store.checkpoint();
  await fs.writeFile(path.join(value.base, "other-profile", "Cookies"), "live replica");
  await other.checkpoint();
  assert.equal(other.status().generation, 2);
  await other.checkpoint();
  assert.equal(other.status().lastError, null);
  const fresh = createChromeStateStore({ ...value.options,
    profileDir: path.join(value.base, "fresh-profile"), authDir: path.join(value.base, "fresh-auth"),
  });
  await fresh.restore();
  assert.equal(await fs.readFile(path.join(value.base, "fresh-profile", "Cookies"), "utf8"), "live replica");
});

test("same-process auth checkpoints are serialized", async (t) => {
  const value = await fixture(t);
  await fs.writeFile(path.join(value.authDir, "oauth.json"), "state");
  const results = await Promise.all([value.store.checkpointAuth(), value.store.checkpointAuth()]);
  assert.deepEqual(results.map((item) => item.authGeneration), [1, 2]);
});

test("hardlinked source files and symlinked root directories are rejected", async (t) => {
  const value = await fixture(t);
  await fs.writeFile(path.join(value.base, "source"), "private");
  await fs.link(path.join(value.base, "source"), path.join(value.profileDir, "linked"));
  await assert.rejects(value.store.checkpoint(), /chrome_state_unsafe_local_state/);
  await fs.rm(value.profileDir, { recursive: true });
  await fs.symlink(value.authDir, value.profileDir);
  await assert.rejects(value.store.checkpoint(), /chrome_state_unsafe_directory/);
});

test("rejecting a hardlinked durable destination never truncates its target", async (t) => {
  const value = await fixture(t);
  await value.store.checkpoint();
  const original = path.join(value.base, "must-not-truncate");
  await fs.writeFile(original, "keep this file intact");
  await fs.link(original, path.join(value.stateDir, "profile-1.ndjson.gz"));
  await assert.rejects(value.store.checkpoint(), /chrome_state_unsafe_file/);
  assert.equal(await fs.readFile(original, "utf8"), "keep this file intact");
});

test("archive content size is bounded before allocation or restore", async (t) => {
  const value = await fixture(t);
  await maliciousArchive(value, [
    { schema: 1, kind: "profile" },
    { type: "file", path: "Cookies", size: 512 * 1024 * 1024 + 1 },
  ]);
  await assert.rejects(coldStart(value), /chrome_state_too_large/);
});

test("mount validation and overlapping roots fail before state access", async (t) => {
  const value = await fixture(t);
  assert.throws(() => createChromeStateStore({ ...value.options, profileDir: value.stateDir }), /chrome_state_overlapping_paths/);
  await assert.rejects(createChromeStateStore({ ...value.options, requireMount: true }).restore(), /chrome_state_not_mounted/);
  await assert.rejects(createChromeStateStore(value.options).checkpoint(), /chrome_state_not_restored/);
});

test("persistence is optional for local development", async () => {
  const store = createChromeStateStore({ stateDir: "" });
  assert.equal((await store.restore()).enabled, false);
  assert.equal((await store.checkpoint()).generation, 0);
});

test("status exposes only a fixed code for the last checkpoint failure", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "chrome-state-error-"));
  const store = createChromeStateStore({ stateDir: path.join(root, "durable"), profileDir: path.join(root, "profile"), authDir: path.join(root, "auth") });
  await assert.rejects(store.checkpoint(), /chrome_state_/);
  assert.match(store.status().lastError, /^chrome_state_[a-z_]+(:E[A-Z]+)?$/);
  await fs.mkdir(path.join(root, "durable"), { recursive: true });
  await store.restore();
  await store.checkpoint();
  assert.equal(store.status().lastError, null);
});

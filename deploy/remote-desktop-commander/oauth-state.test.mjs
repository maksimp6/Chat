import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createOAuthStateStore } from "./oauth-state.mjs";

const origin = "https://alice-dev.example.test";
const ownerId = "owner";
const state = (client) => ({ version: 1, signingKey: "synthetic-signing-key", clients: { [client]: {} }, pending: {}, codes: {}, access: {}, refresh: {} });

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "oauth-state-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateDir = join(root, "durable");
  await mkdir(stateDir);
  await writeFile(join(stateDir, "owner.json"), JSON.stringify({ schema: 1, purpose: "alice-dev-oauth", origin, owner_id: ownerId }));
  const make = (name, extra = {}) => createOAuthStateStore({ stateDir, stateFile: join(root, name, "oauth.json"), origin, ownerId, requireMount: false, ...extra });
  return { root, stateDir, make };
}

async function save(root, name, value) {
  await mkdir(join(root, name), { recursive: true });
  await writeFile(join(root, name, "oauth.json"), JSON.stringify(value), { mode: 0o600 });
}

test("a new local filesystem restores the last acknowledged OAuth state", async (t) => {
  const { root, make } = await fixture(t);
  const first = make("first");
  await first.restore();
  await save(root, "first", state("client-a"));
  await first.persist();
  await save(root, "first", state("client-b"));
  await first.persist();
  await make("replacement").restore();
  assert.deepEqual(JSON.parse(await readFile(join(root, "replacement", "oauth.json"))), state("client-b"));
});

test("an observed stale writer cannot overwrite a newer generation", async (t) => {
  const { root, make } = await fixture(t);
  const first = make("first");
  const stale = make("stale");
  await first.restore();
  await stale.restore();
  await save(root, "first", state("current"));
  await first.persist();
  await save(root, "stale", state("stale"));
  await assert.rejects(stale.persist(), /oauth_state_/);
  await make("replacement").restore();
  assert.deepEqual(JSON.parse(await readFile(join(root, "replacement", "oauth.json"))), state("current"));
});

test("interrupted commit refuses old-token rollback even with a valid previous snapshot", async (t) => {
  const { root, stateDir, make } = await fixture(t);
  const first = make("first");
  await first.restore();
  await save(root, "first", state("a"));
  await first.persist();
  const committed = JSON.parse(await readFile(join(stateDir, "commit.json")));
  await writeFile(join(stateDir, "intent.json"), JSON.stringify({ ...committed, generation: committed.generation + 1 }));
  await assert.rejects(make("replacement").restore(), /oauth_state_/);
});

test("corrupt latest snapshot never falls back to an older valid refresh state", async (t) => {
  const { root, stateDir, make } = await fixture(t);
  const first = make("first");
  await first.restore();
  for (const client of ["a", "b"]) { await save(root, "first", state(client)); await first.persist(); }
  const { generation } = JSON.parse(await readFile(join(stateDir, "commit.json")));
  await writeFile(join(stateDir, `state-${generation % 2}.json`), "broken");
  await assert.rejects(make("replacement").restore(), /oauth_state_/);
});

test("missing marker and wrong origin cannot initialize a new credential store", async (t) => {
  const { stateDir, make } = await fixture(t);
  await assert.rejects(make("wrong", { origin: "https://other.example.test" }).restore(), /oauth_state_/);
  await rm(join(stateDir, "owner.json"));
  await assert.rejects(make("missing").restore(), /oauth_state_/);
});

test("a normal directory is not accepted as the production Object Storage mount", async (t) => {
  const { make } = await fixture(t);
  await assert.rejects(make("first", { requireMount: true }).restore(), /oauth_state_/);
});

test("symlinked state files are rejected without modifying their target", async (t) => {
  const { root, stateDir, make } = await fixture(t);
  const first = make("first");
  await first.restore();
  await save(root, "first", state("a"));
  const target = join(root, "untouched");
  await writeFile(target, "untouched");
  await symlink(target, join(stateDir, "intent.json"));
  await assert.rejects(first.persist(), /oauth_state_/);
  assert.equal(await readFile(target, "utf8"), "untouched");
});

test("disabled local mode does not pretend to provide durable state", async () => {
  const store = createOAuthStateStore({ stateDir: "" });
  assert.equal(store.enabled, false);
  await store.restore();
  await store.persist();
});

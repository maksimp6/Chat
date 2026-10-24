// Single-writer OAuth snapshots on a private managed Object Storage mount.
// The mount needs closed regular files, not POSIX rename/fsync/locks. An intent
// fences incomplete commits: authentication state must NEVER roll back to an
// older valid snapshot, which could revive a spent refresh token or revocation.
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import * as fs from "node:fs/promises";
import path from "node:path";

const MAX_STATE = 32 * 1024 * 1024;
const noFollow = constants.O_NOFOLLOW | constants.O_NONBLOCK;
const fail = (code) => { throw new Error(`oauth_state_${code}`); };
const encode = (value) => Buffer.from(JSON.stringify(value));
const digest = (raw) => createHash("sha256").update(raw).digest("hex");

async function directory(root, create = false) {
  let current = path.parse(root).root;
  for (const part of root.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (create) await fs.mkdir(current, { mode: 0o700 }).catch((error) => {
      if (error.code !== "EEXIST") throw error;
    });
    if (!(await fs.lstat(current)).isDirectory()) fail("unsafe_directory");
  }
}

async function regular(file, flags) {
  const handle = await fs.open(file, flags | noFollow, 0o600);
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.nlink !== 1) fail("unsafe_file");
    return handle;
  } catch (error) { await handle.close(); throw error; }
}

async function read(file, limit) {
  const handle = await regular(file, constants.O_RDONLY);
  try {
    if ((await handle.stat()).size > limit) fail("too_large");
    const raw = await handle.readFile();
    if (raw.length > limit) fail("too_large");
    return raw;
  } finally { await handle.close(); }
}

async function optional(file, limit) {
  try { return await read(file, limit); }
  catch (error) { if (error.code === "ENOENT") return null; throw error; }
}

async function writeClosed(file, raw) {
  // Never truncate until the opened inode has passed the hardlink/type checks.
  const handle = await regular(file, constants.O_WRONLY | constants.O_CREAT);
  try { await handle.truncate(0); await handle.writeFile(raw); }
  finally { await handle.close(); }
  if (!(await read(file, raw.length)).equals(raw)) fail("write_unconfirmed");
}

function manifest(raw) {
  const value = JSON.parse(raw);
  if (!value || Object.keys(value).sort().join() !== "generation,schema,sha256,size"
    || value.schema !== 1 || !Number.isSafeInteger(value.generation) || value.generation < 1
    || !Number.isSafeInteger(value.size) || value.size < 1 || value.size > MAX_STATE
    || !/^[a-f0-9]{64}$/.test(value.sha256)) fail("invalid_manifest");
  return value;
}

function validState(raw) {
  const state = JSON.parse(raw);
  if (!state || state.version !== 1 || typeof state.signingKey !== "string" || !state.signingKey
    || !["clients", "pending", "codes", "access", "refresh"].every((key) =>
      state[key] && typeof state[key] === "object" && !Array.isArray(state[key]))) fail("invalid_snapshot");
}

export function createOAuthStateStore(options = {}) {
  const stateDir = options.stateDir ?? process.env.ALICE_DEV_OAUTH_DURABLE_DIR ?? "";
  if (!stateDir) return { enabled: false, restore: async () => {}, persist: async () => {} };
  const durable = path.resolve(stateDir);
  const local = path.resolve(options.stateFile);
  const owner = { schema: 1, purpose: "alice-dev-oauth", origin: options.origin, owner_id: options.ownerId };
  if (!owner.origin || !owner.owner_id || local === durable || local.startsWith(`${durable}/`)
    || durable.startsWith(`${path.dirname(local)}/`)) fail("invalid_configuration");
  let generation = 0;
  let restored = false;
  let failed = false;
  let queue = Promise.resolve();
  const file = (name) => path.join(durable, name);

  function serialized(operation) {
    const result = queue.then(async () => {
      if (failed) fail("unavailable");
      let timer;
      try {
        // A timed-out mount operation may still finish. Poison this runtime and
        // never retry it concurrently; the intent/commit fence governs recovery.
        return await Promise.race([
          operation(),
          new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error("oauth_state_timeout")), options.timeoutMs ?? 10000);
          }),
        ]);
      } catch { failed = true; fail("unavailable"); }
      finally { clearTimeout(timer); }
    });
    queue = result.catch(() => {});
    return result;
  }

  async function validate() {
    await directory(durable);
    if (options.requireMount ?? true) {
      const mountPath = durable.replaceAll("\\", "\\134").replaceAll(" ", "\\040").replaceAll("\t", "\\011").replaceAll("\n", "\\012");
      const mounts = await fs.readFile("/proc/self/mountinfo", "utf8");
      if (!mounts.split("\n").some((line) => line.split(" ")[4] === mountPath)) fail("not_mounted");
    }
    const marker = JSON.parse(await read(file("owner.json"), 4096));
    if (Object.keys(marker).length !== Object.keys(owner).length
      || Object.entries(owner).some(([key, value]) => marker[key] !== value)) fail("ownership_mismatch");
  }

  async function latest() {
    const intentRaw = await optional(file("intent.json"), 4096);
    const commitRaw = await optional(file("commit.json"), 4096);
    if (!intentRaw && !commitRaw) {
      for (const slot of [0, 1]) if (await optional(file(`state-${slot}.json`), MAX_STATE)) fail("uncommitted_state");
      return null;
    }
    if (!intentRaw || !commitRaw) fail("incomplete_commit");
    const intent = manifest(intentRaw);
    const committed = manifest(commitRaw);
    if (Object.keys(intent).some((key) => intent[key] !== committed[key])) fail("incomplete_commit");
    const raw = await read(file(`state-${committed.generation % 2}.json`), MAX_STATE);
    if (raw.length !== committed.size || digest(raw) !== committed.sha256) fail("checksum_mismatch");
    validState(raw);
    return { ...committed, raw };
  }

  async function restore() {
    if (restored) return;
    await validate();
    const snapshot = await latest();
    await directory(path.dirname(local), true);
    if (snapshot) {
      // Only the disposable local POSIX file uses atomic replacement.
      const temporary = `${local}.restore`;
      await writeClosed(temporary, snapshot.raw);
      await fs.chmod(temporary, 0o600);
      await fs.rename(temporary, local);
      generation = snapshot.generation;
    } else if (await optional(local, MAX_STATE)) fail("uncommitted_local_state");
    restored = true;
  }

  async function persist() {
    if (!restored) fail("not_restored");
    await validate();
    const previous = await latest();
    if ((previous?.generation ?? 0) !== generation) fail("stale_writer");
    await directory(path.dirname(local));
    const raw = await read(local, MAX_STATE);
    validState(raw);
    const next = generation + 1;
    if (!Number.isSafeInteger(next)) fail("generation_exhausted");
    const record = encode({ schema: 1, generation: next, size: raw.length, sha256: digest(raw) });
    await writeClosed(file("intent.json"), record);
    await writeClosed(file(`state-${next % 2}.json`), raw);
    await writeClosed(file("commit.json"), record);
    const confirmed = await latest();
    if (confirmed.generation !== next || confirmed.sha256 !== digest(raw)) fail("write_unconfirmed");
    generation = next;
  }

  return { enabled: true, restore: () => serialized(restore), persist: () => serialized(persist) };
}

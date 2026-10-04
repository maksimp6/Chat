// Closed Chrome profiles run on local POSIX storage. The private Object Storage
// mount holds only completed archives and manifests, never live databases. A
// deployment must have one writer and stop/checkpoint it before replacement;
// Object Storage mounts cannot provide a cross-container POSIX lock.
import { createHash } from "node:crypto";
import { constants, createReadStream, createWriteStream } from "node:fs";
import * as fs from "node:fs/promises";
import path from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { createGzip, createGunzip } from "node:zlib";

const MAX_ARCHIVE = 512 * 1024 * 1024;
const MAX_CONTENT = 512 * 1024 * 1024;
const MAX_ENTRIES = 20000;
const CHUNK = 64 * 1024;
const MAX_LINE = 96 * 1024;
const MAX_EXPANDED = MAX_CONTENT * 1.4 + MAX_ENTRIES * 8192;
const noFollow = constants.O_NOFOLLOW | constants.O_NONBLOCK;
const transient = (name) => name.startsWith("Singleton") || name === "DevToolsActivePort";
const encode = (value) => `${JSON.stringify(value)}\n`;
const fail = (code) => { throw Object.assign(new Error(`chrome_state_${code}`), { stateError: true }); };

async function exists(file) {
  try { await fs.lstat(file); return true; }
  catch (error) { if (error.code === "ENOENT") return false; throw error; }
}

async function directory(root, create = false) {
  let current = path.parse(root).root;
  for (const component of root.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    if (create) await fs.mkdir(current, { mode: 0o700 }).catch((error) => {
      if (error.code !== "EEXIST") throw error;
    });
    if (!(await fs.lstat(current)).isDirectory()) fail("unsafe_directory");
  }
}

async function openRegular(file, flags, mode = 0o600) {
  // Validate the opened inode before truncation: O_TRUNC at open time could
  // damage a hardlinked target before the nlink check rejects it.
  const handle = await fs.open(file, (flags & ~constants.O_TRUNC) | noFollow, mode);
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.nlink !== 1) fail("unsafe_file");
    if (flags & constants.O_TRUNC) await handle.truncate(0);
    return handle;
  } catch (error) { await handle.close(); throw error; }
}

async function fingerprint(file, limit) {
  const handle = await openRegular(file, constants.O_RDONLY);
  try {
    const before = await handle.stat();
    if (before.size > limit) fail("too_large");
    const hash = createHash("sha256");
    let size = 0;
    for await (const block of handle.createReadStream({ autoClose: false })) {
      size += block.length;
      if (size > limit) fail("too_large");
      hash.update(block);
    }
    const after = await handle.stat();
    if (size !== before.size || before.size !== after.size || before.mtimeMs !== after.mtimeMs) fail("changed");
    return { size, sha256: hash.digest("hex") };
  } finally { await handle.close(); }
}

async function readSmall(file) {
  const handle = await openRegular(file, constants.O_RDONLY);
  try {
    if ((await handle.stat()).size > 4096) fail("invalid_manifest");
    const value = await handle.readFile();
    if (value.length > 4096) fail("invalid_manifest");
    return JSON.parse(value);
  } finally { await handle.close(); }
}

function validPath(name) {
  if (typeof name !== "string" || Buffer.byteLength(name) > 4096 || /[\\\x00-\x1f\x7f]/u.test(name)) fail("unsafe_archive");
  if (!name || name.split("/").some((part) => !part || part === "." || part === "..")) fail("unsafe_archive");
  return name;
}

async function* archiveTree(root, kind) {
  let count = 0;
  let total = 0;
  yield encode({ schema: 1, kind });
  async function* walk(base, prefix = "") {
    await directory(base);
    for (const name of (await fs.readdir(base)).sort()) {
      if (kind === "profile" && transient(name)) continue;
      const relative = validPath(prefix ? `${prefix}/${name}` : name);
      const file = path.join(base, name);
      const stat = await fs.lstat(file);
      if (++count > MAX_ENTRIES) fail("too_large");
      if (stat.isDirectory()) {
        yield encode({ type: "directory", path: relative });
        yield* walk(file, relative);
      } else if (stat.isFile() && stat.nlink === 1) {
        total += stat.size;
        if (total > MAX_CONTENT) fail("too_large");
        const handle = await openRegular(file, constants.O_RDONLY);
        try {
          const before = await handle.stat();
          if (before.ino !== stat.ino || before.dev !== stat.dev || before.size !== stat.size) fail("changed");
          yield encode({ type: "file", path: relative, size: before.size });
          let copied = 0;
          for await (const block of handle.createReadStream({ autoClose: false, highWaterMark: CHUNK })) {
            copied += block.length;
            if (copied > before.size) fail("changed");
            yield encode({ type: "data", data: block.toString("base64") });
          }
          const after = await handle.stat();
          if (copied !== before.size || before.size !== after.size || before.mtimeMs !== after.mtimeMs) fail("changed");
        } finally { await handle.close(); }
      } else fail("unsafe_local_state");
    }
  }
  yield* walk(root);
  yield encode({ type: "end", count, bytes: total });
}

function bounded(limit) {
  let size = 0;
  return new Transform({ transform(block, _encoding, done) {
    size += block.length;
    if (size > limit) done(Object.assign(new Error("chrome_state_too_large"), { stateError: true }));
    else done(null, block);
  } });
}

async function* lines(stream) {
  let pending = Buffer.alloc(0);
  for await (const chunk of stream) {
    pending = Buffer.concat([pending, chunk]);
    let index;
    while ((index = pending.indexOf(10)) !== -1) {
      if (index > MAX_LINE) fail("unsafe_archive");
      yield JSON.parse(pending.subarray(0, index).toString("utf8"));
      pending = pending.subarray(index + 1);
    }
    if (pending.length > MAX_LINE) fail("unsafe_archive");
  }
  if (pending.length) fail("incomplete_archive");
}

function keys(value, expected) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join(",") === expected.split(",").sort().join(",");
}

async function unpack(file, root, kind) {
  const input = await openRegular(file, constants.O_RDONLY);
  const expanded = bounded(MAX_EXPANDED);
  const piping = pipeline(input.createReadStream({ autoClose: false }), createGunzip(), expanded);
  // A consumer validation error must not leave the pipeline rejection unhandled.
  piping.catch(() => {});
  let output;
  let remaining = 0;
  let count = 0;
  let total = 0;
  let header = false;
  let end = false;
  const seen = new Map();
  try {
    for await (const value of lines(expanded)) {
      if (end) fail("unsafe_archive");
      if (!header) {
        if (!keys(value, "schema,kind") || value.schema !== 1 || value.kind !== kind) fail("unsafe_archive");
        header = true;
      } else if (remaining) {
        if (!keys(value, "type,data") || value.type !== "data" || typeof value.data !== "string"
          || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(value.data)) fail("unsafe_archive");
        const block = Buffer.from(value.data, "base64");
        if (!block.length || block.length > CHUNK || block.length > remaining) fail("unsafe_archive");
        await output.writeFile(block);
        remaining -= block.length;
        if (!remaining) { await output.close(); output = undefined; }
      } else if (value.type === "end") {
        if (!keys(value, "type,count,bytes") || value.count !== count || value.bytes !== total) fail("unsafe_archive");
        end = true;
      } else {
        if (!keys(value, value.type === "file" ? "type,path,size" : "type,path")
          || !["file", "directory"].includes(value.type)) fail("unsafe_archive");
        const name = validPath(value.path);
        if (seen.has(name) || ++count > MAX_ENTRIES) fail("unsafe_archive");
        const parts = name.split("/");
        if (kind === "profile" && parts.some(transient)) fail("unsafe_archive");
        for (let index = 1; index < parts.length; index++) {
          if (seen.get(parts.slice(0, index).join("/")) !== "directory") fail("unsafe_archive");
        }
        seen.set(name, value.type);
        const destination = path.join(root, ...parts);
        if (value.type === "directory") await fs.mkdir(destination, { mode: 0o700 });
        else {
          if (!Number.isSafeInteger(value.size) || value.size < 0 || (total += value.size) > MAX_CONTENT) fail("too_large");
          output = await openRegular(destination, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL);
          remaining = value.size;
          if (!remaining) { await output.close(); output = undefined; }
        }
      }
    }
    await piping;
    if (!header || !end || remaining) fail("incomplete_archive");
  } finally {
    expanded.destroy();
    await piping.catch(() => {});
    await output?.close();
    await input.close();
  }
}

export function createChromeStateStore(options = {}) {
  const stateDir = options.stateDir ?? process.env.CHROME_STATE_DIR ?? "";
  const roots = {
    profile: path.resolve(options.profileDir ?? process.env.CHROME_PROFILE_DIR ?? "/tmp/chrome-profile"),
    auth: path.resolve(options.authDir ?? "/tmp/chrome-auth"),
  };
  const durable = stateDir ? path.resolve(stateDir) : undefined;
  const enabled = Boolean(durable);
  const generations = { profile: 0, auth: 0 };
  let restored = false;
  let queue = Promise.resolve();
  if (enabled) {
    const paths = [durable, ...Object.values(roots)];
    for (const [index, first] of paths.entries()) for (const second of paths.slice(index + 1)) {
      if (first === second || first.startsWith(`${second}/`) || second.startsWith(`${first}/`)) fail("overlapping_paths");
    }
  }

  // Last failure as a fixed code (plus errno name for I/O), never paths or data,
  // so a deployed worker without log access can still be diagnosed.
  let lastError = null;
  const status = () => ({ enabled, restored, generation: generations.profile, authGeneration: generations.auth, lastError });
  function serialized(operation) {
    const result = queue.then(async () => {
      try { const value = await operation(); lastError = null; return value; }
      catch (error) {
        const errno = typeof error?.code === "string" && /^E[A-Z]+$/.test(error.code) ? `:${error.code}` : "";
        lastError = error.stateError ? error.message : `chrome_state_io_failed${errno}`;
        if (error.stateError) throw error;
        fail("io_failed");
      }
    });
    queue = result.catch(() => {});
    return result;
  }

  async function validate() {
    await directory(durable);
    if (options.requireMount ?? process.env.CHROME_STATE_REQUIRE_MOUNT === "1") {
      const mountPath = durable.replaceAll("\\", "\\134").replaceAll(" ", "\\040").replaceAll("\t", "\\011").replaceAll("\n", "\\012");
      const mounts = await fs.readFile("/proc/self/mountinfo", "utf8");
      if (!mounts.split("\n").some((line) => line.split(" ")[4] === mountPath)) fail("not_mounted");
    }
  }

  const archivePath = (kind, slot) => path.join(durable, `${kind}-${slot}.ndjson.gz`);
  const manifestPath = (kind, slot) => path.join(durable, `${kind}-${slot}.manifest.json`);

  async function latest(kind) {
    const valid = [];
    let present = false;
    for (const slot of [0, 1]) {
      if (!(await exists(archivePath(kind, slot))) && !(await exists(manifestPath(kind, slot)))) continue;
      present = true;
      try {
        const manifest = await readSmall(manifestPath(kind, slot));
        if (!keys(manifest, "schema,kind,generation,size,sha256") || manifest.schema !== 1 || manifest.kind !== kind
          || !Number.isSafeInteger(manifest.generation) || manifest.generation < 1
          || !Number.isSafeInteger(manifest.size) || manifest.size < 1 || manifest.size > MAX_ARCHIVE
          || typeof manifest.sha256 !== "string" || !/^[a-f0-9]{64}$/u.test(manifest.sha256)) fail("invalid_manifest");
        const actual = await fingerprint(archivePath(kind, slot), MAX_ARCHIVE);
        if (actual.size !== manifest.size || actual.sha256 !== manifest.sha256) fail("checksum_mismatch");
        valid.push({ slot, ...manifest });
      } catch { /* The other committed slot survives an interrupted write. */ }
    }
    if (present && !valid.length) fail("no_valid_snapshot");
    valid.sort((first, second) => second.generation - first.generation);
    if (valid.length === 2 && valid[0].generation === valid[1].generation) fail("multiple_writers");
    return valid[0];
  }

  async function restore() {
    if (restored) return status();
    if (enabled) {
      await validate();
      // Validate/extract every archive privately before installing either root.
      const staged = [];
      try {
        for (const [kind, root] of Object.entries(roots)) {
          const snapshot = await latest(kind);
          await directory(path.dirname(root), true);
          if (await exists(root)) {
            await directory(root);
            if (snapshot && (await fs.readdir(root)).length) fail("local_state_not_empty");
          }
          if (!snapshot) { await directory(root, true); continue; }
          const staging = await fs.mkdtemp(path.join(path.dirname(root), ".chrome-restore-"));
          staged.push({ kind, root, staging, snapshot });
          await unpack(archivePath(kind, snapshot.slot), staging, kind);
        }
        for (const item of staged) {
          if (await exists(item.root)) await fs.rmdir(item.root);
          await fs.rename(item.staging, item.root); // Local POSIX storage only.
          item.installed = true;
        }
        for (const item of staged) generations[item.kind] = item.snapshot.generation;
      } catch (error) {
        // Only our validated roots were installed. Return them to private
        // staging so a failed second rename cannot leave a partial restore.
        for (const item of staged.filter((entry) => entry.installed).reverse()) {
          await fs.rename(item.root, item.staging);
        }
        throw error;
      } finally {
        for (const item of staged) await fs.rm(item.staging, { recursive: true, force: true });
      }
    }
    restored = true;
    return status();
  }

  async function checkpointKind(kind) {
    if (!enabled) return;
    if (!restored) fail("not_restored");
    await validate();
    const root = roots[kind];
    await directory(root, true);
    const previous = await latest(kind);
    if ((previous?.generation ?? 0) !== generations[kind]) fail("multiple_writers");
    const generation = (previous?.generation ?? 0) + 1;
    if (!Number.isSafeInteger(generation)) fail("generation_exhausted");
    const slot = previous ? 1 - previous.slot : 0;
    const temporary = await fs.mkdtemp(path.join(path.dirname(root), ".chrome-checkpoint-"));
    try {
      const local = path.join(temporary, "snapshot.ndjson.gz");
      await pipeline(Readable.from(archiveTree(root, kind)), createGzip(), bounded(MAX_ARCHIVE), createWriteStream(local, { flags: "wx", mode: 0o600 }));
      const identity = await fingerprint(local, MAX_ARCHIVE);
      // Write and close archive before manifest. No rename, fsync, flock or live
      // browser files are used on the Object Storage mount.
      const archive = await openRegular(archivePath(kind, slot), constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC);
      try { await pipeline(createReadStream(local), archive.createWriteStream()); }
      finally { await archive.close(); }
      const copied = await fingerprint(archivePath(kind, slot), MAX_ARCHIVE);
      if (copied.sha256 !== identity.sha256 || copied.size !== identity.size) fail("write_failed");
      const metadata = { schema: 1, kind, generation, ...identity };
      const manifest = await openRegular(manifestPath(kind, slot), constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC);
      try { await manifest.writeFile(encode(metadata)); }
      finally { await manifest.close(); }
      const committed = await latest(kind);
      if (committed?.generation !== generation || committed.slot !== slot) fail("write_failed");
      generations[kind] = generation;
    } finally { await fs.rm(temporary, { recursive: true, force: true }); }
  }

  return {
    status,
    restore: () => serialized(restore),
    checkpointAuth: () => serialized(async () => { await checkpointKind("auth"); return status(); }),
    // Caller MUST close Chrome first; the module never reads a live profile.
    checkpoint: () => serialized(async () => {
      await checkpointKind("profile");
      await checkpointKind("auth");
      return status();
    }),
  };
}

# Memory DB Core — local functional contract

Status: **locally accepted for the minimal API** (`get / set / commit`).
Scope: standalone `memory_engine.MemoryStore`, not Alice runtime integration, CI approval, or permission to merge.

## Public usage

```python
from memory_engine import MemoryStore

with MemoryStore("alice.memory") as db:
    db.set("greeting", {"text": "Привет, 你好 🌍"})
    assert db.get("greeting") == {"text": "Привет, 你好 🌍"}
    sequence = db.commit()
```

- `set(name, value)` stages a deep copy in RAM; it does **not** make a durable write.
- `get(name)` reads staged values first, otherwise confirmed state; returns a copy.
- `commit()` writes a journal frame and synchronizes it before acknowledging its sequence. Reopen to read confirmed data.
- An empty name or `None` as a top-level value is rejected. Missing names return `None`.
- The store owns a single-writer file lock. Close it to release ownership.
- Unsupported scalar JSON values may stage but fail at `commit()`; a failed validation does not acknowledge a commit. Correct the staged value and retry.
- The journal is newline-delimited JSON with a chained digest, intended for accidental corruption detection, not adversarial tamper protection.

## Bounds in the current implementation

| Bound | Value | Meaning |
| --- | --- | --- |
| Journal frame | 8 MiB | Serialized JSON, metadata, digest and newline together; enforced on write and replay |
| Text budget | 8 MiB | Sum of UTF-8 bytes in value strings and dictionary keys, per validated value |
| Depth | 64 | Maximum nesting depth of a value |
| Nodes | 100,000 | Traversed elements, including dictionary keys |
| Cycles | Rejected | Shared references without cycles are permitted |

Values larger than one frame, including attachments, must be stored separately. JSON escaping can make a frame exceed the text budget. Limits are implementation constants, not configurable public API parameters.

## Known limitations and failure behavior

- These are **logical bounds**, not a hard process-RSS limit. `deepcopy`, JSON encoding, frame assembly, and journal replay can use additional RAM. `JSONEncoder.iterencode()` can yield an already-large string chunk before the code slices it.
- Python `tuple` is serialized as a JSON array. A staged tuple can be returned as a tuple before commit, but after reopening it is a `list`. Do not rely on tuple type preservation.
- Some invalid scalar types are rejected only at `commit()`, not `set()`.
- A partial trailing journal frame is not silently truncated; recovery requires explicit handling.
- `backup()` does not publish atomically: a crashed copy may leave a partial destination. A backup file's existence is not proof of validity. `restore()` verifies journal integrity.
- This component does not switch Alice's existing `FileMemoryDB` or migrate its files. See `MEMORY_DB_BOUNDARIES.md`.

## Verification and handoff

On Redmi 9, the last completed local run before this documentation update reported **98 passed** for the 13 standalone test modules. This is evidence for the local worktree only, not GitHub Actions on the eventual PR SHA.

Minimal `get / set / commit` was explicitly accepted by the owner locally. New bounds are tested but have not received separate owner acceptance. CI, Alice integration, and merge authorization are separate tasks. No push, commit, or merge is implied by this document.

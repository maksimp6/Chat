# Alice Pro — Local 3D Model Editor (MVP)

Related: [engineering #1079](https://github.com/maksimp6/Chat/issues/1079),
[Model API #1097](https://github.com/maksimp6/Chat/issues/1097),
[printer/slicer #1096](https://github.com/maksimp6/Chat/issues/1096).

## What works

The editor registers six **local** Universal Tools in Alice's existing `3d` category
(`tool_registry.py`). They use the same Responses API, local-agent, and MCP
transports and normal Invocation/Execution Trace pipeline as other local tools.

| Tool | Operation | Writes data |
| --- | --- | --- |
| `model.create` | New empty scene | Immutable scene revision |
| `model.addcube` | New or existing scene; cubic primitive, default 20 mm | New revision, STL |
| `model.addcylinder` | Circular primitive, default 20 x 20 mm, 64 segments | New revision, STL |
| `model.edit` | `move`, `rotate`, `scale`, `delete` one selected object | New revision, STL |
| `model.inspect` | Read exact revision, objects, dimensions, raw volume estimate | No |
| `model.export` | Export exact revision as binary STL | Content-addressed STL |

All dimensions/positions are millimeters. For existing scenes, both `model_id`
and `revision` are required. Omitting **both** from `model.addcube` or
`model.addcylinder` creates a new editable scene. All tool results include an
exact `model_id`, `revision`, scene inspection, and (for nonempty scenes)
`artifact` with storage reference, SHA-256 and size.

Example series of Universal tool calls:

```json
{"name":"model.addcube","arguments":{"size_mm":20}}
{"name":"model.addcylinder","arguments":{"model_id":"<UUID from prior response>","revision":"<SHA256>","diameter_mm":52,"height_mm":20}}
{"name":"model.edit","arguments":{"model_id":"<UUID>","revision":"<SHA256>","object_id":"<UUID>","operation":"move","values":[10,0,0]}}
{"name":"model.inspect","arguments":{"model_id":"<UUID>","revision":"<SHA256>"}}
```

## Persistence, security and verification

Scenes are JSON, immutable and content-addressed by revision SHA-256:
`model_editor/<hash of trusted owner and runtime>/<uuid>/revisions/<sha256>.json`.
Exports are under `.../exports/<revision>.stl`. The storage provider is
resolved via `storage_provider_from_env`: local `data/storage` by default,
`ALICE_STORAGE_LOCAL_ROOT` for a mounted persistent volume, or the existing
Cloud.ru storage provider when configured. **Production must provision durable
storage/backups**; the default container filesystem is not a durability guarantee.

Owner identity comes only from trusted Universal Tool Call context or the
existing authenticated server identity, never model-provided arguments.
Runtime scope comes from trusted invocation metadata; preview/runtime
integration **must** use the existing RuntimeDispatcher boundary.
No user-selected filesystem paths, network URLs, arbitrary Python, imports,
subprocess execution, cloud browsing or printer credentials are accepted.
Names and geometry are bounded, and exported STL and model revisions are
read back and checksum-verified in the storage backend.

Each edit creates a new immutable revision. Old revisions are still available
for inspection/rollback (use that old `revision` for a subsequent edit). A
repeated edit against the same base revision returns the same content address
and does not mutate the previous version.

The actions are low-risk, owner-private local model changes
(`read_only=false` for writes; `requires_approval=false`). Tool access
still goes through UniversalToolExecutor authorization/policy and is traced.
**No printer calls, heating, slicer calls, SD upload, or auto-print.**
Those are owned by #1096 and need separate staging/start gates.

## Explicit MVP limitations

- Supports editable cubes and cylinders, object movement, rotation, scale
  and deletion; **not** arbitrary imported STL/FBX, CadQuery booleans,
  parametric constraints, STEP export, UI viewport, or mesh repair.
- Mesh is an assembly of closed primitive shells. **Intersecting objects
  are not Boolean-unioned**. Summed primitive volume double-counts overlaps.
- A1 mini build-size check is only based on axis-aligned bounding dimensions,
  not wall thickness, physical fit, strength, bed contact or printability.
- A saved STL is **not** a sliced `.gcode.3mf`; slicing and staging
  belong to #1096. Users must not assume a CAD tool has started a print.
- The standalone `scripts/alice_model_addcube.py` remains supported;
  the registry-based tool uses the immutable scene engine instead.

## Local verification

```bash
python -m unittest discover -s tests -p test_model_editor_tools.py -v
python -m unittest discover -s tests -p test_alice_model_addcube.py -v
ruff check model_editor tests/test_model_editor_tools.py
ruff format --check model_editor tests/test_model_editor_tools.py
mypy --config-file pyproject.toml --follow-imports=silent model_editor
```

CI and exact-head review are required before merging into `develop`;
there is no direct push to protected `master`.

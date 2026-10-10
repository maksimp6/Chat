# Dota 2 assets → A1 mini: first reproducible geometry stage

Issue: [#1093](https://github.com/maksimp6/Chat/issues/1093). Parent engineering
integration: [#1079](https://github.com/maksimp6/Chat/issues/1079).

## Responsibilities and status

| Stage | Owner | Current result |
| --- | --- | --- |
| Valve Workshop archive integrity | Asset agent | VERIFIED |
| FBX → GLB | Asset agent | VERIFIED with warnings |
| Named GLB mesh → STL | Asset agent | VERIFIED for \`ember_spirit_head\` |
| Geometry and slicer QA | Independent print QA | BLOCKED |
| Device heating or starting a print | Printer agent | NOT AUTHORIZED |

No printer command is required to run this stage.

## Official test source

Valve Ember Spirit hero reference: https://www.dota2.com/workshop/requirements/ember_spirit?l=english

Direct archive: https://media.steampowered.com/apps/dota2/workshop/ember_spirit.zip?v=11095536

Verified on 2026-10-10:

- ZIP: 31,939,267 bytes, SHA-256
  \`f5456711ed4ba2f595815add7179116d0accecb81d329d041f68dd7d3b1c3f02\`
- Archive integrity: valid, 96 entries
- Contents: \`ember_spirit_econ.fbx\` (858,448 bytes),
  \`ember_spirit_econ.ma\`, and 94 TGA textures
- Original FBX SHA-256:
  \`9ab0f3327471771b1c5afc306f8e3afe91753d17768e35394178cf791e842b28\`
- Converted GLB: 353,720 bytes; local conversion verified using
  \`fbx2gltf@0.9.7-p1\`.

The \`fbx2gltf\` precompiled converter bundles Autodesk FBX SDK technology;
evaluate its license before distributing the converter. It logged warnings
about missing textures and unusual joint transforms. Neither is silently
treated as proof of print-safe geometry.

## Reproduce (Linux x86_64 / Python 3.11+ / Node)

Run in a scratch folder, not in the repository, so third-party assets and
third-party executable binaries are not accidentally committed.

\`\`\`sh
python3 - <<'PY'
import hashlib
import urllib.request
source = "https://media.steampowered.com/apps/dota2/workshop/ember_spirit.zip?v=11095536"
request = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(request, timeout=40) as response, open("ember_spirit.zip", "wb") as output:
    while chunk := response.read(1024 * 1024):
        output.write(chunk)
expected = "f5456711ed4ba2f595815add7179116d0accecb81d329d041f68dd7d3b1c3f02"
assert hashlib.sha256(open("ember_spirit.zip", "rb").read()).hexdigest() == expected
from zipfile import ZipFile
with ZipFile("ember_spirit.zip") as archive:
    assert archive.testzip() is None
    with open("ember_spirit_econ.fbx", "wb") as output:
        output.write(archive.read("ember_spirit_econ.fbx"))
PY
npm install --no-audit --no-fund fbx2gltf@0.9.7-p1
./node_modules/fbx2gltf/bin/Linux/FBX2glTF \
  --binary --input ember_spirit_econ.fbx --output ember_spirit.glb

python3 /path/to/Chat/dota_workshop_mesh.py \
  --glb ember_spirit.glb \
  --mesh ember_spirit_head --orientation head \
  --length-mm 60 \
  --stl ember_spirit_head_60mm.stl \
  --report ember_spirit_head_60mm.json
\`\`\`

## Measured geometry

| Mesh | Native triangles | Welded topology | Outcome |
| --- | ---: | --- | --- |
| \`ember_spirit_head\` | 296 | 0 boundary edges; 1 connected component | STL generated, NOT print-ready |
| \`ember_spirit_weapon\` | 390 | 24 boundary edges, 3 boundary loops, 3 disconnected shells | BLOCKED, needs manual component design and joining |
| \`ember_spirit_offhand_weapon\` | 366 | 16 boundary edges, one nonmanifold edge | BLOCKED |

**Head demonstration output:** 60 × 18.61 × 59.47 mm, ASCII STL, 296
triangles; generated STL SHA-256:
\`784e3a4f537497022b6833b444eee040d3f1a640c18458fb54ae1498234d24e0\`.

The script validates the GLB binary accessors and triangle indices, merges
coordinate seams, checks triangle orientation, caps simple boundary loops,
verifies final edge manifoldness and connectedness, and exports millimeter STL.
It intentionally sets \`"print_ready": false\` on every output. Some nonplanar
boundary loops require an experimental centroid cap, which is not a substitute
for an intersection or thin-wall audit.

## Print acceptance gate

Before converting to A1 mini G-code:

1. Review preview and pose, especially whether the head has a printable base.
2. Verify self-intersections and minimum thickness at the proposed scale.
3. Slice in Bambu Studio with the exact **A1 mini / 0.4-mm nozzle /
   Bambu PLA Basic / one external spool** profile, assess supports and
   adhesion, and log estimated time/material.
4. Independent review and explicit user approval before any printer
   heating, upload/start action, or merge.

Copyright: these assets are Valve-owned references. Source archives and derived
STL models are deliberately **not committed** to the public repository.
Review applicable permission terms before sharing or selling physical models.

## Offline tests

\`\`\`sh
python3 -m unittest -v tests/test_dota_workshop_mesh.py
\`\`\`

In the cloud RDC scratch environment seven focused tests passed. Full
repository CI and independent review are not yet verified.

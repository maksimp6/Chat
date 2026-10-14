# Yorik: GitHub slice → Redmi 9 → A1 mini

Issue #1243. One owner and one approved model/head per run. No merge or physical
print is implied by successful slicing or staging.

The workflow `yorik-redmi9.yml` uses GitHub-hosted Ubuntu for the pinned official
Bambu Studio 02.08.02.61 AppImage. Existing profile/audit/thumbnail helpers are
read from immutable PR #1101 commit `02c4389e88ca6fbcc522c9564c0051a81676c804`.
OpenSCAD builds the v01 single-colour seated Yorik, approximately 34×34×54 mm.
Automatic normal supports are enabled; the sliced thumbnail and support/toolpath
inspection remain distinct from real print quality.

The workflow publishes `yorik-a1mini-redmi9`: one `gcode.3mf`, audit, preview and
worker source. It does not open an inbound channel to the phone. Install the
reviewed worker on the phone through the existing authorized device connection;
verify its SHA-256 against the exact repository commit before running it. This is
a specialized Termux worker, not an unsupported Android GitHub Actions agent.
It never executes code downloaded from an artifact.

Run using existing GitHub credentials, which remain on the phone:

```sh
python yorik_runner.py --head APPROVED_40_HEX_HEAD --wait-seconds 1200 --spool SPOOL
```

The bounded worker waits for this exact workflow/head, or accepts a specific
`--run-id`. It rejects failed, foreign, expired, tampered, oversized and unsliced
handoffs. A successful download records `DOWNLOADED_VERIFIED_NOT_PRINTED`.

Staging is an explicit `--stage --config /absolute/private/printer.json` mode.
The config parent must be owned by the current UID and mode 0700; the config must
be an owned regular non-symlink file, mode 0600. It contains `host` (trusted
private LAN IPv4), `certificate_sha256` (previously trusted certificate pin) and
`access_code` (local printer secret). No credential is stored in GitHub or the
artifact. Do not guess these values or trust a newly observed certificate without
an independently approved binding to the printer.

Implicit FTPS on port 990 verifies the pin on the control and data connections.
The remote filename contains the artifact hash; different existing contents are
never overwritten. One STOR maximum; an uncertain write requires readback, never
a blind retry. Only matching full-byte readback yields
`STAGED_VERIFIED_NOT_PRINTED`. If the control connection is unusable after a
write timeout, stop and reconcile by read-only access with a fresh connection.
There is no MQTT or start/heat/move operation in this worker.

Before physical printing separately confirm the loaded PLA, 0.4 mm nozzle,
Textured PEI plate, clear bed, supports and sliced preview. Real LCD/Handy display,
physical printing and mechanical quality are separate acceptance evidence.

Microscopic triangles below the pinned rasterizer threshold are omitted only from
a derived thumbnail mesh. The complete original mesh is supplied to the slicer;
G-code and model triangles are not removed. The audit records the raster-only
omission count. The derived preview STL is never shipped as a printable part.

The process uses fixed-width classic walls with thin-wall detection disabled.
The variable-width path failed with a negative extrusion-spacing error on a
microscopic contour (requested width 0.0429204 mm at a 0.2 mm layer). The exported
process must preserve classic walls, three perimeters and enabled supports.

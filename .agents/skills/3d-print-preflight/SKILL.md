---
name: 3d-print-preflight
description: Inspect or prepare a single Bambu A1 mini 3MF artifact; verify that source models are sliced before printing.
---

## Purpose

Move from STL/OBJ/3MF (and approved game assets) to **one** verified, printer-targeted `.gcode.3mf` artifact and a traceable preflight report. For Alice Pro's Bambu Lab A1 mini, confirm printer, filament, 0.4 mm nozzle profile, and actual sliced G-code before proposing a print.

## Non-goals

- Do not claim a source `.3mf`, STL, ZIP, screenshot or PNG is a ready-to-print file.
- Do not invent slicing, watertightness, part fit, temperatures, print times or license permissions.
- Do not send a print, replace a job, upload to printer storage, change material or move printer hardware.
- Do not silently convert every incoming ZIP or remote asset into an executable printer artifact.

## Inputs

- Requested model or source file, dimensions and intended purpose.
- Target A1 mini with verified installed nozzle and material (PLA/PETG); unknown values remain unknown.
- Source provenance/license when converting external game/marketplace models.
- Existing sliced artifact when provided; trusted owner workspace for tool-based inspection.

## Tools

- `printing3d.plate.inspect`: local Universal Tool Executor, read-only, owner-scoped; requires operator-configured `ALICE_3D_ARTIFACT_ROOT` and an owner-staged filename.
- `python -m printing3d.plate path/to/file.gcode.3mf`: local offline preflight of a supplied artifact; no network, upload or printer actions.
- Existing `printing3d.quote` for material/time/cost **only after** slicer or provided evidence supplies non-missing drivers.
- Approved STL/mesh/CAD conversion and Bambu Studio/OrcaSlicer only if actually available; do not pretend conversion/slicing occurred.

## Procedure

1. Determine whether the input is **source-only** (.stl, .obj, .glb/.fbx, editable .3mf) or a **sliced** .gcode.3mf. Source-only is never print-ready.
2. Verify model origin, intended use and license; for Dota 2, game assets are source references until lawful reuse and 3D conversion are verified.
3. Check basic mesh units, bed envelope (A1 mini nominal 180 x 180 x 180 mm), orientation, walls, overhangs, supports and assembly tolerances in a real mesh tool. If unavailable, mark checks `not_verified`.
4. Prepare the correct target profile **for the actual installed nozzle and spool**; request real slicing from a supported tool. No generated G-code may be presented unless a slicer actually produced it.
5. Require **one resulting `.gcode.3mf`** rather than an archive of .stl/.png files or decorative renders. Keep source/preview separately only when expressly requested.
6. Inspect via `printing3d.plate.inspect` or read-only `python -m printing3d.plate`. Verify ZIP entry bounds, single plate, embedded G-code, checksums, A1 mini identity, nozzle/filament and heat commands; report SHA-256.
7. Stop on `needs_slicing`, `blocked`, `not_found`, `needs_configuration`, missing evidence or mismatch. The status `ready_for_review` is _not_ a print-start authorization.
8. For an actual upload/print, require the user's separate explicit approval and the printer's current verified idle/material state.

## Approval boundaries

Read-only artifact inspection is allowed for an authorized owner. Asset downloads/licensing, changes to printer settings, storage upload, job start/restart/cancel and new purchases require their normal distinct approval. The 3D skill never grants permissions that the underlying tool lacks.

## Validation

- Status `ready_for_review` with matching single-file SHA-256, printer/material profile, bounded project/G-code entries and integrity checksum.
- Real sliced path and extrusion commands are present; the file is not merely a ZIP/image renamed .3mf.
- Geometry, real spool and live camera adhesion are independent validations and must not be fabricated from archive metadata.
- Adversarial archive, path traversal, owner-isolation and counterfeit metadata tests must pass before making this tool available in production.

## Failure behavior

Do not output a guessed ready-to-print .3mf. Return the precise blocker, preserve the source without mutation and suggest the smallest valid next check (conversion, slicing, material identification or geometry repair).

## Output

Return one candidate artifact name, readiness status, target profile, material, SHA-256, validated facts, **unverified risks** and next required approval. If not actually sliced, explicitly state `no ready-to-print file produced`.

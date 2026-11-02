---
name: 3d-printing
description: Prepare, verify, watch and hand off Bambu A1 mini 3D print artifacts. Use for STL/3MF/G-code checks, sliced-file audits, print telemetry or camera reads, staged-file delivery, and any question whether a model is print-ready or a print succeeded.
---
## Purpose
Carry one 3D artifact through its stages with evidence at each step: source model, sliced `.gcode.3mf`, offline audit, staged file, observed print, cooled-part outcome. Report each stage separately, because a file, a print state and a physical part are different facts (H14–H16).

## Non-goals
- Do not call an STL, OBJ, unsliced 3MF, ZIP, PNG or preview print-ready.
- Do not invent slicing, watertightness, fit, temperatures, print times or license permission.
- Do not send, start, pause, cancel, replace or upload a job, change printer settings, heat the machine or move axes.
- Do not stream video or treat a still frame as a progress reading.
- Do not expose camera access codes, serials, device secrets, MQTT credentials or network tokens.

## Inputs
- Requested model or artifact, intended use, dimensions, and target printer (A1 mini, 0.4 mm nozzle, PLA or PETG). Unknown values stay unknown.
- Source provenance and license for external or game assets.
- Staged artifact name and SHA-256, or an owner-authorized printer connection.
- Latest MQTT telemetry with a timestamp, and at most one fresh JPEG when image quality matters.

## Tools
- `printing3d.plate.inspect`: read-only, owner-scoped inspection of a staged artifact. Needs the operator-configured `LOGOS_3D_ARTIFACT_ROOT` and an owner-staged filename.
- `python -m printing3d.plate <file.gcode.3mf>`: offline preflight of a supplied file, with no network, upload or printer action.
- `printing3d.quote`: material, time and cost, only after a slicer or provided evidence supplies real values.
- Authorized read-only MQTT status and one camera snapshot, when connected. Never use print or stop actions to obtain a status signal.
- Mesh tools and slicers (Bambu Studio, OrcaSlicer) only when actually available. Do not pretend conversion or slicing happened.
- Reference: `docs/mcp/printing3d-tools.md`.

## Procedure
1. **Classify the input.** Source-only files (.stl, .obj, .glb/.fbx, editable .3mf) are never print-ready. Only a sliced `.gcode.3mf` is a print candidate.
2. **Verify origin and license** before any conversion. Treat game or marketplace assets as source references until lawful reuse is confirmed.
3. **Check geometry** in a real mesh tool: units, A1 mini bed envelope (nominal 180 × 180 × 180 mm), orientation, walls, overhangs, supports and assembly tolerances. Anything not checked is `not_verified`.
4. **Slice for the real setup.** Use the profile for the installed nozzle and spool. Require one resulting `.gcode.3mf`, not an archive of STL or PNG files. Keep source and preview files apart and only when asked for.
5. **Audit the sliced file** with `printing3d.plate.inspect` or the offline command. Check ZIP entry bounds, a single plate, embedded G-code, checksums, A1 mini identity, nozzle and filament, heat commands, and report the SHA-256.
6. **Stop on any non-ready status** (`needs_slicing`, `blocked`, `not_found`, `needs_configuration`, missing evidence or mismatch). `ready_for_review` is not permission to print. Honor `print_ready=false` and `unverified_checks`: package structure, machine-path XYZ bounds and heater state at extrusion need independent evidence. Model-entry existence, positive E values and recorded heat targets do not establish them.
7. **Printing needs separate approval.** An upload or start requires the owner's explicit approval and the printer's verified idle state and material.
8. **Watch the print.** Read MQTT `gcode_state`, `gcode_file`, `mc_percent`, `mc_remaining_time`, temperatures and any error, each with a timestamp. Distinguish RUNNING, PAUSE, FINISH, FAILED and offline/unknown. Confirm the running filename; never assume it is the latest upload.
9. **Look only when needed.** Take one fresh JPEG to judge quality. Compare two images only when both have trusted timestamps. Check adhesion, displaced parts, detached supports, spaghetti, blobs and visible collisions. Mark occluded views. A clean image does not prove a defect-free print.
10. **Warn, do not act.** If a serious defect is visible, warn and ask for explicit authorization before any stop or pause. If the view is unclear, ask for a better one-shot observation.
11. **Keep machine state apart from product state.** FINISH or 100% is `print_finished_unverified`, not an accepted part. Ask for the owner's first-use functional test.
12. **Record mechanical failures.** If a latch, hinge, pivot or clip breaks during first use, record `functional_failed` even when the plate looked fine. Ask for a close-up only when it is needed to tell a failed release of a fused joint from a fracture in normal use.
13. **Before revising a failed snap-fit,** verify the original slicer settings (actual wall loops, material, orientation, clearance, version) and the source's documented variants. Do not reprint automatically; a new print needs its own authorization.
14. **Hand off by stage.** READY_FOR_USER, PRINTING and COMPLETE are distinct states. Staging needs its own evidence: a constrained destination and basename, bounded size, verified TLS, size and hash readback, and a durable job identity. After an ambiguous transfer timeout, inspect the state before any retry.

Known outcome to keep in mind: a print-in-place bag clip reached FINISH with only 2 wall loops and broke during its first closing. That is `functional_failed`, and the fracture plane stayed unverified. Confirm the wall count actually sliced; do not trust the count the design recommends.

## Approval boundaries
Reading artifacts, status and one camera frame needs authorized access but no control. Asset download or licensing, printer setting changes, storage upload, job start, restart, pause or cancel, and new purchases each need their own explicit approval. Preparation, staging and physical start have separate authority, and a review never invokes any of them. This skill never grants a permission the underlying tool lacks.

## Validation
- Ready-for-review needs a matching single-file SHA-256, the printer and material profile, bounded entries, an integrity checksum, and the independent evidence listed in `unverified_checks`.
- Geometry, the real spool and live adhesion are separate validations. Do not fabricate them from archive metadata.
- Mechanical acceptance needs a physical test after FINISH. A broken snap-fit is `functional_failed`.
- Adversarial archive, path-traversal, owner-isolation and counterfeit-metadata tests must pass before this tool goes to production. Do not run them unless the owner asks; otherwise report them as `NOT_RUN`.

## Failure behavior
- Do not output a guessed ready-to-print file. Report the blocker and the smallest next check (conversion, slicing, material identification or geometry repair).
- If status cannot be read, say `printer status unavailable`, never `idle`.
- If the camera fails or the view is blocked, say `visual quality unverified`, keep the last telemetry timestamp, and never retry an input action.
- PNG is not a mesh. A manifold mesh is not a recognized shape. An STL or unsliced 3MF is not a print job. A directory listing is not verified bytes. Readback is not printing. FINISH is not fit.

## Output
One candidate artifact name, readiness status, target profile, material, SHA-256, validated facts, unverified risks and the next required approval. If nothing was actually sliced, state `no ready-to-print file produced`. For printed parts, report the post-print outcome (`print_finished_unverified` or `functional_failed`) separately from the printer state. Show a photo only when it was freshly captured. Never show credentials or imply that a print started.

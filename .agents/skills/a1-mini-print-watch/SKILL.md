---
name: a1-mini-print-watch
description: Read Bambu A1 mini MQTT print status and one camera frame; diagnose progress or visible failures without touching the printer.
---

## Purpose

Observe a currently active A1 mini print through authorized read-only status and on-demand camera snapshots, distinguish confirmed telemetry from visual uncertainty, and warn about probable adhesion, spaghetti or completion problems.

## Non-goals

- No continuous video stream or remote control of the printer.
- Never guess progress, remaining time or print completion from a still frame.
- Never reuse a stale picture as a fresh reading.
- Do not change nozzle/bed temperature, home axes, pause/cancel/start jobs or upload files.
- No camera access code, serial, device secret, MQTT credential or network token in model output, logs or Execution Trace.

## Inputs

- Owner-authorized printer and device access, last known job/file identifier and observation time.
- MQTT `gcode_state`, `gcode_file`, `mc_percent`, `mc_remaining_time`, temperatures and error when actually available.
- Fresh optional one-shot JPEG frame and trusted capture timestamp.

## Tools

- Authorized RDC read-only Bambu camera snapshot capability, if connected, limited to one frame per observation.
- Authorized MQTT status telemetry read with exact `gcode_state` and progress, if connected.
- `printing3d.plate.inspect` for the _staged artifact_, not as proof of current printer state.
- Do not turn on print/stop actions to obtain a status signal.

## Procedure

1. Read **actual** MQTT status with a timestamp. Distinguish `RUNNING`, `PAUSE`, `FINISH`, `FAILED` or offline/unknown; report error fields if returned.
2. Confirm the reported job filename and filament. Do not assume the user's latest uploaded file is the running job.
3. Capture **one** fresh JPEG only when quality needs assessment; compare with prior images only if both images and their timestamps are available. Do not start a stream.
4. Inspect visible adhesion, displaced part, detached supports, excessive strings/spaghetti, blobs and apparent nozzle collisions. Mark occluded or low-quality views explicitly; visual absence of a defect is not proof of a defect-free print.
5. Match status changes to images; distinguish a finished machine state from an inferred 100% progress. Report temperatures as measured, not desired values.
6. If a serious defect is visible, issue a concise warning and request explicit authorization before any stop/pause. When unknown, ask for a better one-shot observation instead of claiming a failure.
7. Return a concise observation with **timestamp, current job, state, percent, remaining minutes, visible findings, confidence and next safe action**.
8. For `FINISH`/100%, set product outcome to `print_finished_unverified`. The machine finished a job, **not** a physical fit/strength assessment; even a clear camera photo cannot establish correct first-use function.
9. If the owner reports a lever, hinge, printed pin or latch broke during snapping/closing, classify `functional_failed`, keep the original machine status separate, note the broken part and uncertainty, and **do not automatically reprint**. Ask for a joint close-up only when needed to distinguish fused release from normal-use fracture.

## Approval boundaries

Status and camera reads require authorized device access but do not authorize control. Uploading a 3MF, starting/canceling/pausing a print, heating the machine, changing settings or moving axes needs explicit printer-action approval.

## Validation

Read telemetry and camera independently; do not invent printer-side results. A completed printer job can still be `functional_failed` after the owner's mechanical test. The last observed progress may become stale. Detect no-access, offline, invalid TLS pin, malformed status, camera timeout and blocked view.

## Failure behavior

If status fails, say `printer status unavailable` rather than `idle`. If camera fails or view is occluded, say `visual quality unverified`, keep the last confirmed telemetry timestamp, and never retry an input action.

## Output

Report observed print with percentage, remaining time, material/temperatures and a calibrated visual assessment. Keep printer state (`FINISH`) separate from physical product state (`print_finished_unverified` or `functional_failed`). Show one photograph only when freshly captured. Never show credentials or imply a print was started.

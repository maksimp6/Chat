---
name: 3d-artifact-handoff
description: Review model, slicing and delivery evidence without confusing a concept, mesh, print file, staged file or physical result.
---

## Purpose
Define an evidence-preserving handoff across 3D stages (H14–H16). Reuse the draft preflight/watch skills from #1109 when their integration is available.

## Non-goals
No slicer/controller implementation, printer activation, automatic upload/start/pause/cancel or duplicate preflight/watch package.

## Inputs
Requested deliverable, permitted source assets, editable geometry/revision, actual mesh renders, measured interfaces, machine/material requirements, artifact hashes and destination evidence.

## Tools
Offline archive/hash/mesh inspection, actual-geometry rendering and existing source-pinned slicer reports. Use no printer connection for an offline review.

## Procedure
1. Declare the required endpoint: concept → editable geometry → validated mesh → sliced G-code-bearing 3MF → offline audit → verified storage readback → observed printing → cooled-part acceptance.
2. Pin official-source hashes/importer versions; reject unsafe archives and inspect named meshes/units. Confirm publication licensing separately from download availability.
3. Retain editable source and parameters; render front/side/isometric views from actual geometry. Measure interfaces/tolerances for replacement parts.
4. Check topology, bounds and intended geometry independently from visual fidelity, wall thickness, supports, strength and fit. Preserve immutable owner/runtime-scoped revisions and verify readback hashes.
5. For slicing, inspect effective inherited machine/nozzle/material/plate settings, actual G-code heaters/extrusions, result.json, warnings and ZIP integrity. Check worker memory constraints before a run.
6. Record semantic invariants and each output hash; do not promise identical ZIP bytes across runs. A past temperature profile is not a universal recommendation.
7. For a ready-file request, deliver the actual single sliced .gcode.3mf and verify destination/openability; sources or a preview ZIP are not equivalent.
8. Review separately authorized staging evidence: constrained destination/basename, bounded size, safe overwrite policy, verified TLS, independent size/hash readback and durable job identity.
9. On an ambiguous transfer timeout, inspect state before retrying. Require fault evidence for partial upload, mismatch, full storage, offline/busy device and worker restart before production acceptance.
10. Keep READY_FOR_USER, PRINTING and COMPLETE distinct. Timestamped telemetry and fresh imagery support observations; cooled-part dimensions/fit need separate acceptance.

## Approval boundaries
Preparation, staging and physical start have separate authority. This review does not invoke any of them. Monitoring must not hide control mutations.

## Validation
Every stage claimed has a matching artifact/observation and immutable provenance; unknown dimensions, licensing and quality remain explicit blockers. Backend Draft PRs are not deployed capabilities.

## Failure behavior
PNG is not mesh; manifold is not recognizable; STL/unsliced 3MF is not a print job; a directory listing is not verified bytes; readback is not printing; FINISH is not fit. An obstructed camera cannot establish layer quality.

## Output
Last proven stage, exact artifact identity, safe delivery evidence, unresolved blockers and next separately scoped handoff.
Sources: [H14–H16](../../../docs/lessons/README.md), [#1098](https://github.com/maksimp6/Chat/pull/1098), [#1101](https://github.com/maksimp6/Chat/pull/1101), [#1109](https://github.com/maksimp6/Chat/pull/1109).

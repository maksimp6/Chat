---
name: source-bound-circuit-verification
description: Preserve reproducible KiCad circuit evidence bound to the editable project and prove the validator detects deliberate defects.
---

## Purpose
Review an editable schematic and its archived verification as one versioned artifact (H13).

## Non-goals
No physical energizing, PCB fabrication, substitute simulation model or claim that ERC proves electrical performance.

## Inputs
Native schematic/project, intended topology and values, exact KiCad version/libraries, netlist, ERC output, human-readable exports and source manifest.

## Tools
KiCad CLI when available, temporary configuration directories, hash/netlist validators and deliberate mutations in disposable copies.

## Procedure
1. Bind editable inputs and generated evidence with SHA-256; edits invalidate archived evidence until regenerated.
2. Compare complete pin membership of every net, not generated net labels/codes. Check component inventory, values, symbols/packages, polarity, power/reset/bypass, timing/control and load paths.
3. Derive ideal expected behavior from values read from the actual export.
4. Run live export/ERC with isolated config and explicit symbol libraries; use all severities and violation-sensitive exit status.
5. Prove the validator rejects representative defects such as RESET disconnection, timing-resistor change and reversed LED polarity in temporary copies.
6. Preserve native files, PDF/SVG, netlist, ERC, manifest, tests and limitations together.

## Approval boundaries
Do not alter operator configuration or hardware. Simulation and physical testing require their own valid inputs and task scope.

## Validation
Source/evidence hashes match, topology/value assertions and negative mutations pass, and errors/warnings/exclusions are separately reported.

## Failure behavior
If KiCad is unavailable, run archived checks and explicitly skip live checks. A library file is not a working simulator; an unavailable manufacturer model cannot be silently replaced. ERC does not prove startup, brightness, loaded voltage, heat, fit or PCB correctness.

## Output
Bound manifest, validation report, toolchain identity, live/archived test split and physical/simulation limitations.
Sources: [H13](../../../docs/lessons/README.md), [#1071](https://github.com/maksimp6/Chat/pull/1071).

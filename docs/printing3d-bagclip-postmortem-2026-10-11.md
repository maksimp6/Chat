# A1 mini Bag Clip — first-use hinge failure (2026-10-11)

## Evidence

- Bambu Lab A1 mini reported MQTT `FINISH`, 100% on `Bag Clip by squinn.gcode.3mf` at approximately 09:31 +03:00. PLA was reported with 220 °C nozzle / 60 °C bed targets during print.
- A one-shot camera frame showed the physical clip on the build plate, with no evident spaghetti. Camera cannot prove pivot tolerance, strength or proper operation.
- After removal, the owner reported it **broke while snapping/closing**. The supplied photo shows the detached curved lever and small pivot/hinge features on the remaining body. The fracture plane, exact cause and whether the user was releasing a fused print-in-place joint or closing the mechanism are uncertain.
- **Verdict:** printer job completed; product functional test `functional_failed`. Do not mark this item delivered/accepted or automatically repeat the print.
- **Exact printer-staged sliced file inspected read-only over FTPS:** 494,265 bytes; SHA-256 `2712e9d64f4d744589ba416bf7842f48f3920eb4a7ecd50f66b9a6bb5c5d1433`. The A1 mini project configuration confirms `wall_loops = 2`, PLA, 0.4 mm nozzle, 0.2 mm layer height, 15% infill and no supports. Two walls are below the source author's >=5-wall recommendation and are a plausible contributor to weak hinge strength, but do not prove the precise failure mode.

## Source and possible corrective trial

- Original: **Curvy Cam Lock Bag Clip**, author **squinn**, print-in-place. Published notes recommend **at least 5 wall loops** and identify the original PLA pivot clearance as **0.2 mm**. Alternative source variations with **0.3 mm / 0.4 mm** clearance exist for fused joints or different material.
- Source attribution: https://3dgo.app/models/makerworld/15083 ; https://www.printables.com/model/509410-curvy-cam-lock-bag-clip/files .
- **Before a new print:** recover/inspect the exact original sliced G-code/3MF and source variant, verify actual wall count and model alignment, and inspect the break. If fusion is confirmed, start by evaluating the **author's 0.3 mm** source variant in PLA with ≥5 walls. Increased clearance may reduce holding force; neither a 0.3 nor 0.4 mm version is guaranteed to be sound.
- Re-slice in an actually connected slicer, run the separate owner-scoped preflight, and require a new print-start authorization. Do not claim a repaired `.gcode.3mf` has been generated.

## QA regression

1. `FINISH` + 100% + visible completed geometry -> `print_finished_unverified`, not functional success.
2. After the owner's snap-fit test reveals fracture -> `functional_failed`, regardless of 3MF preflight and machine status.
3. A failed joint must not trigger automatic upload, slicing, reprint or moving printer hardware. Preserve original job ID, filename and evidence; request close-up/measured tolerances only when needed.
4. Keep speculative root cause separate from author-provided configuration recommendations.

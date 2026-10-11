# Alice Print Test — FDM shape capability protocol v0.1

**Issue:** [#1152](https://github.com/maksimp6/Chat/issues/1152). Parent #1079. Scope: dimensional *benchmark* only, not authorization to print and not functional spindle fit. Ownership: geometry and measurement here; model catalog #1112/PR #1114; slicing/FTPS/approval #1096; sliced G-code audit PR #1140. Reuse existing interfaces and do not open duplicate printer-command lanes.

## Evidence and limits

- ISO/ASTM 52902:2023: benchmarking test artefacts, geometric capability and measurement uncertainty; no default ±0.20 mm rule for FDM. https://www.iso.org/standard/79683.html
- NIST AM test artifact: record machine/process parameters, manufactured geometry, observed deviations and repeatability. https://www.nist.gov/el/intelligent-systems-division-73500/production-systems-group/nist-additive-manufacturing-test
- Prusa geometry design: overhang, bridge, thin walls, support roughness. https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135
- Prusa layers/perimeters: Z resolution is different from XY; layer height should remain below 80% of nozzle diameter. https://help.prusa3d.com/article/layers-and-perimeters_1748
- Bambu Lab A1 mini official spec: 180×180×180 mm nominal build volume, included 0.4 mm nozzle; not proof of actual installed hardware. https://bambulab.com/en/a1-mini/tech-specs
- Mesh checks based on Trimesh concepts (`is_watertight`, winding, `split`, bounding box) verified against generated test STLs. https://github.com/mikedh/trimesh

## Artifact contract

Run `python -m printing3d.shape_benchmarks --out /tmp/alice-shape-coupons` from repo root. It only writes six source-only binary STL files and a deterministic SHA-256 `manifest.json`, in declared **millimetres** (STL itself has no units). No printer access, slicing, heating or G-code is performed. The generator uses 96 polygon segments for circular walls; the discretization is a manufacturing input, not a claim that the physically printed object is perfectly circular. Generated shapes are single-shell, oriented and manifold; max coupon bounding axis = 170 mm (conservative fixture-only guard, not a full plate layout safety check).

| Artifact | Nominal specification | Measurements after printing | Main risk |
| --- | --- | --- | --- |
| `circle_vertical.stl` | Ø20 × 12 mm, axis Z | XY diameter at Z=2/6/10, X/Y chords, seam | Faceting, seam bulge, ovality |
| `triangle.stl` | equilateral triangle, circumradius 12 mm, height 12 | each of three edges, vertex rounding, corner angle | Overshoot/rounded corners |
| `square.stl` | 20×20×10 mm | X/Y widths, diagonals, 90° angles at two Z levels | X/Y skew, elephant foot |
| `hole_ring.stl` | outer Ø32, through-hole Ø12, height 8 | bore gauge at top/mid/bottom, outer diameter, wall thickness | Undersized holes; overhang at bore edge |
| `circle_horizontal.stl` | Ø20 × 24 mm, horizontal Y axis | diameter and sag at underside, XY vs vertical cylinder | Line-only bed contact; support artefacts |
| `inclined_wedge.stl` | 12×30×15 mm, ramp to 15 mm | slope/steps, straightness, edge curling | Z stair-stepping and warping |

Each shape is an independent benchmark; do not print all six in one unreviewed plate. Only a slicer preview with a real printer profile can determine supports, adhesion and collisions. Horizontal cylinder explicitly needs an adhesion/support decision; no auto-approval from `watertight=true`.

## Stages and objective gates

| Gate | Decision evidence | Failure handling |
| --- | --- | --- |
| SOURCE | manifest, generator version, SHA-256, declared units & intended shape | Reject absent/ambiguous units or changed hash |
| MESH | file length, finite vertices, normal agreement, no degenerate/duplicate faces, oriented watertight single shell, independent expected bbox | Fail closed; record deviation; no claims of full solid intersection detection |
| SLICED | pinned Bambu Studio, machine/nozzle/plate/material profile, orientation, supports, actual CLI result.json, .gcode.3mf | Delegate to #1096; STL/unsliced 3MF is never a printable job |
| GCODE | actual toolpaths/extrusion, ZIP CRC, heater/bed limits, forbidden G-code, expected bounding placement | Delegate to PR #1140; do not trust filename/profile name alone |
| STAGED | after exact byte/hash readback, printer idle and secrets protected | Delegate to #1096; staging ≠ starting |
| PRINT | **fresh explicit approval** for machine/material/temperatures/file/risks, followed by actual telemetry | No approval → NO_PRINT_START |
| PHYSICAL | photo series + caliper/gauge readings + deviation report and independent review | Unknown or missing measured data → NOT_VERIFIED |

The nominal spindle length **68 mm** is not sufficient to approve a functional spindle. Shaft OD/ID, mating spool bore, allowable play, structural load, heat tolerance and assembly clearance must be measured and recorded under its separate engineering issue.

## Experimental print protocol (after user approval)

1. Record actual A1 mini serial **privately**, nozzle installed (do not infer 0.4 from stock specs), filament brand/type/dryness, plate, slicer and profile versions, layer height, XY compensation, speed/flow, cooling and support settings. Do not record access codes in public logs.
2. First-layer and flow calibration per printer manufacturer's instructions; capture ambient conditions and calibration date. Avoid changing multiple settings within a single comparison group.
3. Print a single sample with chosen orientation; capture G-code checksum, start/end timestamps, printer telemetry and completion status. Set `PRINTING` only on real evidence.
4. Cool to room temperature. Record measurement instrument calibration/resolution, measurement location, uncertainty estimate and at least three repeat readings for each target dimension. Reprint the same coupon three times if evaluating repeatability. Do not conflate measurement repeat readings with independent print replicates.
5. Photograph with scale: top view perpendicular, side view, close-up of seam/corners/overhangs, underside after support removal. No photo-only dimensional acceptance without camera calibration and a scale in plane.
6. Enter measurements into a separate table. Compute error = measured - nominal, absolute error, average, range and uncertainty; assess each against **specified** per-feature tolerance and uncertainty. If tolerance or instrument capability is absent, report `MEASURED_UNRATED`, not PASS.
7. Record aesthetic defects separately: stringing, seam, ringing, porosity, layer separation, bridge sag, warping; color/lighting are not proof of geometry.

### Measurement record schema

Required columns: `job_id, artifact_sha256, material, profile_sha256, measurement_id, nominal_mm, measured_mm, instrument, resolution_mm, uncertainty_mm, tolerance_mm, position, repeat_index, photo_ref, verdict, operator, timestamp_utc`. For polygon edges and hole gauge, state the measurement method. `verdict` is only PASS if all required inputs are present and `abs(measured-nominal)+uncertainty<=tolerance`; otherwise FAIL or MEASURED_UNRATED. This formula is a conservative **project policy**, not the ISO document's mandatory decision rule.

## Validation

```
python -m unittest discover -s tests -p 'test_printing3d_shape_benchmarks.py' -v
python -m printing3d.shape_benchmarks --out /tmp/alice-shape-coupons
```

Test contract covers deterministic hashes, topology, exact source bbox, hole topology, bad normals/winding, NaN, degenerate triangles, duplicate facets, missing faces, malformed lengths, invalid dimensions and out-of-range bounding box. It does **not** validate slicer material, nozzle/bed temperature, printable overhang, FTPS communication, surface roughness, load strength or true roundness. These require other stages/real physical evidence.

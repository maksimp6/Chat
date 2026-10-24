# Logos Print Test — FDM shape capability protocol v0.1

**Issue:** [#1152](https://github.com/maksimp6/Chat/issues/1152). Parent #1079. Scope: dimensional *benchmark* only, not authorization to print and not functional spindle fit. Ownership: geometry and measurement here; model catalog #1112/PR #1114; slicing/FTPS/approval #1096; sliced G-code audit PR #1140. Reuse existing interfaces and do not open duplicate printer-command lanes.

## Binding zero-waste rule (owner decision, 2026-10-11)

**ZERO standalone calibration prints and ZERO required print expenditure.** All six reference
STL meshes are **digital regression fixtures only**; never upload, stage, slice into a
printer-ready job for execution, heat the nozzle/bed or print cubes, rings, cylinders,
triangles or wedges for calibration by default. Offline geometry checks may include
slicing and G-code analysis **without printer communication**, under the separate
owners of #1096 and PR #1140. These checks never authorize actual printing.

**Physical dimensions, roundness, fit, strength, surface finish and repeatability stay
`UNVERIFIED`** until measured on an actual **useful part** whose printing the owner
separately approved. A candidate 68 mm spindle is not approved until the real mating
dimensions, clearances, material and print profile are known. Exceptions for any
standalone calibration print require a **new specific owner authorization**, including
a reason, predicted filament consumption and time; no authorization is inferred here.
This rule supersedes all earlier calibration-coupon print suggestions.

## Evidence and limits

- ISO/ASTM 52902:2023: benchmarking test artefacts, geometric capability and measurement uncertainty; no default ±0.20 mm rule for FDM. https://www.iso.org/standard/79683.html
- NIST AM test artifact: record machine/process parameters, manufactured geometry, observed deviations and repeatability. https://www.nist.gov/el/intelligent-systems-division-73500/production-systems-group/nist-additive-manufacturing-test
- Prusa geometry design: overhang, bridge, thin walls, support roughness. https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135
- Prusa layers/perimeters: Z resolution is different from XY; layer height should remain below 80% of nozzle diameter. https://help.prusa3d.com/article/layers-and-perimeters_1748
- Bambu Lab A1 mini official spec: 180×180×180 mm nominal build volume, included 0.4 mm nozzle; not proof of actual installed hardware. https://bambulab.com/en/a1-mini/tech-specs
- Mesh checks based on Trimesh concepts (`is_watertight`, winding, `split`, bounding box) verified against generated test STLs. https://github.com/mikedh/trimesh

## Artifact contract

Run `python -m printing3d.shape_benchmarks --out /tmp/logos-shape-coupons` from repo root. It only writes six source-only binary STL files and a deterministic SHA-256 `manifest.json`, in declared **millimetres** (STL itself has no units). No printer access, slicing, heating or G-code is performed. The generator uses 96 polygon segments for circular walls; the discretization is a manufacturing input, not a claim that the physically printed object is perfectly circular. Generated shapes are single-shell, oriented and manifold; max coupon bounding axis = 170 mm (conservative fixture-only guard, not a full plate layout safety check).

| Digital fixture (never print for calibration) | Nominal specification | Comparable feature on a useful, separately approved printed part | Main risk |
| --- | --- | --- | --- |
| `circle_vertical.stl` | Ø20 × 12 mm, axis Z | XY diameter at Z=2/6/10, X/Y chords, seam | Faceting, seam bulge, ovality |
| `triangle.stl` | equilateral triangle, circumradius 12 mm, height 12 | each of three edges, vertex rounding, corner angle | Overshoot/rounded corners |
| `square.stl` | 20×20×10 mm | X/Y widths, diagonals, 90° angles at two Z levels | X/Y skew, elephant foot |
| `hole_ring.stl` | outer Ø32, through-hole Ø12, height 8 | bore gauge at top/mid/bottom, outer diameter, wall thickness | Undersized holes; overhang at bore edge |
| `circle_horizontal.stl` | Ø20 × 24 mm, horizontal Y axis | diameter and sag at underside, XY vs vertical cylinder | Line-only bed contact; support artefacts |
| `inclined_wedge.stl` | 12×30×15 mm, ramp to 15 mm | slope/steps, straightness, edge curling | Z stair-stepping and warping |

Each shape is an independent **digital** benchmark, never a required physical coupon.
Slicer previews or offline G-code checks are advisory and must not stage printer jobs.
The horizontal cylinder highlights hypothetical adhesion/support risks; a
`watertight=true` result grants **no** authorization or physical capability claim.

## Stages and objective gates

| Gate | Decision evidence | Failure handling |
| --- | --- | --- |
| SOURCE | manifest, generator version, SHA-256, declared units & intended shape | Reject absent/ambiguous units or changed hash |
| MESH | file length, finite vertices, approximately unit-length facet normals and their directional agreement, no degenerate/duplicate faces, oriented watertight single shell, independent expected bbox | Fail closed; record deviation; no claims of full solid intersection detection |
| SLICED (offline only) | pinned slicer, profile, orientation, supports and real CLI result.json | Delegate to #1096; offline evidence, never print authorization |
| GCODE (offline only) | audited toolpaths/extrusion, ZIP CRC, heater/bed limits, forbidden commands and bounding placement | Delegate to PR #1140; analyze offline, **no printer upload** |
| STAGED (useful part only) | separately approved useful print, exact byte/hash readback, idle printer and protected secrets | Delegate to #1096; **never stage these six reference fixtures** |
| PRINT (useful part only) | fresh separate approval for the specific useful part, machine/material/file/risks and real telemetry | Not in this issue; no approval → NO_PRINT_START |
| PHYSICAL (useful part only) | explicit approval reference, photos, actual measurements, deviation and independent review | Missing actual reading → UNVERIFIED; measured but missing tolerance/uncertainty → MEASURED_UNRATED |

The nominal spindle length **68 mm** is not sufficient to approve a functional spindle. Shaft OD/ID, mating spool bore, allowable play, structural load, heat tolerance and assembly clearance must be measured and recorded under its separate engineering issue.

## Physical measurement protocol — useful parts only

This protocol applies **only when a useful part was separately authorized and
actually printed for its own functional purpose**. It is not a plan to print any
of the digital fixtures. Do not schedule sample prints or three duplicate coupons
for repeatability. If no useful part exists, stop at the digital evidence and
report physical characteristics as **UNVERIFIED**.

1. Link the specific prior useful-part print approval (`approval_ref`) and actual
   job ID; identify the part SHA-256, real installed nozzle, material, profile,
   layer height and orientation. Do not infer hardware from vendor specifications.
   Keep serial numbers and credentials out of public records.
2. After the **already authorized** useful print has cooled, identify existing
   comparable functional features (diameters, holes, angles, wall thickness or
   slopes). Do not introduce sacrificial supports or alter the model solely for
   calibration. Features absent from the part stay **UNVERIFIED**.
3. Record calibrated instrument identity/resolution, uncertainty estimate,
   measurement position and repeated readings on the **same useful part**.
   Different readings are not independent physical-print repeatability evidence.
4. Photograph the useful part with a scale in the same plane, orthogonal views
   where possible, and close-ups of relevant existing defects. Uncalibrated
   photographs do not establish dimensional accuracy.
5. Compute measured minus nominal and evaluate only against the **part's own**
   documented feature tolerance using a guard band
   `abs(measured-nominal) + uncertainty <= tolerance`. No generic ISO FDM
   tolerance may be assumed.
6. Report separate evidence for fit, circularity, finish and strength where
   legitimately observed. Unmeasured properties stay **UNVERIFIED**; do not
   infer them from STL mesh topology, slicer simulation or another measurement.

### Measurement record schema

Columns (see `acceptance-template.csv`): `approval_ref, job_id, useful_part_sha256,
material, profile_sha256, measurement_id, nominal_mm, measured_mm, instrument,
resolution_mm, uncertainty_mm, tolerance_mm, position, repeat_index, photo_ref,
verdict, operator, timestamp_utc`. `approval_ref` must identify separate user
authorization for that **useful part**, not this PR. Use the SHA of the real part,
never the reference coupon. Empty measurement means **UNVERIFIED**. Recorded
measurement lacking feature tolerance or uncertainty means **MEASURED_UNRATED**.
Otherwise `PASS` only when
`abs(measured-nominal)+uncertainty<=tolerance`; else `FAIL`. Record angle,
roughness and strength evidence separately with their appropriate units. This
guard-band rule is **project-local**, not an ISO-mandated tolerance.

## Validation

```
python -m unittest discover -s tests -p 'test_printing3d_shape_benchmarks.py' -v
python -m printing3d.shape_benchmarks --out /tmp/logos-shape-coupons
```

Test contract covers deterministic hashes, topology, exact source bbox, hole topology, bad normals/winding, NaN, degenerate triangles, duplicate facets, missing faces, malformed lengths, invalid dimensions and out-of-range bounding box. It does **not** validate slicer material, nozzle/bed temperature, printable overhang, FTPS communication, surface roughness, load strength or true roundness. Every unmeasured physical feature remains **UNVERIFIED**. None of the six fixtures may be printed, staged, uploaded or used to spend filament without new specific user authorization.

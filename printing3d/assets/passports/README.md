# Model passports and G-code verification

Every model in `models_manifest.json` must have exactly one JSON passport in this directory. The inventory test fails if a passport is missing, duplicated, or its source archive hash / part quantities disagree with the manifest.

`printing3d.gcode_audit.audit_passport(passport, sliced_job)` validates a **real** Bambu `.gcode.3mf`: ZIP CRC, required members, G-code MD5, model/nozzle/material, print area and toolpath bounds, extrusion, heaters, and prohibited firmware commands. It returns `GCODE_VERIFIED_FIT_BLOCKED` when the geometry has not passed physical-fit acceptance. A model without a sliced job remains `SOURCE_ONLY_NOT_PRINT_READY` and must never be described as ready to print.

This static gate does **not** establish firmware acceptance, actual filament compatibility, support removability, or physical fit. Record separate evidence for firmware-open and physical-fit gates. Do not run the printer from CI. Never insert an arbitrary STL/3MF into a printer's `/model` directory as a sliced job.

All three inventoried model families currently have passports. Alternative adapter sizes are **choices**, not a combined print plate. The desk spool models need actual PETG slicing and independent verification before print readiness; the OEM spindle has a PLA prototype job but its physical fit and firmware opening remain unverified.

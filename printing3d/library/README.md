# Logos 3D Model Library

Versioned catalog of printable 3D models. No binaries or models are invented by this index.

## Catalog structure
Each model has its own directory under `models/` with:
- `model.json`: metadata, source paths, units, parts, print settings, and validation status
- `README.md`: purpose, assembly steps, hardware and safety notes
- `source/`: editable CAD sources
- `export/`: STL / 3MF exports
- `preview/`: renders and photographs

Do not mark a model as printable or assembly-verified without evidence. All file paths must be repository-relative and refer to actual committed assets. Store SHA-256 hashes for release exports.

## First candidate
The photographed three-part lever/clamp mechanism needs source-file identification and physical assembly verification before publication as a verified model.

## Integration
Keep this catalog independent of the existing `printing3d/` Python service. A future API can read manifests without migrating or duplicating source files.

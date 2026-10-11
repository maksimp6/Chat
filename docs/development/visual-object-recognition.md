# Alice Pro — visual recognition benchmark

Owner: PR #1146. No printer command, model training, deployment or merge.

## What this measures

The 3D render titled "Накладка на шпиндель A1 mini" depicts a **filament-spool
holder spindle cover**, not a powered cutting spindle or milling machine.
An image-only model must not invent a 24 V / 500 W motor, a collet, a toolpath,
real installation, material, or successful physical printing.

The hash-pinned manifest is tests/fixtures/visual3d_cases.json. It references
the authorized Redmi 9 gallery in printing3d/assets/gallery but does NOT
publish the images to this public GitHub repo. The seven cases contain:

- Two spool holder spindle-cover shells.
- A filament spool holder, viewed assembled.
- Three separate centering adapters.
- Phone stand, test-tube holder, and sauce stand.
- A blank control requiring no detected target objects.

All crops exclude explanatory titles so a recognizer cannot simply read
the model name. Bounding boxes are provisional human visual estimates, not
independently approved ground truth. The samples are CAD renders, NOT physical
photos. Missing, tampered, symlinked or excessively large inputs fail closed.

## Offline inspection on a device with these private PNGs

    python3 -m scripts.visual_object_benchmark \
      --manifest tests/fixtures/visual3d_cases.json \
      --image-root /path/to/authorized/printing3d/assets/gallery \
      --output-root /path/to/private/review

Pillow 12.3.0 is used for isolated crops and PNG overlays.
Without a predictor, expect BLOCKED / model_not_invoked and null precision
and recall. The command creates report.json and overlays/*.png:
green boxes are proposed ground truth; amber boxes are actual predictions.
Do not count these overlays as model inference.

## Real model E2E (requires an available implementation)

Only an explicitly configured loopback HTTP endpoint is permitted:

    python3 -m scripts.visual_object_benchmark \
      --manifest tests/fixtures/visual3d_cases.json \
      --image-root /path/to/authorized/printing3d/assets/gallery \
      --output-root /path/to/private/live-review \
      --endpoint http://127.0.0.1:PORT/recognize

Request JSON carries an opaque image-hash case_id (not the answer label),
mime_type=image/png, image_base64, input image_sha256 and a fixed,
case-independent vocabulary prompt. The reply must be a
strict JSON object with objects containing canonical label, normalized bbox
[x0,y0,x1,y1] and confidence (0..1), plus optional claims array.

Example of output STRUCTURE (not a real model result):

    {"objects":[{"label":"spool_spindle_shell",
                  "bbox":[0.05,0.14,0.54,0.81],
                  "confidence":0.95}],"claims":[]}

The legacy /api/vision endpoint is mentioned in docs/api/API_DOCS.md, but
no implementation of that endpoint exists in the inspected develop checkout.
Do not pretend a model was called or export images to public services.
HTTP redirects, external hosts, shell commands and arbitrary file paths are
not permitted by this harness.

Alternatively --predictions-dir may point to locally collected per-case
JSON files. It is useful for debugging evaluator behavior but is not live AI.

## Status interpretation

- BLOCKED: model not called or unavailable / missing or invalid responses.
- FAILED: predictions miss an object, invent an object, or assert a banned claim.
- REVIEW_PENDING: matches exist but annotations are provisional, or results
  were supplied from response files rather than live inference.
- EVALUATED: reviewed ground truth + structured loopback predictor passed
  the test contract. This does NOT prove independent model identity or
  real-world performance.

IoU >= 0.5; confidence >= 0.5; one-to-one matching; deterministic micro
precision/recall. No scores are published if zero cases were evaluated.

## Still required for acceptance

Collect authorized real physical photos of holder, actual spool, parts
installed, near-lookalikes, partial occlusion and low light. Have another
reviewer confirm boxes and labels. Pin hashes and run the real model over
these photos. Record errors and visual overlays. Only then decide acceptance.
No merge or printer motion/heating/print start in this test slice.

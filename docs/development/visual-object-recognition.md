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
the left panel shows proposed ground truth (green) and the right panel shows
predictor output (amber). Each panel names the source: no model, supplied
response, or loopback endpoint. Do not count these overlays as inference.

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


## Actual local CPU recognizer — Alice shape-template v1

This PR additionally provides an explicit **Pillow-only**, CPU-bound,
supervised reference-shape matcher. It is a real image-processing engine:
foreground connected-component extraction, learned 12x12 shape signatures,
nearest-prototype matching and learned per-part bounding boxes. Neither
class labels nor expected boxes are read from the incoming HTTP case ID.
The pinned training inputs are seven initial CAD cases plus a separately
labeled C-shaped retaining ring. **No pretrained general-purpose neural
vision weights are present**, and photographic accuracy remains unknown.

Train a private 0600 model and evaluate over actual loopback HTTP on the
original hash-pinned local gallery:

    python3 -m scripts.vision_loopback evaluate \
      --train-manifest tests/fixtures/visual3d_cases.json \
      --eval-manifest tests/fixtures/visual3d_cases.json \
      --gallery /path/to/authorized/private/gallery \
      --results /path/to/private/in-sample-results \
      --model /path/to/private/alice-shape-v1.json

Evaluate the distinct pinned overview.png (six held-out render panels):

    python3 -m scripts.vision_loopback evaluate \
      --train-manifest tests/fixtures/visual3d_cases.json \
      --eval-manifest tests/fixtures/visual3d_holdout.json \
      --gallery /path/to/authorized/private/gallery \
      --results /path/to/private/holdout-results \
      --model /path/to/private/alice-shape-v1.json

The same object CADs appear in both sets at different render scales. The
holdout file is separate and never used for training, but it is not
equivalent to independently reviewed physical photos or different objects.
**No training/test split proves photo accuracy** here. The CLI re-trains
from hash-pinned inputs to validate an existing model artifact and rejects
any stale or mutated model signatures. In-sample runs are always
REVIEW_PENDING even when test labels were reviewed.

An optional serve subcommand with --model, --model-sha256 and --port exposes
only the fixed loopback 127.0.0.1 interface; no public bind option exists.
The server accepts bounded PNG+SHA-256 JSON only. It rejects arbitrary file
paths, external URLs, incorrect Host, changed model digest, invalid classes,
oversized requests and unsafe image formats. It does not log image payloads
or prompts. The E2E runner starts that same HTTP endpoint on an ephemeral
loopback port and shuts it down immediately after checks, without touching
RDC, printers, live services or the Android UI.

## Optional private synthetic stress test

To quantify failure modes, generate dim/bright/blur/mirror/grayscale/occluded
variations **on the same authorized device**, never into public GitHub:

    python3 -m scripts.vision_perturbations \
      --manifest tests/fixtures/visual3d_holdout.json \
      --gallery /path/to/authorized/private/gallery \
      --private-out /path/to/private/synthetic-corpus

Evaluate these locally derived PNGs with the above evaluate subcommand, using
the generated synthetic-corpus/manifest.json as --eval-manifest and adding
--eval-gallery /path/to/private/synthetic-corpus. The report is marked
SYNTHETIC_PERTURBATIONS_ONLY. These variants are derived from CAD previews;
success does not qualify the model on real or novel objects.

All code, documentation, provenance hashes, and tests are in this Draft PR.
Raw images, private features/model JSON, overlays and photos remain outside
GitHub. No merge, physical printing, printer activation, cloud upload or
deployment.


## Measured private-device evidence, 11 October 2026

Device: authorized Android Redmi 9 (AArch64, CPU-only, Pillow 12.3.0).
All source/render files stayed on that device; the model is held in a
user-private 0600 JSON artifact, not committed. The HTTP endpoint was
127.0.0.1 with an ephemeral port, and each test shut it down cleanly.
The GitHub source revision for the measured model was 76ca83fb6d61.

| Corpus | True positive | False positive | False negative | Classification |
| --- | ---: | ---: | ---: | --- |
| Same render images used for training | 10 | 0 | 0 | REVIEW_PENDING / IN_SAMPLE_ONLY |
| Six panels of different overview.png raster | 10 | 0 | 0 | REVIEW_PENDING / HELDOUT_RENDER_ONLY |
| 36 synthetic perturbations from overview crops | 37 | 4 | 23 | FAILED / SYNTHETIC_PERTURBATIONS_ONLY |

For the held-out raster, the median local HTTP inference request took
319.57 ms, and p95 was 404.87 ms, over six requests. This excludes offline
training and physical camera acquisition.

**Synthetic robustness by transformation (each category has ten true objects):**

| Mode | Detected | Extra detections | Misses |
| --- | ---: | ---: | ---: |
| Brightness increased | 10 | 0 | 0 |
| Gaussian blur | 10 | 0 | 0 |
| Dimmed | 6 | 3 | 4 |
| Horizontal mirror | 5 | 0 | 5 |
| Partial occlusion | 6 | 1 | 4 |
| Grayscale | 0 | 0 | 10 |

Total synthetic micro precision: 37/(37+4) = 90.24%; micro recall:
37/(37+23) = 61.67%. The failed result remains visible. Current
foreground selection is sensitive to color saturation and object orientation.
CAD-only data does not establish accuracy under real lighting, occlusion,
different camera lenses, or actual physical prints.

**Acceptance gate remains BLOCKED for real photos.** There is no independently
reviewed physical photograph test set, and there is no generalized multimodal
Alice API included by this Draft PR. Its shape-template recognizer is real,
but closed-set and intended only as a local CAD recognition baseline. Do not
change its status to production-ready based on the in-sample 100% result.

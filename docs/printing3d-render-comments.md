# Alice Pro: automatic real 3D previews in GitHub comments

Issue: [#1118](https://github.com/maksimp6/Chat/issues/1118). This is a review visualization **not** a printer controller.

## Upload and trigger

Place licensed, approved **STL** or genuine geometry-bearing **3MF** files in
`printing3d/models/` (one or two simple subdirectories are supported).
Open or update a same-repository pull request. GitHub Actions
`3D previews in PR comments` automatically:

1. Uses **trusted code from the repository default branch**, never checks out
   the PR branch or executes PR G-code/commands.
2. Reads the GitHub changed-file list, allowing up to **3** simple named
   `.stl` / `.3mf` files **only** in `printing3d/models/`. Fetches each
   source as a SHA-verified Git blob through GitHub's API.
3. Refuses oversized files, malformed ZIP/XML, unsupported 3MF components,
   invalid triangles and too-large output. Returns an explicit unavailable
   message rather than an invented preview.
4. Computes deterministic, actual triangle-based **isometric, top, side**
   orthographic views, all in **one PNG per model**, never source ZIP previews.
5. Commits **only rendered PNGs** to the separate `alice-3d-previews` branch
   (not `develop` or `master`), referenced by immutable commit SHA.
6. Creates or updates **one GitHub Actions bot PR comment**, including the
   inline image, originating SHA, face count and bounds. Repeated events on the
   same PR head do **not** write another image commit or duplicate the comment.

Test the publisher contract by running
`python -m pytest scripts/tests/test_render_3d_preview.py` after installing
the pinned **Pillow==12.3.0**; the separate, read-only
`3D render contract` workflow installs these dependencies and runs tests.

Workflow `workflow_dispatch` can manually render/re-render a PR by number
once installed into the default branch. A `refresh` option requests a
regenerated image, but still updates the same bot comment.

## Safety and release status

The PR must be in the **same repository**. Forked PRs are ignored.
The **unprivileged** `pull_request` / `3D render contract` test workflow has
only read permission. When it succeeds, a separate trusted `workflow_run`
publisher starts with only the repository's default-branch code. It **never**
executes or checks out code from the PR or loads its Actions artifacts:
changed model **blobs are re-fetched by Git SHA** and parsed as bounded data.
`GITHUB_TOKEN` is exposed only in the publisher step, never to untrusted
models, using `contents:write`, `issues:write`, `pull-requests:read` permissions.
A stale `workflow_run` head SHA is rejected rather than posting outdated PNGs.
No third-party upload host or user-owned access key is used.

The renderer does **not** infer whether a mesh is watertight, printable,
dimensionally correct, safe for a child, safe for a machine, or a functional
part. STL has no unit metadata, so physical units are **unknown**; 3MF units
are normalized when defined. Visual results are not a substitute for
slicing, engineering review, owner-scoped 3MF preflight, print monitoring or
first-use mechanical acceptance.

A Bambu `.gcode.3mf` can contain a sliced G-code plate but **no embedded
3D mesh** (the user's printer-staged Bag Clip is such a file). This parser
must fail closed and report preview unavailable, rather than synthesizing
geometry or silently parsing/executing G-code.

**Activation:** this trusted event workflow cannot run automatically from
an unmerged PR. It becomes active **only after** code passes its separate
CI/review and the workflow is merged to the repository's default branch
(currently `master`). No merge is performed by the render publisher.
Initial demonstration of GitHub-embedded PNG in
[issue #1118](https://github.com/maksimp6/Chat/issues/1118) was published
as a one-time manual proof, not an auto-triggered run.

## Operating caps

- Source model <= 16 MiB; one ZIP XML part <= 8 MiB; total ZIP
  uncompressed <= 40 MiB; member count <=100.
- Max 25,000 triangles and 50,000 vertices; max 3 models per PR.
- Render image <= 1.2 MiB; fixed three-view PNG; Actions job <=6 minutes.
- Only allowlisted filename/paths; no input file extraction, subprocesses,
  sockets to the printer, model code execution, FTP or camera access.
- Outputs in preview branch are **publicly retrievable** in a public repo.
  Do not commit confidential CAD/model assets to public PRs.

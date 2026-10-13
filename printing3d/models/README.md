# 3D models for GitHub preview comments

Files committed in this directory are treated as **untrusted geometry data**
and rendered automatically in a GitHub pull-request comment after the trusted
workflow is deployed on `master`. Upload only models you own or have a valid
license to share, and never printer passwords, sensitive CAD designs or
personally identifying documents.

Accepted: regular `.stl` and geometry-bearing `.3mf` filenames containing
ASCII letters, numbers, hyphens, underscores or periods. The render publisher
does not extract ZIP archives or run G-code. At most three changed files per PR
are previewed. If no mesh exists inside a sliced 3MF, an explicit failure
is shown instead of a fabricated rendering.

See [the full render/comment pipeline](../../docs/printing3d-render-comments.md).

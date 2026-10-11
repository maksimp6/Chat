# #1136 / PR #1137 validation correction

Canonical issue: #1136; current deliverable: existing foundation PR #1137.
Owner: Codex, Backend/Test Engineer; real shell worktree and GitHub connector.
Stage: reproduce exact-head CI → deterministic regressions → implementation → CI.
Branch: `issue-1136-video-ci-fix`; prepared head
`fde591bfac3acb5ae9c7ee27425c19df8334cb9a`, current master is an ancestor.

Accepted scope: existing offline turntable renderer/storyboard/MP4 verifier and
its missing validation evidence. Fix canonical formatting and newly strict-clean
file registration without raising type/reliability baselines. Pin real test
dependencies; exercise rendering and FFmpeg encoding without optional dependency
skips. Validate malformed manifests and encoded metadata, file/count bounds,
and negative renderer cases. Keep source fixtures immutable.

Real STL regression exposed a pre-existing functional failure: STL duplicates
triangle vertices, so `process=False` followed directly by watertightness rejected
even an exported closed cube. Weld coincident vertices before topology checks,
preserving finite-coordinate/size/complexity guards. Also reject nested manifest
fields and non-finite ffprobe duration rather than accepting invalid evidence.

Owner's repository-completion request authorizes reversible code/test changes
and publication to the same PR. No physical model, inferred assembly, model
library publication, printer operation, device restart or deployment. Real CAD
assembly/catalog integration still depends on #1135/#1118/#1130; this foundation
does not satisfy every output in #1136. Preserve draft and independent review.
Stop on a changed remote head, failed gate or changed contract.

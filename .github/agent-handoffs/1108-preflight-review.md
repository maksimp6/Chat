# #1108 / PR #1109 adversarial review correction

Canonical issue: #1108; current deliverable: PR #1109.
Accepted correction contract: the four independent inline review threads on
`f31102f4a151bee6991d7b68132ca638dac18bde`: reject non-string filament members,
bound wall-count digits, and implement the reviewer's explicitly offered option
to make unverified machine-path and complete 3MF-package validation explicit.

Owner: Codex, Backend/Test Engineer; real shell worktree and GitHub connector.
Stage: failing regressions → implementation → exact-head CI → review handoff.
Branch: `issue-1108-preflight-review-fixes`; worktree `/workspace/Chat-1109`.
Prepared head: `95d3a7be1592b630020871a60a2f1c69cd07f88d`, normal merge of current
develop `9d658587871007a041f1981c084a80d2de6d78bd`; original PR head is an ancestor.

Output: fail-closed profile parsing, explicit `print_ready=false` and unverified
package/XYZ/heater-sequence facts, matching adversarial tests and updated existing
tool/skill documentation. Preserve `ready_for_review` compatibility; inspection
is neither full machine-path certification nor print authorization. Do not copy
the separate model-specific G-code audit from PR #1140 into this slice.

Authorization: owner's repository-completion request covers these reversible
review fixes and publication to the same PR branch. No slicing, upload, printer
operation, device/RDC restart, credentials or deployment. Keep the existing
draft/owner-acceptance boundary; stop on a changed remote head, failed gate or
changed contract. Broader #1108 and live printer acceptance remain open.

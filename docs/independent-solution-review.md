# Independent solution-review evidence (single-owner repository)

The solution review is **not** a GitHub self-approval and does **not** authorize merge. The PR owner still explicitly authorizes merge after readiness and required checks succeed.

## Trust model

- Implementation and review use different agent roles **and distinct sessions**. A string supplied in `workflow_dispatch` is not authentication.
- An independent reviewer service, with a separate protected signing credential, reviews the exact PR head/base and produces a substantive report.
- The report is canonical JSON (UTF-8, sorted keys, no spaces, `ensure_ascii=False`), signed using HMAC-SHA256. The reviewer service holds the signing key outside the implementation agent's scope. The Actions secret `REVIEW_SIGNING_KEY` contains the verification copy (minimum 32 characters).
- The authorized runner is `solution-review.yml` launched from `master`. It checks the live PR head/base, validates the signed report and its contents, then publishes an immutable run artifact.
- Merge readiness trusts only a successful master-dispatched run carrying an artifact whose PR number, reviewed head, reviewed base, and run ID match. The workflow's own `run.head_sha` points to master and is **not** compared to the PR head.
- Any changed PR head or base invalidates the report. A failed run, missing signature, missing report, empty findings metadata, or unknown outcome does not pass.
- The report's review session and signing credential must be established by the independent reviewer system, not typed by the implementer.

## Report shape

The independent review agent signs a JSON object containing:

```json
{
  "schema_version": 2,
  "task": "pr:1004",
  "reviewed_head_sha": "<40-character PR head SHA>",
  "reviewed_base_sha": "<40-character PR base SHA>",
  "outcome": "ACCEPTED",
  "reviewer_role": "security-reviewer",
  "reviewer_session": "<reviewer-generated session id>",
  "implementation_role": "backend-engineer",
  "implementation_session": "<recorded implementation session id>",
  "reviewed_files": ["scripts/merge_readiness.py"],
  "rationale": "<specific evaluation of solution>",
  "risk_assessment": "<risks, limitations and mitigations>",
  "findings": [],
  "review_nonce": "<64 lowercase hex digits>"
}
```

The signing service must derive the actual reviewer identity/session from its authenticated context and produce the decision and narrative after performing the review; do **not** sign a payload submitted unchanged by the implementer. Never disclose the signing key in PRs, comments or logs. Empty findings are permitted only when the reviewer explicitly found none, with a substantive rationale.

For dispatch, provide `signed_report_b64` (base64 of the exact canonical JSON bytes) and `report_hmac_sha256` (hex HMAC over those bytes). Both are transport values, not trust by themselves.

## Bootstrap / rollout

A workflow is not dispatchable from default branch until it exists there. As long as `solution-review.yml` exists only in draft PR #1004, a successful trusted-master evidence run is **not** achievable. The owner must separately approve a safe bootstrap of the trusted workflow onto the default branch, then configure the isolated reviewer signing service and protected repository secret. Do not mark #1004 merge-ready or weaken quality gates to bypass bootstrap.

Independent security review must verify that the signing credential cannot be obtained by the implementation role and that only trusted master code can read it.

# Agent policy consistency and contract impact

Tracking: #685. Related: #604, #607, #663. Baseline: master
9991b057e8c54e32e06738c23ca2d128b672bdb7.

Status: contract accepted (head 9eab78b, test blob 7cbabde); implementation
published to branch contract/685-agent-policy-consistency; CI and independent
solution review pending before maintainer merge.
The policy checker verifies declared configuration, not natural-language intent or
runtime tool authorization. It does not grant capabilities.

## Incident

In #655, the Work Coordinator profile limited tools to read/search while its skill
allowlist authorized docs-sync. A legacy catalog assertion affirmed that permission.
A new negative regression contradicted that assertion. Reviewing only the new tests
missed the old expectation. The code and the old test agreed with each other, but
violated the role boundary.

Sources:
- https://github.com/maksimp6/Chat/issues/663#issuecomment-5924999567
- https://github.com/maksimp6/Chat/pull/655#issuecomment-5924775445
- https://github.com/maksimp6/Chat/pull/655#issuecomment-5924999733

## First automatic slice

Use the existing public entrypoint:

`python tests/validate_skills.py`

The entrypoint must return nonzero for policy contradictions, even when an existing
catalog test agrees with the erroneous registry. Keep its current skill-format
checks. No new workflow, token permissions, runtime imports or model calls.

Implement a stdlib-only helper at `scripts/check_agent_skill_policy.py`, with a
standalone CLI accepting `--root` for controlled fixtures. By default use the
repository root. A policy failure produces a bounded deterministic diagnostic and
exit code 1; valid inputs exit 0. Never write files or access network.

### Versioned metadata

Use `docs/agents/skill-effects.json`:

```json
{
  "version": 1,
  "roles": {
    "work-coordinator": {
      "profile": ".github/agents/work-coordinator.agent.md",
      "allowed_effects": ["read"],
      "optional": true
    },
    "operations-observer": {
      "profile": ".github/agents/operations-observer.agent.md",
      "allowed_effects": ["read"],
      "optional": false
    },
    "security-reviewer": {
      "profile": ".github/agents/security-reviewer.agent.md",
      "allowed_effects": ["read"],
      "optional": false
    }
  },
  "skills": {
    "docs-sync": {"required_effects": ["read", "edit"]}
  }
}
```

This example omits the other seven skills; implementation must classify EVERY
referenced skill after reading its actual procedure. Known effect names:
read, edit, deploy, merge, permissions. Unknown values or incomplete classification
fail closed. The three constrained roles must remain represented with read-only
allowed effects; metadata cannot silently authorize edit/deploy/merge/permissions.
Read-only metadata is an independent constraint on the registry, not a copy of it.

Read `ROLE_SKILL_ALLOWLIST` from `agent_skills/registry.py` using safe AST literal
parsing. Do not import/execute that file. Dynamic or duplicate assignments and
malformed role/skill values are invalid. No fallback to an empty allowlist.

For each referenced skill require a classification and an existing SKILL.md. For
each constrained role reject a selected skill whose effects exceed its allowed
effects. Require its matching role profile, and reject explicit edit/write tools
on a read-only role. Execute alone is not evidence of mutation: existing
read-mostly reviewer/observer profiles use safe validators. Do not expand or
rewrite their tools to make the checker pass.

Work Coordinator is optional only while BOTH its registry entry and profile are
absent on master. Report NOT_PRESENT explicitly. If either exists, require the
other and validate its policy. This makes the check useful before #655 merges;
fixtures must reproduce that role now.

Reject duplicate JSON keys, invalid schema/version, unknown effects, incomplete
required role metadata, missing files, unsafe relative paths and symlinks. Emit
stable diagnostics identifying role/skill/path and failure class without dumping
file bodies. Valid metadata does not prove that its semantic classifications are
correct: independent reviewer checks classifications against skill procedures.

### Behavioral contract tests

Test Engineer uses controlled repository fixtures and the ACTUAL public
`tests/validate_skills.py` entrypoint. Copy current validation/registry source
and real skill structures into a temporary root; change only fixture policy data.
A helper may be copied when it exists, but tests must not implement its behavior
or inject a mock validator.

On the pre-implementation base, a well-formed role/skill fixture violating
read-only policy must incorrectly exit 0; an accepted negative test must expose
that as executed RED. Import/collection errors or a missing future helper do not
count as semantic RED evidence. After implementation the SAME tests require
nonzero with the relevant bounded diagnostic.

Cover:
1. Work Coordinator + docs-sync violates read-only policy.
2. Correct read-only skill selection passes.
3. Reviewer/observer safe validators with execute tools remain permitted.
4. Referenced skill missing classification fails.
5. Unknown effect or invalid metadata/schema/version fails.
6. Read-only profile with edit/write tools fails.
7. Optional role absent on both surfaces reports NOT_PRESENT and passes.
8. Optional role present on only one surface fails.
9. Missing skill/profile or traversal/symlink inputs fail.
10. Dynamic/duplicate registry assignment and duplicate JSON keys fail.
11. Diagnostics order is deterministic; checker makes no repo writes/network calls.

Include a docs-sync mutation fixture even if a fixture catalog assertion also
agrees with its selection; test agreement cannot disable the policy gate.

### Diagnostic tokens

The checker must emit exactly one stable class token per failure in stdout or stderr.
A bounded diagnostic identifies role/skill/path and the failure class token; it does
not dump file bodies. Tests assert `returncode != 0` AND at least one expected token
in combined stdout+stderr — a generic crash that exits nonzero does not satisfy them.

| Token | Failure class |
| --- | --- |
| `POLICY_VIOLATION` | Skill requires effects exceeding the role's `allowed_effects`; or a read-only role profile lists edit/write tools. |
| `MISSING_CLASSIFICATION` | A skill referenced in a role allowlist has no entry in `skill-effects.json`. |
| `SCHEMA_ERROR` | Invalid/unsupported `version`; unknown effect name; duplicate JSON keys; `allowed_effects` grants edit/deploy/merge/permissions on a constrained read-only role; or other `skill-effects.json` invariant violation. |
| `UNSAFE_PATH` | A role profile path or skill directory is a symlink, contains `..`, or otherwise escapes the repository root. |
| `REGISTRY_ERROR` | `ROLE_SKILL_ALLOWLIST` has a dynamic mutation (`dict["k"] = v`) or a second assignment after the literal definition. |
| `ONE_SIDED_OPTIONAL` | An optional role is present on exactly one of registry or profile, but absent from the other. |
| `MISSING_FILE` | A required file is absent: `skill-effects.json`, a constrained role profile, or a referenced skill's `SKILL.md`. |
| `MISSING_ROLE_METADATA` | A role with a registry entry or profile is absent from the `roles` map in `skill-effects.json`. |

Tokens are stable identifiers; the implementer may surround them with context prose.
The `NOT_PRESENT` token must appear in output when an optional role is absent from
both surfaces and the check passes.

## Contract impact map

Before contract acceptance, record one compact map for each changed rule:

| Field | Required evidence |
| --- | --- |
| Rule | Intended behavior and authoritative policy source |
| Implementation | Public entrypoint and relevant production paths |
| Existing tests | Related positive and negative expectations |
| New tests | Required regressions and exact expected RED cases |
| Superseded expectations | Old assertion, reason, independent amendment approval |
| Documentation | Matching claims or explicit OPEN limitations |
| Provenance | Exact head and accepted test blobs |
| Responsibility | One owner, next stage and actual capability/blocker |

Team Lead reviews the whole affected map before Backend/Infra handoff. Test
Engineer searches related assertions, including existing catalog expectations.
Implementation preserves accepted tests; obsolete expectations are amended only
with independent review. Reuse #607 for machine protection rather than a second
approval mechanism. Docs Engineer separates shipped behavior from OPEN design.
Automatic detection of arbitrary prose/test semantic contradictions is not promised.

## Delivery stages

Contract document and behavioral RED tests -> independent contract review ->
different Infra Engineer implements helper/metadata/validation hook -> full
exact-head CI and coverage -> independent solution review -> synchronized,
protected maintainer merge. Keep working PR draft. Do not alter #655's branch.

Process Governor owns policy/templates and nonprivileged validation scope, never
application production code, self-approval or self-merge. Preserve existing
protected-master, single-vendor-review and owner-approval boundaries.

## Measurement

Baseline incident: two test-contract inconsistencies and one stale prose claim
in #655 recovery. Track unexpected failures after acceptance, amendment rounds,
checker runtime and future detected drift. Do not claim measured token savings
without observed before/after data.

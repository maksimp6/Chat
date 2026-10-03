# Agent policy consistency and contract impact

Tracking: #685. Related: #604, #607, #663. Baseline: master
9991b057e8c54e32e06738c23ca2d128b672bdb7.

Status: all 43 behavioral contract cases independently accepted; implementation
published on draft #686, branch `contract/685-agent-policy-consistency`.
Implementation checkpoint `5bc4c1bb70395c87c1fe9ef4c8d4eb165266503f` passed
full CI. Whole-solution acceptance remains PENDING. This documentation checkpoint
requires fresh full CI before independent solution review and protected maintainer
merge; these changes are not shipped on master.
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
| `SCHEMA_ERROR` | Invalid/unsupported `version`; unknown effect name; duplicate JSON keys; missing/invalid `required_effects` or constrained-role `allowed_effects`; non-read `allowed_effects`; unsupported profile `tools:` forms such as nested lists; or other metadata invariant violation. |
| `UNSAFE_PATH` | An unsafe constrained-role profile or referenced skill path, including traversal/escape or a symlinked profile, skill directory, or `SKILL.md`. |
| `REGISTRY_ERROR` | Invalid/nonliteral or duplicate allowlist definition; direct rooted assignment, augmented assignment, deletion, or known mutating method call, including calls inside assignment RHS or wrappers. Benign read methods such as `.get()` are permitted. |
| `ONE_SIDED_OPTIONAL` | An optional role is present on exactly one of registry or profile, but absent from the other. |
| `MISSING_FILE` | A required checker/registry/metadata file, constrained-role profile, or referenced skill directory/`SKILL.md` is absent. |
| `MISSING_ROLE_METADATA` | Required constrained-role metadata is absent; or an optional role present on both registry/profile surfaces is absent from the `roles` map. |

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

## Accepted provenance and implementation checkpoint

Accepted test blobs are frozen and remain byte-identical at the implementation
checkpoint. Contract acceptance does not constitute whole-solution acceptance.

| File | Cases | Accepted blob | Acceptance head and review |
| --- | --- | --- | --- |
| `tests/test_agent_skill_policy_consistency.py` | 23 | `7cbabde0e9240a2fbfd093324f89741227aeda67` | `9eab78b6bd0466b1f2cf7d14656c141ba34d01c0`; [CONTRACT_ACCEPTED](https://github.com/maksimp6/Chat/pull/686#issuecomment-5926091215) |
| `tests/test_agent_skill_policy_supplement.py` | 9 | `eb96fc5cfc71bf7fd4e6ced2d40f8a85974ed3b5` | `bd4b15bd81edfa626e12e88bd68b544ab5203e08`; [CONTRACT_ACCEPTED](https://github.com/maksimp6/Chat/pull/686#issuecomment-5927352373) |
| `tests/test_agent_skill_policy_edges.py` | 8 | `f0e74d2667df216b79332daefe3a15978828044a` | `e90cf1f7497f28692c6d3a55142b004cca506664`; [CONTRACT_ACCEPTED](https://github.com/maksimp6/Chat/pull/686#issuecomment-5927917298) |
| `tests/test_agent_skill_policy_schema_reads.py` | 3 | `5aadfdff5dd6b1637b04e88f774521c2174a7b79` | `8ddd21855839316b729744414019b77a4ddb1c83`; [CONTRACT_ACCEPTED](https://github.com/maksimp6/Chat/pull/686#issuecomment-5928545401) |

At implementation head `5bc4c1bb70395c87c1fe9ef4c8d4eb165266503f`, helper blob
is `b16cdd5f097c647d5587459a799b1140a7250294`; public validator blob is
`f68205f72a03d852070b65f91b14b86503b19948`.
[Full CI 36843435478](https://github.com/maksimp6/Chat/actions/runs/36843435478)
passed: Application 1611 passed/8 skipped; PostgreSQL 1616 passed/3 skipped with
37-table restore verified; Android passed. All 43 contract cases are GREEN.
[Format](https://github.com/maksimp6/Chat/actions/runs/36843435475),
[Security](https://github.com/maksimp6/Chat/actions/runs/36843435686), and
[CodeQL](https://github.com/maksimp6/Chat/actions/runs/36843435575) also passed on
that head. These results precede this documentation commit; its fresh CI and
independent whole-solution review remain the next stage.

Earlier independent solution reviews returned SOLUTION_CHANGES_REQUIRED:
[23-case checkpoint `ad2fa871bd719e781df727a062efbd3a79c8035d`](https://github.com/maksimp6/Chat/pull/686#issuecomment-5926832751),
[32-case checkpoint `1dcda5a352a39fd4273da47e6689f9b962ada39c`](https://github.com/maksimp6/Chat/pull/686#issuecomment-5927624064),
and [40-case checkpoint `b462ca45f545bf1cf6f11c8ebe4b482b9d0017de`](https://github.com/maksimp6/Chat/pull/686#issuecomment-5928250312).
Their GREEN test counts did not establish whole-contract correctness; amendments
added the cause-specific cases below.

### Current source bounds

The public entrypoint requires the helper and runs the policy gate BEFORE importing
`agent_skills.registry` or calling `SkillRegistry.validate_all()`. The helper
reads the registry through AST literal analysis without executing it. Every skill
referenced by any role requires classification and filesystem validation, including
skill-directory and `SKILL.md` symlink checks. Effects-subset and profile checks
apply to the three constrained roles.

Mutation detection walks every `ast.Call` for known mutators on Name/Subscript/
Attribute receivers directly rooted at `ROLE_SKILL_ALLOWLIST`. This covers direct
rooted calls inside wrappers as well as assignments. Aliases, call-return receivers,
and arbitrary dataflow remain untracked. The earlier wrapper limitation at
`b462ca45` is historical, not the current implementation's behavior.

Profile tool parsing supports flat inline arrays and block lists of identifiers,
optionally enclosed in matching quotes, with comments outside quotes. Unsupported
nested forms produce SCHEMA_ERROR. This is a bounded grammar, not full YAML.
Safe execute validators remain permitted. Work Coordinator absent on both surfaces
reports NOT_PRESENT; one-sided presence produces ONE_SIDED_OPTIONAL.

Source comparison at `8ddd21855839316b729744414019b77a4ddb1c83` found no mismatch
between metadata blob `8fd4febf4da1d70a90cb70d2ab2a45d016d15848` and all eight
actual skill procedures: docs-sync, issue-to-pr, and alice-runtime-debugging require
read/edit; cloudru-change requires read/edit/deploy; github-ci-diagnosis,
github-pr-readiness, security-review, and release-readiness require read.
In particular, alice-runtime-debugging step 6 adds a regression test; cloudru-change
steps 6–8 apply/verify/rollback subject to its existing approval boundary. These
classifications describe declared effects and grant no runtime authorization;
independent whole-solution review must check the actual sources again.

OPEN #655 compatibility is tracked in
[#663's source-based finding](https://github.com/maksimp6/Chat/issues/663#issuecomment-5928035701).
At #655 head `6ee43d9e6b1ec43a465cc6717830e8d20599a31f`, the read/search
Work Coordinator selects alice-runtime-debugging, whose procedure requires edit.
Retaining that selection after synchronization with this change should produce
POLICY_VIOLATION. This is a source-based inference; #655 remains a separate lane
for its owner/maintainer to resolve during protected synchronization.

## Supplemental tests

Independent solution review (SOLUTION_CHANGES_REQUIRED) confirmed five failure groups
not covered by the accepted 23-case primary file.  Supplemental cases live in
`tests/test_agent_skill_policy_supplement.py`; primary blob
`7cbabde0e9240a2fbfd093324f89741227aeda67` unchanged.

"public" = `tests/validate_skills.py`; "standalone" = `scripts/check_agent_skill_policy.py --root`.
Standalone fixtures isolate checker diagnostics from legacy skill-format checks.
Historical fixture comments describe older implementation order; the current public
gate runs before registry import and skill-format validation.

| Case | Group | Entrypoint | Targeted violation | Required token |
| --- | --- | --- | --- | --- |
| 1 | A2 | public | Missing helper silently skipped (`if helper.exists()`) | `MISSING_FILE` |
| 2 | A1 | public | Registry module imported before gate; version=99 sentinel proof | `SCHEMA_ERROR` + sentinel absent |
| 3 | B | standalone | `ROLE_SKILL_ALLOWLIST.update(...)` method-call mutation | `REGISTRY_ERROR` |
| 4 | C | public | Profile `tools: ["read","edit"]` quoted-array representation | `POLICY_VIOLATION` |
| 5 | C | public | Profile `tools:` YAML block sequence containing edit | `POLICY_VIOLATION` |
| 6 | D1 | public | Skill classification `{}` — no `required_effects` key | `SCHEMA_ERROR` |
| 7 | D2 | public | Optional role present on both surfaces, absent from `roles` map | `MISSING_ROLE_METADATA` |
| 8 | E | standalone | Skill name `../../outside` traversal in literal allowlist | `UNSAFE_PATH` |
| 9 | — | public | Referenced skill unclassified in unconstrained role | `MISSING_CLASSIFICATION` |

Case 9 was independently accepted with the nine-case amendment: the original
contract already required classification for every referenced skill. No authority
was expanded.

Historical RED proof: at `61341e0`, cases 1 and 3–9 exited 0 for their violations;
Case 2 was an invalid fixture failure because a sentinel preceded
`from __future__` and caused SyntaxError. At corrected head
`bd4b15bd81edfa626e12e88bd68b544ab5203e08`,
[CI 36833830981](https://github.com/maksimp6/Chat/actions/runs/36833830981)
executed all nine behavioral RED cases. Cases 1 and 3–9 were exit-0 violations;
Case 2 returned SCHEMA_ERROR but wrote the import sentinel, failing the required
sentinel-absent assertion. This gate-after-import behavior belongs to that historical
head. The current gate precedes registry import.

## Edge regressions

The second solution review produced eight independently accepted cases in
`tests/test_agent_skill_policy_edges.py`.

| Case | Targeted behavior | Entrypoint | Required token |
| --- | --- | --- | --- |
| 1 | Assigned `ROLE_SKILL_ALLOWLIST.update(...)` mutation | standalone | `REGISTRY_ERROR` |
| 2 | Assigned `ROLE_SKILL_ALLOWLIST.pop(...)` mutation | standalone | `REGISTRY_ERROR` |
| 3 | Subscript-rooted set `.add(...)` mutation | standalone | `REGISTRY_ERROR` |
| 4 | Unconstrained-role referenced skill directory missing | public | `MISSING_FILE` |
| 5 | Unconstrained-role referenced skill directory symlinked | standalone | `UNSAFE_PATH` |
| 6 | Referenced `SKILL.md` file symlinked | standalone | `UNSAFE_PATH` |
| 7 | Block-list `edit` followed by an inline comment | public | `POLICY_VIOLATION` |
| 8 | `required_effects` is a mapping instead of an array | public | `SCHEMA_ERROR` |

Historical RED proof at `e90cf1f7497f28692c6d3a55142b004cca506664`:
[CI 36837925059](https://github.com/maksimp6/Chat/actions/runs/36837925059)
executed eight cause-specific failures in both suites, with the prior 32 cases
GREEN. Application recorded 1600 passed/8 skipped plus eight failures; PostgreSQL
1605 passed/3 skipped plus eight failures. All targeted violations exited 0,
without collection/import errors. The accepted directory-symlink fixture keeps its
valid target outside skill discovery at `root / "safe-target"`.

## Schema and benign-read regressions

The third solution review produced three independently accepted cases in
`tests/test_agent_skill_policy_schema_reads.py`.

| Case | Targeted behavior | Entrypoint | Required result |
| --- | --- | --- | --- |
| 1 | Delete only constrained-role `allowed_effects`; retain other role fields | public | Nonzero + `SCHEMA_ERROR` |
| 2 | Profile `tools: [[read], [edit]]` is an unsupported nested list | public | Nonzero + `SCHEMA_ERROR` |
| 3 | Assigned `ROLE_SKILL_ALLOWLIST.get('operations-observer')` is a benign read | standalone | Exit 0 |

Historical RED proof at canonical accepted head
`8ddd21855839316b729744414019b77a4ddb1c83`:
[CI 36842134671](https://github.com/maksimp6/Chat/actions/runs/36842134671)
executed exactly these three cause-specific failures in both suites; the prior
40 cases remained GREEN. The two invalid inputs incorrectly exited 0; the positive
read case incorrectly returned REGISTRY_ERROR. They are now GREEN at the
implementation checkpoint above. Frozen test comments remain historical evidence.

## Measurement

Existing coverage configuration excludes `scripts/*` and tests. The application
diff-coverage report at the implementation checkpoint says "No lines with coverage
information"; it does not establish checker coverage or a 100% checker claim.
Executed public/standalone contract behavior is the checker evidence.

Baseline incident: two test-contract inconsistencies and one stale prose claim
in #655 recovery. Track unexpected failures after acceptance, amendment rounds,
checker runtime and future detected drift. Do not claim measured token savings
without observed before/after data.

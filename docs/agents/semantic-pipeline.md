# Semantic citation checks

Canonical task: #1413. First implementation: PR #1435,
`scripts/semantic_pipeline.py` and `.github/workflows/semantic-work.yml`.

## Scope

The pipeline retrieves sentence candidates from open Issues labelled `rag-source`
using BM25. An explicitly allowed model call can select candidates and propose an
answer. Mechanical checks establish only that a nonempty quotation occurs in the
specified candidate and hashed source snapshot. They do not establish relevance,
semantic entailment, source truth, or a real device outcome.

Every proposed claim therefore has `assessment: INSUFFICIENT_EVIDENCE` and
`semantic: NOT_PERFORMED`. A model can produce an irrelevant but verbatim quote;
the report must keep that claim unverified. The broader four-result assessment,
contradiction resolution and dependency invalidation in #1413 remain future work.

## Evidence and outcomes

The report contains the proposed claim, stable content-derived ID, rule version,
source URLs, extracted-text and full-body SHA256, exact character offsets in both
texts, source update/retrieval times, corpus fingerprint, code SHA when available,
and the provider response ID and usage. Source hashes identify a fetched snapshot;
they do not prove that an editable Issue remains unchanged after retrieval.

The report uses a fenced JSON block for untrusted query, answer and quotations.
The fence is longer than any backtick run in the data. Structural status labels
are produced by code, never by the model.

| Outcome | Meaning |
| --- | --- |
| `CITATIONS_ONLY` | Citation identity checked; claim meaning remains unverified |
| `MODEL_REFUSAL` | An actual completed model call returned a consistent refusal |
| `NO_CANDIDATES` | BM25 found no candidates; the model was not called |
| `CONTRACT_VIOLATION` | Invalid query/schema/citation or RED nonrefusal; no publication |
| `PROVIDER_ERROR` / `TECHNICAL_ERROR` | Transport or provider failure; no automatic retry |
| `PUBLICATION_UNCERTAIN` | A POST was attempted but confirmation failed; inspect the existing Issue before retrying |

RED is `PASS` only after a model call returns a valid refusal. A search miss in RED
mode is `NOT_RUN`. Offline tests of a supplied model response prove the publication
guard, not the behaviour of the live model.

## Admission and bounds

Offline controls: `python scripts/semantic_pipeline.py --selftest` and
`python -m pytest -q tests/test_semantic_pipeline.py`.

The CLI defaults to no paid call. `--allow-model-call` is an explicit admission
flag, used only within an owner-approved scope and budget; credentials alone are
not approval. The manual workflow accepts only the repository owner, defaults to
no model call and requires a budget approval reference when enabling one. It has
no Issue-label trigger and does not cancel an in-flight run in favour of a new run.
Both initiating actors must be the owner and only the first run attempt is admitted;
reruns cannot replay the paid call. A new experience requires a new explicit dispatch.
The reference records external approval; it is not a machine-enforced currency cap.

At most one model request is made. Input is bounded to 8,000 query characters,
60,000 prompt/instruction characters and 2,000,000 corpus-body characters; output
is bounded to 4,096 tokens and 262,144 response bytes. These are resource limits,
not a guarantee of price. Usage/cost absent from provider evidence remain unknown.
Publication requires a positive Issue number and a token. No model or POST retry
is performed after an uncertain result.

## Quality experiment

Live experiments require separate approved scope/call count/budget. Freeze the
corpus, queries, hidden expected answers, retrieval parameters and code revision.
Measure retrieval recall separately from selection quality over the candidates,
false confirmations, unsupported refusals, latency and usage. A model downstream
of BM25 cannot recover an excluded candidate. This first slice neither executes
that experiment nor claims improved search quality.

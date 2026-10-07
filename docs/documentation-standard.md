# Documentation standard

Status: current  
Owner: Docs Engineer  
Canonical issue: #978

Alice Pro documentation follows a docs-as-code model. Documentation must describe the same system that code, configuration and tests implement; it must never turn roadmap intent into shipped behavior.

## Information model

Use a Diátaxis-inspired document kind:

- **tutorial** — guided learning path;
- **how-to** — accomplish one practical goal;
- **runbook** — operational procedure with safety, rollback and verification;
- **reference** — precise API/config/contract facts;
- **explanation** — architecture, rationale and boundaries;
- **decision** — durable ADR;
- **historical** — audit, experiment or superseded evidence;
- **index** — navigation only.

Do not mix a tutorial, reference dump and operational runbook into one page.

## Lifecycle status

Every Markdown file under `docs/` is inventoried in `docs/catalog.json` with one status:

- `current` — valid description of the current repository contract;
- `experimental` — implemented but not a stable/canonical contract;
- `planned` — intended behavior that is not shipped;
- `historical` — evidence retained for history, not current instructions;
- `retired` — no longer used; kept only for compatibility/history.

A retired document cannot be canonical.

## Sources of truth

Use the narrowest authoritative source:

1. code/config/tests for implemented behavior;
2. canonical Issues for unfinished scope and acceptance;
3. workflows/runbooks for operational procedure;
4. ADRs for durable decisions;
5. docs for explanation/navigation, never as proof that code exists.

When sources disagree, fix the documentation or open a focused implementation issue. Do not make the documentation "win" over reality.

## Evidence language

Keep these states distinct:

- **planned** — issue/decision exists;
- **implemented** — code/config landed;
- **tested** — deterministic tests passed;
- **verified live** — the real external/runtime scenario passed on an identified revision.

A green CI run is not production acceptance. A local OAuth/MCP/browser test is not evidence that ChatGPT or Cloud.ru production works.

## Runbooks

Operational runbooks must state:

1. purpose and scope;
2. prerequisites and required permissions;
3. non-secret inputs;
4. safe procedure;
5. expected evidence;
6. failure modes;
7. rollback/recovery;
8. post-change verification;
9. owner/canonical issue.

Never include real secret values.

## References and examples

Examples must be executable or explicitly marked illustrative. Do not document commands, environment variables, endpoints, files or services that do not exist unless the document status is `planned` and the text says so.

Legacy behavior that still runs is documented as compatibility behavior until verified cutover. Do not silently delete a working path merely because a target architecture exists.

## Style

- Prefer short sections and descriptive headings.
- Put the conclusion/status near the top.
- Use exact file paths, commands and issue/PR references where they materially prove a claim.
- Avoid duplicating the same contract across multiple files; link to the canonical page.
- Keep Russian or English consistently within a section.
- Use Mermaid only when it clarifies relationships better than prose.
- Do not copy secret values, tokens, cookies, private keys or sensitive logs into docs.

## Change workflow

Documentation changes follow the same protected flow:

`Issue → branch → docs/tests → PR → exact-head CI → review → merge → verification`

Docs-only changes must remain cheap: Markdown changes alone must not select application, PostgreSQL, Android, MCP or infrastructure suites.

## Automation

Run:

```bash
python scripts/check_docs.py
```

The check validates the catalog against every Markdown file under `docs/`. The ratchet will grow incrementally to cover internal links, changed-doc formatting, command/path existence and freshness without forcing a giant rewrite of historical debt.

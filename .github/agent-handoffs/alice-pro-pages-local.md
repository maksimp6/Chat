# Alice Pro Pages: local preparation handoff

## Admission and scope

- Canonical task / accepted contract: explicit user delegation in source thread
  `01a1217a-d759-71e5-9ffd-b3f4a97650c5`, 2026-10-11. Prepare a Russian/English,
  grey, responsive static introduction for GitHub Pages; initially local files only. On 2026-10-11 at 12:32:40 UTC the user
  explicitly approved maxxxpavlov.online and creation of a Draft PR in develop
  (reply «Да», evidence supplied by parent in the same source thread).
- Primary owner: current Codex agent, Frontend/Docs role. Actual capabilities:
  shell checkout, Git, filesystem edits, local Chromium, read-only GitHub connector.
  No additional agents or paid browser runs dispatched.
- Stage: approved-URL implementation and Draft PR publication; await exact-head CI.
- Write branch: `docs/alice-pro-pages-local`, based on freshly fetched develop
  `9d658587871007a041f1981c084a80d2de6d78bd`; remote develop rechecked unchanged
  at closeout. Master inspected at `51aaf8273f4f465fe78aa0a79e76a22d8f141e35`.
- Instructions read: AGENTS.md, CONTRIBUTING.md, role-based-agent-office.md,
  Frontend Engineer profile, issue-to-pr and docs-sync skills. docs-sync applied.
- The user's explicit develop/local-only instruction overrides normal master/PR
  publication defaults. Branch push and Draft PR are now authorized. Pages settings, CNAME, DNS,
  CI, application, secrets, protection, and paid resources remain out of scope.
- Stop before merge, enabling Pages, domain binding or DNS. Next owner: parent
  task / project owner for review and a separate deployment approval. Follow
  github-ci-diagnosis for failures and exact-head checks from github-pr-readiness;
  a Draft PR is deliberately not merge-ready.

## Duplicate and domain review

All 394 fetched remote branch trees were scanned for landing/index HTML, sitemap,
robots and CNAME paths. Only application entries `templates/index.html` and
`r/frontend/index.html` were found; neither is a static project introduction.
No competing landing page found. Do not replace these application entries.

CLI GraphQL and REST PR listing returned `Forbidden`. Read-only GitHub connector
succeeded: reviewed the latest 100 open PR summaries and targeted landing/Pages
search (no matches). This is not an exhaustive manual review of every old PR.
Related domain work: `docs/platform-deployment-domain-boundary`, PR #960,
and especially [PR #943](https://github.com/maksimp6/Chat/pull/943): its planned
app acceptance probe expects root 401 and /healthz 200 on maxxxpavlov.ru.
The user resolved the host split: maxxxpavlov.online for this static project
page, maxxxpavlov.ru for the separate app. The probe remains unchanged.

## Deliverables

- `website/`: index.html, styles.css, favicon.svg, robots.txt, sitemap.xml and
  .nojekyll. Under 17 KB, no JavaScript or third-party UI resources.
- `docs/deployment/alice-pro-pages.md`: local preview, evidence for copy,
  address/SEO decisions and a non-applied GitHub Pages/domain runbook.
- `tests/test_alice_pro_website.py`: four offline semantics, resource, approved SEO and link tests.

Canonical and Open Graph URL are https://maxxxpavlov.online/. Sitemap has one
matching location; robots allows crawling and names the absolute sitemap URL.
The preparatory noindex and placeholder sitemap template were removed. No CNAME
was added and no actual site publication is claimed.

## Validation, 2026-10-11

- Focused unittest and pytest: 4 passed.
- `bash scripts/check_code_rules.sh`: 66 passed, pinned mypy 2.4.0 / pytest 9.1.1.
  Initial attempt lacked mypy; installing the repository-pinned tool resolved it.
- Canonical `bash scripts/pre_push.sh`: passed (Ruff 0.16.9; existing local
  Prettier). Its glob does not cover website HTML/CSS; checked HTML separately.
- `python -m compileall -q .`: passed; pre-existing invalid-escape SyntaxWarning
  at tests/test_runtime_loader.py:134. No application edits.
- `python tests/validate_runtime_modules.py`: 6 modules, 0 violations.
- `python tests/validate_skills.py`: 8 validated skills.
- html5lib 1.1 strict HTML5 parser: passed. html-validate 10.5.0 recommended rules:
  passed with lowercase doctype/self-closing void style matching repository
  conventions; only those two style settings configured.
- Local Chromium, JS disabled: 393x851 mobile, 320x740 narrow, 768x1024 tablet,
  1440x1000 desktop. No horizontal overflow; skip link focuses main; feature and
  English anchor navigation works; no page errors. Enlarged text also reflows.
- Desktop/mobile screenshots inspected visually. Replaced an unsupported arrow
  glyph; removed smooth scrolling after an automation stability timeout. Final
  browser checks passed on all four viewports.
- axe-core 4.10.3: 0 violations, 33 passing checks on desktop and mobile. Its
  contrast check retained an incomplete result; manually calculated all text
  palette pairs, minimum 5.09:1 (normal text). Focus ring contrast 5.51:1.
- Local /Chat/ project-path HTTP test: HTML/CSS/favicon 200, styles applied,
  navigation correct, no overflow.
- All five unique external page URLs return HTTP 200; README anchor IDs verified.
  LICENSE initially returned transient 503, then 200 on a bounded recheck.
- `git diff --check`: passed. No runtime dependency added for browser tools.

Evidence stays local at `/workspace/alice-pages-evidence/`: final screenshots,
browser-results.json, accessibility.json, links.json, validation logs and browser
check scripts. Diff is saved outside the repository at
`/workspace/alice-pro-pages.patch`; source branch is authorized for push; PR/CI identity is reported in the parent handoff.

## Limits and next action

Physical Android and screen-reader testing were not available; Chromium mobile
emulation is not device evidence. Full backend pytest and Android builds were
not run locally; selected remote CI must be checked on the final pushed head.
Real public indexing is not tested. No production/deployment readiness or
ranking guarantee is claimed.

After green exact-head CI, keep the PR in Draft as requested; report its link,
head and checks. Merge needs separate approval. Then follow the runbook to
export only website contents to gh-pages, enable Pages and connect the approved
maxxxpavlov.online domain/DNS, each within explicit publication authorization.
The runbook uses current official GitHub documentation.

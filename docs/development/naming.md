# Naming conventions

One rule set for user-visible text, frontend code, files, Python modules,
branches and pull requests. `tests/test_naming_conventions.py` enforces the
mechanical rules. Existing violations are listed there as a shrinking
allowlist: rename an entry, then delete it from the list.

## User-visible text

- The interface language is Russian. Every visible label, `title`,
  `aria-label`, placeholder, status and error is written in Russian.
- Product and protocol names keep their own spelling: GitHub, MCP, SSH,
  Dozzle, Cloud.ru, Playwright. Generic words around them are translated:
  «Среда SSH», not «SSH Runtime»; «Отделы», not «Departments».
- A control says what it does: «Новый чат», «Обновить приложение». Errors say
  what failed and what to do next: «Не удалось загрузить расходы. Повторите
  позже.»
- One concept has one name everywhere. Use these terms:

  | Concept | Term |
  |---|---|
  | conversation | диалог |
  | assistant reply | ответ |
  | tool | инструмент |
  | execution trace | трассировка |
  | approval | подтверждение |
  | treasury | казна |

- Emoji may mark a message type in chat metadata (📊, ⏱️). They do not
  replace words in buttons or headings.

## Frontend code

- Files in `static/` use `snake_case.js` and are named after the feature they
  own (`trace_viewer.js`). A file name never describes a patch: no `_fix`,
  `_new`, `_old`, `_v2`, `_tmp`, `_auto`. Merge such code into the feature
  module or name the feature it adds.
- Public browser globals are `window.Alice<Feature>` in PascalCase
  (`AliceTraceSummary`, `AliceExecutionSurface`). Everything else stays inside
  the module IIFE.
- Functions and variables use `camelCase`. Constants use `UPPER_SNAKE_CASE`.
- DOM `id` and CSS classes use `kebab-case` and name what the element is
  (`model-modal`, `msg-meta`). No positional or numbered names such as
  `header-actions-2`; name the group instead.
- Chat message roles in the UI vocabulary are `user`, `bot` and `error`.
  Domain/API roles (`assistant`, `system`, `tool`) are mapped once, in
  `normalizeChatRole`.

## Python

- Modules and packages use `snake_case`. Implementation lives in packages
  (#430). The repository root holds only entrypoints and project metadata;
  `tests/test_repository_root_layout.py` rejects new root modules and lists
  the legacy ones still waiting to move.
- Classes use `PascalCase`, functions and variables `snake_case`, constants
  `UPPER_SNAKE_CASE`.
- Name a module after the capability it owns (`trace_manager`), never after a
  vendor quirk, ticket number or temporary state.

## Branches and pull requests

- Branch: `<type>/<issue>-<short-slug>`, for example `fix/714-chat-roles` or
  `feat/537-execution-surface`. Use `<type>/<short-slug>` when there is no
  issue. Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`.
  Agent sessions may be assigned a generated branch name; keep it rather than
  closing an open pull request just to rename the branch.
- Pull request and commit titles follow Conventional Commits:
  `<type>(<scope>): <imperative summary>`, at most 72 characters. Refer to the
  issue in the body (`Fixes #714`, `Refs #537`), not in the title.

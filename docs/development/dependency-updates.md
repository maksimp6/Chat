# Обновления зависимостей (Dependabot)

Конфигурация: [`.github/dependabot.yml`](../../.github/dependabot.yml). Её
инварианты проверяет `tests/test_dependabot_policy.py`.

## Version updates

| Экосистема | Каталоги | Частота |
|------------|----------|---------|
| pip | `/` (`requirements*.txt`) | еженедельно, понедельник |
| Gradle | `/android` | еженедельно, понедельник |
| GitHub Actions | `/` (`.github/workflows`) | еженедельно, понедельник |
| Docker | `/`, `deploy/chrome-worker`, `deploy/oauth-idp`, `deploy/remote-desktop-commander` | еженедельно, понедельник |
| npm | `/`, `deploy/chrome-worker`, `deploy/remote-desktop-commander` (каталоги с `package-lock.json`) | ежемесячно |

- Minor и patch обновления одной экосистемы приходят одним групповым PR на каталог
  (`*-minor-patch`). Patch-обновления больше не игнорируются, а группируются.
- Каждое major-обновление приходит отдельным PR: его проверяют и откатывают
  независимо от остальных.
- Cooldown 7 дней: новая версия предлагается не раньше чем через неделю после
  релиза.
- `android/requirements.txt` в version updates не входит: версии там согласованы с
  wheel-репозиторием Chaquopy и проверяются `tests/test_android_requirements.py`.
  Security updates для него приходят как обычно.

## Security updates

Группы в конфигурации не задают `applies-to: security-updates`, поэтому каждое
security-обновление остаётся отдельным PR. Cooldown и `open-pull-requests-limit`
на security updates не действуют, так что групповые и major PR их не блокируют.
Группировку security updates на уровне репозитория (настройка GitHub, не этот
файл) конфигурация не меняет.

## Какие проверки запускаются

Dependabot PR проходят тот же CI и тот же required check `CI required`, что и
остальные PR; branch protection не меняется. Набор jobs выбирается по изменённым
файлам ([platform-ci-routing.md](platform-ci-routing.md)):

| Изменённый манифест | Платформы |
|---------------------|-----------|
| `android/**` (Gradle) | Android |
| `.github/workflows/*.yml`, кроме `ci.yml` | Infrastructure |
| `deploy/<worker>/package*.json`, `deploy/<worker>/Dockerfile*` | MCP, Infrastructure |
| `requirements.txt`, `requirements-dev.txt`, `.github/workflows/ci.yml` | все |
| корневой `Dockerfile` | все |
| корневые `package.json`, `package-lock.json` | web и зависящие от него backend, Android, PostgreSQL, MCP |

Общие runtime-зависимости, корневой образ и web-инструменты затрагивают несколько
платформ, поэтому для них широкий прогон необходим.

# Проверки по платформам

CI определяет затронутые платформы по полному git diff. Удалённые пути также входят
в diff; имена передаются с NUL-разделителем. Ошибка чтения diff останавливает CI,
а неизвестный путь включает все платформы. Изменение самого CI или общих
зависимостей проверяется во всех затронутых окружениях.

## Исполняемые проверки

- `Infrastructure tests`: Cloud.ru, deployment и CI regression suites, без APK и
  PostgreSQL. Для infrastructure-only изменений действует changed-line coverage.
- `MCP worker tests`: Node-тесты Chrome, identity provider и Alice Dev, включая
  OAuth и credential handoff. Выполняются отдельно от Python application suite.
- `Application tests`: Python/web-контракт приложения и его существующие coverage
  gates. Изменения runtime Python дополнительно проверяют совместимость с SQL до
  завершения миграции Memory DB.
- `Android debug APK`: native Android/build изменения и общий web-контракт.
  `android/scripts/stage_python.py` пока включает общие Python-файлы в APK;
  классификация не объявляет эти файлы архитектурно независимыми и не меняет
  состав APK. Полное устранение этой зависимости требует отдельного изменения.
- `PostgreSQL integration`: SQL/backend compatibility, не зависимость MCP от БД.

Общее web-приложение включает проверки backend, Android, PostgreSQL и MCP.
Отдельные `deploy/chrome-worker`, `deploy/oauth-idp` и Alice Dev не считаются
изменением общего web UI и сами по себе не запускают Android.

## Защита от ложного успеха

`CI required` сверяет план с фактическими результатами jobs. Выбранная платформа
обязана завершиться `success`. `skipped` допустим только для невыбранной платформы.
Отсутствующий результат, отмена, ошибка классификатора или ошибка code-rules
останавливают итоговую проверку. Тесты самого классификатора входят в code-rules
и не исчезают при пропуске application suite.

Права GitHub и branch protection этим изменением не меняются. Успех CI доказывает
тесты данного SHA, но не успешный cloud rollout или подключение клиента к MCP.

# Аудит CLOSED Issues maksimp6/Chat: партия 2

25 из 91 Issue, закрытых 2026-10-04. Приоритет отдан completed, SQL/Memory DB, RDC/browser, secrets, CI и security. Master `dfda42c`.

| Issue | исходная цель | почему закрыт | evidence в master | verdict |
|---|---|---|---|---|
| #545 | Флейк разбора timestamp `b'45+00'` | not planned, без пояснения | `db.py:101,113` `PARSE_DECLTYPES` без конвертера; воспроизвёл `ValueError` при `microsecond=0`; фикс Codex `fbf5084` не опубликован | **reopened** |
| #784 | Контракт Alice Platform: docs+config+code | completed, вручную (код из #781) | `alice_platform/`, `config/alice/*.yaml`, `docs/platform/*.md`, `tests/test_platform_*.py`, `ci.yml:96` `python -m alice_platform validate` | verified closed |
| #757 | CI: Python suite ≤15 с | **auto-close** связанным PR #762, хотя PR писал «Do not close #757» | цели не достигнуты (91 с / 421 runner-с) | covered by #761 (оставил пояснение) |
| #691 | Закрыть инцидент утёкших ключей Yandex | completed, подтверждение владельца | в комментарии явно сказано, что телеметрию не восстанавливали; алерты сканера ушли в #695 | verified closed |
| #532 | Метрика hot-file churn | completed | `scripts/hot_file_metrics.py`, `tests/test_hot_file_metrics.py`, `ci.yml:81` | verified closed |
| #477 | Адаптер Cloud.ru Secret Management | completed | `cloud/cloudru/secret_management.py`, `secret_management_refs` в `provider_credentials.py:181`, `docs/security/cloudru-secret-management.md`; миграция потребителей в #755 | verified closed |
| #421 | Отчёт frontend coverage без блокировки | completed | `tests/report_frontend_coverage.py:72` («V8 function coverage»), `ci.yml:137,183` (артефакт), порога нет | verified closed |
| #482 | Дашборд Observer | completed | #687, #696 смержены; преемник — открытый #810 | verified closed |
| #717 | Пробный диалог native Claude | completed | backend Claude удалён в #844 (`49bc7e6`), испытание неактуально | verified closed |
| #782 | RED-контракт запуска без SQL | duplicate | Parent #776: «Minimal implementation slice»; смержены #856, #857 | covered by #776 |
| #769 | Типизированный storage без SQL | not planned, без пояснения | архитектура заменена file-native #776 | covered by #776 (дописал ссылку) |
| #442 | Убрать SQLite-специфику, CI на PG | not planned | устарело после #776 (Wave 4) | covered by #776 (дописал ссылку) |
| #447 | Бэкапы PG и лимит бюджета | not planned, без пояснения | backup/restore — #776/#857; cost evidence — #783; budget guard из #459 в master | covered by #776 (дописал ссылку) |
| #427 | Production на Cloud.ru Container Apps | not planned | решение про Managed PG заменено #776; платформа — #783, выпуск — #104, RDC — #787 | covered by #783 (дописал ссылку) |
| #423 | Защитить master, production-деплой | not planned | Supabase устарел; разрыв нативного ruleset описан в #496 | covered by #496 (дописал ссылку) |
| #525 | Обязательный merge-readiness gate | duplicate | #496 прямо пишет: «#525 … DoD was not achieved» | covered by #496 |
| #760 | Эксперименты с латентностью CI | duplicate | открытый #761 с тем же scope | covered by #761 |
| #751 | Chrome Worker вместо RDC за API Gateway | not planned | сделано иначе, без Gateway: #753, #758, #846, #855 | covered by #409 |
| #426 | Облачный браузинг без ограничений | duplicate | канонический браузер — #409 | covered by #409 |
| #479 | Cloud.ru KMS для шифрования бэкапов | duplicate, без пояснения | сценарий «шифровать бэкап PG» устарел; шифрования бэкапов Memory DB at rest нет ни в #776, ни в #755 | **needs canonical replacement** (решение за тобой) |
| #692 | Symlink в file routes, утечка путей | duplicate #695 | дефекты на месте: `file_routes.py:213-234`, `:24`, `:76` | covered by #695 (дописал конкретику) |
| #693 | Сырые исключения Termux | duplicate #695 | `tool_providers/termux.py:26,135,289`, `termux_system.py:44` | covered by #695 (дописал конкретику) |
| #694 | Уязвимый cryptography на Android | duplicate #695 | `android/requirements.txt:9` `cryptography==42.0.8` | covered by #695 (дописал конкретику) |
| #530 | Убрать прямой вызов из memory_extractor | duplicate | #433 «Current-master direct-call inventory» описывает именно его | covered by #433 |
| #455 | Guardrail-тесты единого AI pipeline | duplicate | #433: «add a regression guard that rejects new … direct model-call paths» | covered by #433 |

## Системные находки

- Второй механизм случайного закрытия: Issue, привязанный к PR через Development, закрывается при merge, даже если тело PR этого не требует (#757 ← #762). Первый механизм — `Closes #N` в коммитах (партия 1).
- В тексте открытого #409 остались устаревшие формулировки: #791 описан как незакрытый, а emulator уже смержен в #843. Это drift внутри Issue, docs он не затрагивает.

## Ждут решения

- #479: добавить «шифрование бэкапов Memory DB at rest» в #776 или считать, что требование снято.

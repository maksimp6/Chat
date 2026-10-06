# Аудит CLOSED Issues maksimp6/Chat: партия 1

Проверены Issues, закрытые с 2026-10-05 (22 из 271), master `dfda42c`.

| Issue | исходная цель | почему закрыт | evidence в master | verdict |
|---|---|---|---|---|
| #869 | Прояснить SSH и RDC в docs, решить судьбу `production-deploy.yml` | completed вручную через 2 мин, без PR | `docs/production-deployment.md` описывает только SSH/`PREVIEW_SSH_*`; `production-deploy.yml` на месте; PR #871 в draft | **reopened** |
| #847 | Быстрый hot-update Chrome ≤10 с | completed, PR #855 | `scripts/cloudru_chrome.py:57` `HOT_UPDATE_ORCHESTRATION_BUDGET_SECONDS = 10.0`, `hot_update_eligible()`, `tests/test_cloudru_chrome.py` | verified closed |
| #467 | Машиночитаемый каталог интеграций + 4 seed | completed, PR #866 | `config/integrations/catalog.json` (mcp, browser, sber_business, gigachat), schema, `tests/test_integration_catalog.py`, `docs/integrations/catalog.md:18` | verified closed |
| #551 | Разовая сверка docs с master | completed, PR #833 | убран #340 из README; дрейф дальше ведёт отдельная регулярная сверка docs (#867) | verified closed |
| #538 | Repository Intelligence: индекс + impact-запросы | **auto-close**: `Closes #538 minimal slice.` в squash-коммите `263d536` (#834) | есть только `query_affected()` (#834); frontend, churn, бенчмарк, аудит #531 не сделаны; #533 зависит от #538 | **reopened** |
| #823 | Allowlisted workflow_dispatch | not planned, PR #825 закрыт без merge | кода нет; в Issue записано решение не делать | verified closed |
| #822 | Formatter baseline + fail-closed статус Format | not planned (baseline чистый) | baseline чистый; но `format.yml` зелёный даже при diff, а `merge-readiness.yml:33` требует его | covered by #496 (дописал остаток) |
| #808 | Законы и property-тесты | duplicate | раздел «Property/law-based testing» в #604 | covered by #604 |
| #775 | HyperOS: прыгающие уведомления | not planned | решение владельца в комментарии; минимум перенесён в #650 | verified closed |
| #759 | Генератор гипотез | duplicate | #533 явно владеет границей fact/hypothesis через #433 | covered by #533 |
| #733 | Pet UI | not planned | решение владельца; frontend в #227 | verified closed |
| #681 | Planning tool | duplicate | раздел «Planning model» в #576 | covered by #576 |
| #671 | Зеркало private/public документов | not planned | скаффолда #672 в master нет | verified closed |
| #670 | USB recovery image | not planned | решение владельца; recovery в #783/#776 | verified closed |
| #666 | Локальные embeddings + бенчмарк | not planned | решение владельца; кода нет | verified closed |
| #652 | PyTorch CPU foundation | duplicate #666, а #666 not planned | `pytorch_adapter` нет; PR #657/#660 закрыты | verified closed (дописал, что scope снят намеренно) |
| #646 | App Surface Broker | not planned | решение владельца; поверхности в #409/#787/#643 | verified closed |
| #552 | 3D-печать: заказы и экономика | not planned | MVP в master (`printing3d/`, `docs/printing3d.md`, PR #553/#555); hardware снят намеренно | verified closed |
| #469 | GPU workloads | not planned | решение владельца; PR #790 закрыт | verified closed |
| #468 | Kwork sales workflow | not planned | решение владельца | verified closed |
| #466 | Yandex Direct | not planned | решение владельца | verified closed |
| #340 | Хранилище артефактов, Google Drive | not planned | `runtime/storage.py` остался как локальный фундамент; Drive снят; #551 фиксирует, что #340 исторический | verified closed |

Примечание: «verified closed» у not planned означает, что подтверждено явное решение владельца не делать задачу, а не реализация.

## Системная находка

Workflow `.github/workflows/alice.yml:172,176` и бот-ветки `claude/issue-*` пишут `Closes #N` в коммиты. При squash-merge это закрывает родительский Issue, даже если PR был только одним срезом (#538). Поиск по 572 коммитам master нашёл ещё два случая, `Closes #200` (`b830a6d`) и `Closes #102` (`bd299c8`). Их проверю в следующих партиях.

## Документация (для потока docs)

- `docs/production-deployment.md` описывает только SSH. Это предмет #869 и PR #871, правку docs здесь не делал.

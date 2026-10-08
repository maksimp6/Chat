# Аудит CLOSED Issues maksimp6/Chat: партия 3

25 Issues. В партию вошли два типа: Issues, закрытые merge PR, в тексте которого прямо сказано «slice», «remain» или «Do not close», и дубли/not planned без ссылки ни из одного открытого Issue. Master `dfda42c`.

Метод: выгрузил все 268 CLOSED Issues с комментариями и timeline. Для каждого нашёл PR, смерженный за 0–20 с до закрытия, и проверил, какие открытые Issues на него ссылаются.

| Issue | исходная цель | почему закрыт | evidence в master | verdict |
|---|---|---|---|---|
| #226 | Кеш, HTTP-сжатие, production-сервер | PR #305 «Closes #226 (cache/security slice)» | `Dockerfile:29` `python app.py` → `app.run` (dev-сервер Werkzeug); WSGI-сервера и сжатия нет | **reopened** |
| #703 | Целевые dev-сессии с постоянным контекстом | **ключевое слово-ловушка**: «Do not close #703» в PR #716 | native runner удалён в #844 (`49bc7e6`) | covered by #576 (дописал пояснение) |
| #229 | ChatGPT App/MCP-плагин | PR #302 «safe integration slice» | read-only MCP есть; production-endpoint и проверка из ChatGPT — нет | covered by #326 (дописал ссылку) |
| #10 | Government Department: открытие ИП | PR #329 | `government.py`; внешняя отправка — future по критериям Issue | verified closed |
| #133 | Ключ подписи Android | PR #139 | `.github/workflows/release.yml:60-79`, проверка подписи, `docs/release.md` | verified closed |
| #195 | Настроить секреты подписи | duplicate | проверку публикации и подписи ведёт #104; сами секреты проверить не могу | covered by #104 |
| #443 | Адаптер Cloud.ru S3 | PR #458 | `cloud/cloudru/object_storage.py`, `tests/test_cloudru_object_storage.py` | verified closed |
| #41 | /api/chat на InvocationContext | PR #49 | `invocation/trace.py` `create_invocation_trace`; остальное вынесено в отдельные Issues | verified closed |
| #105 | Голосовой ассистент | PR #331 | `voice_routes.py`; миграция ключей SpeechKit в #755 | verified closed |
| #12 | Treasury: бюджет, P&L, UI | PR #115 | `static/treasury.js:118,142` (кнопки), `budget_controller.py:33` SUSPENDED, `docs/treasury-billing-followup.md`; расчёта ROI не нашёл | verified closed (мелкий пробел: ROI) |
| #100 | Доделать settlement Treasury | duplicate #12 | `billing.py:127` `settle_billing_to_treasury`, идемпотентность в docs | verified closed |
| #564 | Skill-first архитектура | PR #566 | `.agents/skills/*` | verified closed |
| #570 | Семантические снимки браузера | PR #571 | `browser/semantic.py` | verified closed |
| #574 | Observer и process governor | PR #575 | `agent_office/observer.py` | verified closed |
| #577 | TaskPacket + exact-head кеш | PR #582 | смержено; дальнейшее — #576 | verified closed |
| #578 | Общая память агентов | PR #587 | #576 называет #587 shipped | verified closed |
| #579 | Гибридный RAG | PR #591 | #576 называет #591 shipped | verified closed |
| #492 | TTL plaintext-кеша секретов | duplicate → #493 | `secret_management.py:121-142`: кеш всегда пуст, plaintext не хранится | verified closed |
| #607 | Защита принятых RED-тестов | duplicate, ссылок нет | раздел Remaining в #604 | covered by #604 (дописал ссылку) |
| #600 | Live health / evidence 99.99% | duplicate, ссылок нет | #592 | covered by #592 (дописал ссылку) |
| #595 | SLO snapshot | duplicate, ссылок нет | #592 | covered by #592 (дописал ссылку) |
| #549 | Profiler / impact graph | duplicate, ссылок нет | #538 (переоткрыт) | covered by #538 (дописал ссылку) |
| #66 | Local Tool обрывается после tool call | duplicate #59 | #59 закрыт после PR #151 | covered by #59 |
| #17 | Полный ExecutionTrace при ошибке API | not planned | `request_mixin.py:205` `api_request_error`, `trace_manager.py:393`; закрытые позже #18, #26 | verified closed |
| #568 | Docs про browser role DAG | duplicate, ссылок нет | разовая сверка #551 + регулярная сверка docs | covered by #551 (дописал ссылку) |

## Системные находки

- **Ключевое слово-ловушка:** фраза «Do not close #N» в теле PR сама закрывает Issue, потому что GitHub видит в ней `close #N`. Так закрылись #703 (PR #716) и #757 (PR #762).
- В выгрузке 111 закрытий через merge PR. В 23 из них тело PR говорит о «slice», «remain» или «follow-up»; самые рискованные из них проверены в этой партии.
- 51 дубль или not planned не упоминается ни в одном открытом Issue. Явно значимые из них проверены здесь; остальные — в основном ранние placeholder-ы и вертикали, снятые владельцем (бизнес-зеркало #673–#680, GitLab Duo #230–#232, Kwork).

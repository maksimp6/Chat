# Ultra-профили моделей Logos

Каноническое дело: #1413. Поручение владельца 24.10.2026: Ultra для каждой модели.
[Каталог JSON](../../config/model_profiles/ultra-profiles.json) покрывает **все 14 ключей**
`core.config.ALL_MODELS` на develop `081d07af8f854c81201c9341c46c7ed5d3ee9ecf`:
11 text + 3 voice. Это подготовленные конфигурационные планы, **не активированные
runtime-профили и не доказательство поддержки запроса провайдером**. Три снятые
модели имеют `blocked_retired`, пустой wire request и явную дату остановки.

## Что означает Ultra

Полный применимый контекст; максимально поддерживаемые выход и reasoning;
все совместимые зарегистрированные инструменты с существующими разрешениями.
Бюджет ограничивает объём работы, не снижает качество скрыто. Нет автоматической
смены модели, уменьшения параметров, обрезания контекста или повторного POST.
Температура и VAD — не шкала интеллекта. Отключённые IDs сохраняются для учёта,
автоматической замены нет. Максимум глубины не выбирается по generic enum.

| Ключ реестра | Окно, токены | Выход | Reasoning |
| --- | ---: | --- | --- |
| `logos-runtime` | 131072 | ceiling UNKNOWN | UNKNOWN |
| `yandexgpt-5.1` | 32768 | 32768 candidate | unsupported |
| `yandexgpt-5-pro` | 32768 | ceiling UNKNOWN | UNKNOWN |
| `yandexgpt-5-lite` | 32768 | ceiling UNKNOWN | UNKNOWN |
| `logos-runtime-flash` | 65536 | ceiling UNKNOWN | UNKNOWN |
| `deepseek-v4.1-flash` | 1048576 | ceiling UNKNOWN | UNKNOWN |
| `deepseek-v4-flash` | 1048576 | ceiling UNKNOWN | UNKNOWN |
| `qwen3-235b-a22b-fp8` | UNKNOWN | retired | UNKNOWN |
| `gpt-oss-120b` | 131072 | ceiling UNKNOWN | high |
| `gpt-oss-20b` | 131072 | ceiling UNKNOWN | high |
| `qwen3.6-35b-a3b` | 262144 | ceiling UNKNOWN | enable wire UNKNOWN |
| `speech-realtime-260528` | UNKNOWN | retired | UNKNOWN |
| `speech-realtime-250923` | UNKNOWN | retired | UNKNOWN |
| `speech-realtime-deepseek-v4-flash` | 1048576 | inf selector | UNKNOWN |

Числа окна — суммарная граница контекста, **не отдельный потолок выхода**.
В просмотренных Yandex docs отдельный hosted output ceiling text-моделей не
опубликован. Поэтому поле `documented_hosted_ceiling_tokens` равно null, а
`hosted_output_ceiling_unverified` блокирует выполнение. Null не означает ноль,
бесконечность или разрешение использовать default API. Не добавлять выход,
равный окну, без отдельного доказательства.

У Pro5.1 сохранён [предыдущий профиль](logos-pro51-async-profile.md) и его явный
запрос `maxTokens="32768"` как compatibility candidate, без автоматического
понижения до 8192. Его async transport не поддерживает tools и reasoning.
Существующий файл не изменён. Текущий native DeepSeek запрос 393216/high сохранён
как отдельный candidate с provenance, а не Yandex-подтверждённый максимум.

У Qwen3.6 семейная поддержка reasoning не доказывает wire включения на этом host;
выбор глубины не подтверждён и high не отправляется. У GPT OSS используется high
из model-specific Yandex examples; xhigh из generic reference не подставляется.

Realtime имеет собственную схему: `max_output_tokens="inf"`, максимум
`yc_timeout_seconds=3600`, одна output modality и ручной audio commit. Это
максимальный документированный selector, не доказанный численный output ceiling
именно speech DeepSeek. Значение 393216 модели Max TTS сюда не переносится.
Поля Responses вроде `truncation` в Realtime event не добавляются. Управление
полным контекстом требует локального token accounting и отдельной проверки
поведения провайдера. Reasoning exact speech-модели остаётся UNKNOWN.

## Инструменты, стоимость и активация

Профили называют поддержанные в источнике категории инструментов, а не содержат
готовые схемы или разрешения. Конкретные registered tools, зависимости, vector
store IDs, schemas и approvals перед запуском должен проверить исполнитель.
Положительное поле означает документированную возможность, не живой tool E2E.

Цены реестра не скопированы как текущий тариф. У каждой модели есть ссылка на
pricing, незаполненная дата проверки ставок и `provider_billed_total=null`.
Перед разрешённым запуском нужны актуальная ревизия тарифов, бюджет, учёт
input/output/cache и отдельных tool/session charges. Receipt различает requested,
accepted и effective/UNKNOWN; estimate не выдаётся за счёт провайдера.

Каталог загружает модуль `core/model_profiles.py`, и только для отображения: `/api/models`
добавляет каждой модели сводку `ultra_profile` (статус, окно контекста, число блокеров,
без wire-шаблонов и стоимости), а выбор модели показывает строку Ultra. Снятые модели
(`available: false` в реестре) видны, но недоступны для выбора. Если каталог не
покрывает реестр ровно или повреждён, сводки не показываются (fail closed), модели
остаются доступными по прежним правилам.

Путь запроса к провайдеру профили не использует. Значения по умолчанию, D0, переменная
поколения, текущая модель, workflow, credentials и разрешения не менялись. Утилита
`validate_ultra_profiles.py` остаётся офлайн-валидатором, а не runtime admission gate.
Формат envelope нельзя отправлять в API целиком. Запуск и заполнение неизвестных
capability/limit требуют отдельной проверки; текущие blockers запрещают объявлять
эти артефакты исполнимыми.

## Offline-проверка

```bash
python3 scripts/validate_ultra_profiles.py
python3 scripts/validate_ultra_profiles.py --model gpt-oss-120b
python3 -m unittest discover -s tests -p test_ultra_profiles.py -v
python3 -m pytest -q tests/test_model_ultra_catalog.py
node --test tests/test_model_ultra_ui.js
```

Утилита разбирает literals из `core/config.py` через AST: не импортирует этот
модуль, не читает .env/credentials и не создаёт client/network request. Она
проверяет точное покрытие реестра, alias/upstream/URI, границы transports,
максимальные подтверждённые selectors и сохранение старого async-профиля.
`require_executable_profile` явно отказывает неизвестному ID, lifecycle boundary
и prepared/blocked профилю; фактическая интеграция guard в runtime не выполнялась.

Отрицательные контроли: новая/пропущенная модель, подмена URI, скрытый fallback,
непроверенный output ceiling, context→output, async 8192, Qwen high, GPT OSS xhigh,
Responses-поля в Realtime, auto-retry и duplicate JSON keys. Проверки
совместимости, фактического качества и расходов провайдера — **NOT_RUN**.

## Первоисточники, проверены 24.10.2026

- [Yandex model catalog](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)
- [Reasoning by model family](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/chain-of-thought)
- [Release notes and retirements](https://aistudio.yandex.ru/ru/docs/ai-studio/release-notes/)
- [Realtime event schema](https://aistudio.yandex.ru/ru/docs/ai-studio/realtime/)
- [Speech DeepSeek example and tools](https://aistudio.yandex.ru/ru/docs/ai-studio/operations/agents/create-voice-agent)
- [Async schema and ignored tools](https://aistudio.yandex.ru/ru/docs/ai-studio/text-generation/api-ref/TextGenerationAsync/completion)
- [Current pricing](https://aistudio.yandex.ru/ru/docs/ai-studio/pricing)

Перед запуском перепроверить даты: обе старые speech-модели отключены
12.10.2026, Qwen235 — 30.09.2026. GPT OSS доступен до указанной провайдером даты
30.10.2026 (guard требует recheck с этой даты); Yandex Lite5 и DeepSeekV4
объявлены к отключению 15.11.2026. Эти записи не являются разрешением на migration.


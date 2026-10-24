# Ultra-профили всех моделей Яндекса

Каноническое дело: #1413; существующий PR #1572. Уточнение владельца 24.10.2026:
«все модели», затем «только яндекс». Область расширена с 14 записей локального
`ALL_MODELS` до опубликованных модельных каталогов Yandex AI Studio и SpeechKit.
Модели других разработчиков включены только в вариантах, размещённых у Яндекса.

[Единый каталог](../../config/model_profiles/yandex-ultra-profiles.json) содержит
подготовленные планы параметров, ограничения, источники, транспорт и состояние
каждой записи. Он не является API body, runtime adapter или разрешением генерации.
Ни один production consumer этим PR не переводится на новый каталог.

## Охват

| Каталог/режим | Что включено |
| --- | --- |
| Базовый текстовый | Все 10 текущих моделей; прежние aliases Logos сохранены |
| Базовый Realtime | Все 3 модели: DeepSeek, Max TTS Live, TTS Live |
| Изображения | Alice AI ART 3.0 |
| Эмбеддинги | Document/query обеих версий: 4 модели |
| Prompt classifiers | Lite и Pro: 2 модели |
| Пакетная обработка | Все 21 текстовый и 8 мультимодальных вариантов |
| Выделенный инстанс | Все 5 опубликованных моделей |
| SpeechKit | 6 STT и 4 TTS model/version selectors |
| Пользовательское дообучение | 3 шаблона: текст, классификация, embeddings |
| Исторические ключи | 3 снятые модели сохранены с blocked_retired |

Счётчик каталога измеряет **профили моделей и режимов**, а не количество разных
семейств. Одна модель может иметь разные лимиты в batch text, batch multimodal
и dedicated. Голоса не считаются отдельными моделями. Дообученный шаблон не
является доказательством существования пользовательской модели.

Всего **70 записей**: 64 публичных model/deployment/version-профиля, 3 исторические
снятые записи и 3 шаблона пользовательских моделей. В 64 входят отключённые
batch/dedicated режимы; это не число доступных сейчас работающих моделей.

Точные instance-specific IDs выделенных и дообученных моделей неизвестны без
инвентаризации разрешённого аккаунта. В каталоге сохранены шаблоны и блокеры;
`GET /v1/models` в этом этапе не выполнялся. Наличие в документации не означает
доступность модели в аккаунте. Внутренние непубличные модели Яндекса, OCR и
Translate не имеют в этой сборке выдуманных Ultra-профилей.

## Правила максимума

- Полный применимый контекст; неизвестный лимит не означает ноль, бесконечность
  или API default. Контекст нельзя копировать в отдельный потолок выхода.
- Reasoning задаётся только в документированной model/transport схеме.
  Температура, скорость речи, число классов и число изображений не являются
  шкалой интеллекта и не увеличиваются ради слова Ultra.
- Нет скрытой замены модели, снижения параметров, обрезания исходного текста
  или повторного платного POST. Несовместимость остаётся явным блокером.
- Стоимость и живое качество отделены от конфигурации: provider acceptance
  `NOT_RUN`, effective values неизвестны, фактическая стоимость не установлена.

Полные существующие варианты сохранены: [native DeepSeek](logos-native-quality.md)
и [Pro 5.1 async](logos-pro51-async-profile.md). Запросы `393216/high` и `32768`
остаются кандидатами совместимости. Максимум выхода Max TTS Live `393216`
подтверждён именно строкой этой модели; он не переносится на Speech DeepSeek.

## Различия транспортов

**Batch и dedicated:** обе функции отмечены Яндексом отключёнными с 10.10.2026.
Все их записи имеют `blocked_service_disabled`, без outbound body. Это не
per-model retirement. Для dedicated нельзя придумать URI из названия: identifier
и model_version выдаёт карточка созданного инстанса. Batch использует dataset
и `batch.run_deferred`, а не Pro 5.1 `completionAsync`. Опубликованные конечные
`/` в мультимодальных URI сохранены. Контекст Qwen VL32B — 32768 для dedicated,
4096 для batch multimodal. Не смешивать эти режимы.

**ART:** максимальные перечисленные прямоугольные размеры 1536×1024 и 1024×1536;
квадрат 1024×1024 сохранён для соответствующей задачи. Prompt до 500 символов.
`quality=high` и другие поля с отметкой CURRENTLY NOT SUPPORTED не отправляются.
Максимальный размер не разрешает менять заданную ориентацию изображения.

**Realtime:** сессия до 3600 секунд. У Speech DeepSeek — селектор `inf` без
подтверждённого численного потолка; Max TTS Live — 393216. TTS Live синтезирует
текст без LLM и audio input: нет reasoning, instructions и LLM tools.
LiveTTS не получает неподдерживаемое `speed`. PCM 44100 — default/example,
а не доказанный максимум; голоса general и livetts не взаимозаменяются.

**Embeddings:** v1 сохраняет 256 измерений, v2 явно запрашивает 768 как наибольшую
model-specific размерность. Это не измеренное улучшение retrieval. Reasoning,
temperature, tools и LLM output limits неприменимы. OpenAI-совместимый input
содержит одну строку либо массив из одной строки. REST `dim` и OpenAI
`dimensions` не смешиваются.

**Классификация:** prompt classifiers синхронные, с реальными классами задачи
в пределах 2–20 и применимыми размеченными примерами до 6000 токенов. Не заполнять
фиктивными классами до максимума. Multilabel относится к отдельным дообученным
классификаторам, а не к prompt classifier. Конкретный tuned URI и его версия
разрешаются по фактической модели, без замены `rc` на `latest`.

## Сохранённые противоречия источников

В embeddings API указан input 8192 токена, общая limits-страница — 2048.
В выбранном профиле предел остаётся `null`, оба утверждения сохранены. В v2
выбраны 768 по model-specific каталогу, но generic limits ещё говорит 256;
provider acceptance не установлен. В одном описании tuned embeddings показана
схема `gpt://`, в точной инструкции и Models API — `emb://`; шаблон использует
`emb://` и требует фактически выданного URI перед исполнением.

Batch guide пишет `TextTextToTextGenerationRequest`, страница типов datasets —
`TextToTextGenerationRequest`. Эти строки сохранены как конфликт документации;
интеграция должна проверить доступный тип. Ни один конфликт не замаскирован
тихим понижением параметров.

## Проверка без модели

```bash
python3 scripts/validate_ultra_profiles.py
python3 scripts/validate_yandex_ultra_profiles.py
python3 -m unittest discover -s tests -p '*ultra_profiles.py' -v
```

Валидаторы читают JSON и исходный реестр, не читают credentials и не создают
сетевой клиент. Проверяется полное покрытие source inventory, сохранение прежних
профилей, только Yandex endpoints, отсутствие ложной активации, блокировка
отключённых режимов, корректные поля image/voice/embedding/classifier API и
сохранение конфликтов. Это проверки артефакта, не production execution guard.
Точные результаты и HEAD публикуются в handoff/PR после завершения проверки.

## Источники

Источники каждой записи находятся в `source_urls` и namespaced `sources` каталога.
Основные каталоги прочитаны 24.10.2026:

- [Базовый инстанс](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)
- [Dedicated](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/dedicated-instance)
- [Batch](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/batch-processing)
- [Embeddings](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/embeddings)
- [Классификаторы](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/classifier/models)
- [SpeechKit STT](https://aistudio.yandex.ru/ru/docs/speechkit/stt/models)
- [SpeechKit TTS](https://aistudio.yandex.ru/ru/docs/speechkit/tts/models)
- [История изменений](https://aistudio.yandex.ru/ru/docs/ai-studio/release-notes/)

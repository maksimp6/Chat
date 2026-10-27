# Модели Yandex AI Studio в Logos

Каталог сверяется с официальной документацией Яндекса. Наличие записи в исходниках
не подтверждает доступ конкретного ключа или успешный живой вызов.
Поддерживаются только существовавшие до ремонта типы: текст, голос, изображения
и отдельный API эмбеддингов. Новые типы моделей не подключаются.

| Назначение | Модели | Транспорт Logos |
| --- | --- | --- |
| Текст | Alice AI LLM, Alice AI LLM Flash, YandexGPT Pro 5.1, Pro 5, Lite 5, DeepSeek V4.1 Flash, V4 Flash, GPT OSS 120B, 20B, Qwen3.6 35B | `/api/chat`, Responses API |
| Изображения | Alice AI ART, `aliceai-image-art-3.0` | `/api/images/generate`, Images API |
| Голосовой диалог | Speech Realtime DeepSeek V4 Flash, Speech Realtime Max TTS Live | `/api/voice/session`, Realtime WebSocket |
| Синтез текста | Speech TTS Live | `/api/voice/text`, Realtime WebSocket, без аудиовхода |
| Эмбеддинги | `text-search-doc`, `text-search-query`, `text-embeddings-v2-doc`, `text-embeddings-v2-query` | `/api/embeddings`, Embeddings API |

Pro 5.1 также сохраняет отдельный совместимый `/api/completion`. Профиль этого
эндпоинта не исключает модель из обычного чата. URI содержат точные версии;
неизвестные дообученные URI из Models API не сокращаются до базовой модели.

`/api/models` возвращает отдельные секции `text`, `voice`, `image`, `embedding`.
Эмбеддинги не являются моделями чата. Классификаторы не подключены. Discovery
разрешает действующий ключ перед чтением кеша, передаёт его папку и не использует
каталог от другого ключа после ротации. При сбое возвращается статический каталог
с `degraded: true`; это не подтверждение прав аккаунта.

У GPT OSS 120B/20B в документации указан срок доступности до 30 октября 2026 года.
YandexGPT Lite 5 и DeepSeek V4 Flash объявлены устаревшими с отключением
15 ноября 2026 года. Эти даты видны в выборе моделей; скрытой замены модели нет.

Большой `yandex-ultra-profiles.json` остаётся справочником публичных семейств,
режимов, версий SpeechKit, исторических записей и шаблонов дообученных моделей.
Его 70 записей не означают 70 разных работающих моделей чата. Batch и Dedicated
не включаются как доступные: соответствующие функции отключены поставщиком.
SpeechKit-версии имеют собственный протокол и не подменяют Realtime-модель.

Для ремонта от 27 октября 2026 года тесты, сборки, компиляция, workflow и платные
вызовы не запускались. Живые вызовы и пользовательская приёмка: `NOT_RUN`.

## Официальные источники

- [Базовые модели и жизненный цикл](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/models)
- [Models API](https://aistudio.yandex.ru/ru/docs/ai-studio/operations/models/get)
- [Images API](https://aistudio.yandex.ru/ru/docs/ai-studio/api/Images/createImage)
- [Realtime API](https://aistudio.yandex.ru/ru/docs/ai-studio/realtime/)
- [Эмбеддинги](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/embeddings)

# Markdown-зеркало документации Cloud.ru

Статус: planned  
Issue: #428  
Назначение: хранить максимально близкую к официальному источнику Markdown-копию документации Cloud.ru для Alice Pro и подключаемых AI-агентов.

## Важное разделение

В проекте есть два разных слоя знаний:

- `docs/mirrors/cloudru/` — сырой или минимально очищенный Markdown, близкий к официальной документации Cloud.ru;
- `docs/notes/cloudru/` — наши конспекты, выводы и адаптация под Alice Pro.

Зеркало не должно переписывать документацию нашими словами. Если страница была сокращена, переосмыслена или дополнена проектными выводами, это уже конспект и ей место в `docs/notes/cloudru/`.

Приоритет для проверки фактов:

```text
live official docs → актуальный mirror → project notes
```

## Зачем это нужно

Alice Pro должна уметь обращаться к документации Cloud.ru как к локальному знанию: быстро находить нужный раздел, читать только релевантные Markdown-файлы и обновлять зеркало без повторной смысловой обработки каждой страницы моделью.

Главное правило: зеркало строится детерминированным crawler/fetch pipeline. LLM используется для поиска пробелов, выбора релевантных документов и анализа по запросу, но не для скачивания и пересказа каждой страницы.

## Каноническое размещение

Локально в репозитории:

```text
docs/mirrors/cloudru/
├── manifest.json
├── README.md
├── evolution/
├── workflow-studio/
├── repo/
├── container-apps/
├── artifact-registry/
├── iam/
├── api/
├── terraform/
├── managed-postgresql/
├── object-storage/
└── networking/
```

Конспекты размещаются отдельно:

```text
docs/notes/cloudru/
├── README.md
├── workflow-studio.md
├── container-apps.md
├── deployment-for-alice.md
└── ...
```

На Google Drive:

```text
Alice Pro/
└── Cloud.ru/
    ├── Конспекты/
    └── 90 — Markdown Mirror/
        ├── manifest.json
        └── ...
```

Репозиторий является машиночитаемой рабочей копией для агентов и тестов. Google Drive является внешней копией для просмотра, обмена и восстановления.

## Источники

Разрешены официальные источники Cloud.ru:

- `https://cloud.ru/docs/doc-contents.html`
- `https://cloud.ru/docs/console`
- `https://cloud.ru/docs/advanced`
- `https://cloud.ru/docs/vmware`
- сервисные страницы `/ug/doc-contents`
- внутренние официальные ссылки внутри `cloud.ru/docs/`
- официальные юридические страницы `cloud.ru/documents/`, если они входят в выбранный scope

Exa используется как вспомогательный инструмент:

1. найти корневые разделы и пропущенные страницы;
2. получить читаемый Markdown для известных URL;
3. проверить полноту отдельных тематических разделов.

Exa Search не является единственным источником списка URL.

## Pipeline

```text
seed URLs
  ↓
URL enumerator / crawler
  ↓
URL normalization
  ↓
deduplication
  ↓
fetch
  ↓
HTML → minimally cleaned Markdown
  ↓
SHA-256
  ↓
manifest.json
  ↓
local mirror
  ↓
Google Drive sync
```

### Требования к crawler

- не использовать LLM для обхода ссылок;
- ограничиваться официальными доменами и разрешёнными путями;
- нормализовать URL до канонической формы;
- удалять tracking/query-параметры, не влияющие на содержимое;
- не создавать дубликаты `.html` и URL без расширения, если содержимое идентично;
- соблюдать разумный rate limit;
- продолжать работу после единичных ошибок fetch;
- писать ошибки и изменения в технический лог;
- не пересказывать и не упрощать смысл страницы;
- допустимо удалять навигационный шум, меню, повторяющиеся footer/header-блоки и технический HTML.

## manifest.json

Минимальная запись:

```json
{
  "url": "https://cloud.ru/docs/...",
  "canonical_url": "https://cloud.ru/docs/...",
  "path": "workflow-studio/overview.md",
  "sha256": "...",
  "fetched_at": "2026-09-28T00:00:00Z",
  "last_modified": null,
  "content_length": 12345,
  "status": "ok"
}
```

Manifest должен позволять определить:

- какие URL уже известны;
- куда сохранён документ;
- изменилось ли содержимое;
- когда документ последний раз проверялся;
- завершился ли fetch успешно;
- какие файлы нужно обновить или удалить.

Если надёжный `Last-Modified` или ETag отсутствует, источником истины для изменения контента является SHA-256 нормализованного Markdown.

## Инкрементальное обновление

Повторный запуск не должен заново обрабатывать весь корпус через AI.

Порядок:

1. получить/обновить список URL;
2. проверить метаданные страницы, если источник их предоставляет;
3. скачать страницу;
4. минимально очистить Markdown;
5. вычислить SHA-256;
6. если hash прежний, оставить файл без изменений;
7. если hash изменился, заменить файл и обновить manifest;
8. новые URL добавить;
9. исчезнувшие URL пометить как missing/deprecated до безопасного удаления.

Удаление документов должно быть отдельной операцией, чтобы временная ошибка сайта не уничтожила рабочее зеркало.

## Контракт для AI-агентов

Обычный порядок чтения:

```text
docs/notes/cloudru/
  ↓ если нужна точность или деталь
docs/mirrors/cloudru/manifest.json
  ↓
1–3 релевантных Markdown-файла
  ↓ если нужна проверка актуальности
live official docs
```

AI-агентам запрещено по умолчанию загружать весь mirror в контекст.

Для фактических утверждений из зеркала агент должен сохранять возможность восстановить исходный официальный URL через manifest.

## Первая волна зеркала

Приоритет для Alice Pro:

1. Workflow Studio
2. Evolution Repo
3. Container Apps
4. Artifact Registry
5. IAM
6. API Cloud.ru
7. Terraform Evolution
8. Managed PostgreSQL
9. Object Storage
10. Networking

После прохождения тестов scope расширяется:

1. весь Evolution;
2. Advanced;
3. VMware;
4. юридические документы и тарифы при необходимости.

## Экономия токенов

Запрещён pipeline:

```text
page → LLM → summary → mirror
```

Основной pipeline:

```text
page → deterministic fetch/cleanup → Markdown → mirror
```

Если нужен пересказ:

```text
mirror/live docs → LLM → docs/notes/
```

LLM подключается только там, где нужна семантика:

- подготовка конспекта;
- поиск релевантных документов;
- объяснение;
- сравнение;
- поиск пробелов;
- подготовка ответа пользователю.

## Хранилища

Минимально поддерживаются:

- локальная файловая система;
- репозиторий Alice Pro для контролируемого snapshot;
- Google Drive для внешней копии.

Supabase не является обязательной зависимостью зеркала.

## Наблюдаемость

Сохранять минимум:

- URL;
- HTTP/fetch status;
- длительность;
- размер результата;
- old/new SHA-256;
- created/updated/unchanged/error;
- сообщение ошибки.

При интеграции с Alice Pro операции могут попадать в Execution Trace либо в отдельный технический журнал, если полный trace создаёт неоправданный объём.

## Тесты

Нужны тесты на:

- canonical URL normalization;
- deduplication;
- безопасное преобразование URL в path;
- manifest create/update;
- unchanged content;
- changed content;
- failed fetch;
- удалённую/исчезнувшую страницу;
- incremental refresh;
- отсутствие обращения к LLM в crawler path;
- отсутствие AI-summary в mirror pipeline.

## Acceptance criteria

- [ ] crawler/URL enumerator работает без LLM;
- [ ] страницы сохраняются как близкий к источнику Markdown;
- [ ] зеркало не содержит обязательного AI-пересказа;
- [ ] URL нормализуются и дедуплицируются;
- [ ] создаётся `manifest.json`;
- [ ] содержимое хешируется SHA-256;
- [ ] повторный запуск различает changed/unchanged;
- [ ] первая волна Cloud.ru зеркалируется;
- [ ] зеркало синхронизируется в Google Drive;
- [ ] конспекты хранятся отдельно в `docs/notes/cloudru/`;
- [ ] Alice/AI-агенты сначала читают notes, затем выборочные mirror-файлы;
- [ ] есть тесты;
- [ ] есть документация запуска и обновления.

## Связанные материалы

- GitHub issue: https://github.com/maksimp6/Chat/issues/428
- `docs/notes/README.md`
- `docs/mirrors/README.md`
- `docs/notes/cloudru/README.md`
- Cloud provider foundation: `docs/integrations/cloud-provider-foundation.md`
- Cloud.ru IAM wizard: `docs/integrations/cloudru-iam-wizard.md`

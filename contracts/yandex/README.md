# Контракты Yandex API

Каноническая задача: [#1567](https://github.com/maksimp6/Chat/issues/1567).

`catalog.json` — именованные машинные контракты **всех 267 API-страниц**
репозиторного снимка AI Studio: **107 HTTP, 104 gRPC, 56 realtime events**.
Проверены все 563 Markdown-файла; оставшиеся 296 явно перечислены в `excluded`
как материалы без привязки метода/события. Два YAML-файла навигации не являются API.

Источник: [yandex-cloud/docs, fddbac0226d030d57d089b730f92abd02e583df2](https://github.com/yandex-cloud/docs/tree/fddbac0226d030d57d089b730f92abd02e583df2/ru/ai-studio),
**2 марта 2026 года**, получен 23 октября. Это контракт исторического снимка,
не актуализированная спецификация сервиса. Оригиналы не менялись.
Авторство: YANDEX LLC, 2018 и участники; [CC BY 4.0](https://github.com/yandex-cloud/docs/blob/fddbac0226d030d57d089b730f92abd02e583df2/LICENSE).
Изменения Logos: выделение схем/привязок, именование, строгая политика неизвестных
полей, пометки неполноты. Путь и SHA-256 исходника есть у каждого контракта.

## Содержание

- `request`: ссылки на схемы `path`, `query`, `body` или gRPC `message`.
- `responses`: ссылки по явно документированному HTTP-коду или gRPC `message`.
- `schema`: JSON Schema Draft 7, с общей таблицей `definitions` в пределах метода.
- `transport`: метод/шаблон URL либо RPC-сигнатура и направления streaming.
- `availability`: отметка неподдерживаемого API/события из снимка либо отсутствие
  живой проверки. В снимке 41 страница помечена неподдерживаемой целиком.
- `sections`: исходные разделы с номерами строк, примерами и ограничениями;
  примеры JSON не интерпретируются как JSON Schema.

Имена образуются из пути: префикс `yandex.`, путь без `.md`, `/` заменены точками.
Например: `yandex.responses.createResponse`,
`yandex.vectorStores.searchVectorStore`,
`yandex.text-generation.api-ref.Tokenizer.tokenize`.

## Использование

```python
from api.yandex_contracts import get_yandex_contract, validate_yandex_contract

name = "yandex.text-generation.api-ref.Tokenizer.tokenize"
contract = get_yandex_contract(name)
validate_yandex_contract(name, {"modelUri": "gpt://example/model", "text": "hello"})
validate_yandex_contract(
    name,
    {"tokens": [{"id": "42", "text": "hello", "special": False}]},
    direction="responses",
    part="200",
)
```

Проверяется один JSON payload/словарь параметров или декодированное gRPC-сообщение,
не HTTP-пакет и не protobuf wire bytes. `ContractViolation` означает нарушение
описанной формы. Ошибки не содержат отклонённого значения, ключей доступа и аргументов.
`IncompleteYandexContract` означает, что проверка **не может дать PASS**:
неизвестный тип, внешний protobuf reference, непреобразованное ограничение прозой,
неизвестный format, multipart binary, неоднозначное объединение или отсутствующая
схема ответа. Неполнота даже необязательной ветки блокирует весь выбранный payload.
Неподдерживаемая страница также блокирует валидацию.

## Границы доказанного

Охват страниц полный; полнота исполняемых ограничений различается. В частности,
Responses содержит `unknown`, а большинство страниц не описывает error schemas.
Неизвестные схемы не заменены разрешающим `ANY_JSON`. Все ограничения сохранены
в тексте; сложные ограничения требуют отдельной явной схемы и проверки.

Из `apiPlayground` перенесены структурные ограничения, enum, nullable, required,
числовые/строковые границы, шаблоны и объединения. Ограничения, оставшиеся только
в описании playground, помечаются отдельно и блокируют проверку.
Из Markdown-таблиц преобразованы типы, ссылки, массивы, maps, enum, nullable и
явные `Required field`. Необозначенная обязательность не домысливается:
пустой запрос Tokenizer допускается схемой источника, хотя работа сервиса по нему
не установлена. Числа gRPC `int64` проверяются как integer в декодированном сообщении;
REST `string (int64)` проверяется как десятичная строка в пределах signed int64.

Для таблиц Logos добавляет `additionalProperties: false`; произвольные словари
разрешены только в документированных object/map полях. В playground сохраняется
собственная политика `additionalProperties` поставщика.

Auth, заголовки, глобальные квоты, транспортные ошибки, совместимость конкретной
модели и реальный сервис этими payload-схемами не проверяются. Их источники:
[authentication](https://github.com/yandex-cloud/docs/blob/fddbac0226d030d57d089b730f92abd02e583df2/ru/ai-studio/api-ref/authentication.md),
[support headers](https://github.com/yandex-cloud/docs/blob/fddbac0226d030d57d089b730f92abd02e583df2/ru/ai-studio/api-ref/support-headers.md),
[limits](https://github.com/yandex-cloud/docs/blob/fddbac0226d030d57d089b730f92abd02e583df2/ru/ai-studio/concepts/limits.md).
Псевдо-HTTP привязки realtime сохранены как описание событий с направлением;
они не объявляются рабочими самостоятельными HTTP endpoints.

Каталог отделён от внутренних контрактов `api/contracts.py`. По следующему
поручению владельца исходящие Yandex-вызовы переведены на контрактную границу:
[транспорт и охват](transport.md). Исторический каталог не переписывается;
22 явно именованных клиентских профиля уточняют используемые формы запросов
по документации, проверенной 24.10.2026. Неизвестные формы не допускаются в сеть.

## Воспроизведение и проверка

Локального зеркала `docs/mirrors/yandex-ai-studio/` и каталога `tests/` в текущем
дереве нет: команды ниже требуют их восстановления. Ссылки на источник в этом
документе ведут на тот же снимок `yandex-cloud/docs` по SHA.

```bash
git lfs pull --include="docs/mirrors/yandex-ai-studio/source/**" --exclude=""
python scripts/build_yandex_contracts.py
python scripts/build_yandex_contracts.py --check
python -m pytest -q tests/test_yandex_documentation_contracts.py tests/test_api_contracts.py
```

Генератор не обращается в сеть, проверяет каждый исходный Markdown по manifest
(размер и SHA-256), отклоняет LFS pointers, изменённые/исчезнувшие/новые файлы,
неразобранные привязки и дубли. `--check` сравнивает весь результат побайтово.
При обновлении снимка сначала пересматриваются источник/manifest и ограничения,
затем перегенерируется каталог.

Обычные тесты CI работают и при checkout с LFS pointers: проверяют весь каталог,
его происхождение по manifest, валидность JSON Schema, настоящий validator на
запросах/ответах и конвертер на отрицательных fixtures. Полное повторное извлечение
с `--check` требует загруженных LFS-исходников и проверяется отдельно.

# Бизнес-документы: публичный и закрытый контуры

Задача: [#671](https://github.com/maksimp6/Chat/issues/671).

## Текущий статус

Это начальный provisioning/contract slice, а не работающий сервис синхронизации.
Целевой самостоятельный private repository: `maksimp6/Chat-business-docs`.
Его создание и настройка доступа ещё не подтверждены. Конфигурация
[mirror-policy.json](mirror-policy.json) намеренно содержит `enabled: false`.
Изменение этого значения само по себе ничего не запускает.

## Связь репозиториев

```text
Chat (public)                         Chat-business-docs (private)
docs/business/public/   <----------> public/
                                      private/   только закрытые документы
                                      .mirror/   закрытое состояние и approvals
```

Зеркалируется полностью общий публичный раздел, а не весь Git-репозиторий.
Другие документы и код Chat не включаются автоматически. Все новые деловые
материалы по умолчанию относятся к private; название папки `public` само по себе
не является разрешением раскрыть её содержимое.

В Chat остаются этот безопасный указатель, политика и публичные материалы.
Не добавлять обязательный private submodule: обычный clone и CI Chat не должны
нуждаться в доступе к закрытым документам. Файлы, имена закрытых документов,
private commit IDs, переписка и журналы не должны попадать в публичные PR/Issues.

## Создание закрытого хранилища

На авторизованном backend с `gh`, `git` и настроенной GPG-подписью:

```bash
bash scripts/bootstrap_business_docs.sh --check
bash scripts/bootstrap_business_docs.sh --create
```

`--check` только проверяет metadata. `--create` создаёт новый PRIVATE repository
и начальный подписанный `master` с безопасным каркасом. Существующий репозиторий
не изменяется. Чужая identity, public/fork target, ошибка сети/403, невозможность
подписи или несовпадение default branch блокируют операцию.

Скрипт не переносит пользовательские документы, не создаёт credentials, не меняет
Chat/master, protections или CODEOWNERS, не запускает автоматическую синхронизацию.
После ошибки push новый private repository может остаться пустым: это частичный
provisioning, требующий проверки, а не повод удалять/пересоздавать репозиторий.
Временный каталог намеренно сохраняется; скрипт не выполняет удаления.

## Следующий implementation slice

Сначала независимый contract-review, затем Infra Engineer реализует orchestration
в PRIVATE repository. Security Reviewer проверяет границу раскрытия данных.
GitHub App получает только необходимые cross-repository permissions. Его ключи
и private checkout недоступны public CI, fork PR, artifacts, cache и Pages.
Секреты не копируются из общего административного токена без отдельного разрешения.

Обязательные свойства:

- Exact-head snapshot и SHA-256 baseline. Одновременные несовместимые изменения
  блокируют весь пакет; запрещён last-writer-wins.
- Public → private: инкрементальный импорт публичного раздела с сохранением private.
  Private → public: только заранее одобренные владельцем точные bytes/paths, через
  новую ветку на PUBLIC истории и protected PR в Chat.
- Одобрение требуется ДО публикации ветки/PR. Поле `approved: true` из документа
  или самоодобрение агента не считается доверенным доказательством. При изменении
  bytes/path/head старое одобрение теряет силу.
- Запрещены private Git history/objects, refs, issues, logs, internal provenance,
  symlinks, submodules, path traversal и непроверенные/неполные file listings.
- Не выполнять удаления, переименования как удаления, revert или force-push.
  Изменения такого рода блокируются и требуют отдельного решения владельца.
- No-op не создаёт commit/PR. Один пакет на точную пару refs, concurrency,
  idempotency и повторная проверка head непосредственно перед записью.
- Новые branch/PR строятся из разрешённого публичного набора, не из копии private
  working tree с последующим удалением секретов. `.gitignore` не является DLP.
- `master` обновляется только действующим workflow после CI exact head и
  независимого solution-review. Никакого обхода существующих protections.

Автоматизация может выполнять все разрешённые переходы, но не может сама менять
классификацию документа или разрешать раскрытие частной информации.

## Доказательства готовности

Отдельно фиксировать `provisioned`, `configured`, `verified`. Наличие PR или
успешного bootstrap не означает завершения зеркала. Нужны реальные round-trip
публичного canary, private-canary negative test для истории/refs/PR/logs/artifacts,
а также conflict, no-op, stale-head, missing-auth, unauthorized-export и deletion
tests. Не включать зеркало до этих проверок.

Локальные проверки начального slice:

```bash
bash -n scripts/bootstrap_business_docs.sh
python -m unittest discover -s tests -p 'test_business_docs_bootstrap.py' -v
bash scripts/format.sh check
```

## Источники

- [GitHub: duplicating a repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/duplicating-a-repository)
  описывает обычное Git-зеркало; здесь оно не используется между public/private.
- [GitHub: GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)
  ограничивает встроенный токен репозиторием workflow.
- [GitHub CLI: gh repo create](https://cli.github.com/manual/gh_repo_create)
  описывает явное создание с `--private`.

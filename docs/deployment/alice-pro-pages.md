# Alice Pro: подготовка GitHub Pages

Статус: **локальный макет, не опубликован**. Каталог `website/` независим от
приложения: HTML, CSS, SVG-иконка, robots.txt и `.nojekyll`, без JavaScript,
сборщика, внешних шрифтов, аналитики или API. Это информационная страница;
GitHub Pages не запускает Python backend и не заменяет хостинг Alice Pro.

## Просмотр и проверка

Из корня репозитория:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory website
```

Открыть `http://127.0.0.1:8765/`. Проверить также размещение в подпапке `/Chat/`:
все локальные ресурсы используют относительные URL. Не открывать сервер наружу.

```bash
python -m unittest discover -s tests -p test_alice_pro_website.py -v
python tests/validate_runtime_modules.py
python tests/validate_skills.py
bash scripts/format.sh check
```

Перед публикацией обязателен `bash scripts/pre_push.sh` и проверки защищённого
PR. Текущий форматтер репозитория не охватывает `website/`; это не основание
менять CI или вызывать альтернативный форматтер. HTML проверяется отдельно.
Мобильная эмуляция Chromium не равнозначна проверке на физическом Android.

## Адрес и индексация: решение ещё не принято

`maxxxpavlov.ru` и `maxxxpavlov.online` принадлежат владельцу, но окончательный
адрес этой страницы не утверждён. В исходном HTML нет canonical, `og:url`,
структурированных данных с фиктивным адресом или ссылки на ещё не работающий сайт.
`noindex, follow` намеренно оставлен для проверки макета. robots.txt разрешает
обход, чтобы робот мог прочитать noindex. Это не механизм приватности.
Шаблон sitemap лежит вне публикуемого каталога:
`docs/deployment/alice-pro-sitemap.xml.example`.

Перед разрешённой публикацией, единым изменением:

1. Утвердить один основной HTTPS URL, включая завершающий `/` и, если нужно,
   подпуть проекта. Возможный технический адрес —
   `https://maksimp6.github.io/Chat/`; это предположение до проверки настроек Pages.
   `https://maxxxpavlov.ru/` — только один из вариантов, не действующий адрес сайта.
2. В `<head>` добавить `<link rel="canonical" href="APPROVED_ABSOLUTE_URL" />`,
   подставив утверждённый URL, а не буквальный placeholder.
3. Скопировать шаблон sitemap как `website/sitemap.xml`, заменить
   `https://example.invalid/` тем же URL. Не добавлять вымышленный `lastmod`.
4. В robots.txt добавить `Sitemap: APPROVED_ABSOLUTE_URLsitemap.xml`, подставив URL.
5. Удалить meta robots с `noindex` и комментарий о локальном черновике в HTML;
   обновить комментарий в robots.txt. Проверить отсутствие placeholder и noindex,
   согласованность canonical/sitemap, доступность всех ресурсов. Обновить тест
   `test_review_draft_cannot_claim_a_publication_url` на утверждённый контракт
   публикации: точный canonical, sitemap и отсутствие noindex.

Для project URL `/Chat/` файл `/Chat/robots.txt` не является robots.txt хоста:
роботы читают `/robots.txt`. В этом случае не публиковать обещание управления
robots всего `maksimp6.github.io`; отправить sitemap с подпутём напрямую в панели
поисковиков. На собственном корневом домене robots.txt будет в нужном месте.
Не публиковать независимые одинаковые копии на двух доменах. DNS сам по себе
не выполняет HTTP-редирект между ними; второй домен пока оставить без изменений.

## Только после отдельного согласия: публикация

Сначала согласовать окончательный URL, push/PR и целевую ветку интеграции.
Эта работа основана на `develop` по прямому поручению пользователя, хотя
AGENTS.md описывает обычные PR в `master`; при публикации согласовать этот маршрут
и пройти действующие review/CI, не обходя защиту веток.

Перед выбором домена учесть открытый [PR #943](https://github.com/maksimp6/Chat/pull/943):
его acceptance probe предполагает приложение на `maxxxpavlov.ru` (root 401 и
`/healthz` 200). Статический Pages с root 200 — другой контракт. Согласовать
назначение хоста до публикации; существующий probe и конфигурацию не менять.

Предпочтительный минимальный путь без изменения CI приложения:

1. Повторно проверить актуальные develop, PR и отсутствие другой реализации.
   Завершить SEO-замены выше, проверки, review и разрешённую интеграцию.
2. Подготовить отдельную ветку публикации `gh-pages`, содержащую **только**
   проверенное содержимое `website/` в корне. Проверить существующую ветку и сайт
   перед её созданием; не перезаписывать чужую историю и не использовать force push.
   Не копировать исходники приложения, служебные документы или тестовые материалы.
3. После разрешения push и включения Pages выбрать Settings → Pages → Deploy
   from a branch → `gh-pages` → `/(root)`. Каталог `/website` нельзя выбрать как
   источник branch deployment; `/docs` уже занят документацией репозитория.
4. Дождаться успешной сборки Pages, проверить HTTPS, HTTP 200, CSS, favicon,
   canonical, sitemap, robots и навигацию на фактическом публичном URL.

Источник: [официальная документация источников GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
Ни ветка публикации, ни workflow, ни настройки Pages этим изменением не создаются.

## Только после согласия на домен и DNS

Если выбран собственный домен, сначала подтвердить его в GitHub Pages, затем
добавить в Settings → Pages → Custom domain и лишь затем менять DNS по актуальной
[инструкции GitHub](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).
Google Search Console verification не заменяет проверку домена в GitHub.
При branch deployment GitHub создаёт CNAME в публикационной ветке; сохранять его
при последующих обновлениях. Сейчас CNAME отсутствует намеренно.

Перед DNS-изменениями сохранить текущие записи, включая verification TXT,
почту и другие сервисы. Использовать только нужные записи для выбранного хоста,
без wildcard. После DNS и выдачи сертификата проверить HTTPS и включить
Enforce HTTPS, когда это доступно. Сверить canonical и sitemap с итоговым доменом.

После успешного HTTP/HTTPS и отсутствия noindex выполнить live test в Google
Search Console, отправить sitemap и запросить индексирование страницы; добавить
итоговый адрес в Яндекс Вебмастер и отправить sitemap там. Владение доменом ещё
не означает наличие веб-хостинга. Текущий Host unknown не исправляется метатегами.
Сроки индексации и позиции в выдаче не гарантируются.

## Основания для текста

Сверено с `develop` 9d658587871007a041f1981c084a80d2de6d78bd:

- `README.md`: описание, shipped/roadmap, debug Android, MIT.
- `yandex_client.py` и `yandex_client_modules/`: Responses API.
- `universal_tool_platform.py`: UniversalToolExecutor, контекст и вызовы.
- `trace_manager.py`: ExecutionTrace, запросы, инструменты, время, ошибки, billing.
- `android/app/src/main/java/com/alicepro/mobile/MainActivity.kt`: WebView.

Страница не заявляет production-ready, публичный deployment, стабильный Android
release, клиентов, отзывы, метрики или неподтверждённые интеграции.

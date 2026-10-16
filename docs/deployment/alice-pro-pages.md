# Alice Pro: подготовка GitHub Pages

Статус: **подготовлено для review; сайт не опубликован**. Пользователь утвердил
`https://maxxxpavlov.online/` как адрес страницы проекта и Draft PR в `develop`.
`maxxxpavlov.ru` остаётся для отдельного приложения, в том числе контракта PR #943.
Merge, включение Pages, подключение домена и DNS этим согласием не разрешены.

## Содержимое

Каталог `website/` независим от приложения: HTML, CSS, SVG-иконка, robots.txt,
sitemap.xml и `.nojekyll`, без JavaScript, сборщика, внешних шрифтов, аналитики
или API. GitHub Pages не запускает Python backend Alice Pro.

Canonical, Open Graph URL, sitemap и ссылка Sitemap в robots.txt согласованы с
`https://maxxxpavlov.online/`. Подготовительный noindex снят в исходниках для
будущей публикации. Это не утверждение, что сайт уже доступен или индексируется.
В sitemap только одна страница, без вымышленного lastmod. Русский основной текст
дополнен английским блоком с `lang="en"`; отдельной английской страницы нет.
CNAME намеренно отсутствует: подключение домена — отдельный разрешаемый этап.

## Локальный просмотр и проверки

Из корня репозитория:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory website
```

Открыть `http://127.0.0.1:8765/`. Локальный сервер не открывать наружу.
Относительные CSS/favicon работают и в подпапке `/Chat/`, но SEO-адрес остаётся
утверждённым корневым доменом. Не индексировать альтернативную копию страницы.

```bash
python -m unittest discover -s tests -p test_alice_pro_website.py -v
python tests/validate_runtime_modules.py
python tests/validate_skills.py
bash scripts/check_code_rules.sh
bash scripts/pre_push.sh
```

Перед push обязателен clean-worktree formatting gate; перед интеграцией —
действующие проверки защищённого PR на точном head. Форматтер репозитория не
охватывает `website/`; HTML проверяется отдельно, CI не меняется ради страницы.
Мобильная эмуляция Chromium не равнозначна проверке на физическом Android.

## Последующие действия: только после отдельного согласия

1. Завершить review/CI Draft PR в `develop`, согласовать и выполнить интеграцию
   по действующей политике. Не обходить защиту веток. Основа develop и Draft PR
   в develop прямо разрешены пользователем; обычные правила AGENTS.md описывают
   master, поэтому не менять target самостоятельно.
2. Проверить отсутствие уже настроенного сайта и ветки `gh-pages`. Подготовить
   публикационную ветку с **только** проверенным содержимым `website/` в корне.
   Не переносить исходники приложения, служебные документы или тесты; не
   перезаписывать чужую историю и не использовать force push.
3. После разрешения публикации выбрать Settings → Pages → Deploy from a branch
   → `gh-pages` → `/(root)`. `/website` не является поддерживаемым источником
   branch deployment, а `/docs` уже занят документацией репозитория.
4. Для домена получить отдельное согласие. Подтвердить `maxxxpavlov.online` в
   GitHub Pages, добавить его в Settings → Pages → Custom domain, затем менять
   DNS по актуальной инструкции GitHub. Google Search Console verification
   не заменяет GitHub verification. При branch deployment GitHub создаёт CNAME
   в публикационной ветке; сохранять его при последующих обновлениях.
5. Перед изменениями DNS сохранить записи, включая verification TXT, почту и
   существующие поддомены. Менять только необходимые записи выбранного хоста,
   без wildcard. Не менять `maxxxpavlov.ru` и маршруты приложения. Дождаться
   проверки DNS и сертификата; включить Enforce HTTPS, когда оно доступно.
6. Проверить публичные HTTPS/HTTP 200, CSS/favicon, canonical, sitemap и robots
   на `https://maxxxpavlov.online/`. Проверить редирект с технического Pages URL
   на основной домен. Повторить мобильную навигацию на публичном адресе.
7. Выполнить live test в Google Search Console, отправить
   `https://maxxxpavlov.online/sitemap.xml` и запросить индексирование главной.
   Добавить итоговый адрес в Яндекс Вебмастер и отправить sitemap там.

Источники: [источники публикации GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site),
[подключение собственного домена](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).
Эти шаги описаны, но не применены. DNS сам по себе не выполняет HTTP-редирект;
владение доменом ещё не означает наличие веб-хостинга. Host unknown не исправляется
метатегами. Сроки индексации и позиции в выдаче не гарантируются.

## Основания для текста

Сверено с develop `9d658587871007a041f1981c084a80d2de6d78bd`:

- `README.md`: описание, shipped/roadmap, debug Android, MIT.
- `yandex_client_modules/client.py` и `yandex_client_modules/`: Responses API.
- `universal_tool_platform.py`: UniversalToolExecutor, контекст и вызовы.
- `observability/trace_manager.py`: ExecutionTrace, запросы, инструменты, время, ошибки, billing.
- `android/app/src/main/java/com/alicepro/mobile/MainActivity.kt`: WebView.

Страница не заявляет production-ready, публичный deployment, стабильный Android
release, клиентов, отзывы, метрики или неподтверждённые интеграции.

# Структура интерфейса

Web UI — repository-local Flask/static интерфейс, используемый также Android WebView. Frontend сохраняет progressive-enhancement путь и проверяется deterministic BrowserShim/VM тестами; BrowserShim не является доказательством реального browser rendering.

## Локальные ресурсы

Все ресурсы веб-интерфейса Alice Pro должны храниться в репозитории и загружаться по локальным путям. Это относится к JavaScript, CSS, шрифтам, иконкам, изображениям и отладочным библиотекам.

Не подключайте CDN, удалённые stylesheet/script URL или другие внешние asset URL в пользовательском интерфейсе. В частности, Eruda должна загружаться из `/static/eruda.js`, а favicon из `/static/favicon.svg`.

Для этого правила поддерживается регрессионный тест `tests/test_local_web_assets.py`.


## Runtime и проверки

- Основной HTML находится в `templates/index.html`, JavaScript/CSS — в `static/`.
- Frontend вызывает backend API; он не должен реализовывать отдельную модельную/secret/storage архитектуру.
- BrowserShim используется для детерминированных DOM/contract тестов и не подменяет real-browser acceptance там, где важны rendering, JavaScript engine/browser profile или внешний OAuth.
- Для production пользовательского сценария зелёные unit/BrowserShim tests должны дополняться отдельным live browser E2E evidence.

## Progressive enhancement

Изменения UI должны сохранять базовый shell и не превращать необязательную enhancement-функцию в обязательную зависимость загрузки приложения. Исторический аудит находится в [progressive-enhancement-audit.md](progressive-enhancement-audit.md); фактический current-master contract определяется кодом и текущими тестами, а не датой старого audit snapshot.

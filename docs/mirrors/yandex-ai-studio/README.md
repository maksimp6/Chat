# Русская документация Yandex AI Studio в Git LFS

Каталог `source/ru/ai-studio/` содержит **565 исходных файлов** русской документации
AI Studio в распакованном виде. Рядом сохранены необходимые доступные общие вставки,
таблицы цен и `ru/presets.yaml`, а также исходные `LICENSE`, `AUTHORS`, `README.md`.
Все файлы внутри `source/` хранятся через Git LFS. Английский раздел не включён.

Источник: [официальный yandex-cloud/docs](https://github.com/yandex-cloud/docs/tree/fddbac0226d030d57d089b730f92abd02e583df2/ru/ai-studio),
ревизия `fddbac0226d030d57d089b730f92abd02e583df2` от **2 марта 2026 года**.
Получено 23 октября 2026 года. Авторство YANDEX LLC и участников, лицензия CC BY 4.0.
Файлы поставщика не изменены.

Это исторические исходники Markdown/YFM. Для актуальных моделей, тарифов и параметров
API проверяйте [текущую документацию AI Studio](https://aistudio.yandex.ru/ru/docs/).

## Получение

Из корня checkout репозитория:

```bash
git lfs pull --include="docs/mirrors/yandex-ai-studio/source/**" --exclude=""
python - <<'PY'
import hashlib, json, pathlib
root = pathlib.Path("docs/mirrors/yandex-ai-studio")
manifest = json.loads((root / "manifest.json").read_text())
for entry in manifest["files"]:
    data = (root / entry["path"]).read_bytes()
    assert len(data) == entry["bytes"], entry["path"]
    assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]
print(f"Verified {len(manifest['files'])} files")
PY
```

Распаковка не нужна. Нужные страницы открываются в `source/ru/ai-studio/` после
скачивания LFS. [manifest.json](manifest.json) содержит пути, размеры, SHA-256
каждого файла и явный список неразрешённых include-ссылок исходной ревизии.

Изображения и внешние страницы не скачивались. Это не собранный офлайн-сайт:
часть шаблонов зависит от дополнительных материалов и настроек поставщика.
Сначала выберите нужную страницу по manifest; не загружайте весь корпус в контекст модели.

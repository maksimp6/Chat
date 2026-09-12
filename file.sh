#!/bin/bash
OUTPUT="project_dump.txt"
MAX_LOG_LINES=15000
MAX_FILE_SIZE=50000  # 50KB лимит на файл

echo "Собираю файлы проекта в $OUTPUT..."

{
echo "============================================================"
echo "ALICE PRO - ПОЛНЫЙ ДАМП ПРОЕКТА"
echo "Дата: $(date)"
echo "============================================================"
echo ""

FILES=(
  # Backend Python
  "config.py"
  "app.py"
  "db.py"
  "sdk.py"
  "yandex_client.py"
  "mcp_routes.py"
  "mcp_storage.py"
  "git_mcp_tools.py"
  "file_manager.py"
  "file_routes.py"
  "logger.py"
  "start.sh"

  # Frontend HTML
  "templates/index.html"

  # Frontend JS (основные)
  "static/core.js"
  "static/sidebar.js"
  "static/models.js"
  "static/chat.js"
  "static/voice.js"
  "static/file_manager.js"

  # Frontend JS (настройки)
  "static/settings.js"
  "static/settings/ui_helpers.js"
  "static/settings/settings_storage.js"
  "static/settings/settings_mcp.js"
  "static/settings/settings_params.js"
  "static/settings/settings_modal.js"

  # Frontend CSS
  "static/style.css"

  # Логи (только хвост)
  "logs/app.txt"
  "logs/api_debug.txt"
)

for file in "${FILES[@]}"; do
  echo "============================================================"
  echo "Файл: $file"

  if [ ! -f "$file" ]; then
    echo "[ФАЙЛ НЕ НАЙДЕН]"
    echo "============================================================"
    echo ""
    continue
  fi

  SIZE=$(wc -c < "$file")
  echo "Размер: ${SIZE} байт"
  echo "============================================================"

  # Логи — только последние N строк
  if [[ "$file" == logs/* ]]; then
    echo "[... Последние ${MAX_LOG_LINES} строк ...]"
    tail -n "$MAX_LOG_LINES" "$file"

  # eruda.js пропускаем (сторонняя библиотека, ~2МБ)
  elif [[ "$file" == *"eruda"* ]]; then
    echo "[ПРОПУЩЕН: сторонняя библиотека]"

  # Большие файлы — обрезаем
  elif [ "$SIZE" -gt "$MAX_FILE_SIZE" ]; then
    echo "[... Файл большой (${SIZE} байт), показаны первые ${MAX_FILE_SIZE} байт ...]"
    head -c "$MAX_FILE_SIZE" "$file"
    echo ""
    echo "[... ОБРЕЗАНО ...]"

  else
    cat "$file"
  fi

  echo ""
  echo ""
done

echo "============================================================"
echo "СТРУКТУРА ПРОЕКТА"
echo "============================================================"
find . -type f \
  ! -path "./__pycache__/*" \
  ! -path "./node_modules/*" \
  ! -name "*.db" \
  ! -name "*.tar.gz" \
  ! -name "project_dump.txt" \
  ! -name "eruda.js" \
  | sort

} > "$OUTPUT"

echo "✅ Готово! Файл создан: $OUTPUT"
echo "Размер: $(ls -lh "$OUTPUT" | awk '{print $5}')"

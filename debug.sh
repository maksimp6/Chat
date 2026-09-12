#!/bin/bash
# === DEBUG.SH: Скрипт диагностики проекта Alice Pro ===
# Использование: ./debug.sh [logs|errors|api|all]

LOGS_DIR="logs"
APP_LOG="$LOGS_DIR/app.txt"
API_LOG="$LOGS_DIR/api_debug.txt"

case "${1:-all}" in
  logs)
    echo "=== Последние 50 строк app.txt ==="
    tail -50 "$APP_LOG" 2>/dev/null || echo "Файл не найден"
    ;;
  errors)
    echo "=== Ошибки в логах ==="
    echo "--- app.txt ---"
    grep -i "error\|500\|502\|503" "$APP_LOG" 2>/dev/null | tail -20 || echo "Ошибок не найдено"
    echo ""
    echo "--- api_debug.txt ---"
    grep -i "error\|ERR\|403\|404\|405\|502" "$API_LOG" 2>/dev/null | tail -20 || echo "Ошибок не найдено"
    ;;
  api)
    echo "=== Анализ API-запросов ==="
    echo "Всего запросов: $(grep -c '>>>' "$APP_LOG" 2>/dev/null || echo 0)"
    echo "Успешных (2xx): $(grep '<<< 2' "$APP_LOG" 2>/dev/null | wc -l)"
    echo "Ошибок (4xx/5xx): $(grep -E '<<< [45]' "$APP_LOG" 2>/dev/null | wc -l)"
    echo ""
    echo "=== Последние API-запросы ==="
    grep '>>>' "$APP_LOG" 2>/dev/null | tail -10
    ;;
  all|*)
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║       Диагностика проекта Alice Pro                      ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo "Дата: $(date)"
    echo ""
    echo "📂 Структура проекта:"
    find . -maxdepth 2 -type f -name "*.py" -o -name "*.js" -o -name "*.html" -o -name "*.css" -o -name "*.sh" | grep -v __pycache__ | sort
    echo ""
    echo "📊 Статистика логов:"
    echo "  app.txt: $(wc -l < "$APP_LOG" 2>/dev/null || echo 0) строк"
    echo "  api_debug.txt: $(wc -l < "$API_LOG" 2>/dev/null || echo 0) строк"
    echo ""
    echo "🔍 Последние ошибки:"
    grep -i "error\|500\|502\|503" "$APP_LOG" "$API_LOG" 2>/dev/null | tail -5 || echo "Ошибок не найдено"
    echo ""
    echo "📈 Последние API-запросы:"
    grep '>>>' "$APP_LOG" 2>/dev/null | tail -5
    echo ""
    echo "💡 Рекомендации:"
    if grep -q "502" "$APP_LOG" 2>/dev/null; then
      echo "  ⚠️  Обнаружены ошибки 502. Проверьте API-ключ и доступность сервиса."
    fi
    if grep -q "403" "$API_LOG" 2>/dev/null; then
      echo "  ⚠️  Обнаружены ошибки 403. Проверьте права API-ключа для модели."
    fi
    if grep -q "extra_headers" "$API_LOG" 2>/dev/null; then
      echo "  ⚠️  Обнаружена ошибка extra_headers. Используйте additional_headers."
    fi
    echo ""
    echo "✅ Диагностика завершена"
    ;;
esac

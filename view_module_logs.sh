#!/bin/bash
# Скрипт для просмотра логов по модулям

echo "╔══════════════════════════════════════════════════════════╗"
echo "║       Просмотр логов по модулям Alice Pro                ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

if [ ! -d "logs" ]; then
    echo "❌ Папка logs не найдена"
    exit 1
fi

echo "📁 Доступные лог-файлы по модулям:"
echo ""
echo "  1) api_debug.txt      - Основной API (Responses, Conversations)"
echo "  2) vision.txt         - Vision API (анализ изображений)"
echo "  3) embeddings.txt     - Embeddings API (векторные представления)"
echo "  4) tts.txt            - TTS API (синтез речи)"
echo "  5) tokenizer.txt      - Tokenizer API (подсчёт токенов)"
echo "  6) voice.txt          - Realtime API (голосовой режим)"
echo "  7) app.txt            - Основной лог приложения"
echo "  8) errors.txt         - Все ошибки"
echo ""
echo "Выберите модуль (1-8) или 'all' для просмотра всех:"
read -p "Ваш выбор: " choice

case $choice in
    1) tail -f logs/api_debug.txt ;;
    2) tail -f logs/vision.txt ;;
    3) tail -f logs/embeddings.txt ;;
    4) tail -f logs/tts.txt ;;
    5) tail -f logs/tokenizer.txt ;;
    6) tail -f logs/voice.txt ;;
    7) tail -f logs/app.txt ;;
    8) tail -f logs/errors.txt ;;
    all)
        echo ""
        echo "=== Последние записи из всех логов ==="
        echo ""
        for file in logs/*.txt; do
            echo "--- $(basename $file) ---"
            tail -5 "$file" 2>/dev/null
            echo ""
        done
        ;;
    *) echo "Неверный выбор" ;;
esac

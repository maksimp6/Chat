#!/bin/bash
echo "╔══════════════════════════════════════════════════════════╗"
echo "║       Alice Pro - Запуск с полной поддержкой API         ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Создаём папку logs если её нет
mkdir -p logs

# Проверяем зависимости
echo "🔍 Проверка зависимостей..."
python3 -c "import flask, requests, websockets" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  Устанавливаю зависимости..."
    pip install flask requests websockets
fi

echo ""
echo "✅ Все зависимости установлены"
echo ""
echo "📦 Реализованные функции:"
echo "  1. ✅ Responses API - текстовый чат"
echo "  2. ✅ Vision API - анализ изображений"
echo "  3. ✅ Embeddings API - векторные представления"
echo "  4. ✅ TTS API - синтез речи"
echo "  5. ✅ Tokenizer API - подсчёт токенов"
echo "  6. ✅ Realtime API - голосовой режим"
echo "  7. ✅ Система логирования"
echo "  8. ✅ База данных SQLite"
echo "  9. ✅ Экспорт/импорт диалогов"
echo "  10. ✅ Поиск по истории"
echo "  11. ✅ История промптов"
echo "  12. ✅ Горячие клавиши"
echo "  13. ✅ Контекстные меню"
echo "  14. ✅ Статистика использования"
echo ""
echo "🚀 Запуск сервера..."
echo ""
python3 app.py

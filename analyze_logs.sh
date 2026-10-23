#!/bin/bash
# Скрипт для анализа логов

echo "╔══════════════════════════════════════════════════════════╗"
echo "║       Анализ логов Alice Pro                             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

if [ ! -d "logs" ]; then
    echo "❌ Папка logs не найдена"
    exit 1
fi

echo "📊 Статистика логов:"
echo ""

# Подсчет ошибок
ERRORS=$(grep -h "ERROR\|✗" logs/*.txt 2>/dev/null | wc -l)
echo "  Ошибок: $ERRORS"

# Подсчет запросов к API
API_REQUESTS=$(grep -h ">>>" logs/app.txt 2>/dev/null | wc -l)
echo "  HTTP запросов: $API_REQUESTS"

# Подсчет голосовых сессий
VOICE_SESSIONS=$(grep -h "Создание голосовой сессии" logs/voice.txt 2>/dev/null | wc -l)
echo "  Голосовых сессий: $VOICE_SESSIONS"

# Подсчет сообщений чата
CHAT_MESSAGES=$(grep -h "Запрос чата" logs/chat.txt 2>/dev/null | wc -l)
echo "  Сообщений в чате: $CHAT_MESSAGES"

# Подсчет операций с БД
DB_OPS=$(grep -h "\[DB\]" logs/database.txt 2>/dev/null | wc -l)
echo "  Операций с БД: $DB_OPS"

# Подсчет поисковых запросов
SEARCH_OPS=$(grep -h "\[SEARCH\]" logs/search.txt 2>/dev/null | wc -l)
echo "  Поисковых запросов: $SEARCH_OPS"

echo ""
echo "🔍 Последние ошибки:"
echo ""
grep -h "ERROR\|✗" logs/*.txt 2>/dev/null | tail -10

echo ""
echo "📈 Топ-5 моделей по использованию:"
echo ""
grep -h "модель=" logs/chat.txt 2>/dev/null | grep -o "модель=[^,]*" | sort | uniq -c | sort -rn | head -5

echo ""
echo "💰 Общая стоимость (из логов):"
grep -h "стоимость:" logs/chat.txt 2>/dev/null | grep -o "стоимость: [0-9.]*₽" | awk '{sum+=$2} END {print "  " sum "₽"}'

echo ""
echo "✅ Анализ завершен"

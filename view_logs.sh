#!/bin/bash
# Скрипт для просмотра логов Alice Pro

echo "╔══════════════════════════════════════════════════════════╗"
echo "║       Просмотр логов Alice Pro                           ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

if [ ! -d "logs" ]; then
    echo "❌ Папка logs не найдена"
    exit 1
fi

echo "Доступные лог-файлы:"
echo ""
ls -lh logs/*.txt 2>/dev/null | awk '{print "  " $9 " (" $5 ")"}'
echo ""

echo "Выберите действие:"
echo "  1) Просмотр app.txt (основной лог)"
echo "  2) Просмотр api_debug.txt (API запросы)"
echo "  3) Просмотр voice.txt (голосовой режим)"
echo "  4) Просмотр chat.txt (текстовый чат)"
echo "  5) Просмотр database.txt (операции с БД)"
echo "  6) Просмотр errors.txt (ошибки)"
echo "  7) Просмотр search.txt (поиск)"
echo "  8) Просмотр prompts.txt (промпты)"
echo "  9) Просмотр export.txt (экспорт/импорт)"
echo "  10) Просмотр stats.txt (статистика)"
echo "  11) Поиск ошибок во всех логах"
echo "  12) Очистить все логи"
echo "  0) Выход"
echo ""
read -p "Ваш выбор: " choice

case $choice in
    1) tail -f logs/app.txt ;;
    2) tail -f logs/api_debug.txt ;;
    3) tail -f logs/voice.txt ;;
    4) tail -f logs/chat.txt ;;
    5) tail -f logs/database.txt ;;
    6) tail -f logs/errors.txt ;;
    7) tail -f logs/search.txt ;;
    8) tail -f logs/prompts.txt ;;
    9) tail -f logs/export.txt ;;
    10) tail -f logs/stats.txt ;;
    11)
        echo ""
        echo "Поиск ошибок во всех логах..."
        echo ""
        grep -h "ERROR\|error\|✗" logs/*.txt | tail -50
        ;;
    12)
        read -p "Вы уверены? Все логи будут удалены (y/n): " confirm
        if [ "$confirm" = "y" ]; then
            rm -f logs/*.txt
            echo "✅ Все логи очищены"
        else
            echo "Отменено"
        fi
        ;;
    0) exit 0 ;;
    *) echo "Неверный выбор" ;;
esac

#!/bin/bash
OUTPUT="project_dump.txt"
MAX_LOG=15000
MAX_SIZE=50000

echo "Собираю файлы проекта (Git + логи) в $OUTPUT..."

{
echo "============================================================"
echo "ALICE PRO - ДАМП ПРОЕКТА (Git tracked + logs)"
echo "Дата: $(date)"
echo "============================================================"
echo ""

# Получаем список файлов под Git
GIT_FILES=$(git ls-files 2>/dev/null)

if [ -z "$GIT_FILES" ]; then
    echo "⚠️  Git не инициализирован или нет отслеживаемых файлов"
    echo "Использую альтернативный список файлов..."
    GIT_FILES=$(find . -type f \
        ! -path "./.git/*" \
        ! -path "./node_modules/*" \
        ! -path "./__pycache__/*" \
        ! -path "./r/*" \
        ! -name "*.db" \
        ! -name "*.tar.gz" \
        ! -name "project_dump.txt" \
        ! -name "eruda.js" \
        ! -name "*.pyc" \
        | sed 's|^\./||' | sort)
fi

# Добавляем логи (они могут не быть в Git)
LOG_FILES=$(find logs -type f 2>/dev/null | sort)

# Объединяем списки
ALL_FILES=$(echo -e "$GIT_FILES\n$LOG_FILES" | sort -u)

for file in $ALL_FILES; do
    echo "============================================================"
    echo "Файл: $file"
    
    if [ ! -f "$file" ]; then
        echo "[НЕ НАЙДЕН]"
        echo "============================================================"
        echo ""
        continue
    fi
    
    SIZE=$(wc -c < "$file")
    echo "Размер: ${SIZE} байт"
    echo "============================================================"
    
    # Логи — только последние N строк
    if [[ "$file" == logs/* ]]; then
        echo "[... Последние ${MAX_LOG} строк ...]"
        tail -n "$MAX_LOG" "$file"
    
    # Большие файлы — обрезаем
    elif [ "$SIZE" -gt "$MAX_SIZE" ]; then
        echo "[... Файл большой (${SIZE} байт), показаны первые ${MAX_SIZE} байт ...]"
        head -c "$MAX_SIZE" "$file"
        echo ""
        echo "[... ОБРЕЗАНО ...]"
    
    else
        cat "$file"
    fi
    
    echo ""
done

echo "============================================================"
echo "ИТОГО ФАЙЛОВ: $(echo "$ALL_FILES" | wc -l)"
echo "============================================================"

} > "$OUTPUT"

echo "✅ Готово! Файл создан: $OUTPUT"
echo "Размер: $(ls -lh "$OUTPUT" | awk '{print $5}')"
echo "Количество файлов: $(echo "$ALL_FILES" | wc -l)"

#!/data/data/com.termux/files/usr/bin/bash
# Ищет мусорные файлы и пишет отчёт. Ничего не удаляет.
# Использование: find-junk.sh [файл_отчёта]
REPORT="${1:-$HOME/junk-report.txt}"

# Пути, которые не трогаем вообще
EXCL=(
  "$HOME/.logos-rdc"
  "$HOME/.ssh" "$HOME/.local" "$HOME/.config"
  "$HOME/.claude-server-commander" "$HOME/.desktop-commander-device"
)
PRUNE=()
for e in "${EXCL[@]}"; do PRUNE+=( -path "$e" -prune -o ); done

section() {
  local title="$1"; shift
  echo
  echo "## $title"
  "$@" 2>/dev/null | while IFS= read -r p; do
    [ -n "$p" ] || continue
    printf '%8s KB  %s\n' "$(du -sk "$p" 2>/dev/null | cut -f1)" "$p"
  done
}

{
  echo "# Отчёт поиска мусора: $(date '+%F %T')"

  section "Временные и служебные файлы (Termux home)" \
    find "$HOME" "${PRUNE[@]}" \( -name '__pycache__' -o -name '*.pyc' -o -name '*.tmp' \
      -o -name '*.bak' -o -name '*~' -o -name '*.part' -o -name '*.crdownload' -o -name '.DS_Store' \) -print

  section "Логи старше 30 дней и больше 1 МБ" \
    find "$HOME" "${PRUNE[@]}" -name '*.log' -mtime +30 -size +1M -print

  section "Пустые директории (Termux home)" \
    find "$HOME" "${PRUNE[@]}" -type d -empty -print

  section "Кэш и временные файлы на /sdcard (нужен root)" \
    su -c "find /sdcard/Download /sdcard/Logos /sdcard/Documents -maxdepth 4 \( -name '*.tmp' -o -name '*.part' -o -name '*(1).apk' \) -print"

  section "APK-установщики (проверьте, установлены ли приложения)" \
    su -c "find /sdcard/Logos /sdcard/Download -maxdepth 4 -name '*.apk' -print"

  echo
  echo "Это только список кандидатов. Перед удалением проверьте каждый путь."
} > "$REPORT"

echo "Отчёт: $REPORT"
echo "Кандидатов: $(grep -c '^ *[0-9]* KB' "$REPORT")"

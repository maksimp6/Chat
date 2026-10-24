#!/data/data/com.termux/files/usr/bin/bash
# Стадия COMPILE. Вход: исходник C. Выход: .so для arm64-v8a.
# -mcmodel=tiny: адреса данных одной инструкцией adr вместо пары adrp+add (код и данные в пределах 1 МБ).
# clang вызывается напрямую: обёртка aarch64-linux-android-clang — bash-скрипт, лишний запуск shell.
# Использование: compile.sh <launcher.c> <out.so>
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: compile.sh <src.c> <out.so>" >&2; exit 2; }
exec clang --target=aarch64-linux-android24 -shared -fPIC -O2 -Wall -mcmodel=tiny -o "$2" "$1" -landroid -llog

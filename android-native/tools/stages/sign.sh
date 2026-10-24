#!/data/data/com.termux/files/usr/bin/bash
# Стадия SIGN. Вход: base.apk + .so. Выход: подписанный APK ровно заданного размера.
# Подпись v1 через tools/sign_v1.py. Бюджет: см. perf-budget.env.
# Использование: sign.sh <base.apk> <libalicelauncher.so> <out.apk> <key.pem> <cert.pem> <exact_bytes>
set -euo pipefail
[ $# -eq 6 ] || { echo "usage: sign.sh <base> <so> <out> <key> <cert> <exact>" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$HERE/sign_v1.py" "$1" "$3" "$4" "$5" \
  "lib/arm64-v8a/libalicelauncher.so=$2" --exact "$6" --pad assets/reserved.bin

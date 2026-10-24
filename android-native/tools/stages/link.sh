#!/data/data/com.termux/files/usr/bin/bash
# Стадия LINK. Вход: AndroidManifest.xml. Выход: base.apk (ресурсы + манифест, без кода).
# targetSdk 29: установка принимает подпись v1. Бюджет: см. perf-budget.env.
# Использование: link.sh <AndroidManifest.xml> <android.jar> <out base.apk>
set -euo pipefail
[ $# -eq 3 ] || { echo "usage: link.sh <manifest> <jar> <out.apk>" >&2; exit 2; }
aapt2 link -o "$3" -I "$2" --manifest "$1" \
  --min-sdk-version 26 --target-sdk-version 29 \
  --version-code 1 --version-name 0.1.0

#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
set +x
umask 077
test "$(/system/bin/getprop ro.product.model)" = 'Redmi 9'
mkdir -p "$HOME/logos-local-state"
exec 9>"$HOME/logos-local-state/yandex-issuance.lock"
flock -n 9 || { echo 'Yandex key issuance is already running'; exit 1; }
python "$(dirname "$0")/redmi_issue_yandex_key.py"

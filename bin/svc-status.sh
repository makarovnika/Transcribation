#!/usr/bin/env bash
# svc-status.sh — короткий status check сервиса.

set -euo pipefail

LABEL="com.muraveika.transcriber"
DOMAIN="gui/$(id -u)"

if ! launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  echo "✗ Agent НЕ установлен. bin/svc-install.sh — поставить."
  exit 1
fi

STATE=$(launchctl print "${DOMAIN}/${LABEL}" | awk '/state =/{print $3}')
PID=$(launchctl print "${DOMAIN}/${LABEL}" | awk '/pid =/{print $3}')

case "$STATE" in
  running)
    echo "✓ Сервис работает. PID=$PID"
    echo "  URL: http://127.0.0.1:7860"
    ;;
  not\ running|exited)
    echo "✗ Сервис не работает (state=$STATE). Логи: bin/svc-logs.sh"
    ;;
  *)
    echo "? Состояние: $STATE"
    ;;
esac

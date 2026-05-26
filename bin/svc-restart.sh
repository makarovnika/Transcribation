#!/usr/bin/env bash
# svc-restart.sh — перезапустить сервис (применить изменения в коде).
#
# Используем `launchctl kickstart -k` — официальная команда для рестарта
# уже загруженного Agent'а. В отличие от unload+load она:
#   - не дёргает overrides.plist (быстрее),
#   - не страдает от EIO-багов на macOS 14+.

set -euo pipefail

LABEL="com.muraveika.transcriber"
DOMAIN="gui/$(id -u)"

if ! launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  echo "ERROR: сервис не установлен. Сначала: bin/svc-install.sh"
  exit 1
fi

OLD_PID=$(launchctl print "${DOMAIN}/${LABEL}" 2>/dev/null | awk '/^\s*pid\s*=/{print $3; exit}')

echo "[..] kickstart -k ${DOMAIN}/${LABEL} (PID до=${OLD_PID:-?})"
launchctl kickstart -k "${DOMAIN}/${LABEL}"
sleep 3

NEW_PID=$(launchctl print "${DOMAIN}/${LABEL}" 2>/dev/null | awk '/^\s*pid\s*=/{print $3; exit}')
STATE=$(launchctl print "${DOMAIN}/${LABEL}" 2>/dev/null | awk '/^\s*state\s*=/{print $3; exit}')

if [ "$STATE" = "running" ] && [ -n "$NEW_PID" ] && [ "$NEW_PID" != "$OLD_PID" ]; then
  echo "[ok] сервис перезапущен, PID=${NEW_PID}"
elif [ "$STATE" = "running" ]; then
  echo "[warn] state=running, но PID не сменился (${OLD_PID}). Возможно, kickstart не убил процесс — проверь bin/svc-logs.sh"
else
  echo "[fail] state=${STATE:-unknown} — проверь bin/svc-logs.sh"
  exit 1
fi

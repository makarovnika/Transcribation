#!/usr/bin/env bash
# svc-uninstall.sh — снять launchd Agent и удалить plist из ~/Library/LaunchAgents.
# После этого сервис больше не будет запускаться автоматически.

set -euo pipefail

LABEL="com.muraveika.transcriber"
DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
  echo "[..] unload $LABEL"
  launchctl unload -w "$DST" 2>/dev/null || true
else
  echo "[skip] Agent не зарегистрирован"
fi

if [ -f "$DST" ]; then
  rm -f "$DST"
  echo "[ok] удалён $DST"
else
  echo "[skip] plist уже отсутствует: $DST"
fi

echo "Готово. Сервис больше не запускается автоматически."

#!/usr/bin/env bash
# svc-logs.sh — стримит логи сервиса в режиме tail -F.
# Прерывание — Ctrl+C.

set -euo pipefail

LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"

if [ ! -d "$LOG_DIR" ]; then
  echo "ERROR: $LOG_DIR не существует"
  exit 1
fi

# -F (а не -f) переоткрывает файл при ротации — полезно если launchd
# рестартанул процесс и пересоздал лог.
echo "=== Логи: $LOG_DIR (Ctrl+C чтобы выйти) ==="
exec tail -F \
  "${LOG_DIR}/transcriber.out.log" \
  "${LOG_DIR}/transcriber.err.log"

#!/usr/bin/env bash
# svc-install.sh — установка launchd Agent транскрибатора.
#
# Что делает:
#   1) Копирует bin/com.muraveika.transcriber.plist → ~/Library/LaunchAgents/
#   2) Загружает Agent через `launchctl load -w` (это и стартует процесс)
#   3) Печатает статус и URL UI.
#
# Идемпотентно: если Agent уже зарегистрирован, сначала unload, потом load.
#
# Почему `load -w`, а не `launchctl bootstrap`:
#   На некоторых macOS (особенно 14+) `bootstrap` срыгивает
#   "Bootstrap failed: 5: Input/output error" без видимой причины,
#   а старый `load -w` работает стабильно. Apple официально объявил
#   load/unload устаревшими, но не убрал их.

set -euo pipefail

LABEL="com.muraveika.transcriber"
BIN_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${BIN_DIR}/.." && pwd)"
TEMPLATE="${BIN_DIR}/${LABEL}.plist.template"
DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

# Используем шаблон с маркерами __HOME__ / __PROJECT__ — это даёт переносимость
# между машинами без правки plist под конкретный username.
# Если шаблона нет (старая установка) — fallback на готовый plist рядом.
if [ -f "$TEMPLATE" ]; then
  echo "[..] рендерю plist из шаблона (HOME=${HOME}, PROJECT=${PROJECT_DIR})"
  RENDERED="$(mktemp /tmp/transcriber.plist.XXXXXX)"
  sed -e "s|__HOME__|${HOME}|g" -e "s|__PROJECT__|${PROJECT_DIR}|g" \
    "$TEMPLATE" > "$RENDERED"
  SRC="$RENDERED"
elif [ -f "${BIN_DIR}/${LABEL}.plist" ]; then
  SRC="${BIN_DIR}/${LABEL}.plist"
else
  echo "ERROR: ни шаблона ($TEMPLATE), ни plist не найдено"
  exit 1
fi

# Если уже загружен — снимаем, иначе load молча ничего не сделает (или ругнётся).
if launchctl list 2>/dev/null | grep -q "$LABEL"; then
  echo "[..] снимаю старую регистрацию"
  launchctl unload -w "$DST" 2>/dev/null || true
  sleep 1
fi

# Прибиваем любой ручной python app.py, иначе оба будут драться за порт 7860.
if pgrep -f "python.*app\\.py" >/dev/null 2>&1; then
  echo "[..] нахожу запущенный вручную app.py — глушу"
  pkill -f "python.*app\\.py" 2>/dev/null || true
  sleep 1
fi

mkdir -p "${HOME}/Library/LaunchAgents"
cp "$SRC" "$DST"
# Если рендерили во временный файл — удалим оригинал, чтобы не мусорить в /tmp.
if [ -n "${RENDERED:-}" ] && [ -f "$RENDERED" ]; then
  rm -f "$RENDERED"
fi
echo "[ok] plist установлен: $DST"

# -w: записать в overrides.plist, чтобы launchd помнил «enable» между перезагрузками.
launchctl load -w "$DST"
echo "[ok] Agent загружен"

# Дадим пару секунд на старт и покажем статус.
sleep 3
DOMAIN="gui/$(id -u)"
if launchctl print "${DOMAIN}/${LABEL}" 2>/dev/null | grep -qE 'state\s*=\s*running'; then
  PID=$(launchctl print "${DOMAIN}/${LABEL}" 2>/dev/null | awk '/pid =/{print $3}')
  echo "[ok] сервис запущен, PID=${PID}"
else
  echo "[warn] сервис ещё не в state=running — посмотри logs/transcriber.err.log"
fi

echo
echo "URL:     http://127.0.0.1:7860"
echo "Status:  bin/svc-status.sh"
echo "Логи:    bin/svc-logs.sh"
echo "Restart: bin/svc-restart.sh"
echo "Снять:   bin/svc-uninstall.sh"

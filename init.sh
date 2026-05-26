#!/usr/bin/env bash
# init.sh — стандартный путь старта проекта.
# Идея: после клонирования репо запуск этого скрипта должен поднять окружение
# и прогнать минимальные проверки. Если падает — чинить его первым.

set -euo pipefail

cd "$(dirname "$0")"

echo "=== transcriber: init.sh ==="

# 1. ffmpeg — системная зависимость, без неё не конвертируем форматы.
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ERROR: ffmpeg не найден. Установи: brew install ffmpeg"
  exit 1
fi
echo "[ok] ffmpeg: $(ffmpeg -version | head -n1)"

# 2. uv — быстрый менеджер зависимостей. Если нет — fallback на python -m venv.
if command -v uv >/dev/null 2>&1; then
  PKG_MGR="uv"
  echo "[ok] uv найден"
else
  PKG_MGR="pip"
  echo "[warn] uv не найден, используем стандартный python -m venv + pip"
fi

# 3. venv. Не пересоздаём, если уже есть — это ускоряет повторные запуски.
if [ ! -d ".venv" ]; then
  echo "[..] создаю .venv"
  if [ "$PKG_MGR" = "uv" ]; then
    uv venv
  else
    python3 -m venv .venv
  fi
fi

# Активация — для текущего шелла init.sh; пользователь должен сделать source отдельно.
# shellcheck disable=SC1091
source .venv/bin/activate

# 4. Установка зависимостей. -e . чтобы код src/ был импортируем как пакет.
if [ "$PKG_MGR" = "uv" ]; then
  uv pip install -e ".[dev]" --quiet
else
  pip install -e ".[dev]" --quiet
fi
echo "[ok] зависимости установлены"

# 5. Smoke-тесты: только то, что не требует моделей и сети.
#    Тесты на alignment и exporters — детерминированные, должны проходить всегда.
if [ -d "tests" ] && find tests -name 'test_*.py' | grep -q .; then
  echo "[..] pytest (только smoke)"
  pytest tests/ -q --no-header -x || {
    echo "[fail] smoke-тесты упали — это блокер";
    exit 1;
  }
  echo "[ok] smoke-тесты пройдены"
else
  echo "[warn] тестов пока нет — пропускаю pytest"
fi

echo "=== init.sh OK ==="
echo "Запуск приложения: python app.py"

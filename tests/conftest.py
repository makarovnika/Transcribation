"""Конфиг pytest. Тут только общие фикстуры — пока пусто, добавим по мере роста."""

import sys
from pathlib import Path

# Делаем src/ импортируемым в тестах без обязательной установки пакета.
# Не идеально (правильнее ставить пакет через `uv pip install -e .`),
# но удобно для запуска pytest без полного init.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

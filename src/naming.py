"""Slug и сборка stem для имени файлов экспорта (F12 ТЗ v2).

Зачем выделено в модуль:
- Чистая логика без I/O, легко тестируется юнит-тестами.
- app.py остаётся «UI-склейкой», бизнес-правила переезжают в src/.

Стратегия по Unicode:
- НЕ транслитерируем (не делаем «команды» → «komandy»). Кириллица в имени файла
  допустима на macOS/Windows/Linux в UTF-8 FS, а слаг с латиницей теряет смысл
  для пользователя, который писал «Sync команды» — он хочет видеть это название
  в файлах.
- Регэксп ``[^\\w\\-]+`` пропускает все Unicode word chars (буквы, цифры, _)
  и дефис, всё остальное (пробелы, пунктуация, эмодзи) превращается в дефис.

Формат stem: `{YYYY-MM-DD}_{slug}_{hash6}`.
"""

from __future__ import annotations

import re
from datetime import datetime

# Длина slug — 60 символов. Достаточно длинно для смысла, не лезет в OS-limits.
SLUG_MAX_LEN = 60

# Хеш — 6 символов от audio_fingerprint. Защищает от коллизий, когда за день
# несколько встреч с одним названием. Длина 6 — компромисс читаемости и
# вероятности коллизии (~1 на 16 млн).
SHORT_HASH_LEN = 6

# Fallback-слаг для пустого title — это короче и понятнее, чем «безымянный».
DEFAULT_SLUG = "untitled"

# Регэксп для slug'a. Захватывает «не word и не дефис», заменяет на дефис.
# re.UNICODE по умолчанию в Python 3 — \w включает кириллицу и др.
_SLUG_RE = re.compile(r"[^\w\-]+", re.UNICODE)


def slugify(title: str) -> str:
    """Превратить произвольный заголовок в slug для имени файла.

    Примеры:
        ""                  → "untitled"
        "Sync команды"      → "sync-команды"
        "Q3 / planning!"    → "q3-planning"
        "  ---  "           → "untitled"
        "очень длинная..."  → обрезается до SLUG_MAX_LEN
    """
    if not title:
        return DEFAULT_SLUG

    # Lowercase ДО регэкспа — чтобы единообразно. casefold лучше для UTF
    # (правильно обрабатывает турецкое i, немецкое ß), но lower достаточно
    # для русского/английского.
    s = title.lower()

    # Заменяем не-word на дефис.
    s = _SLUG_RE.sub("-", s)

    # Убираем повторяющиеся дефисы (`a---b` → `a-b`).
    s = re.sub(r"-{2,}", "-", s)

    # Обрезаем крайние дефисы.
    s = s.strip("-")

    # Длина.
    if len(s) > SLUG_MAX_LEN:
        # Обрезаем по последнему дефису, чтобы не резать посреди слова.
        s = s[:SLUG_MAX_LEN].rstrip("-")
        # На случай если последний дефис был очень близко к концу — оставляем как есть.

    if not s:
        return DEFAULT_SLUG
    return s


def build_stem(
    title: str | None,
    audio_fingerprint: str,
    *,
    date: datetime | None = None,
) -> str:
    """Собрать stem (имя файла без расширения).

    Args:
        title: то, что ввёл пользователь (может быть пустым).
        audio_fingerprint: длинный хэш из transcription._audio_fingerprint;
            берём первые SHORT_HASH_LEN символов.
        date: локальная дата старта (для тестов можно передать явно).

    Returns:
        Строка вида '2026-05-26_sync-команды_a1b2c3'.
    """
    when = date if date is not None else datetime.now()
    date_str = when.strftime("%Y-%m-%d")
    slug = slugify(title or "")
    short_hash = (audio_fingerprint or "")[:SHORT_HASH_LEN]
    if not short_hash:
        # Совсем без хеша — добавим эпоху как fallback (уникальность важнее красоты).
        short_hash = f"{int(when.timestamp())}"[:SHORT_HASH_LEN]
    return f"{date_str}_{slug}_{short_hash}"

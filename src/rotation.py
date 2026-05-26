"""Ротация файлов в outputs/ и cache/ (F26 ТЗ v2).

Зачем:
- За месяц-два регулярных встреч outputs/ может вырасти до 5+ ГБ.
- partial.json в cache/ накапливаются для каждой сессии × модель.
- Без ротации диск медленно но верно забивается.

Стратегия:
- Группа = stem (всё до расширения). Например, `2026-05-26_sync_abc123` — это
  одна группа из ~6 файлов: .txt, .srt, .vtt, .json, .md, .meta.json.
- Удаляем по группам, не по отдельным файлам — иначе .meta.json может пережить
  свой .json и остаться сиротой.
- Два критерия:
  1. retention_days — всё старше N дней удаляется.
  2. max_entries — если групп больше N, удаляем самые старые до лимита.
  Применяются обе в указанном порядке.
- mtime берём максимальный среди файлов группы (если кто-то трогал — считаем «свежим»).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("transcriber.rotation")

# Дефолты по ТЗ §F26. Можно поднять/опустить в config.
DEFAULT_RETENTION_DAYS = 60
DEFAULT_MAX_ENTRIES = 100

# Расширения, относящиеся к одной «сессии транскрипции».
# .meta.json исключаем из суффикс-фильтра — у него двойное расширение, обрабатываем отдельно.
SESSION_EXTS = {".txt", ".srt", ".vtt", ".json", ".md"}


@dataclass(frozen=True)
class GroupInfo:
    """Группа файлов с одинаковым stem. mtime — самый свежий среди файлов группы."""

    stem: str
    files: tuple[Path, ...]
    total_bytes: int
    mtime: float


@dataclass(frozen=True)
class CleanupResult:
    """Что удалили в результате прохода."""

    scanned_groups: int
    removed_groups: int
    removed_bytes: int
    reason_old: int       # сколько групп удалили по retention
    reason_overflow: int  # сколько по max_entries


def _group_stem(p: Path) -> str:
    """Стем без расширения. Для .meta.json — двойного — убираем оба."""
    if p.name.endswith(".meta.json"):
        return p.name[: -len(".meta.json")]
    return p.stem


def _looks_like_session_file(p: Path) -> bool:
    """Понимаем ли мы этот файл как часть транскрипционной группы."""
    if p.name.endswith(".meta.json"):
        return True
    return p.suffix.lower() in SESSION_EXTS


def scan_outputs(dir_path: Path) -> list[GroupInfo]:
    """Сканирует папку и группирует файлы по stem'у. Сортирует по mtime убыванию."""
    if not dir_path.is_dir():
        return []

    by_stem: dict[str, list[Path]] = defaultdict(list)
    for entry in dir_path.iterdir():
        if not entry.is_file():
            continue
        if not _looks_like_session_file(entry):
            continue
        by_stem[_group_stem(entry)].append(entry)

    groups: list[GroupInfo] = []
    for stem, files in by_stem.items():
        files_t = tuple(sorted(files))
        # Берём максимальный mtime — если кто-то открывал/менял, оставляем свежим.
        # min также вариант (когда последняя запись = атомарная), но max интуитивнее.
        try:
            mt = max(f.stat().st_mtime for f in files_t)
            sz = sum(f.stat().st_size for f in files_t)
        except OSError:
            continue  # файл исчез между iterdir и stat — игнорим
        groups.append(GroupInfo(stem=stem, files=files_t, total_bytes=sz, mtime=mt))

    groups.sort(key=lambda g: g.mtime, reverse=True)
    return groups


def cleanup_outputs(
    dir_path: Path,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    dry_run: bool = False,
    now: float | None = None,
) -> CleanupResult:
    """Удалить устаревшие группы файлов.

    Args:
        dir_path: outputs/ или другая папка с тем же layout.
        retention_days: сколько хранить. 0 → отключено.
        max_entries: предельное число групп. 0 → отключено.
        dry_run: не удалять, только посчитать (для тестов и preview-UI).
        now: фейковое «сейчас» для тестов. Без него — time.time().

    Returns:
        CleanupResult со статистикой.
    """
    groups = scan_outputs(dir_path)
    scanned = len(groups)
    now_ts = now if now is not None else time.time()

    to_remove: list[GroupInfo] = []
    reason_old = 0
    reason_overflow = 0

    if retention_days > 0:
        cutoff = now_ts - retention_days * 86400
        old = [g for g in groups if g.mtime < cutoff]
        to_remove.extend(old)
        reason_old = len(old)

    # Считаем «выжившие» после retention и применяем max_entries только к ним.
    remaining = [g for g in groups if g not in to_remove]
    if max_entries > 0 and len(remaining) > max_entries:
        # remaining уже отсортирован по mtime DESC. Лишние — это «хвост».
        overflow = remaining[max_entries:]
        to_remove.extend(overflow)
        reason_overflow = len(overflow)

    removed_bytes = 0
    if not dry_run:
        for g in to_remove:
            for f in g.files:
                try:
                    sz = f.stat().st_size
                    f.unlink()
                    removed_bytes += sz
                except OSError as e:
                    log.warning("rotation: failed to remove %s: %s", f, e)
            log.info("rotation: removed group %s (%d files)", g.stem, len(g.files))
    else:
        removed_bytes = sum(g.total_bytes for g in to_remove)

    return CleanupResult(
        scanned_groups=scanned,
        removed_groups=len(to_remove),
        removed_bytes=removed_bytes,
        reason_old=reason_old,
        reason_overflow=reason_overflow,
    )


def folder_stats(dir_path: Path) -> tuple[int, int]:
    """(total_bytes, total_files) по всем файлам в папке (рекурсивно). 0/0 если папки нет."""
    if not dir_path.is_dir():
        return (0, 0)
    total_bytes = 0
    total_files = 0
    for f in dir_path.rglob("*"):
        if f.is_file():
            try:
                total_bytes += f.stat().st_size
                total_files += 1
            except OSError:
                continue
    return total_bytes, total_files


def human_size(num_bytes: int) -> str:
    """120 → '120 B', 1500 → '1.5 КБ', 1500000 → '1.5 МБ'."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    for unit in ("КБ", "МБ", "ГБ", "ТБ"):
        num_bytes /= 1024  # type: ignore[assignment]
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
    return f"{num_bytes:.1f} ПБ"

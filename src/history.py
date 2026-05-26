"""История встреч (F14 ТЗ v2).

Считывает все `.meta.json` из outputs/ + конструирует summary-список:
[id, title, created_at, duration_sec, speakers_count, stem_path].

Legacy: для `transcript_<unix>.*` без `.meta.json` возвращаем заглушку с
title=«Без названия» и id из stem — чтобы пользователь видел старые сессии.

Сортировка: по created_at desc (новые сверху).

Удаление: сносит ВСЕ файлы с этим stem'ом (.txt, .srt, .vtt, .json, .md,
.meta.json). Опасный путь — поэтому путь сохраняем в MeetingItem и не
принимаем произвольный stem от UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import meta as meta_mod
from .rotation import _group_stem  # переиспользуем — двойное .meta.json

log = logging.getLogger("transcriber.history")


@dataclass(frozen=True)
class MeetingItem:
    """Один элемент истории — что показать в списке + чем оперировать при click/delete."""

    id: str                     # обычно = stem
    title: str                  # человеческое название (или 'Без названия' для legacy)
    created_at: str             # ISO-8601 или ""
    created_ts: float           # для сортировки и форматирования (Unix timestamp)
    duration_sec: float
    speakers_count: int
    stem_path: Path             # абсолютный путь к stem (для удаления и load)
    is_legacy: bool             # True если нет .meta.json
    has_summary: bool           # F19: True если в .meta.json есть summary.tldr


def _parse_iso_to_ts(iso: str) -> float:
    """ISO-8601 → Unix timestamp. На ошибке — 0.0."""
    if not iso:
        return 0.0
    try:
        # Терпимо: парсим '2026-05-26T10:42:18Z' и '2026-05-26T10:42:18+00:00'.
        s = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _legacy_item(stem_path: Path) -> MeetingItem | None:
    """Построить заглушку для transcript_<unix>.* без .meta.json."""
    name = stem_path.name
    # Стем мы получаем как `dir/transcript_1779736448` (без расширения).
    # Из неё мы вытащим '1779736448' и используем как timestamp.
    parts = name.rsplit("_", 1)
    try:
        ts = float(parts[-1])
    except ValueError:
        # Имя не похоже на legacy формат — пропускаем (не показываем в истории).
        return None

    # Длительность достать без чтения большого .json — берём mtime файла.
    try:
        mt = stem_path.with_suffix(".json").stat().st_mtime
    except OSError:
        mt = ts

    return MeetingItem(
        id=name,
        title="Без названия",
        created_at=datetime.fromtimestamp(ts).isoformat() + "Z",
        created_ts=ts,
        duration_sec=0.0,           # неизвестно без чтения JSON
        speakers_count=0,
        stem_path=stem_path,
        is_legacy=True,
        has_summary=False,
    )


def _meta_to_item(stem_path: Path, m: meta_mod.MeetingMeta) -> MeetingItem:
    """Построить из MeetingMeta — основной путь."""
    ts = _parse_iso_to_ts(m.created_at)
    if ts == 0.0:
        try:
            ts = stem_path.with_suffix(".json").stat().st_mtime
        except OSError:
            ts = 0.0
    speakers_count = len(m.speakers) if m.speakers else 0
    return MeetingItem(
        id=m.id or stem_path.name,
        title=m.title or "Без названия",
        created_at=m.created_at,
        created_ts=ts,
        duration_sec=m.duration_sec,
        speakers_count=speakers_count,
        stem_path=stem_path,
        is_legacy=False,
        has_summary=bool((m.summary or {}).get("tldr")),
    )


def scan_meetings(outputs_dir: Path) -> list[MeetingItem]:
    """Просканировать outputs/ и вернуть список встреч, новые сверху."""
    if not outputs_dir.is_dir():
        return []

    seen_stems: set[str] = set()
    items: list[MeetingItem] = []

    # Сначала проходим по .meta.json — это наш основной источник.
    for f in outputs_dir.glob("*.meta.json"):
        stem = _group_stem(f)
        seen_stems.add(stem)
        stem_path = outputs_dir / stem
        m = meta_mod.load_meta(stem_path)
        if m is None:
            continue  # битый JSON — пропускаем
        items.append(_meta_to_item(stem_path, m))

    # Дальше — legacy без .meta.json. Ищем по .json (наш основной экспорт).
    # Если у файла стем не в seen_stems и имя начинается с 'transcript_' —
    # это legacy.
    for f in outputs_dir.glob("transcript_*.json"):
        if f.name.endswith(".meta.json"):
            continue
        stem = f.stem
        if stem in seen_stems:
            continue
        legacy = _legacy_item(outputs_dir / stem)
        if legacy:
            seen_stems.add(stem)
            items.append(legacy)

    items.sort(key=lambda i: -i.created_ts)
    return items


def format_relative_date(ts: float, now: float | None = None) -> str:
    """`сегодня`, `вчера`, или `DD MMM` для UI-списка."""
    import time
    now_ts = now if now is not None else time.time()
    if ts <= 0:
        return ""
    delta = now_ts - ts
    if delta < 0:
        delta = 0
    days = int(delta // 86400)
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days < 7:
        return f"{days}д назад"
    # Дальше — дата
    d = datetime.fromtimestamp(ts)
    months = ["янв", "фев", "мар", "апр", "май", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек"]
    return f"{d.day} {months[d.month - 1]}"


def delete_meeting(stem_path: Path) -> int:
    """Удалить все файлы группы. Возвращает количество удалённых файлов.

    Сносит .txt, .srt, .vtt, .json, .md, .meta.json и любые ассоциированные
    через тот же stem. Не падает, если файл уже отсутствует.
    """
    parent = stem_path.parent
    name = stem_path.name
    removed = 0
    # Перечисляем явные расширения + .meta.json. Не используем glob 'stem.*' —
    # он бы захватил stem.json.tmp и другие промежуточные файлы.
    candidates = [
        parent / f"{name}.txt",
        parent / f"{name}.srt",
        parent / f"{name}.vtt",
        parent / f"{name}.json",
        parent / f"{name}.md",
        parent / f"{name}.meta.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                p.unlink()
                removed += 1
            except OSError as e:
                log.warning("delete_meeting: failed to remove %s: %s", p, e)
    log.info("deleted meeting %s: %d files removed", name, removed)
    return removed

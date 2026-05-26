"""Тесты для F14 — история встреч."""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.history import (
    delete_meeting,
    format_relative_date,
    scan_meetings,
)


def _write_meta(dir_path: Path, stem: str, *, title: str, created_at: str,
                duration: float = 0.0, speakers_count: int = 0) -> None:
    """Создаёт пару stem.json + stem.meta.json."""
    (dir_path / f"{stem}.json").write_text("{}", encoding="utf-8")
    speakers = {
        f"SPEAKER_{i:02d}": {"display_name": "", "speech_seconds": 0.0, "turns": 0}
        for i in range(speakers_count)
    }
    meta = {
        "schema_version": 2,
        "id": stem,
        "title": title,
        "created_at": created_at,
        "duration_sec": duration,
        "speakers": speakers,
        "summary": {},
        "files": {"json": f"{stem}.json"},
    }
    (dir_path / f"{stem}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _write_legacy(dir_path: Path, unix_ts: int) -> None:
    """Старый формат transcript_<unix>.* без .meta.json."""
    (dir_path / f"transcript_{unix_ts}.json").write_text("{}", encoding="utf-8")
    (dir_path / f"transcript_{unix_ts}.txt").write_text("hi", encoding="utf-8")


# ---------- scan_meetings ----------

def test_scan_empty(tmp_path: Path) -> None:
    assert scan_meetings(tmp_path) == []


def test_scan_missing_dir(tmp_path: Path) -> None:
    assert scan_meetings(tmp_path / "nope") == []


def test_scan_three_meetings_sorted_desc(tmp_path: Path) -> None:
    _write_meta(tmp_path, "2026-05-24_old", title="Старая", created_at="2026-05-24T10:00:00Z")
    _write_meta(tmp_path, "2026-05-26_new", title="Новая", created_at="2026-05-26T10:00:00Z")
    _write_meta(tmp_path, "2026-05-25_mid", title="Средняя", created_at="2026-05-25T10:00:00Z")

    items = scan_meetings(tmp_path)
    assert len(items) == 3
    # Новые сверху.
    assert [i.title for i in items] == ["Новая", "Средняя", "Старая"]


def test_scan_includes_legacy(tmp_path: Path) -> None:
    _write_meta(tmp_path, "2026-05-26_new", title="Новая", created_at="2026-05-26T10:00:00Z")
    _write_legacy(tmp_path, 1700000000)  # legacy 2023-11-15

    items = scan_meetings(tmp_path)
    assert len(items) == 2
    legacy = [i for i in items if i.is_legacy]
    assert len(legacy) == 1
    assert legacy[0].title == "Без названия"
    assert legacy[0].id == "transcript_1700000000"


def test_scan_skips_corrupted_meta(tmp_path: Path) -> None:
    (tmp_path / "broken.meta.json").write_text("not json {", encoding="utf-8")
    items = scan_meetings(tmp_path)
    assert items == []


def test_scan_speakers_count(tmp_path: Path) -> None:
    _write_meta(tmp_path, "x", title="T", created_at="2026-05-26T10:00:00Z",
                speakers_count=3)
    items = scan_meetings(tmp_path)
    assert items[0].speakers_count == 3


# ---------- format_relative_date ----------

def test_format_today() -> None:
    now = time.time()
    assert format_relative_date(now - 60, now=now) == "сегодня"


def test_format_yesterday() -> None:
    now = time.time()
    assert format_relative_date(now - 86400 - 10, now=now) == "вчера"


def test_format_days_ago() -> None:
    now = time.time()
    assert format_relative_date(now - 3 * 86400, now=now) == "3д назад"


def test_format_older_date() -> None:
    # Старше 7 дней — формат DD MMM (русские месяцы).
    now = time.time()
    s = format_relative_date(now - 30 * 86400, now=now)
    # Месяц зависит от текущей даты — проверим что строка не пустая и не «сегодня».
    assert s and s != "сегодня" and s != "вчера" and "назад" not in s


def test_format_invalid() -> None:
    assert format_relative_date(0) == ""
    assert format_relative_date(-1, now=time.time()) == ""


# ---------- delete_meeting ----------

def test_delete_meeting_removes_all_files(tmp_path: Path) -> None:
    _write_meta(tmp_path, "ABC", title="X", created_at="2026-05-26T10:00:00Z")
    (tmp_path / "ABC.txt").write_text("x")
    (tmp_path / "ABC.srt").write_text("x")
    (tmp_path / "ABC.md").write_text("x")

    n = delete_meeting(tmp_path / "ABC")
    # txt + srt + md + json + meta.json = 5 (vtt не создавался).
    assert n == 5
    # Совсем ничего не осталось.
    assert not any(tmp_path.iterdir())


def test_delete_meeting_missing_files(tmp_path: Path) -> None:
    # Stem не существует — должен вернуть 0, не упасть.
    n = delete_meeting(tmp_path / "nothing")
    assert n == 0

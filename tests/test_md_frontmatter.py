"""Тесты для F23 — YAML frontmatter в MD-экспорте."""

from __future__ import annotations

import yaml

from src.alignment import AlignedSegment
from src.exporters import _format_duration_human, to_md


def _seg(start: float, end: float, text: str, speaker: str = "Никита") -> AlignedSegment:
    return AlignedSegment(start=start, end=end, text=text, speaker=speaker)


def test_no_frontmatter_when_not_passed() -> None:
    out = to_md([_seg(0, 1, "hi")], title="X")
    assert not out.startswith("---")
    assert out.startswith("# X")


def test_frontmatter_basic_fields() -> None:
    out = to_md(
        [_seg(0, 1, "hi", "Никита"), _seg(1, 2, "world", "Артём")],
        frontmatter={
            "title": "Sync команды",
            "date": "2026-05-26",
            "duration_sec": 47 * 60 + 3,
            "model": "medium",
            "language": "ru",
        },
    )
    # Должен начинаться с frontmatter блока.
    assert out.startswith("---\n")
    # Frontmatter блок до второго ---
    fm_block = out.split("---")[1]
    fm = yaml.safe_load(fm_block)
    assert fm["title"] == "Sync команды"
    # YAML.safe_load парсит ISO-даты в datetime.date. Сравниваем как строку.
    assert str(fm["date"]) == "2026-05-26"
    assert fm["model"] == "medium"
    assert fm["language"] == "ru"
    assert fm["duration"] == "47m 03s"
    # Speakers подтянуты из сегментов в порядке появления.
    assert fm["speakers"] == ["Никита", "Артём"]


def test_frontmatter_speakers_unique_and_ordered() -> None:
    # Тот же спикер несколько раз — выводим один раз. Порядок появления сохраняется.
    out = to_md(
        [_seg(0, 1, "a", "B"), _seg(1, 2, "b", "A"), _seg(2, 3, "c", "B")],
        frontmatter={"title": "X"},
    )
    fm = yaml.safe_load(out.split("---")[1])
    assert fm["speakers"] == ["B", "A"]


def test_frontmatter_omits_empty_fields() -> None:
    out = to_md(
        [_seg(0, 1, "hi")],
        frontmatter={"title": "X"},  # только title
    )
    fm = yaml.safe_load(out.split("---")[1])
    assert "title" in fm
    # Других полей не должно быть.
    assert "model" not in fm
    assert "language" not in fm
    assert "date" not in fm


def test_yaml_safe_load_handles_special_chars() -> None:
    # Двоеточие в title — должно быть в кавычках, чтобы YAML не сломался.
    out = to_md(
        [_seg(0, 1, "hi")],
        frontmatter={"title": "Q3: planning meeting"},
    )
    fm = yaml.safe_load(out.split("---")[1])
    assert fm["title"] == "Q3: planning meeting"


# ---------- _format_duration_human ----------

def test_format_duration_short() -> None:
    assert _format_duration_human(0) == "0s"
    assert _format_duration_human(45) == "45s"


def test_format_duration_minutes() -> None:
    assert _format_duration_human(60) == "1m 00s"
    assert _format_duration_human(47 * 60 + 3) == "47m 03s"


def test_format_duration_hours() -> None:
    assert _format_duration_human(3600) == "1h 00m 00s"
    assert _format_duration_human(3600 + 23 * 60 + 45) == "1h 23m 45s"


def test_format_duration_none() -> None:
    assert _format_duration_human(None) == "0s"
    assert _format_duration_human(-1) == "0s"

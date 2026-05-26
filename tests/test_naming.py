"""Тесты для src/naming.py (F12 ТЗ v2)."""

from __future__ import annotations

from datetime import datetime

from src.naming import DEFAULT_SLUG, SLUG_MAX_LEN, build_stem, slugify


# ---------- slugify ----------

def test_slugify_empty() -> None:
    assert slugify("") == DEFAULT_SLUG
    assert slugify(None) == DEFAULT_SLUG  # type: ignore[arg-type]


def test_slugify_ascii_simple() -> None:
    assert slugify("sync") == "sync"
    assert slugify("Q3 planning") == "q3-planning"


def test_slugify_keeps_cyrillic() -> None:
    # Стратегия: НЕ транслитерируем. Кириллица в имени FS — норм.
    assert slugify("Sync команды") == "sync-команды"
    assert slugify("Планирование Q3") == "планирование-q3"


def test_slugify_drops_punctuation() -> None:
    assert slugify("Hello, world!") == "hello-world"
    assert slugify("команды / Q3 / 2026") == "команды-q3-2026"


def test_slugify_collapses_multiple_dashes() -> None:
    assert slugify("a---b") == "a-b"
    assert slugify("hello!!!world") == "hello-world"


def test_slugify_strips_edge_dashes() -> None:
    assert slugify("---hello---") == "hello"
    assert slugify("-edge-") == "edge"


def test_slugify_all_garbage_becomes_default() -> None:
    assert slugify("!!!") == DEFAULT_SLUG
    assert slugify("   ") == DEFAULT_SLUG
    assert slugify("---") == DEFAULT_SLUG


def test_slugify_truncates_long_input() -> None:
    long_title = "слово-" * 30  # ~180 символов
    out = slugify(long_title)
    assert len(out) <= SLUG_MAX_LEN
    # Не должно заканчиваться дефисом.
    assert not out.endswith("-")


def test_slugify_keeps_numbers_and_underscore() -> None:
    assert slugify("Q3_planning_2026") == "q3_planning_2026"
    assert slugify("test 42") == "test-42"


def test_slugify_handles_emoji() -> None:
    # Эмодзи — не word char, превращается в дефис, потом схлопывается.
    assert slugify("🚀 launch") == "launch"


# ---------- build_stem ----------

def test_build_stem_format() -> None:
    stem = build_stem(
        "Sync команды", "abcdef123456789",
        date=datetime(2026, 5, 26),
    )
    assert stem == "2026-05-26_sync-команды_abcdef"


def test_build_stem_empty_title_uses_untitled() -> None:
    stem = build_stem("", "1234567890", date=datetime(2026, 5, 26))
    assert stem == "2026-05-26_untitled_123456"


def test_build_stem_none_title() -> None:
    stem = build_stem(None, "abc123def", date=datetime(2026, 1, 1))
    assert stem == "2026-01-01_untitled_abc123"


def test_build_stem_short_fingerprint() -> None:
    # Если fingerprint короче 6 символов — берём всё что есть.
    stem = build_stem("X", "ab", date=datetime(2026, 5, 26))
    assert stem == "2026-05-26_x_ab"


def test_build_stem_empty_fingerprint_falls_back_to_timestamp() -> None:
    # На совсем безфингерпринтном кейсе берём кусок timestamp — уникальность важнее.
    stem = build_stem("X", "", date=datetime(2026, 5, 26, 12, 0, 0))
    parts = stem.split("_")
    assert parts[0] == "2026-05-26"
    assert parts[1] == "x"
    # Третья часть — какие-то цифры (timestamp).
    assert parts[2].isdigit()

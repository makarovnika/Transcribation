"""Тесты для src/speakers.py — расчёт статистики и применение маппинга.

Используем синтетические AlignedSegment — никакой pyannote или модели.
"""

from __future__ import annotations

from src.alignment import AlignedSegment
from src.speakers import (
    SpeakerStats,
    apply_mapping,
    compute_stats,
    normalize_mapping,
    resolve_display_name,
)


def _s(start: float, end: float, text: str, speaker: str) -> AlignedSegment:
    return AlignedSegment(start=start, end=end, text=text, speaker=speaker)


# --------- compute_stats ---------

def test_compute_stats_empty() -> None:
    assert compute_stats([]) == []


def test_compute_stats_single_speaker() -> None:
    segs = [_s(0, 2, "a", "SPEAKER_00"), _s(3, 5, "b", "SPEAKER_00")]
    out = compute_stats(segs)
    assert out == [SpeakerStats(label="SPEAKER_00", speech_seconds=4.0, turns=2)]


def test_compute_stats_two_speakers_sorted_by_speech() -> None:
    # SPEAKER_01 говорит дольше (4 сек), SPEAKER_00 — короче (1 сек).
    # Сортировка по убыванию speech_seconds → SPEAKER_01 первый.
    segs = [
        _s(0, 1, "a", "SPEAKER_00"),
        _s(1, 3, "b", "SPEAKER_01"),
        _s(3, 5, "c", "SPEAKER_01"),
    ]
    out = compute_stats(segs)
    assert [s.label for s in out] == ["SPEAKER_01", "SPEAKER_00"]
    assert out[0].speech_seconds == 4.0
    assert out[0].turns == 2
    assert out[1].speech_seconds == 1.0
    assert out[1].turns == 1


def test_compute_stats_negative_duration_treated_as_zero() -> None:
    # Битый сегмент с end < start — длительность 0, count всё ещё считается.
    segs = [_s(5, 3, "bad", "SPEAKER_00"), _s(0, 2, "ok", "SPEAKER_00")]
    out = compute_stats(segs)
    assert out[0].speech_seconds == 2.0  # 0 + 2
    assert out[0].turns == 2


def test_compute_stats_stable_sort_by_label_on_tie() -> None:
    # Одинаковая длительность → сортируем по label по возрастанию (стабильно).
    segs = [
        _s(0, 1, "a", "SPEAKER_01"),
        _s(1, 2, "b", "SPEAKER_00"),
    ]
    out = compute_stats(segs)
    # Оба по 1.0 секунде; tiebreaker — алфавит → SPEAKER_00 первый.
    assert [s.label for s in out] == ["SPEAKER_00", "SPEAKER_01"]


# --------- normalize_mapping ---------

def test_normalize_mapping_strips_and_drops_empty() -> None:
    raw = {
        "SPEAKER_00": "  Никита  ",
        "SPEAKER_01": "",
        "SPEAKER_02": "   ",
        "SPEAKER_03": "Артём",
    }
    out = normalize_mapping(raw)
    assert out == {"SPEAKER_00": "Никита", "SPEAKER_03": "Артём"}


def test_normalize_mapping_none() -> None:
    assert normalize_mapping(None) == {}
    assert normalize_mapping({}) == {}


# --------- apply_mapping ---------

def test_apply_mapping_replaces_labels() -> None:
    segs = [_s(0, 1, "a", "SPEAKER_00"), _s(1, 2, "b", "SPEAKER_01")]
    out = apply_mapping(segs, {"SPEAKER_00": "Никита", "SPEAKER_01": "Артём"})
    assert [s.speaker for s in out] == ["Никита", "Артём"]
    # Текст и таймкоды не должны поменяться.
    assert out[0].text == "a"
    assert out[0].start == 0
    assert out[1].end == 2


def test_apply_mapping_empty_keeps_original() -> None:
    segs = [_s(0, 1, "a", "SPEAKER_00")]
    out = apply_mapping(segs, {"SPEAKER_00": ""})
    assert out[0].speaker == "SPEAKER_00"


def test_apply_mapping_unmapped_kept() -> None:
    segs = [_s(0, 1, "a", "SPEAKER_00"), _s(1, 2, "b", "SPEAKER_01")]
    out = apply_mapping(segs, {"SPEAKER_00": "Никита"})
    assert out[0].speaker == "Никита"
    assert out[1].speaker == "SPEAKER_01"  # без маппинга — оригинал


def test_apply_mapping_duplicate_names_merge() -> None:
    # Фича из F11 §6: два разных лейбла → одно имя (один спикер,
    # которого pyannote разделил).
    segs = [
        _s(0, 1, "a", "SPEAKER_00"),
        _s(1, 2, "b", "SPEAKER_01"),
        _s(2, 3, "c", "SPEAKER_00"),
    ]
    out = apply_mapping(segs, {"SPEAKER_00": "Никита", "SPEAKER_01": "Никита"})
    assert all(s.speaker == "Никита" for s in out)


def test_apply_mapping_none_or_empty_passes_through() -> None:
    segs = [_s(0, 1, "a", "SPEAKER_00")]
    assert apply_mapping(segs, None) == [_s(0, 1, "a", "SPEAKER_00")]
    assert apply_mapping(segs, {}) == [_s(0, 1, "a", "SPEAKER_00")]


def test_apply_mapping_returns_new_list_not_alias() -> None:
    # frozen dataclass + replace() — не должно быть мутации входа.
    segs = [_s(0, 1, "a", "SPEAKER_00")]
    out = apply_mapping(segs, {"SPEAKER_00": "Никита"})
    assert segs[0].speaker == "SPEAKER_00"  # оригинал не тронут
    assert out[0].speaker == "Никита"


# --------- resolve_display_name ---------

def test_resolve_display_name_with_mapping() -> None:
    mapping = {"SPEAKER_00": "Никита"}
    assert resolve_display_name("SPEAKER_00", mapping) == "Никита"
    assert resolve_display_name("SPEAKER_01", mapping) == "SPEAKER_01"


def test_resolve_display_name_without_mapping() -> None:
    assert resolve_display_name("SPEAKER_00", None) == "SPEAKER_00"
    assert resolve_display_name("SPEAKER_00", {}) == "SPEAKER_00"

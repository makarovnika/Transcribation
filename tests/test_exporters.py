"""Тесты экспортёров — проверка форматов на эталонных кусках."""

from __future__ import annotations

import json

from src.alignment import AlignedSegment
from src.exporters import (
    _format_timestamp,
    to_json,
    to_md,
    to_srt,
    to_txt,
    to_vtt,
)


def _aseg(start: float, end: float, text: str, speaker: str = "SPEAKER_00") -> AlignedSegment:
    return AlignedSegment(start=start, end=end, text=text, speaker=speaker)


# ---------- timestamp ----------

def test_format_timestamp_srt() -> None:
    # SRT — запятая, три знака мс. 1.5 секунды → 00:00:01,500.
    assert _format_timestamp(1.5, decimal_sep=",") == "00:00:01,500"
    assert _format_timestamp(3661.123, decimal_sep=",") == "01:01:01,123"


def test_format_timestamp_vtt() -> None:
    # VTT — точка.
    assert _format_timestamp(1.5, decimal_sep=".") == "00:00:01.500"
    assert _format_timestamp(0, decimal_sep=".") == "00:00:00.000"


def test_format_timestamp_negative_clamped_to_zero() -> None:
    # Битый сегмент не должен ломать формат.
    assert _format_timestamp(-1.0, decimal_sep=",") == "00:00:00,000"


def test_format_timestamp_rounding() -> None:
    # 1.0009 ≈ 1001ms (стандартное округление).
    assert _format_timestamp(1.0009, decimal_sep=",") == "00:00:01,001"


# ---------- TXT ----------

def test_txt_groups_consecutive_speaker() -> None:
    segments = [
        _aseg(0, 1, "Привет"),
        _aseg(1, 2, "как дела"),
        _aseg(2, 3, "Хорошо", speaker="SPEAKER_01"),
    ]
    out = to_txt(segments)
    lines = out.strip().split("\n")
    assert lines == [
        "[SPEAKER_00]: Привет как дела",
        "[SPEAKER_01]: Хорошо",
    ]


def test_txt_empty() -> None:
    assert to_txt([]) == ""


# ---------- SRT ----------

def test_srt_format_basic() -> None:
    segments = [
        _aseg(0, 1.5, "Hello"),
        _aseg(1.5, 3.0, "World", speaker="SPEAKER_01"),
    ]
    out = to_srt(segments)
    # Должны быть два блока с правильной нумерацией и запятой в ms.
    assert out.startswith("1\n00:00:00,000 --> 00:00:01,500\n[SPEAKER_00]: Hello")
    assert "2\n00:00:01,500 --> 00:00:03,000\n[SPEAKER_01]: World" in out


def test_srt_empty_segment_text_uses_placeholder() -> None:
    out = to_srt([_aseg(0, 1, "")])
    # Пустой текст → плейсхолдер, не падение
    assert "[…]" in out


# ---------- VTT ----------

def test_vtt_has_webvtt_header() -> None:
    out = to_vtt([_aseg(0, 1, "hi")])
    assert out.startswith("WEBVTT\n\n")


def test_vtt_uses_dot_in_timestamp() -> None:
    out = to_vtt([_aseg(0, 1.5, "x")])
    assert "00:00:00.000 --> 00:00:01.500" in out
    assert "," not in out.split("WEBVTT")[1].split("-->")[0]  # никаких запятых в ts


# ---------- JSON ----------

def test_json_round_trip() -> None:
    segments = [_aseg(0, 1.5, "Привет"), _aseg(1.5, 3.0, "мир", speaker="SPEAKER_01")]
    out = to_json(segments, meta={"language": "ru", "model": "tiny"})
    obj = json.loads(out)
    assert obj["schema_version"] == 1
    assert obj["meta"]["language"] == "ru"
    assert len(obj["segments"]) == 2
    assert obj["segments"][0] == {
        "start": 0.0, "end": 1.5, "speaker": "SPEAKER_00", "text": "Привет",
    }


def test_json_ensure_ascii_false() -> None:
    # Кириллица должна быть читаемой, а не \uXXXX.
    out = to_json([_aseg(0, 1, "Привет")])
    assert "Привет" in out
    assert "\\u" not in out


# ---------- MD ----------

def test_md_headers_per_speaker() -> None:
    segments = [
        _aseg(0, 1, "Привет"),
        _aseg(1, 2, "как дела"),
        _aseg(2, 3, "Хорошо", speaker="SPEAKER_01"),
    ]
    out = to_md(segments, title="Test")
    assert out.startswith("# Test")
    assert "## SPEAKER_00" in out
    assert "## SPEAKER_01" in out
    # Между двумя смежными SPEAKER_00 не должно быть второго заголовка
    assert out.count("## SPEAKER_00") == 1
    assert out.count("## SPEAKER_01") == 1


def test_md_timestamps_in_hms() -> None:
    out = to_md([_aseg(65, 70, "after a minute")])
    assert "_(00:01:05)_" in out

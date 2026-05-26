"""Тесты для F19 — Ollama summarization."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.alignment import AlignedSegment
from src.summarize import (
    SummarizeError,
    SummaryResult,
    _extract_json,
    parse_summary,
    render_transcript,
    summarize_transcript,
)


def _seg(start: float, end: float, text: str, speaker: str = "SPEAKER_00") -> AlignedSegment:
    return AlignedSegment(start=start, end=end, text=text, speaker=speaker)


# ---------- render_transcript ----------

def test_render_groups_consecutive_speaker() -> None:
    segs = [
        _seg(0, 1, "Привет", "A"),
        _seg(1, 2, "Как дела", "A"),
        _seg(2, 3, "Норм", "B"),
    ]
    text = render_transcript(segs)
    assert text == "[A]: Привет Как дела\n[B]: Норм"


def test_render_uses_speakers_map() -> None:
    segs = [_seg(0, 1, "hi", "SPEAKER_00")]
    text = render_transcript(segs, {"SPEAKER_00": "Никита"})
    assert "[Никита]:" in text
    assert "SPEAKER_00" not in text


def test_render_truncates_long_input() -> None:
    # 100 сегментов по 200 символов — > 100k.
    segs = [_seg(i, i + 1, "x" * 200, "A") for i in range(100)]
    out = render_transcript(segs, max_chars=1000)
    assert len(out) <= 2000
    assert "обрезаны" in out


# ---------- _extract_json ----------

def test_extract_json_clean() -> None:
    assert _extract_json('{"tldr": "x"}') == {"tldr": "x"}


def test_extract_json_with_fence() -> None:
    raw = '```json\n{"tldr": "x"}\n```'
    assert _extract_json(raw) == {"tldr": "x"}


def test_extract_json_inside_garbage() -> None:
    raw = 'Вот резюме встречи:\n{"tldr": "ok", "decisions": []}\nКонец.'
    assert _extract_json(raw) == {"tldr": "ok", "decisions": []}


def test_extract_json_invalid_raises() -> None:
    with pytest.raises(SummarizeError):
        _extract_json("просто текст без JSON")
    with pytest.raises(SummarizeError):
        _extract_json("{unclosed")


# ---------- parse_summary ----------

def test_parse_summary_full() -> None:
    raw = json.dumps({
        "tldr": "Встреча про планирование Q3.",
        "decisions": ["Решили использовать Postgres", "Архитектура — микросервисы"],
        "action_items": ["Никита: написать ADR [пятница]", "Артём: поднять CI [понедельник]"],
    })
    r = parse_summary(raw)
    assert isinstance(r, SummaryResult)
    assert r.tldr.startswith("Встреча про")
    assert len(r.decisions) == 2
    assert len(r.action_items) == 2


def test_parse_summary_action_items_as_objects() -> None:
    # LLM иногда возвращает dict вместо строки.
    raw = json.dumps({
        "tldr": "X",
        "action_items": [
            {"who": "Никита", "what": "написать ADR", "due_date": "пятница"},
            "Артём: поднять CI",
        ],
    })
    r = parse_summary(raw)
    assert len(r.action_items) == 2
    assert "Никита" in r.action_items[0]


def test_parse_summary_missing_fields() -> None:
    # Только tldr — остальное должно быть пустым.
    r = parse_summary('{"tldr": "X"}')
    assert r.tldr == "X"
    assert r.decisions == ()
    assert r.action_items == ()


def test_parse_summary_to_dict_roundtrip() -> None:
    r = parse_summary('{"tldr": "X", "decisions": ["a"], "action_items": ["b"]}')
    d = r.to_dict()
    assert d == {"tldr": "X", "decisions": ["a"], "action_items": ["b"]}


# ---------- summarize_transcript (с моками) ----------

def test_summarize_connection_error() -> None:
    segs = [_seg(0, 1, "hi")]

    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    with patch("httpx.post", side_effect=raise_connect_error):
        with pytest.raises(SummarizeError) as exc:
            summarize_transcript(segs)
    assert "Ollama не отвечает" in str(exc.value)
    assert "ollama pull" in str(exc.value).lower()


def test_summarize_timeout() -> None:
    segs = [_seg(0, 1, "hi")]

    def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timeout")

    with patch("httpx.post", side_effect=raise_timeout):
        with pytest.raises(SummarizeError) as exc:
            summarize_transcript(segs, timeout=1.0)
    assert "не ответил" in str(exc.value).lower()


def test_summarize_non_200() -> None:
    segs = [_seg(0, 1, "hi")]
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "internal error"

    with patch("httpx.post", return_value=mock_resp):
        with pytest.raises(SummarizeError) as exc:
            summarize_transcript(segs)
    assert "HTTP 500" in str(exc.value)


def test_summarize_happy_path() -> None:
    segs = [_seg(0, 1, "Привет", "Никита"), _seg(1, 2, "Норм", "Артём")]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": json.dumps({
            "tldr": "Короткое приветствие.",
            "decisions": [],
            "action_items": [],
        }),
    }

    with patch("httpx.post", return_value=mock_resp):
        r = summarize_transcript(segs)
    assert r.tldr == "Короткое приветствие."


def test_summarize_empty_segments() -> None:
    with pytest.raises(SummarizeError):
        summarize_transcript([])

"""Тесты для src/alignment.py — синтетические интервалы, без моделей."""

from __future__ import annotations

from src.alignment import UNKNOWN_SPEAKER, align_speakers_with_segments
from src.diarization import SpeakerSegment
from src.transcription import Segment


def _w(start: float, end: float, text: str = "x") -> Segment:
    return Segment(start=start, end=end, text=text)


def _s(start: float, end: float, speaker: str) -> SpeakerSegment:
    return SpeakerSegment(start=start, end=end, speaker=speaker)


def test_empty_transcription_returns_empty() -> None:
    assert align_speakers_with_segments([], []) == []
    assert align_speakers_with_segments([], [_s(0, 1, "SPEAKER_00")]) == []
    assert align_speakers_with_segments(None, None) == []  # type: ignore[arg-type]


def test_no_speakers_marks_all_unknown() -> None:
    ws = [_w(0, 1), _w(1, 2)]
    out = align_speakers_with_segments(ws, [])
    assert len(out) == 2
    assert all(o.speaker == UNKNOWN_SPEAKER for o in out)
    # Текст и таймкоды не должны теряться
    assert (out[0].start, out[0].end, out[0].text) == (0, 1, "x")


def test_segment_fully_inside_one_speaker() -> None:
    # ws [2..3] лежит внутри sp [0..5]
    out = align_speakers_with_segments(
        [_w(2, 3, "hi")],
        [_s(0, 5, "SPEAKER_00")],
    )
    assert out[0].speaker == "SPEAKER_00"
    assert out[0].text == "hi"


def test_segment_overlaps_two_speakers_picks_max() -> None:
    # ws [4..7]: с SP_00 пересечение [4..5]=1s, с SP_01 — [5..7]=2s.
    out = align_speakers_with_segments(
        [_w(4, 7, "...")],
        [_s(0, 5, "SPEAKER_00"), _s(5, 10, "SPEAKER_01")],
    )
    assert out[0].speaker == "SPEAKER_01"


def test_segment_outside_all_speakers_unknown() -> None:
    # ws [10..11] лежит правее всех sp
    out = align_speakers_with_segments(
        [_w(10, 11)],
        [_s(0, 5, "SPEAKER_00"), _s(5, 8, "SPEAKER_01")],
    )
    assert out[0].speaker == UNKNOWN_SPEAKER


def test_touch_boundary_is_not_overlap() -> None:
    # ws.start == sp.end → длина пересечения 0, спикер UNKNOWN.
    out = align_speakers_with_segments(
        [_w(5, 6)],
        [_s(0, 5, "SPEAKER_00")],
    )
    assert out[0].speaker == UNKNOWN_SPEAKER


def test_multiple_ws_keeps_order() -> None:
    ws = [_w(0, 1, "a"), _w(1.5, 2.5, "b"), _w(3, 4, "c")]
    sp = [_s(0, 2, "SPEAKER_00"), _s(2.5, 4, "SPEAKER_01")]
    out = align_speakers_with_segments(ws, sp)

    assert [(o.text, o.speaker) for o in out] == [
        ("a", "SPEAKER_00"),
        ("b", "SPEAKER_00"),  # [1.5..2.5] пересекает SP0 на 0.5s, SP1 — 0s
        ("c", "SPEAKER_01"),
    ]


def test_unsorted_inputs_still_correct() -> None:
    # Принципиально подаём в обратном порядке — функция должна сама отсортировать.
    ws_reversed = [_w(3, 4, "c"), _w(0, 1, "a"), _w(1.5, 2.5, "b")]
    sp_reversed = [_s(2.5, 4, "SPEAKER_01"), _s(0, 2, "SPEAKER_00")]
    out = align_speakers_with_segments(ws_reversed, sp_reversed)
    # Сортируется внутри по start, так что порядок ответа должен идти по возрастанию start.
    assert [o.text for o in out] == ["a", "b", "c"]
    assert [o.speaker for o in out] == ["SPEAKER_00", "SPEAKER_00", "SPEAKER_01"]


def test_speaker_gap_in_middle() -> None:
    # Между sp[0] и sp[1] есть «дыра» [5..7]. ws попадает в дыру → UNKNOWN.
    out = align_speakers_with_segments(
        [_w(5.5, 6.5, "тишина")],
        [_s(0, 5, "SPEAKER_00"), _s(7, 10, "SPEAKER_01")],
    )
    assert out[0].speaker == UNKNOWN_SPEAKER


def test_equal_overlap_picks_first_in_order() -> None:
    # Равное пересечение по 1 секунде с двумя спикерами.
    # Тай-брейкер — порядок прохода (по start). Это документированное поведение.
    out = align_speakers_with_segments(
        [_w(4, 6)],
        [_s(0, 5, "SPEAKER_00"), _s(5, 10, "SPEAKER_01")],
    )
    # SP_00 встретился первым (по start), best_overlap инициализируется 0,
    # поэтому ov > best_overlap для SP_00 истинно, дальше SP_01 даёт равный ov,
    # `>` не строгое не сработает — побеждает первый.
    assert out[0].speaker == "SPEAKER_00"

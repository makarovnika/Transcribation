"""Сопоставление сегментов транскрипции со спикерами.

Задача:
- На входе два списка непересекающихся интервалов:
    * сегменты Whisper:   [(start, end, text), …]
    * сегменты pyannote:  [(start, end, speaker), …]
- Они не выровнены: VAD у whisper и speaker turns у pyannote режут аудио по разным правилам.
- На выходе для каждого whisper-сегмента нужен один спикер — тот, с кем у
  whisper-сегмента максимальное временное пересечение.

Алгоритм:
- Sweep-line по speaker-сегментам с двумя указателями за O(N + M) для отсортированных
  входов. Для надёжности всё равно сортируем по start — стоит копейки, защищает
  от багов выше по стеку.
- Если whisper-сегмент не пересекается ни с одним speaker-интервалом — ставим
  UNKNOWN_SPEAKER. Падать нельзя: pyannote может пропустить тихие участки,
  а у нас там может быть распознанный шёпот.

Граничные случаи (закреплены тестами):
- ws[start, end] полностью внутри одного sp-интервала → этот спикер.
- ws пересекается с двумя sp-интервалами → тот, с кем больше overlap.
- ws вне всех sp → UNKNOWN_SPEAKER.
- Пустой список спикеров → все UNKNOWN_SPEAKER.
- Пустой список сегментов → пустой результат.
- ws.end == sp.start (касание) → overlap = 0, к этому спикеру не относим.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diarization import SpeakerSegment
from .transcription import Segment

UNKNOWN_SPEAKER = "UNKNOWN"


@dataclass(frozen=True)
class AlignedSegment:
    """Сегмент транскрипции + назначенный спикер.

    speaker может быть UNKNOWN_SPEAKER, если pyannote не покрыл этот интервал
    или если диаризация не запускалась вовсе.
    """

    start: float
    end: float
    text: str
    speaker: str


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Длина пересечения двух полуоткрытых интервалов. ≥ 0."""
    left = max(a_start, b_start)
    right = min(a_end, b_end)
    return max(0.0, right - left)


def align_speakers_with_segments(
    transcription_segments: list[Segment] | None,
    speaker_segments: list[SpeakerSegment] | None,
) -> list[AlignedSegment]:
    """Для каждого whisper-сегмента найти speaker с max перекрытием.

    Args:
        transcription_segments: сегменты от transcription.transcribe().
        speaker_segments: сегменты от diarization.diarize(). Может быть None/пустым,
            тогда все сегменты получат UNKNOWN_SPEAKER (диаризация отключена).

    Returns:
        Список AlignedSegment в том же порядке, что и transcription_segments.
    """
    if not transcription_segments:
        return []

    # Защитная сортировка: контракт «уже отсортировано» легко нарушить выше по стеку.
    ws_sorted = sorted(transcription_segments, key=lambda s: s.start)

    if not speaker_segments:
        return [
            AlignedSegment(start=w.start, end=w.end, text=w.text, speaker=UNKNOWN_SPEAKER)
            for w in ws_sorted
        ]

    sp_sorted = sorted(speaker_segments, key=lambda s: s.start)

    out: list[AlignedSegment] = []

    # Указатель в sp_sorted: первая sp, у которой sp.end > ws.start.
    # Двигаем его монотонно по мере прохода по ws_sorted (sweep-line).
    sp_ptr = 0
    sp_n = len(sp_sorted)

    for w in ws_sorted:
        # Сдвинем sp_ptr вперёд, пока sp[sp_ptr].end <= w.start
        # (sp полностью левее ws — больше не пригодится).
        while sp_ptr < sp_n and sp_sorted[sp_ptr].end <= w.start:
            sp_ptr += 1

        # Если sp_ptr ушёл за конец — никакой sp не сможет пересечь w (мы движемся
        # только вправо). UNKNOWN.
        if sp_ptr >= sp_n:
            out.append(AlignedSegment(w.start, w.end, w.text, UNKNOWN_SPEAKER))
            continue

        # Идём вперёд по sp до тех пор, пока sp.start < w.end. Все такие sp могут
        # пересекать w — выбираем тот, у кого пересечение максимальное.
        best_speaker = UNKNOWN_SPEAKER
        best_overlap = 0.0
        i = sp_ptr
        while i < sp_n and sp_sorted[i].start < w.end:
            ov = _overlap(w.start, w.end, sp_sorted[i].start, sp_sorted[i].end)
            if ov > best_overlap:
                best_overlap = ov
                best_speaker = sp_sorted[i].speaker
            i += 1

        out.append(AlignedSegment(w.start, w.end, w.text, best_speaker))

    return out

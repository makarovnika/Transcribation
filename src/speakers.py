"""Работа со спикерами: статистика и переименование.

Эта функциональность отделена от alignment.py намеренно:
- alignment.py решает «кто говорит в момент N» (читает диаризацию).
- speakers.py решает «как ОТОБРАЗИТЬ спикера пользователю» (display name).

Поток:
  diarize → align → compute_stats → UI таблица → пользователь вводит имена
  → apply_mapping → re-export

Маппинг хранится в .meta.json (см. src/meta.py и §1.1 TZ-v2). Здесь только
in-memory логика.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .alignment import AlignedSegment


@dataclass(frozen=True)
class SpeakerStats:
    """Агрегированная статистика по одной метке спикера.

    speech_seconds — суммарная длительность речи (sum of end-start по сегментам).
    turns — число сегментов (реплик), отнесённых к этой метке.
    """

    label: str           # 'SPEAKER_00', 'SPEAKER_01', 'UNKNOWN', …
    speech_seconds: float
    turns: int


def compute_stats(segments: Iterable[AlignedSegment]) -> list[SpeakerStats]:
    """Сгруппировать сегменты по speaker и вернуть статистику.

    Сортируем по убыванию speech_seconds — самый «говорливый» сверху, удобно
    для UI-таблицы.

    Edge cases:
    - пустой input → пустой список.
    - сегмент с end < start (битый) → не валим, считаем длительность = 0.
    """
    by_label: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))
    for seg in segments:
        # max(0, ...) защищает от перевёрнутых таймкодов
        dur = max(0.0, float(seg.end) - float(seg.start))
        sec, cnt = by_label[seg.speaker]
        by_label[seg.speaker] = (sec + dur, cnt + 1)

    return sorted(
        (SpeakerStats(label=lbl, speech_seconds=sec, turns=cnt)
         for lbl, (sec, cnt) in by_label.items()),
        key=lambda s: (-s.speech_seconds, s.label),
    )


def normalize_mapping(raw: dict[str, str] | None) -> dict[str, str]:
    """Очистка пользовательского ввода с UI-таблицы.

    Пустые/whitespace-only строки убираем — это значит «оставить SPEAKER_XX».
    Trim — чтобы случайные пробелы по краям не создавали разные spelling.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for label, name in raw.items():
        if not name:
            continue
        cleaned = str(name).strip()
        if cleaned:
            out[str(label)] = cleaned
    return out


def apply_mapping(
    segments: Iterable[AlignedSegment],
    mapping: dict[str, str] | None,
) -> list[AlignedSegment]:
    """Заменить speaker согласно mapping. Возвращает новый список.

    Поведение:
    - mapping[label] → speaker заменяется на mapping[label].
    - mapping без записи для label → оставляем как есть (SPEAKER_XX).
    - Несколько label с одинаковым display_name → объединение (это фича,
      см. F11 §6: pyannote может разделить одного человека на двух из-за
      смены аудио-канала). Никакого «слияния» физически не делаем — просто
      разные сегменты получают одинаковое имя, alignment остаётся прежним.
    """
    cleaned = normalize_mapping(mapping)
    if not cleaned:
        # Быстрый путь: ничего не меняем, но всё равно делаем list — чтобы
        # вызывающий код мог дальше итерировать несколько раз.
        return list(segments)

    out: list[AlignedSegment] = []
    for seg in segments:
        new_name = cleaned.get(seg.speaker)
        if new_name is None:
            out.append(seg)
        else:
            # dataclass(frozen=True) → используем replace вместо мутации.
            out.append(replace(seg, speaker=new_name))
    return out


def resolve_display_name(
    label: str,
    mapping: dict[str, str] | None,
) -> str:
    """Что показать пользователю для конкретной метки.

    Используется когда мы НЕ переписываем сегменты, а просто рендерим (например,
    в превью). В JSON-экспорте оба значения сохраняются: speaker (display) и
    speaker_id (исходная метка).
    """
    if mapping:
        cleaned = normalize_mapping(mapping)
        if label in cleaned:
            return cleaned[label]
    return label

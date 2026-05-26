"""Экспорт результата в txt/srt/vtt/json/md.

Все экспортёры берут единый вход — list[AlignedSegment] — и пишут на диск
либо возвращают строку. Возвращаем строку из core-функций (легко тестировать),
а write_* — тонкая обёртка над open(...).write(string).

Форматы:
- TXT: `[SPEAKER]: text` построчно. Без таймкодов. Соседние реплики одного
  спикера объединяем в один абзац — для читаемости.
- SRT: блоки `N\\n HH:MM:SS,mmm --> HH:MM:SS,mmm\\n [SPEAKER]: text\\n\\n`.
  Запятая в миллисекундах — это часть стандарта SRT.
- VTT: header `WEBVTT\\n\\n`, дальше блоки `HH:MM:SS.mmm --> HH:MM:SS.mmm`.
  Точка в миллисекундах — это часть стандарта WebVTT.
- JSON: полный дамп всех полей. Стабильная схема, см. _segments_to_json_obj.
- MD: заголовки спикеров через `##`, таймкод в скобках.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .alignment import AlignedSegment


# ---------- Форматтеры таймкодов ----------

def _format_timestamp(seconds: float, *, decimal_sep: str = ",") -> str:
    """HH:MM:SS,mmm (SRT) или HH:MM:SS.mmm (VTT).

    Если отрицательное — клампим в 0 (битый сегмент не должен ломать формат).
    """
    if seconds < 0 or seconds != seconds:  # NaN check
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d}{decimal_sep}{ms:03d}"


# ---------- TXT ----------

def to_txt(segments: Iterable[AlignedSegment]) -> str:
    """Чистый текст, метки спикеров в квадратных скобках.

    Соседние сегменты одного спикера сшиваем пробелом — это читается лучше,
    чем «[SPEAKER]: » на каждой строке транскрипции.
    """
    lines: list[str] = []
    current_speaker: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if current_speaker is not None and buf:
            lines.append(f"[{current_speaker}]: {' '.join(buf).strip()}")

    for seg in segments:
        if seg.speaker != current_speaker:
            flush()
            current_speaker = seg.speaker
            buf = []
        text = seg.text.strip()
        if text:
            buf.append(text)

    flush()
    return "\n".join(lines) + ("\n" if lines else "")


# ---------- SRT ----------

def to_srt(segments: Iterable[AlignedSegment]) -> str:
    """Стандартный SRT. Нумерация с 1. Запятая в ms."""
    blocks: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = _format_timestamp(seg.start, decimal_sep=",")
        end = _format_timestamp(seg.end, decimal_sep=",")
        text = seg.text.strip() or "[…]"  # пустой сегмент возможен — не ломаем формат
        speaker_prefix = f"[{seg.speaker}]: " if seg.speaker else ""
        blocks.append(f"{i}\n{start} --> {end}\n{speaker_prefix}{text}\n")
    # SRT-блоки разделяются пустой строкой; в конце файла принято иметь \n\n.
    return "\n".join(blocks)


# ---------- VTT ----------

def to_vtt(segments: Iterable[AlignedSegment]) -> str:
    """WebVTT. Точка в ms. Header WEBVTT обязателен."""
    out: list[str] = ["WEBVTT", ""]  # пустая строка после header — требование RFC
    for seg in segments:
        start = _format_timestamp(seg.start, decimal_sep=".")
        end = _format_timestamp(seg.end, decimal_sep=".")
        text = seg.text.strip() or "[…]"
        speaker_prefix = f"[{seg.speaker}]: " if seg.speaker else ""
        out.append(f"{start} --> {end}")
        out.append(f"{speaker_prefix}{text}")
        out.append("")  # пустая строка между cue
    return "\n".join(out)


# ---------- JSON ----------

def _segments_to_json_obj(
    segments: Iterable[AlignedSegment],
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Стабильная схема. Меняется только с миграцией формата."""
    seg_list = [
        {
            "start": round(s.start, 3),
            "end": round(s.end, 3),
            "speaker": s.speaker,
            "text": s.text,
        }
        for s in segments
    ]
    return {
        "schema_version": 1,
        "meta": meta or {},
        "segments": seg_list,
    }


def to_json(
    segments: Iterable[AlignedSegment],
    *,
    meta: dict[str, Any] | None = None,
    indent: int = 2,
) -> str:
    """JSON-дамп. ensure_ascii=False — чтобы русский был читаем."""
    obj = _segments_to_json_obj(segments, meta=meta)
    return json.dumps(obj, ensure_ascii=False, indent=indent)


# ---------- MD ----------

def to_md(
    segments: Iterable[AlignedSegment],
    *,
    title: str | None = None,
) -> str:
    """Markdown с заголовками спикеров и таймкодами в скобках.

    Структура: каждая смена спикера — новый блок `## SPEAKER` с реплик`ой
    под ним. Таймкод реплики — в начале абзаца в `(HH:MM:SS)`.
    """
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
        lines.append("")

    current_speaker: str | None = None
    for seg in segments:
        if seg.speaker != current_speaker:
            current_speaker = seg.speaker
            lines.append("")  # пустая строка перед новым разделом
            lines.append(f"## {seg.speaker}")
            lines.append("")
        ts = _format_timestamp(seg.start, decimal_sep=".").split(".")[0]
        text = seg.text.strip()
        if text:
            lines.append(f"_({ts})_ {text}")
    return "\n".join(lines).strip() + "\n"


# ---------- Write helpers ----------

def write_txt(segments: Iterable[AlignedSegment], path: Path | str) -> Path:
    return _write(path, to_txt(segments))


def write_srt(segments: Iterable[AlignedSegment], path: Path | str) -> Path:
    return _write(path, to_srt(segments))


def write_vtt(segments: Iterable[AlignedSegment], path: Path | str) -> Path:
    return _write(path, to_vtt(segments))


def write_json(
    segments: Iterable[AlignedSegment],
    path: Path | str,
    *,
    meta: dict[str, Any] | None = None,
) -> Path:
    return _write(path, to_json(segments, meta=meta))


def write_md(
    segments: Iterable[AlignedSegment],
    path: Path | str,
    *,
    title: str | None = None,
) -> Path:
    return _write(path, to_md(segments, title=title))


def _write(path: Path | str, content: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p

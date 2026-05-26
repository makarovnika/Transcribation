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

def _resolve(speaker: str, speakers_map: dict[str, str] | None) -> str:
    """Подменить SPEAKER_XX на display_name из speakers_map. Пустые имена игнорим.

    Локальная копия того, что в src.speakers.resolve_display_name, чтобы не
    плодить circular imports (exporters не должен зависеть от speakers).
    """
    if speakers_map:
        name = speakers_map.get(speaker, "").strip() if isinstance(speakers_map.get(speaker), str) else ""
        if name:
            return name
    return speaker


def to_txt(
    segments: Iterable[AlignedSegment],
    *,
    speakers_map: dict[str, str] | None = None,
) -> str:
    """Чистый текст, метки спикеров в квадратных скобках.

    Соседние сегменты одного спикера (с учётом резолва имени) сшиваем пробелом —
    это читается лучше, чем «[SPEAKER]: » на каждой строке транскрипции.

    speakers_map — опциональный маппинг SPEAKER_XX → display_name (F11).
    """
    lines: list[str] = []
    current_display: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if current_display is not None and buf:
            lines.append(f"[{current_display}]: {' '.join(buf).strip()}")

    for seg in segments:
        display = _resolve(seg.speaker, speakers_map)
        if display != current_display:
            flush()
            current_display = display
            buf = []
        text = seg.text.strip()
        if text:
            buf.append(text)

    flush()
    return "\n".join(lines) + ("\n" if lines else "")


# ---------- SRT ----------

def to_srt(
    segments: Iterable[AlignedSegment],
    *,
    speakers_map: dict[str, str] | None = None,
) -> str:
    """Стандартный SRT. Нумерация с 1. Запятая в ms."""
    blocks: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = _format_timestamp(seg.start, decimal_sep=",")
        end = _format_timestamp(seg.end, decimal_sep=",")
        text = seg.text.strip() or "[…]"  # пустой сегмент возможен — не ломаем формат
        display = _resolve(seg.speaker, speakers_map)
        speaker_prefix = f"[{display}]: " if display else ""
        blocks.append(f"{i}\n{start} --> {end}\n{speaker_prefix}{text}\n")
    # SRT-блоки разделяются пустой строкой; в конце файла принято иметь \n\n.
    return "\n".join(blocks)


# ---------- VTT ----------

def to_vtt(
    segments: Iterable[AlignedSegment],
    *,
    speakers_map: dict[str, str] | None = None,
) -> str:
    """WebVTT. Точка в ms. Header WEBVTT обязателен."""
    out: list[str] = ["WEBVTT", ""]  # пустая строка после header — требование RFC
    for seg in segments:
        start = _format_timestamp(seg.start, decimal_sep=".")
        end = _format_timestamp(seg.end, decimal_sep=".")
        text = seg.text.strip() or "[…]"
        display = _resolve(seg.speaker, speakers_map)
        speaker_prefix = f"[{display}]: " if display else ""
        out.append(f"{start} --> {end}")
        out.append(f"{speaker_prefix}{text}")
        out.append("")  # пустая строка между cue
    return "\n".join(out)


# ---------- JSON ----------

def _segments_to_json_obj(
    segments: Iterable[AlignedSegment],
    *,
    meta: dict[str, Any] | None = None,
    speakers_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Стабильная схема. Меняется только с миграцией формата.

    speaker:    отображаемое имя (display_name если есть, иначе исходная метка).
    speaker_id: ИСХОДНАЯ метка SPEAKER_XX. Нужна для трассировки (F11 §4)
                и для re-export после изменения маппинга.
    """
    seg_list = []
    for s in segments:
        display = _resolve(s.speaker, speakers_map)
        seg_dict: dict[str, Any] = {
            "start": round(s.start, 3),
            "end": round(s.end, 3),
            "speaker": display,
            "speaker_id": s.speaker,  # F11: трассировка к pyannote-метке
            "text": s.text,
        }
        # F20: word-level timestamps. Если words заполнен — выводим в JSON.
        # AlignedSegment не имеет поля words (это уровень Segment), но через
        # getattr на всякий случай — на случай если в будущем AlignedSegment расширится.
        words = getattr(s, "words", None)
        if words:
            seg_dict["words"] = [
                {
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "text": w.text,
                    "probability": w.probability,
                }
                for w in words
            ]
        seg_list.append(seg_dict)
    return {
        "schema_version": 1,
        "meta": meta or {},
        "segments": seg_list,
    }


def to_json(
    segments: Iterable[AlignedSegment],
    *,
    meta: dict[str, Any] | None = None,
    speakers_map: dict[str, str] | None = None,
    indent: int = 2,
) -> str:
    """JSON-дамп. ensure_ascii=False — чтобы русский был читаем."""
    obj = _segments_to_json_obj(segments, meta=meta, speakers_map=speakers_map)
    return json.dumps(obj, ensure_ascii=False, indent=indent)


# ---------- MD ----------

def _format_duration_human(sec: float | None) -> str:
    """`47m 03s` / `1h 23m 45s`. Используется в YAML frontmatter."""
    if not sec or sec <= 0:
        return "0s"
    total = int(round(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def to_md(
    segments: Iterable[AlignedSegment],
    *,
    title: str | None = None,
    speakers_map: dict[str, str] | None = None,
    frontmatter: dict[str, Any] | None = None,
) -> str:
    """Markdown с заголовками спикеров и таймкодами в скобках.

    F23: если передан frontmatter — выводим YAML-frontmatter (---\\n...\\n---) в
    начале файла. Распознаётся Obsidian/Logseq/Notion-импортом. Формат полей:
      title, date, duration, speakers (list), model, language.
    Все опциональные; пустые значения пропускаются.

    Структура тела: каждая смена спикера — новый блок `## SPEAKER` с репликой
    под ним. Таймкод реплики — в начале абзаца в `(HH:MM:SS)`.
    """
    # Материализуем сегменты один раз — мы будем по ним ходить дважды
    # (один раз для frontmatter speakers, один — для тела).
    seg_list = list(segments)

    lines: list[str] = []

    # F23: YAML frontmatter.
    if frontmatter:
        lines.append("---")
        # Собираем уникальных спикеров в порядке появления — это «человечнее»
        # чем алфавитный.
        seen: set[str] = set()
        speaker_list: list[str] = []
        for seg in seg_list:
            d = _resolve(seg.speaker, speakers_map)
            if d not in seen and d:
                seen.add(d)
                speaker_list.append(d)

        # Поля frontmatter. Только те, что нам передал caller.
        items: list[tuple[str, Any]] = []
        if frontmatter.get("title"):
            items.append(("title", frontmatter["title"]))
        if frontmatter.get("date"):
            items.append(("date", frontmatter["date"]))
        if frontmatter.get("duration_sec") is not None:
            items.append(("duration", _format_duration_human(frontmatter["duration_sec"])))
        if speaker_list:
            items.append(("speakers", speaker_list))
        if frontmatter.get("model"):
            items.append(("model", frontmatter["model"]))
        if frontmatter.get("language"):
            items.append(("language", frontmatter["language"]))

        # Ручное YAML-форматирование, чтобы не зависеть от PyYAML здесь
        # (он есть в зависимостях, но генерация простого dict короче через f-strings).
        # Для строк со спецсимволами оборачиваем в двойные кавычки.
        for key, value in items:
            if isinstance(value, list):
                inline = ", ".join(_yaml_value(v) for v in value)
                lines.append(f"{key}: [{inline}]")
            else:
                lines.append(f"{key}: {_yaml_value(value)}")
        lines.append("---")
        lines.append("")

    if title:
        lines.append(f"# {title}")
        lines.append("")

    current_display: str | None = None
    for seg in seg_list:
        display = _resolve(seg.speaker, speakers_map)
        if display != current_display:
            current_display = display
            lines.append("")  # пустая строка перед новым разделом
            lines.append(f"## {display}")
            lines.append("")
        ts = _format_timestamp(seg.start, decimal_sep=".").split(".")[0]
        text = seg.text.strip()
        if text:
            lines.append(f"_({ts})_ {text}")
    return "\n".join(lines).strip() + "\n"


def _yaml_value(v: Any) -> str:
    """Безопасная YAML-сериализация скалярного значения.

    Если содержит спецсимволы (двоеточие, кавычки, начинается с # и т.п.) —
    оборачиваем в двойные кавычки с escape. Иначе выводим как есть.
    """
    s = str(v)
    needs_quote = (
        ":" in s or "#" in s or s.strip() != s
        or s.startswith(("-", "[", "{", "&", "*", "!", "|", ">"))
        or s.lower() in ("true", "false", "yes", "no", "null", "~", "")
    )
    if needs_quote:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


# ---------- Write helpers ----------

def write_txt(
    segments: Iterable[AlignedSegment],
    path: Path | str,
    *,
    speakers_map: dict[str, str] | None = None,
) -> Path:
    return _write(path, to_txt(segments, speakers_map=speakers_map))


def write_srt(
    segments: Iterable[AlignedSegment],
    path: Path | str,
    *,
    speakers_map: dict[str, str] | None = None,
) -> Path:
    return _write(path, to_srt(segments, speakers_map=speakers_map))


def write_vtt(
    segments: Iterable[AlignedSegment],
    path: Path | str,
    *,
    speakers_map: dict[str, str] | None = None,
) -> Path:
    return _write(path, to_vtt(segments, speakers_map=speakers_map))


def write_json(
    segments: Iterable[AlignedSegment],
    path: Path | str,
    *,
    meta: dict[str, Any] | None = None,
    speakers_map: dict[str, str] | None = None,
) -> Path:
    return _write(path, to_json(segments, meta=meta, speakers_map=speakers_map))


def write_md(
    segments: Iterable[AlignedSegment],
    path: Path | str,
    *,
    title: str | None = None,
    speakers_map: dict[str, str] | None = None,
    frontmatter: dict[str, Any] | None = None,
) -> Path:
    return _write(path, to_md(
        segments, title=title, speakers_map=speakers_map, frontmatter=frontmatter,
    ))


def _write(path: Path | str, content: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p

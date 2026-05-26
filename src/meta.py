"""Sidecar `.meta.json` — метаданные сессии транскрипции.

Каждой группе экспортов `<stem>.{txt,srt,vtt,json,md}` соответствует один
`<stem>.meta.json`. Это «источник правды» по человеческим атрибутам встречи:
название, имена спикеров (display_name), резюме, ссылки на файлы.

Зачем sidecar, а не отдельная БД:
- Один в один как ТЗ §1.1 v2.
- Простота: всё в одной папке `outputs/`, никаких миграций.
- Cross-tool: можно открыть JSON в любом редакторе и поправить руками.

Совместимость:
- schema_version=2 (текущая). При загрузке v1 (которой нет — это всегда был v2)
  возвращаем дефолты.
- Legacy `transcript_<unix>.*` без `.meta.json` → возвращаем «пустой» MeetingMeta
  при попытке загрузки.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2


@dataclass
class SpeakerMeta:
    """Один спикер в meta.json. display_name='' значит «нет имени, оставить SPEAKER_XX»."""

    display_name: str = ""
    speech_seconds: float = 0.0
    turns: int = 0


@dataclass
class MeetingMeta:
    """Метаданные одной транскрипции. Соответствует схеме v2 (§1.1 TZ-v2)."""

    schema_version: int = SCHEMA_VERSION
    id: str = ""                      # короткий стабильный идентификатор (= stem)
    title: str = ""                   # человеческое название (заполняет пользователь, F12)
    created_at: str = ""              # ISO-8601 UTC
    source_file: dict[str, Any] = field(default_factory=dict)  # {"name", "size_bytes"}
    duration_sec: float = 0.0
    model_size: str = ""
    detected_language: str | None = None
    diarized: bool = False
    speakers: dict[str, SpeakerMeta] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)  # F19 заполнит позже
    files: dict[str, str] = field(default_factory=dict)    # {"txt": "...", "srt": "..."}

    # ---------- (de)serialize ----------

    def to_dict(self) -> dict[str, Any]:
        """Готовый к json.dump словарь. speakers→ вложенный dict."""
        d = asdict(self)
        # Pydantic-like ручной фикс: speakers пишем как dict-of-dict.
        # asdict уже это делает, но через dataclass-asdict — здесь явно проконтролируем.
        d["speakers"] = {k: asdict(v) if isinstance(v, SpeakerMeta) else v
                         for k, v in self.speakers.items()}
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MeetingMeta:
        """Строит из словаря. Терпимо к неполным данным — заполняет дефолтами.

        Если в файле есть лишние ключи (forward-compat) — игнорируем.
        Если каких-то полей нет — берём дефолт из dataclass.
        """
        if not isinstance(data, dict):
            return cls()

        # Speakers: dict[label, {display_name, speech_seconds, turns}].
        raw_speakers = data.get("speakers", {}) or {}
        speakers: dict[str, SpeakerMeta] = {}
        if isinstance(raw_speakers, dict):
            for label, info in raw_speakers.items():
                if isinstance(info, dict):
                    speakers[str(label)] = SpeakerMeta(
                        display_name=str(info.get("display_name", "")),
                        speech_seconds=float(info.get("speech_seconds", 0.0)),
                        turns=int(info.get("turns", 0)),
                    )

        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            created_at=str(data.get("created_at", "")),
            source_file=dict(data.get("source_file") or {}),
            duration_sec=float(data.get("duration_sec", 0.0)),
            model_size=str(data.get("model_size", "")),
            detected_language=(data.get("detected_language") or None),
            diarized=bool(data.get("diarized", False)),
            speakers=speakers,
            summary=dict(data.get("summary") or {}),
            files=dict(data.get("files") or {}),
        )

    # ---------- helpers ----------

    def display_name_mapping(self) -> dict[str, str]:
        """Маппинг SPEAKER_XX → display_name только для НЕпустых имён.

        Используется speakers.apply_mapping() и resolvers в экспортах.
        """
        return {
            label: spk.display_name
            for label, spk in self.speakers.items()
            if spk.display_name
        }


# ---------- I/O ----------

def meta_path_for(stem_path: Path | str) -> Path:
    """По пути к stem (без расширения) возвращает путь к meta.json.

    Пример: outputs/transcript_xxx → outputs/transcript_xxx.meta.json
    """
    return Path(stem_path).with_suffix(".meta.json")


def load_meta(stem_path: Path | str) -> MeetingMeta | None:
    """Прочитать `.meta.json` рядом со stem. None если файла нет или битый.

    Возврат None (а не пустой MeetingMeta) — чтобы вызывающий мог отличить
    «legacy без meta» от «meta есть, но дефолтная».
    """
    p = meta_path_for(stem_path)
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return MeetingMeta.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None


def save_meta(stem_path: Path | str, meta: MeetingMeta) -> Path:
    """Записать meta.json атомарно. Возвращает путь записанного файла."""
    p = meta_path_for(stem_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(meta.to_dict(), f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    return p


def utc_now_iso() -> str:
    """ISO-8601 UTC с секундной точностью. Тот же формат, что в §1.1 примере."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

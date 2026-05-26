"""Тесты для src/meta.py — sidecar .meta.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.meta import (
    SCHEMA_VERSION,
    MeetingMeta,
    SpeakerMeta,
    load_meta,
    meta_path_for,
    save_meta,
    utc_now_iso,
)


# ---------- MeetingMeta ----------

def test_meeting_meta_defaults() -> None:
    m = MeetingMeta()
    assert m.schema_version == SCHEMA_VERSION
    assert m.id == ""
    assert m.title == ""
    assert m.duration_sec == 0.0
    assert m.diarized is False
    assert m.speakers == {}
    assert m.files == {}


def test_meeting_meta_roundtrip() -> None:
    m = MeetingMeta(
        id="2026-05-26_test",
        title="Sync",
        created_at="2026-05-26T10:00:00Z",
        duration_sec=120.5,
        model_size="small",
        detected_language="ru",
        diarized=True,
        speakers={
            "SPEAKER_00": SpeakerMeta(display_name="Никита", speech_seconds=80.0, turns=12),
            "SPEAKER_01": SpeakerMeta(display_name="", speech_seconds=40.5, turns=8),
        },
        files={"txt": "out.txt", "srt": "out.srt"},
    )
    d = m.to_dict()
    # JSON-сериализуемо.
    json.dumps(d, ensure_ascii=False)
    # speakers — вложенные dict, не SpeakerMeta объекты.
    assert isinstance(d["speakers"]["SPEAKER_00"], dict)
    assert d["speakers"]["SPEAKER_00"]["display_name"] == "Никита"

    # Восстановили обратно — содержимое совпадает.
    m2 = MeetingMeta.from_dict(d)
    assert m2.id == "2026-05-26_test"
    assert m2.title == "Sync"
    assert m2.diarized is True
    assert m2.speakers["SPEAKER_00"].display_name == "Никита"
    assert m2.speakers["SPEAKER_00"].speech_seconds == 80.0
    assert m2.speakers["SPEAKER_01"].display_name == ""


def test_from_dict_tolerates_partial_data() -> None:
    # Только schema_version — остальное должно прийти как defaults.
    m = MeetingMeta.from_dict({"schema_version": 2})
    assert m.id == ""
    assert m.speakers == {}


def test_from_dict_ignores_extra_keys() -> None:
    # Forward-compat: новые поля в будущей версии не должны валить нас.
    m = MeetingMeta.from_dict({
        "schema_version": 99,
        "title": "X",
        "future_field": {"foo": "bar"},
    })
    assert m.schema_version == 99
    assert m.title == "X"


def test_from_dict_garbage_input() -> None:
    assert MeetingMeta.from_dict({}).id == ""
    assert MeetingMeta.from_dict("not a dict").id == ""  # type: ignore[arg-type]


def test_display_name_mapping_filters_empty() -> None:
    m = MeetingMeta(speakers={
        "SPEAKER_00": SpeakerMeta(display_name="Никита"),
        "SPEAKER_01": SpeakerMeta(display_name=""),
        "SPEAKER_02": SpeakerMeta(display_name="   Артём  "),  # whitespace intentional
    })
    mapping = m.display_name_mapping()
    # SPEAKER_01 не попадает (пустое имя). SPEAKER_02 попадает в исходном виде,
    # обрезку делает normalize_mapping() в speakers.py.
    assert mapping == {"SPEAKER_00": "Никита", "SPEAKER_02": "   Артём  "}


# ---------- I/O ----------

def test_meta_path_for() -> None:
    p = Path("outputs/transcript_xxx")
    assert meta_path_for(p) == Path("outputs/transcript_xxx.meta.json")
    # Уже с расширением — заменяет.
    p2 = Path("outputs/transcript_xxx.txt")
    assert meta_path_for(p2) == Path("outputs/transcript_xxx.meta.json")


def test_load_meta_missing(tmp_path: Path) -> None:
    stem = tmp_path / "nope"
    assert load_meta(stem) is None


def test_load_meta_corrupted(tmp_path: Path) -> None:
    stem = tmp_path / "broken"
    (stem.parent / "broken.meta.json").write_text("not json {", encoding="utf-8")
    assert load_meta(stem) is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    stem = tmp_path / "transcript_smoke"
    m = MeetingMeta(
        id="smoke",
        title="Тест",
        speakers={"SPEAKER_00": SpeakerMeta(display_name="Никита", speech_seconds=10, turns=2)},
    )
    p = save_meta(stem, m)
    assert p.exists()
    # tmp файл должен быть прибран.
    assert not p.with_suffix(".json.tmp").exists()

    loaded = load_meta(stem)
    assert loaded is not None
    assert loaded.title == "Тест"
    assert loaded.speakers["SPEAKER_00"].display_name == "Никита"


def test_utc_now_iso_format() -> None:
    s = utc_now_iso()
    # Формат YYYY-MM-DDTHH:MM:SSZ (Z в конце, без микросекунд).
    assert s.endswith("Z")
    assert len(s) == 20  # 'YYYY-MM-DDTHH:MM:SSZ'
    assert "T" in s

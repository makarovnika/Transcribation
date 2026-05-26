"""Тесты для F28: версия pyannote в ключе diar-кэша."""

from __future__ import annotations

from pathlib import Path

from src.diarization import _diar_cache_path


def test_different_pipeline_versions_give_different_paths(tmp_path: Path) -> None:
    """F28: апгрейд pyannote → старый кэш не должен использоваться."""
    p1 = _diar_cache_path(tmp_path, "fp1", "n0_min0_max0", pipeline_version="abc12345")
    p2 = _diar_cache_path(tmp_path, "fp1", "n0_min0_max0", pipeline_version="def67890")
    assert p1 != p2
    assert "abc12345" in p1.name
    assert "def67890" in p2.name


def test_same_version_same_path(tmp_path: Path) -> None:
    p1 = _diar_cache_path(tmp_path, "fp1", "n2_min0_max0", pipeline_version="v1")
    p2 = _diar_cache_path(tmp_path, "fp1", "n2_min0_max0", pipeline_version="v1")
    assert p1 == p2


def test_default_pipeline_version_is_resolved(tmp_path: Path) -> None:
    # Без явного pipeline_version путь всё равно содержит какую-то версию (не пустую).
    p = _diar_cache_path(tmp_path, "fp1", "params")
    assert p.name.startswith("diar_fp1_")
    # Между fp1 и params должна быть версия (символы).
    parts = p.stem.split("_")
    # diar / fp1 / <ver> / params... — части 0..3+
    assert len(parts) >= 4
    assert parts[2]  # непустая версия

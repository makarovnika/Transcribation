"""Тесты для F26 — ротация outputs/cache."""

from __future__ import annotations

import os
import time
from pathlib import Path

from src.rotation import (
    _group_stem,
    cleanup_outputs,
    folder_stats,
    human_size,
    scan_outputs,
)


# ---------- helper: создать фейковую группу ----------

def _make_group(dir_path: Path, stem: str, *, age_days: float = 0.0) -> list[Path]:
    """Создаёт 5 файлов одной транскрипционной группы + .meta.json.

    age_days >0 — выставляем mtime в прошлом, чтобы тесты не зависели от того,
    как быстро записан файл.
    """
    files = []
    for ext in (".txt", ".srt", ".vtt", ".json", ".md", ".meta.json"):
        f = dir_path / f"{stem}{ext}"
        f.write_text("x", encoding="utf-8")
        if age_days > 0:
            past = time.time() - age_days * 86400
            os.utime(f, (past, past))
        files.append(f)
    return files


# ---------- _group_stem ----------

def test_group_stem_normal_ext() -> None:
    assert _group_stem(Path("dir/2026-05-26_sync_abc.txt")) == "2026-05-26_sync_abc"
    assert _group_stem(Path("dir/2026-05-26_sync_abc.json")) == "2026-05-26_sync_abc"


def test_group_stem_meta_json() -> None:
    # .meta.json — двойное расширение, должно сниматься целиком.
    assert _group_stem(Path("dir/2026-05-26_sync_abc.meta.json")) == "2026-05-26_sync_abc"


# ---------- scan_outputs ----------

def test_scan_empty(tmp_path: Path) -> None:
    assert scan_outputs(tmp_path) == []


def test_scan_groups_files(tmp_path: Path) -> None:
    _make_group(tmp_path, "A")
    _make_group(tmp_path, "B")
    groups = scan_outputs(tmp_path)
    assert len(groups) == 2
    assert {g.stem for g in groups} == {"A", "B"}
    # Каждая группа должна включать все 6 расширений.
    for g in groups:
        assert len(g.files) == 6


def test_scan_ignores_unrelated_files(tmp_path: Path) -> None:
    (tmp_path / "random.bin").write_bytes(b"x")
    _make_group(tmp_path, "A")
    groups = scan_outputs(tmp_path)
    assert len(groups) == 1


def test_scan_missing_dir(tmp_path: Path) -> None:
    assert scan_outputs(tmp_path / "nope") == []


# ---------- cleanup_outputs ----------

def test_cleanup_removes_old_groups(tmp_path: Path) -> None:
    _make_group(tmp_path, "OLD", age_days=100)
    _make_group(tmp_path, "RECENT", age_days=1)
    res = cleanup_outputs(tmp_path, retention_days=60, max_entries=100)
    assert res.scanned_groups == 2
    assert res.removed_groups == 1
    assert res.reason_old == 1
    assert res.reason_overflow == 0
    # OLD-файлы удалены, RECENT остались.
    remaining = list(tmp_path.glob("*.txt"))
    assert len(remaining) == 1
    assert remaining[0].name.startswith("RECENT")


def test_cleanup_respects_max_entries(tmp_path: Path) -> None:
    # 5 свежих групп, лимит 3 → 2 старейших удалены.
    for i in range(5):
        _make_group(tmp_path, f"G{i}", age_days=i)  # G0 свежее всех, G4 старее
    res = cleanup_outputs(tmp_path, retention_days=0, max_entries=3)
    assert res.removed_groups == 2
    assert res.reason_overflow == 2
    # Сохранились G0, G1, G2 (свежие).
    remaining_stems = {p.stem for p in tmp_path.glob("*.txt") if not p.name.endswith(".meta.json")}
    assert remaining_stems == {"G0", "G1", "G2"}


def test_cleanup_dry_run_does_not_delete(tmp_path: Path) -> None:
    _make_group(tmp_path, "OLD", age_days=100)
    res = cleanup_outputs(tmp_path, retention_days=60, dry_run=True)
    assert res.removed_groups == 1
    assert (tmp_path / "OLD.txt").exists()


def test_cleanup_retention_zero_disabled(tmp_path: Path) -> None:
    # retention_days=0 → ничего не должно удаляться по возрасту.
    _make_group(tmp_path, "VERY_OLD", age_days=10_000)
    res = cleanup_outputs(tmp_path, retention_days=0, max_entries=100)
    assert res.removed_groups == 0


def test_cleanup_empty_dir(tmp_path: Path) -> None:
    res = cleanup_outputs(tmp_path, retention_days=30)
    assert res.scanned_groups == 0
    assert res.removed_groups == 0


# ---------- folder_stats ----------

def test_folder_stats(tmp_path: Path) -> None:
    _make_group(tmp_path, "A")
    sz, cnt = folder_stats(tmp_path)
    assert cnt == 6
    assert sz == 6  # 6 файлов по 1 байту


def test_folder_stats_empty(tmp_path: Path) -> None:
    sz, cnt = folder_stats(tmp_path / "nope")
    assert (sz, cnt) == (0, 0)


# ---------- human_size ----------

def test_human_size_bytes() -> None:
    assert human_size(0) == "0 B"
    assert human_size(512) == "512 B"


def test_human_size_kb_mb_gb() -> None:
    assert human_size(2048) == "2.0 КБ"
    assert human_size(5 * 1024 * 1024) == "5.0 МБ"
    assert human_size(3 * 1024 * 1024 * 1024) == "3.0 ГБ"

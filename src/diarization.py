"""Диаризация (определение «кто когда говорит») через pyannote.audio.

Почему так:
- pyannote/speaker-diarization-3.1 — текущий SOTA пайплайн open-source.
  Внутри: VAD + segmentation + embedding + clustering. Нам это всё чёрный ящик.
- Требует HF-токен и однократное «accept terms» на странице модели.
- Поддерживает MPS на Apple Silicon. На 1 час аудио уходит ~5-6 минут.

Контракт наружу:
- На вход: путь к WAV 16 kHz mono (одинаковый с тем, что идёт в Whisper).
- На выход: список SpeakerSegment — непересекающихся интервалов с меткой спикера.
- Метки: SPEAKER_00, SPEAKER_01, … (так их называет сам pyannote — мы не меняем,
  только в UI можно потом переименовать).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .device import get_pyannote_device

log = logging.getLogger("transcriber.diarization")

# Имя модели на HuggingFace Hub.
PIPELINE_NAME = "pyannote/speaker-diarization-3.1"

ProgressCallback = Callable[[str, float, str], None]


def _noop_progress(stage: str, fraction: float, note: str) -> None:
    return None


@dataclass(frozen=True)
class SpeakerSegment:
    """Один интервал с одним спикером. start/end в секундах."""

    start: float
    end: float
    speaker: str  # 'SPEAKER_00', 'SPEAKER_01', ...


class DiarizationError(Exception):
    """Базовая ошибка модуля. Ловим в UI."""


class HFAuthError(DiarizationError):
    """Нет токена или не приняты terms модели."""


def _load_pipeline(hf_token: str) -> object:
    """Загрузка pyannote pipeline. Импорт ленивый — см. transcription.py."""
    try:
        from pyannote.audio import Pipeline  # type: ignore[import-not-found]
    except ImportError as e:
        raise DiarizationError(
            "pyannote.audio не установлен. uv pip install -e ."
        ) from e

    if not hf_token:
        raise HFAuthError(
            "HuggingFace токен пуст. Получи токен на huggingface.co/settings/tokens "
            "и прими условия модели pyannote/speaker-diarization-3.1."
        )

    try:
        # В pyannote.audio 3.3+ параметр use_auth_token был переименован в token.
        # Сначала пробуем новый API, при TypeError откатываемся на старый —
        # так код переживёт оба варианта без жёсткой привязки к версии.
        try:
            pipeline = Pipeline.from_pretrained(  # type: ignore[attr-defined]
                PIPELINE_NAME, token=hf_token
            )
        except TypeError:
            pipeline = Pipeline.from_pretrained(  # type: ignore[attr-defined]
                PIPELINE_NAME, use_auth_token=hf_token
            )
    except Exception as e:
        # Самая частая ошибка — 401/403 от HF, когда не приняты условия модели.
        msg = str(e)
        if "401" in msg or "403" in msg or "gated" in msg.lower():
            raise HFAuthError(
                "HuggingFace отклонил доступ к pyannote/speaker-diarization-3.1. "
                "Проверь, что:\n"
                "  1) Токен валидный (huggingface.co/settings/tokens)\n"
                "  2) Ты принял условия модели на её странице "
                "(huggingface.co/pyannote/speaker-diarization-3.1)"
            ) from e
        raise DiarizationError(f"Не удалось загрузить pipeline: {e}") from e

    # Переносим на MPS если есть. На pyannote это поддерживается; на CPU тоже работает,
    # просто медленнее. Делаем ленивый импорт torch внутри — не нужен в тестах.
    import torch  # type: ignore[import-not-found]

    target_device_str = get_pyannote_device()  # 'mps' | 'cpu'
    pipeline.to(torch.device(target_device_str))  # type: ignore[attr-defined]
    return pipeline


def _audio_fingerprint(audio_path: Path) -> str:
    """Лёгкий отпечаток файла. Тот же приём, что в transcription._audio_fingerprint."""
    h = hashlib.sha1()
    size = audio_path.stat().st_size
    h.update(str(size).encode())
    with audio_path.open("rb") as f:
        h.update(f.read(1024 * 1024))
        if size > 2 * 1024 * 1024:
            f.seek(-1024 * 1024, 2)
            h.update(f.read(1024 * 1024))
    return h.hexdigest()[:16]


def _cache_key(num_speakers: int | None, min_s: int | None, max_s: int | None) -> str:
    """Параметрический суффикс для cache-файла — чтобы разные настройки не пересекались."""
    return f"n{num_speakers or 0}_min{min_s or 0}_max{max_s or 0}"


def _pyannote_version() -> str:
    """F28: версия pyannote-pipeline для инвалидации старого кэша после обновлений.

    Берём pyannote.audio.__version__ — если схема embeddings/кластеризации
    поменяется в новом релизе, старые .json несовместимы. Дополнительно мешаем
    PIPELINE_NAME — если в будущем переключимся на другой pipeline (например,
    diarization-3.2), старый кэш не подхватится.

    Возвращаемая строка короткая (sha1[:8]) — чтобы имя файла оставалось читаемым.
    """
    try:
        import pyannote.audio
        ver = str(pyannote.audio.__version__)
    except (ImportError, AttributeError):
        ver = "unknown"
    h = hashlib.sha1(f"{PIPELINE_NAME}@{ver}".encode()).hexdigest()[:8]
    return h


def _diar_cache_path(
    cache_dir: Path,
    fingerprint: str,
    params: str,
    *,
    pipeline_version: str | None = None,
) -> Path:
    """Путь к JSON-кэшу диаризации.

    F28: pipeline_version (хэш от PIPELINE_NAME@version) включён в имя файла,
    чтобы после апгрейда pyannote старый кэш не reused. По умолчанию
    вычисляется через _pyannote_version() — но в тестах можно передать явно.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    ver = pipeline_version if pipeline_version is not None else _pyannote_version()
    return cache_dir / f"diar_{fingerprint}_{ver}_{params}.json"


def _load_diar_cache(path: Path) -> list[SpeakerSegment] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return None
        return [SpeakerSegment(**item) for item in data]
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def _save_diar_cache(path: Path, segments: list[SpeakerSegment]) -> None:
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in segments], f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def diarize(
    audio_path: Path | str,
    *,
    hf_token: str,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    progress_callback: ProgressCallback | None = None,
    cache_dir: Path | str | None = None,
) -> list[SpeakerSegment]:
    """Запустить диаризацию.

    Args:
        audio_path: WAV 16 kHz mono.
        hf_token: токен HuggingFace.
        num_speakers: точное число спикеров, если известно. Иначе авто.
        min_speakers / max_speakers: альтернатива num_speakers — диапазон.
        progress_callback: для UI. Pyannote не отдаёт инкрементальный прогресс,
            поэтому колбэк вызывается только в ключевых точках.
        cache_dir: куда писать diar-кэш. None → не пишем. Кэш по fingerprint
            файла + параметрам диаризации. Это спасает от повтора 9-минутного
            pyannote при отладке последующих шагов pipeline.

    Returns:
        Список SpeakerSegment, отсортированный по start. Может быть пустым,
        если pyannote не нашёл ни одного speaker turn (странный/тихий файл).
    """
    p = Path(audio_path)
    if not p.exists():
        raise FileNotFoundError(f"Аудиофайл не найден: {p}")

    progress = progress_callback or _noop_progress

    # Проверка кэша. Делаем до загрузки тяжёлой модели — это бесплатно.
    cache_file: Path | None = None
    if cache_dir is not None:
        fp = _audio_fingerprint(p)
        params = _cache_key(num_speakers, min_speakers, max_speakers)
        cache_file = _diar_cache_path(Path(cache_dir), fp, params)
        cached = _load_diar_cache(cache_file)
        if cached is not None:
            log.info("diar cache hit: %d segments from %s", len(cached), cache_file.name)
            progress(
                "diarize_done",
                1.0,
                f"Из кэша: {_count_speakers(cached)} спикеров, {len(cached)} turns",
            )
            return cached

    progress("diarize_load", 0.0, "Загружаю pipeline диаризации…")
    pipeline = _load_pipeline(hf_token)

    progress("diarize_run", 0.05, "Идёт диаризация (это самый долгий шаг)…")

    # API pyannote: pipeline(file_or_dict, num_speakers=..., min_speakers=..., max_speakers=...).
    # Чистим None-аргументы, чтобы pyannote не получил num_speakers=None и не споткнулся.
    kwargs: dict[str, int] = {}
    if num_speakers is not None and num_speakers > 0:
        kwargs["num_speakers"] = num_speakers
    else:
        if min_speakers is not None:
            kwargs["min_speakers"] = min_speakers
        if max_speakers is not None:
            kwargs["max_speakers"] = max_speakers

    try:
        annotation = pipeline(str(p), **kwargs)  # type: ignore[operator]
    except Exception as e:
        raise DiarizationError(f"Pyannote упал при обработке: {e}") from e

    # В pyannote.audio 3.3+ Pipeline возвращает DiarizeOutput (dataclass),
    # внутри которого .speaker_diarization — это старый Annotation. В более
    # ранних версиях Pipeline сам возвращал Annotation. Берём оба варианта.
    ann = getattr(annotation, "speaker_diarization", annotation)

    # ann.itertracks(yield_label=True) → (Segment, track_id, speaker_label).
    # Нам важен только turn-сегмент и метка.
    out: list[SpeakerSegment] = []
    for turn, _track, speaker in ann.itertracks(yield_label=True):  # type: ignore[attr-defined]
        # turn — pyannote.core.Segment с .start / .end (секунды, float).
        out.append(
            SpeakerSegment(
                start=float(turn.start),
                end=float(turn.end),
                speaker=str(speaker),
            )
        )

    out.sort(key=lambda s: s.start)

    # Кэш сохраняем ПОСЛЕ парсинга — чтобы не сохранять полуготовый результат.
    if cache_file is not None:
        try:
            _save_diar_cache(cache_file, out)
            log.info("diar cache saved: %d segments → %s", len(out), cache_file.name)
        except OSError as e:
            log.warning("diar cache save failed: %s", e)

    progress("diarize_done", 1.0, f"Диаризация: найдено {_count_speakers(out)} спикеров")
    return out


def _count_speakers(segments: list[SpeakerSegment]) -> int:
    return len({s.speaker for s in segments})

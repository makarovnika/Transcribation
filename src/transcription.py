"""Транскрибация с чанкингом и промежуточным кэшем — кросс-платформенно.

Почему так:
- Whisper-бэкенд абстрагирован: на Apple Silicon — MLX (Metal, 5-8× быстрее),
  на Windows/Linux — faster-whisper (CUDA если есть, иначе CPU+int8).
  Выбор делает src/whisper_backends/__init__.py через device.get_whisper_backend().
- Длинный файл (3+ часа) на любом бэкенде блокирующий и не выдаёт прогресс.
  Поэтому мы режем wav на 10-минутные чанки сами и yield-им сегменты по мере
  готовности чанка.
- После каждого чанка сохраняем накопленные сегменты в cache/<hash>.partial.json.
  Если процесс упадёт на середине — следующий запуск того же файла поднимется
  с последнего успешного чанка (см. ТЗ §3.5).

Параметры качества (ТЗ §3.3) — общие для бэкендов:
- condition_on_previous_text=False — критично против галлюцинаций на длинных
  файлах (Whisper иначе застревает в повторяющихся фразах).
- VAD-фильтр — у faster-whisper включён через vad_filter=True; mlx-whisper
  делает свой VAD внутри.
- beam_size=5 (только faster-whisper, у mlx это управляется через DecodingOptions).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from . import whisper_backends

log = logging.getLogger("transcriber.transcription")

# Размер чанка в секундах. 600 сек = 10 мин — хороший баланс:
# - достаточно длинный, чтобы amortize накладные расходы на запуск transcribe;
# - достаточно короткий, чтобы прогресс-индикатор обновлялся осмысленно;
# - кратен 30 сек (внутреннее окно Whisper), границы попадают на тишину чаще.
DEFAULT_CHUNK_SECONDS = 600

# Целевая частота дискретизации (соответствует prepare_audio()).
TARGET_SAMPLE_RATE = 16_000

WhisperModelName = Literal["tiny", "base", "small", "medium", "large-v3"]
SUPPORTED_MODELS: tuple[WhisperModelName, ...] = (
    "tiny", "base", "small", "medium", "large-v3",
)
DEFAULT_MODEL: WhisperModelName = "large-v3"

# Маппинг размеров → имён моделей для каждого бэкенда лежит в самих модулях
# whisper_backends/mlx_backend.py и whisper_backends/fw_backend.py.
# faster-whisper понимает короткие имена ('tiny', 'small', ...) и сам тянет
# с HF — никакой подмены тут не нужно.


@dataclass(frozen=True)
class Segment:
    """Один сегмент транскрипции. start/end в секундах от начала файла."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptionMeta:
    """Метаданные транскрибации."""

    detected_language: str | None
    duration: float | None
    model_size: str
    chunks_total: int
    resumed_from_chunk: int  # 0 если запускали с нуля


ProgressCallback = Callable[[str, float, str], None]


def _noop_progress(stage: str, fraction: float, note: str) -> None:
    return None


# ---------------------------------------------------------------------------
# Кэш промежуточных результатов
# ---------------------------------------------------------------------------

def _audio_fingerprint(audio_path: Path) -> str:
    """Стабильный отпечаток файла для cache key.

    SHA1 от размера + первого и последнего мегабайта. Этого хватает чтобы
    отличить один файл от другого, и считается за миллисекунды даже для 3 ГБ wav.
    """
    h = hashlib.sha1()
    size = audio_path.stat().st_size
    h.update(str(size).encode())
    with audio_path.open("rb") as f:
        h.update(f.read(1024 * 1024))
        if size > 2 * 1024 * 1024:
            f.seek(-1024 * 1024, 2)
            h.update(f.read(1024 * 1024))
    return h.hexdigest()[:16]


def _cache_path(cache_dir: Path, fingerprint: str, model_size: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{fingerprint}_{model_size}.partial.json"


def _load_partial(path: Path) -> dict[str, Any] | None:
    """Прочитать частичный результат. Если файл битый — игнорируем."""
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        # Минимальная валидация схемы — если поля не те, лучше пересчитать.
        if not isinstance(data, dict) or "segments" not in data:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_partial(
    path: Path,
    *,
    fingerprint: str,
    model_size: str,
    language: str | None,
    chunks_done: int,
    chunks_total: int,
    chunk_seconds: int,
    segments: list[Segment],
    duration: float | None,
) -> None:
    """Атомарная запись прогресса. На случай SIGKILL в середине — данные не потеряются."""
    payload = {
        "schema": 1,
        "fingerprint": fingerprint,
        "model_size": model_size,
        "language": language,
        "chunks_done": chunks_done,
        "chunks_total": chunks_total,
        "chunk_seconds": chunk_seconds,
        "duration": duration,
        "segments": [asdict(s) for s in segments],
    }
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Основной API
# ---------------------------------------------------------------------------

def transcribe(
    audio_path: Path | str,
    *,
    model_size: WhisperModelName = DEFAULT_MODEL,
    language: str | None = None,
    progress_callback: ProgressCallback | None = None,
    audio_duration: float | None = None,
    chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
    cache_dir: Path | str | None = None,
    resume: bool = True,
) -> tuple[Iterator[Segment], TranscriptionMeta]:
    """Транскрибировать аудиофайл через mlx-whisper с чанкингом.

    Args:
        audio_path: путь к WAV 16 kHz mono (см. audio_utils.prepare_audio).
        model_size: один из SUPPORTED_MODELS.
        language: 'ru' / 'en' / None для авто-определения.
        progress_callback: вызывается на каждом чанке и сегменте.
        audio_duration: длительность файла в секундах. Если None — будет посчитана
            через soundfile (тоже быстро).
        chunk_seconds: размер чанка для прогресса и promejutkov.
        cache_dir: куда писать partial JSON. None → не пишем (бывает удобно в тестах).
        resume: если есть подходящий partial — продолжить с него.

    Returns:
        (iterator сегментов, метаданные). Итератор «ленивый»: сегменты приходят
        по мере обработки чанков, а не все сразу в конце.

    Raises:
        FileNotFoundError если файла нет.
        ValueError если model_size не из SUPPORTED_MODELS.
        RuntimeError если backend упал на загрузке модели или обработке чанка.
            Текст ошибки включает имя backend ('mlx' / 'faster-whisper') для отладки.
    """
    p = Path(audio_path)
    if not p.exists():
        raise FileNotFoundError(f"Аудиофайл не найден: {p}")
    if model_size not in SUPPORTED_MODELS:
        raise ValueError(
            f"Неизвестная модель: {model_size}. Допустимо: {SUPPORTED_MODELS}"
        )

    progress = progress_callback or _noop_progress
    progress("transcribe_init", 0.0, "Открываю файл…")

    # Ленивый импорт. soundfile нужен только тут, mlx-whisper — тоже.
    import numpy as np
    import soundfile as sf

    # Прочитать sample rate без полного чтения файла. На WAV это мгновенно.
    info = sf.info(str(p))
    if info.samplerate != TARGET_SAMPLE_RATE:
        raise ValueError(
            f"Ожидаю 16 kHz mono, получил {info.samplerate} Hz / {info.channels} ch. "
            f"Перед вызовом прогони audio_utils.prepare_audio()."
        )

    duration_sec = audio_duration if audio_duration else float(info.duration)
    total_samples = info.frames
    samples_per_chunk = chunk_seconds * TARGET_SAMPLE_RATE
    chunks_total = max(1, (total_samples + samples_per_chunk - 1) // samples_per_chunk)

    # Resume: если в кэше есть partial с тем же fingerprint и моделью — продолжим.
    fingerprint = _audio_fingerprint(p)
    cache_p: Path | None = None
    accumulated: list[Segment] = []
    start_chunk = 0
    detected_lang: str | None = None

    if cache_dir is not None:
        cache_p = _cache_path(Path(cache_dir), fingerprint, model_size)
        if resume:
            partial = _load_partial(cache_p)
            if partial and partial.get("chunk_seconds") == chunk_seconds:
                accumulated = [Segment(**s) for s in partial["segments"]]
                start_chunk = int(partial.get("chunks_done", 0))
                detected_lang = partial.get("language")
                if 0 < start_chunk < chunks_total:
                    log.info(
                        "resume from chunk %d/%d (cached %d segments)",
                        start_chunk, chunks_total, len(accumulated),
                    )
                    progress(
                        "transcribe_resume", start_chunk / chunks_total,
                        f"Продолжаю с чанка {start_chunk + 1}/{chunks_total}",
                    )
                elif start_chunk >= chunks_total:
                    # Уже всё сделано — отдаём как есть, MLX не дёргаем.
                    log.info("cache complete, no work to do")
                    meta = TranscriptionMeta(
                        detected_language=detected_lang,
                        duration=duration_sec,
                        model_size=model_size,
                        chunks_total=chunks_total,
                        resumed_from_chunk=start_chunk,
                    )
                    progress("transcribe_done", 1.0, "Готово (из кэша)")
                    return iter(accumulated), meta

    backend = whisper_backends.get_backend()
    backend_name = whisper_backends.get_backend_name()
    log.info(
        "backend=%s model=%s chunks=%d (start=%d) chunk_sec=%d dur=%.1fs",
        backend_name, model_size, chunks_total, start_chunk, chunk_seconds, duration_sec,
    )

    meta = TranscriptionMeta(
        detected_language=detected_lang,
        duration=duration_sec,
        model_size=model_size,
        chunks_total=chunks_total,
        resumed_from_chunk=start_chunk,
    )

    def _gen() -> Iterator[Segment]:
        # Сначала отдаём то, что уже было в кэше (resume).
        for s in accumulated:
            yield s

        # Загрузка модели — один раз перед циклом. Для faster-whisper это
        # критично: WhisperModel() весит 1-3 ГБ, повторять на каждый чанк было бы дорого.
        # Для MLX open_model() возвращает строку и почти бесплатен, но интерфейс общий.
        progress("backend_open", start_chunk / max(chunks_total, 1), f"Загружаю модель {model_size}…")
        try:
            model_handle = backend.open_model(model_size)
        except Exception as e:
            # Первая загрузка модели качает 0.5-3 ГБ с HuggingFace. Сеть упала,
            # диск кончился, HF down — всё через эту точку. Превращаем в RuntimeError
            # с подсказкой, иначе сырой huggingface_hub traceback пугает пользователя.
            raise RuntimeError(
                f"Не удалось загрузить модель {model_size} ({backend_name}): {e}. "
                "Проверь интернет, место на диске (~3 ГБ для large-v3) и доступность HuggingFace."
            ) from e

        nonlocal detected_lang
        segs_buf = list(accumulated)  # для дампа в cache

        for idx in range(start_chunk, chunks_total):
            offset_sec = idx * chunk_seconds
            start_sample = idx * samples_per_chunk
            end_sample = min(start_sample + samples_per_chunk, total_samples)

            # Читаем чанк отдельным запросом — не держим весь wav в RAM. На длинных
            # файлах (3+ ч) это экономит сотни МБ, пока память нужна pyannote.
            chunk_arr, _ = sf.read(
                str(p),
                start=start_sample,
                stop=end_sample,
                dtype="float32",
                always_2d=False,
            )
            if chunk_arr.ndim == 2:
                # Защита от стерео. prepare_audio() выдаёт mono, но мало ли.
                chunk_arr = chunk_arr.mean(axis=1)
            chunk_arr = np.ascontiguousarray(chunk_arr, dtype=np.float32)

            progress(
                "transcribe_chunk",
                idx / chunks_total,
                f"Чанк {idx + 1}/{chunks_total} ({backend_name})…",
            )

            try:
                seg_dicts, lang_from_chunk = backend.transcribe_chunk(
                    model_handle, chunk_arr, language,
                )
            except Exception as e:
                raise RuntimeError(
                    f"{backend_name} упал на чанке {idx + 1}/{chunks_total}: {e}"
                ) from e

            if detected_lang is None and lang_from_chunk:
                detected_lang = lang_from_chunk

            for seg in seg_dicts:
                ours = Segment(
                    start=float(seg["start"]) + offset_sec,
                    end=float(seg["end"]) + offset_sec,
                    text=str(seg.get("text", "")).strip(),
                )
                segs_buf.append(ours)
                yield ours

            # Промежуточный дамп в cache. Делаем после ЦЕЛОГО чанка, не после
            # каждого segment — иначе ввалит I/O на 1000 сегментах.
            if cache_p is not None:
                try:
                    _save_partial(
                        cache_p,
                        fingerprint=fingerprint,
                        model_size=model_size,
                        language=detected_lang,
                        chunks_done=idx + 1,
                        chunks_total=chunks_total,
                        chunk_seconds=chunk_seconds,
                        segments=segs_buf,
                        duration=duration_sec,
                    )
                except OSError as e:
                    log.warning("partial save failed (non-fatal): %s", e)

            progress(
                "transcribe_chunk_done",
                (idx + 1) / chunks_total,
                f"Готово {idx + 1}/{chunks_total} чанков",
            )

        progress("transcribe_done", 1.0, "Транскрибация завершена")

    return _gen(), meta

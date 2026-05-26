"""faster-whisper бэкенд для transcribe (cross-platform).

Контракт: open_model + transcribe_chunk — см. whisper_backends/__init__.py.

Особенности:
- На Windows с NVIDIA → CUDA + float16 (быстро).
- На Linux с NVIDIA → CUDA + float16.
- На macOS / без GPU → CPU + int8 (CTranslate2 не поддерживает Metal).
- Модель открывается один раз в open_model() и реюзается между чанками,
  поэтому overhead на чанк минимальный (в отличие от того, как мог бы выглядеть
  loop с пересозданием модели).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("transcriber.fw")


def _choose_device_and_compute() -> tuple[str, str]:
    """Возвращает (device, compute_type) для WhisperModel.

    Приоритет: CUDA → CPU+int8. Metal через MPS не поддерживается CTranslate2.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except ImportError:
        pass
    return "cpu", "int8"


def open_model(model_size: str) -> Any:
    """Загрузить WhisperModel в память. Возвращает объект для transcribe_chunk."""
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    device, compute_type = _choose_device_and_compute()
    log.info("faster-whisper: model=%s device=%s compute=%s", model_size, device, compute_type)

    # cpu_threads=0 → faster-whisper подберёт по числу ядер.
    # num_workers=1 — одного достаточно для одного файла.
    return WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        cpu_threads=0,
        num_workers=1,
    )


def transcribe_chunk(
    handle: Any,
    chunk_arr: Any,
    language: str | None,
    *,
    word_timestamps: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Транскрибировать чанк через faster-whisper.

    faster-whisper принимает np.ndarray float32 16 kHz mono напрямую,
    так что никакой конверсии не нужно.

    Args:
        word_timestamps: F20 — добавить word-level start/end в каждый сегмент.
    """
    segments_gen, info = handle.transcribe(
        chunk_arr,
        language=language,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        word_timestamps=word_timestamps,  # F20
    )

    # segments_gen — generator; материализуем сразу, в верхний слой нужен список.
    # Это безопасно: в чанке 10 мин редко >500 сегментов.
    segments: list[dict[str, Any]] = []
    for s in segments_gen:
        seg_dict: dict[str, Any] = {
            "start": float(s.start),
            "end": float(s.end),
            "text": (s.text or "").strip(),
        }
        if word_timestamps:
            # faster-whisper Segment имеет .words: list of Word(start, end, word, probability).
            ws = getattr(s, "words", None) or []
            seg_dict["words"] = [
                {
                    "start": float(w.start),
                    "end": float(w.end),
                    "text": (w.word or "").strip(),
                    "probability": float(getattr(w, "probability", 0.0)),
                }
                for w in ws
            ]
        segments.append(seg_dict)

    detected = getattr(info, "language", None)
    return segments, detected

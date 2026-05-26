"""MLX-бэкенд для transcribe (Apple Silicon, Metal).

Контракт совпадает с whisper_backends/fw_backend.py — см. модульный docstring
там для деталей про interface.

MLX «модель» в этом API — это строка path_or_hf_repo, потому что
mlx_whisper кэширует загруженные веса по этой строке внутри своего
process-local кэша. То есть «open_model» здесь не загружает веса, а только
решает какой репозиторий использовать.
"""

from __future__ import annotations

from typing import Any

# Маппинг названия размера на MLX-репозиторий с конвертированными весами.
# Все веса в mlx-community формате (npz + config.json).
_MLX_REPO_BY_MODEL: dict[str, str] = {
    "tiny":     "mlx-community/whisper-tiny-mlx",
    "base":     "mlx-community/whisper-base-mlx",
    "small":    "mlx-community/whisper-small-mlx",
    "medium":   "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


def open_model(model_size: str) -> str:
    """Возвращает идентификатор репозитория MLX-весов.

    Веса скачаются на первом transcribe_chunk() в HF-кэш (~/.cache/huggingface/).
    """
    if model_size not in _MLX_REPO_BY_MODEL:
        raise ValueError(f"Неизвестный размер модели для MLX: {model_size}")
    return _MLX_REPO_BY_MODEL[model_size]


def transcribe_chunk(
    handle: str,
    chunk_arr: Any,
    language: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Транскрибировать один чанк (np.ndarray float32 16 kHz mono).

    Returns:
        segments — список словарей с полями start/end/text (секунды от начала чанка),
        detected_language — код языка ('ru', 'en', ...) или None.
    """
    # Ленивый импорт — тяжёлый, не таскать в момент import модуля.
    import mlx_whisper  # type: ignore[import-not-found]

    result = mlx_whisper.transcribe(
        chunk_arr,
        path_or_hf_repo=handle,
        language=language,
        condition_on_previous_text=False,  # критично против галлюцинаций на длинных файлах
        verbose=None,  # не печатать в stdout
    )

    segments_raw = result.get("segments", []) or []
    segments = [
        {
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": str(seg.get("text", "")).strip(),
        }
        for seg in segments_raw
    ]
    detected = result.get("language")
    return segments, detected

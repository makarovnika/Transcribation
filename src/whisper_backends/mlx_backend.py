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
    "tiny":           "mlx-community/whisper-tiny-mlx",
    "base":           "mlx-community/whisper-base-mlx",
    "small":          "mlx-community/whisper-small-mlx",
    "medium":         "mlx-community/whisper-medium-mlx",
    "large-v3":       "mlx-community/whisper-large-v3-mlx",
    # F21: turbo — облегчённый decoder поверх large-v3 encoder. Качество
    # ~ large-v3, скорость в 3-5× быстрее. Веса ~1.5 ГБ против 3 ГБ у large-v3.
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
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
    *,
    word_timestamps: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Транскрибировать один чанк (np.ndarray float32 16 kHz mono).

    Args:
        word_timestamps: F20 — если True, в каждом сегменте появится массив
            words с start/end/text/probability для отдельных слов.
            Замедляет inference на 5-10%, плюс +RAM на word matrix.

    Returns:
        segments — список словарей с полями start/end/text (и words если включено),
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
        word_timestamps=word_timestamps,  # F20
    )

    segments_raw = result.get("segments", []) or []
    segments: list[dict[str, Any]] = []
    for seg in segments_raw:
        seg_dict: dict[str, Any] = {
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": str(seg.get("text", "")).strip(),
        }
        if word_timestamps:
            # mlx-whisper выдаёт words: list of {word, start, end, probability}.
            # Нормализуем под наш формат (text вместо word).
            words = seg.get("words") or []
            seg_dict["words"] = [
                {
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "text": str(w.get("word", w.get("text", ""))).strip(),
                    "probability": (float(w["probability"])
                                    if w.get("probability") is not None else None),
                }
                for w in words
            ]
        segments.append(seg_dict)
    detected = result.get("language")
    return segments, detected

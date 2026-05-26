"""Бэкенды транскрибации: MLX (macOS arm64) и faster-whisper (Windows/Linux/Mac fallback).

Интерфейс бэкенда — две функции:
  open_model(model_size: str) -> Any        # handle, специфичный для бэкенда
  transcribe_chunk(handle, chunk_arr, language)
    -> tuple[list[dict[start,end,text]], str|None]

`transcription.py` берёт активный бэкенд через get_backend() и не знает,
какой именно используется. Это позволяет нам:
  - на Apple Silicon крутить MLX (5–8× быстрее);
  - на Windows с CUDA — faster-whisper + float16 на GPU;
  - на Linux/CPU — faster-whisper + int8.

Принудительно переключить можно через env var TRANSCRIBER_BACKEND=mlx|faster-whisper.
"""

from __future__ import annotations

import os
from types import ModuleType

from .. import device

BACKEND_ENV = "TRANSCRIBER_BACKEND"


def get_backend_name() -> str:
    """Имя выбранного бэкенда. ENV-override > автоопределение."""
    forced = os.environ.get(BACKEND_ENV, "").strip().lower()
    if forced in {"mlx", "faster-whisper", "faster_whisper", "fw"}:
        # Нормализуем синонимы к каноничному имени.
        return "mlx" if forced == "mlx" else "faster-whisper"
    return device.get_whisper_backend()


def get_backend() -> ModuleType:
    """Импортирует и возвращает модуль активного бэкенда.

    Ленивый импорт: тяжёлые либы (mlx_whisper, faster_whisper) тянутся только
    тогда, когда реально нужно транскрибировать. Это держит pytest быстрым.
    """
    name = get_backend_name()
    if name == "mlx":
        from . import mlx_backend
        return mlx_backend
    # default — faster-whisper. Подходит для Windows/Linux и как fallback на Mac.
    from . import fw_backend
    return fw_backend

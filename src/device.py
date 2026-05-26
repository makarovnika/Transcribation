"""Автоопределение устройства и бэкенда для инференса.

Платформенная матрица:
- Apple Silicon (M1+):  whisper=mlx (Metal),    pyannote=mps
- Windows + NVIDIA:     whisper=faster-whisper, pyannote=cuda (если установлен torch с CUDA)
- Windows без NVIDIA:   whisper=faster-whisper, pyannote=cpu
- Linux + NVIDIA:       whisper=faster-whisper, pyannote=cuda
- CPU only (любая ОС):  whisper=faster-whisper (int8), pyannote=cpu

Переключить бэкенд принудительно: env TRANSCRIBER_BACKEND=mlx|faster-whisper.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceInfo:
    """Снимок состояния устройств для отображения в UI/логах."""

    pyannote_device: str   # 'mps' | 'cuda' | 'cpu'
    whisper_backend: str   # 'mlx' | 'faster-whisper'
    mps_available: bool
    cuda_available: bool
    note: str              # человекочитаемое пояснение


def _is_mps_available() -> bool:
    """True, если PyTorch видит Apple MPS-бэкенд."""
    try:
        import torch
    except ImportError:
        return False
    return bool(
        getattr(torch.backends, "mps", None)
        and torch.backends.mps.is_available()
        and torch.backends.mps.is_built()
    )


def _is_cuda_available() -> bool:
    """True, если PyTorch видит CUDA (NVIDIA GPU)."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _is_mlx_available() -> bool:
    """True если mlx-whisper установлен (только Apple Silicon)."""
    try:
        import mlx_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def get_pyannote_device() -> str:
    """Куда грузить pyannote: cuda > mps > cpu.

    pyannote.audio поддерживает все три. CUDA быстрее MPS, MPS быстрее CPU.
    """
    if _is_cuda_available():
        return "cuda"
    if _is_mps_available():
        return "mps"
    return "cpu"


def get_whisper_backend() -> str:
    """Какой бэкенд транскрибации.

    Приоритет:
      1. ENV TRANSCRIBER_BACKEND — для отладки и принудительного режима.
      2. MLX если доступен (только Apple Silicon).
      3. Иначе faster-whisper (cross-platform, при наличии CUDA — на GPU).
    """
    forced = os.environ.get("TRANSCRIBER_BACKEND", "").strip().lower()
    if forced == "mlx":
        return "mlx"
    if forced in {"faster-whisper", "faster_whisper", "fw"}:
        return "faster-whisper"

    if _is_mlx_available():
        return "mlx"
    return "faster-whisper"


def get_device_info() -> DeviceInfo:
    """Собирает полный снимок выбранных устройств. Удобно для логов и UI."""
    mps = _is_mps_available()
    cuda = _is_cuda_available()
    mlx_ok = _is_mlx_available()
    backend = get_whisper_backend()
    pyannote_dev = get_pyannote_device()

    if backend == "mlx" and mps:
        note = "MLX (Metal) для транскрибации, pyannote на MPS — оптимум для Apple Silicon."
    elif backend == "mlx" and not mps:
        note = "MLX установлен, но MPS не доступен — pyannote пойдёт на CPU."
    elif backend == "faster-whisper" and cuda:
        note = "faster-whisper на CUDA + pyannote на CUDA — оптимум для Windows/Linux с NVIDIA."
    elif backend == "faster-whisper" and mps:
        # Странный случай: Mac arm64 с принудительным TRANSCRIBER_BACKEND=faster-whisper.
        note = "faster-whisper в режиме CPU+int8 (CTranslate2 не поддерживает Metal). pyannote на MPS."
    elif backend == "faster-whisper":
        note = "faster-whisper на CPU+int8. Медленно. Поставь CUDA-сборку torch для ускорения."
    else:
        note = "Состояние неизвестно — посмотри логи."

    if not mlx_ok and not cuda and not mps:
        note += " Внимание: без MLX/CUDA/MPS обработка длинных файлов будет очень медленной."

    return DeviceInfo(
        pyannote_device=pyannote_dev,
        whisper_backend=backend,
        mps_available=mps,
        cuda_available=cuda,
        note=note,
    )

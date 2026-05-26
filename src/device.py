"""Автоопределение устройства для инференса.

Контекст по платформе:
- На Apple Silicon (M1+) есть Metal Performance Shaders.
- Транскрибация — mlx-whisper (Apple MLX, нативный Metal). Параметры precision
  и компиляции под Metal управляются внутри MLX.
- Диаризация — pyannote.audio. Поддерживает MPS через PyTorch.

Для не-arm64 macOS этот проект не предназначен (см. ТЗ §2 и pyproject.toml).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceInfo:
    """Снимок состояния устройств для отображения в UI/логах."""

    pyannote_device: str   # 'mps' | 'cpu'
    whisper_backend: str   # 'mlx'
    mps_available: bool
    note: str              # человекочитаемое пояснение


def _is_mps_available() -> bool:
    """True, если PyTorch видит MPS-бэкенд."""
    try:
        import torch
    except ImportError:
        return False
    return bool(
        getattr(torch.backends, "mps", None)
        and torch.backends.mps.is_available()
        and torch.backends.mps.is_built()
    )


def _is_mlx_available() -> bool:
    """True если mlx-whisper установлен (а значит и mlx, и Metal-бэкенд)."""
    try:
        import mlx_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def get_pyannote_device() -> str:
    """Куда грузить pyannote: 'mps' если доступен, иначе 'cpu'."""
    return "mps" if _is_mps_available() else "cpu"


def get_whisper_backend() -> str:
    """Какой бэкенд транскрибации использовать.

    Возвращает 'mlx' — единственный поддерживаемый сейчас (см. модульный docstring).
    Функция оставлена для будущей возможности добавить fallback (например,
    openai-whisper на CPU для не-arm64).
    """
    return "mlx"


def get_device_info() -> DeviceInfo:
    """Собирает полный снимок выбранных устройств. Удобно для логов и UI."""
    mps = _is_mps_available()
    mlx_ok = _is_mlx_available()
    pyannote_dev = get_pyannote_device()

    if mlx_ok and mps:
        note = "MLX (Metal) для транскрибации, pyannote на MPS — оптимум для Apple Silicon."
    elif mlx_ok and not mps:
        note = "MLX (Metal) есть, но MPS PyTorch не доступен — pyannote пойдёт на CPU."
    else:
        note = (
            "mlx-whisper не установлен. Установи: "
            "uv pip install -e '.[dev]'"
        )

    return DeviceInfo(
        pyannote_device=pyannote_dev,
        whisper_backend="mlx",
        mps_available=mps,
        note=note,
    )

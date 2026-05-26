"""Тесты для src/device.py.

Цель — зафиксировать инварианты, а не наличие железа: backend Whisper всегда 'mlx',
pyannote_device — один из {mps, cpu}, DeviceInfo согласован сам с собой.
"""

from src.device import (
    DeviceInfo,
    get_device_info,
    get_pyannote_device,
    get_whisper_backend,
)


def test_whisper_backend_is_mlx() -> None:
    # Инвариант: транскрибация через mlx-whisper (см. CLAUDE.md и pyproject).
    assert get_whisper_backend() == "mlx"


def test_pyannote_device_is_mps_or_cpu() -> None:
    assert get_pyannote_device() in {"mps", "cpu"}


def test_device_info_consistency() -> None:
    info = get_device_info()
    assert isinstance(info, DeviceInfo)
    assert info.whisper_backend == "mlx"
    assert info.pyannote_device == get_pyannote_device()
    if info.mps_available:
        assert info.pyannote_device == "mps"
    else:
        assert info.pyannote_device == "cpu"
    assert info.note  # непустое пояснение для UI

"""Тесты для src/device.py.

Цель — зафиксировать инварианты, а не наличие железа:
- whisper_backend всегда один из {mlx, faster-whisper};
- pyannote_device — один из {mps, cuda, cpu};
- DeviceInfo согласован сам с собой и совпадает с отдельными функциями.
"""

from src.device import (
    DeviceInfo,
    get_device_info,
    get_pyannote_device,
    get_whisper_backend,
)


def test_whisper_backend_is_supported() -> None:
    assert get_whisper_backend() in {"mlx", "faster-whisper"}


def test_pyannote_device_is_supported() -> None:
    assert get_pyannote_device() in {"mps", "cuda", "cpu"}


def test_device_info_consistency() -> None:
    info = get_device_info()
    assert isinstance(info, DeviceInfo)
    assert info.whisper_backend == get_whisper_backend()
    assert info.pyannote_device == get_pyannote_device()

    # Инварианты по флагам железа:
    if info.cuda_available:
        # CUDA приоритет над MPS, поэтому pyannote должен пойти на cuda.
        assert info.pyannote_device == "cuda"
    elif info.mps_available:
        # MPS если есть и CUDA нет.
        assert info.pyannote_device == "mps"
    else:
        assert info.pyannote_device == "cpu"

    assert info.note  # непустое пояснение для UI

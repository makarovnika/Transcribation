"""Подготовка аудио ко входу в Whisper и pyannote.

Зачем отдельный модуль:
- faster-whisper и pyannote ожидают WAV 16 kHz mono. На вход же приходит что угодно:
  mp4 с двумя дорожками, m4a, ogg/opus, webm и т.п.
- Чтобы оба движка работали с одним и тем же файлом (и pyannote было дёшево
  держать в памяти), один раз конвертируем в нормализованный WAV во временную
  папку и используем дальше его путь.

Используем ffmpeg-python только как тонкую обёртку — он формирует argv,
а реально работу делает системный ffmpeg (его и устанавливаем через brew).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Форматы из ТЗ §3.2. Расширения сравниваем в нижнем регистре.
SUPPORTED_AUDIO_EXTS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
)
SUPPORTED_VIDEO_EXTS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".webm"}
)
SUPPORTED_EXTS: frozenset[str] = SUPPORTED_AUDIO_EXTS | SUPPORTED_VIDEO_EXTS

# Параметры нормализации. 16 kHz mono — это «контракт» Whisper и pyannote.
# Менять без понимания не надо: pyannote.audio модели обучены на 16 kHz.
TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_FORMAT = "wav"


class AudioError(Exception):
    """Базовая ошибка модуля. Ловим в UI и показываем сообщение пользователю."""


class UnsupportedFormatError(AudioError):
    pass


class FFmpegMissingError(AudioError):
    pass


class ProbeError(AudioError):
    pass


@dataclass(frozen=True)
class PreparedAudio:
    """Результат подготовки. `path` — обязательно WAV 16 kHz mono."""

    path: Path
    duration_sec: float
    sample_rate: int
    channels: int
    # True, если файл был сконвертирован во временный (его удалим в finally).
    # False, если вход уже был валидным WAV 16 kHz mono — переиспользуем как есть.
    is_temporary: bool


def _check_ffmpeg() -> None:
    """Гарантия, что бинарь ffmpeg доступен. Иначе — понятная ошибка в UI."""
    if shutil.which("ffmpeg") is None:
        raise FFmpegMissingError(
            "ffmpeg не найден в PATH. Установи: brew install ffmpeg"
        )


def _ext(path: Path) -> str:
    return path.suffix.lower()


def is_supported(path: Path | str) -> bool:
    """Расширение в списке поддерживаемых? Сам файл не открываем."""
    return _ext(Path(path)) in SUPPORTED_EXTS


def probe_duration(path: Path | str) -> float:
    """Длительность в секундах через ffprobe.

    Используем ffprobe, а не считаем сами по битрейту — для VBR/контейнеров
    это единственный надёжный способ.
    """
    _check_ffmpeg()
    p = Path(path)
    if not p.exists():
        raise ProbeError(f"Файл не существует: {p}")

    # -v error: молчим про инфо/варнинги. -show_entries format=duration: только нужное.
    # -of default=...: выводим только значение, без ключа.
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(p),
    ]
    try:
        out = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=30
        )
    except subprocess.CalledProcessError as e:
        raise ProbeError(
            f"ffprobe упал на {p.name}: {e.stderr.strip() or e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"ffprobe не ответил за 30 сек на {p.name}") from e

    raw = out.stdout.strip()
    try:
        return float(raw)
    except ValueError as e:
        # У некоторых контейнеров ffprobe возвращает 'N/A' — длительность неизвестна.
        raise ProbeError(f"Не удалось определить длительность ({raw!r})") from e


def _probe_audio_stream(path: Path) -> tuple[int, int]:
    """Возвращает (sample_rate, channels) первой аудио-дорожки.

    Нужно, чтобы понять: можно ли использовать файл как есть, или надо ресемплить.
    Если аудио-дорожки нет — ProbeError (например, видео без звука).
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-of", "default=noprint_wrappers=1",
        str(path),
    ]
    try:
        out = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=30
        )
    except subprocess.CalledProcessError as e:
        raise ProbeError(
            f"ffprobe stream-probe упал: {e.stderr.strip() or e}"
        ) from e

    sr = ch = None
    for line in out.stdout.splitlines():
        if line.startswith("sample_rate="):
            sr = int(line.split("=", 1)[1])
        elif line.startswith("channels="):
            ch = int(line.split("=", 1)[1])

    if sr is None or ch is None:
        raise ProbeError("Файл не содержит аудио-дорожки или дорожка неисправна")
    return sr, ch


def prepare_audio(
    src: Path | str,
    workdir: Path | None = None,
    *,
    denoise: bool = False,
) -> PreparedAudio:
    """Привести аудио к WAV 16 kHz mono.

    Args:
        src: исходный файл (любой из SUPPORTED_EXTS).
        workdir: куда класть temp-wav (для тестов). По умолчанию системный TMP.
        denoise: F15 — применить ffmpeg-фильтры loudnorm + afftdn. По умолчанию
            False, чтобы не ломать существующее поведение. Включается через
            чекбокс UI на шумных записях.

    Логика:
    1) Проверяем расширение и наличие ffmpeg.
    2) Пробим длительность и параметры аудио-дорожки.
    3) Если уже WAV 16 kHz mono И denoise выключен — переиспользуем
       (is_temporary=False), чтобы не тратить I/O на копирование 2 ГБ-файла.
    4) Иначе — конвертируем в temp-wav. denoise добавляет в ffmpeg -af цепочку
       loudnorm (EBU R128) + afftdn (spectral denoise) — помогает Whisper'у
       на тихих/шумных записях ценой ~1.5× времени конвертации.

    Параметр workdir нужен для отладки и тестов: явно указать, куда класть temp.
    """
    src_path = Path(src).expanduser().resolve()
    if not src_path.exists():
        raise AudioError(f"Файл не найден: {src_path}")
    if not is_supported(src_path):
        raise UnsupportedFormatError(
            f"Формат не поддерживается: {src_path.suffix}. "
            f"Допустимо: {sorted(SUPPORTED_EXTS)}"
        )

    _check_ffmpeg()
    duration = probe_duration(src_path)
    sr, ch = _probe_audio_stream(src_path)

    # Если уже соответствует контракту И denoise не нужен — не трогаем (быстрая ветка).
    # F15: при denoise=True пропускаем эту оптимизацию, потому что нам нужно
    # пропустить файл через ffmpeg-фильтры.
    if (
        not denoise
        and _ext(src_path) == ".wav"
        and sr == TARGET_SAMPLE_RATE
        and ch == TARGET_CHANNELS
    ):
        return PreparedAudio(
            path=src_path,
            duration_sec=duration,
            sample_rate=sr,
            channels=ch,
            is_temporary=False,
        )

    # Иначе — конвертим. NamedTemporaryFile с suffix=.wav, чтобы ffmpeg
    # понял формат по расширению (мы дополнительно даём -f wav, но так надёжнее).
    out_dir = Path(workdir) if workdir else Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(tempfile.mkstemp(suffix=".wav", dir=out_dir)[1])

    # -y перезаписываем; -vn отключаем видео; -ac 1 моно; -ar 16000 — нужная частота;
    # -acodec pcm_s16le — стандартный 16-бит WAV (этого ждут pyannote/whisper).
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src_path),
        "-vn",
        "-ac", str(TARGET_CHANNELS),
        "-ar", str(TARGET_SAMPLE_RATE),
        "-acodec", "pcm_s16le",
    ]
    # F15: фильтры. loudnorm — нормализация громкости по EBU R128
    # (помогает Whisper'у на тихих/громких записях). afftdn — спектральное
    # шумоподавление, nf=-25 — умеренно (агрессивнее может убить тихую речь).
    # Цепочка через запятую — ffmpeg синтаксис.
    if denoise:
        cmd += ["-af", "loudnorm=I=-16:LRA=11:TP=-1.5,afftdn=nf=-25"]
    cmd += [
        "-f", "wav",
        str(out_path),
        "-loglevel", "error",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # Удалим недописанный wav и пробросим осмысленную ошибку.
        out_path.unlink(missing_ok=True)
        raise AudioError(
            f"ffmpeg не смог сконвертировать {src_path.name}: "
            f"{e.stderr.strip()[:500] if e.stderr else e}"
        ) from e

    return PreparedAudio(
        path=out_path,
        duration_sec=duration,
        sample_rate=TARGET_SAMPLE_RATE,
        channels=TARGET_CHANNELS,
        is_temporary=True,
    )


def cleanup(prepared: PreparedAudio) -> None:
    """Снести временный wav, если он был создан. Идемпотентно."""
    if prepared.is_temporary:
        Path(prepared.path).unlink(missing_ok=True)

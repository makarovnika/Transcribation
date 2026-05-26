"""transcriber — локальная транскрибация с диаризацией.

Публичные модули:
- device:        автоопределение MPS/CPU
- audio_utils:   конвертация форматов через ffmpeg
- transcription: обёртка над faster-whisper
- diarization:   обёртка над pyannote.audio
- alignment:     сопоставление сегментов со спикерами
- exporters:     генерация txt/srt/vtt/json/md
- config:        хранение HF-токена
"""

__version__ = "0.1.0"

# CLAUDE.md — Локальный сервис транскрибации аудио с диаризацией

Ты работаешь в репозитории, рассчитанном на длительную разработку с участием
агента. Главное — надёжное завершение фич, непрерывность между сессиями и явная
проверка результата, а не скорость генерации кода.

Заказчик — **Никита**, уровень Python — базовый. Объясняй:
- **что** делаем на этом шаге,
- **почему** именно так, а не иначе,
- **что проверить**, чтобы убедиться, что работает,
- любые **подводные камни**. По-русски.

---

## Контекст проекта

Локальное оффлайн-приложение для транскрибации аудио в текст с разделением
по спикерам и генерацией субтитров. Работает на macOS (Apple Silicon) и
Windows (включая NVIDIA GPU). Никаких облачных API.

**Ключевые компоненты:**
- **Whisper backend — выбирается в рантайме** (см. `src/whisper_backends/`):
  - `mlx-whisper` — на Apple Silicon (нативный Metal, ~18× realtime на large-v3)
  - `faster-whisper` — на Windows/Linux (CUDA + float16 если есть GPU, иначе CPU + int8)
  - Переключатель: env `TRANSCRIBER_BACKEND=mlx|faster-whisper`
- `pyannote.audio` 3.3+ — диаризация. Поддерживает cuda > mps > cpu (см. `src/device.py`).
- `Gradio` — простой локальный веб-UI.
- `ffmpeg` — конвертация видео/аудио форматов.

---

## Operating Loop (обязательно в начале каждой сессии)

1. Выполни `pwd` и убедись, что находишься в корне репозитория `transcriber/`.
2. Прочитай `claude-progress.md` — последнее подтверждённое состояние и следующий шаг.
3. Прочитай `feature_list.json` — выбери фичу с наивысшим приоритетом и статусом `not_started` или `in_progress`.
4. Посмотри последние коммиты: `git log --oneline -5`.
5. Запусти `./init.sh`.
6. Проверь, что базовая верификация (smoke) проходит. Если нет — чини её первой.

Затем выбери **ровно одну** незавершённую фичу и работай только над ней,
пока не подтвердишь её или не задокументируешь блокер.

---

## Архитектура

```
Gradio UI (app.py)
    │
    ├─▶ audio_utils.py (ffmpeg: видео → wav 16kHz mono)
    ├─▶ transcription.py (faster-whisper, CPU+int8)
    ├─▶ diarization.py (pyannote.audio, MPS)
    ├─▶ alignment.py (max-overlap матчинг)
    └─▶ exporters.py (txt/srt/vtt/json/md)

Конфиг: ~/.config/transcriber/config.json (HF-токен)
Кэш промежуточных результатов: cache/
Готовые файлы: outputs/
```

## Структура проекта

```
transcriber/
├── CLAUDE.md
├── AGENTS.md
├── README.md
├── init.sh
├── claude-progress.md
├── feature_list.json
├── session-handoff.md
├── clean-state-checklist.md
├── evaluator-rubric.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── app.py
├── src/
│   ├── __init__.py
│   ├── device.py             ← выбор backend и устройства
│   ├── audio_utils.py
│   ├── transcription.py      ← чанкинг, partial-cache, resume
│   ├── whisper_backends/     ← cross-platform фасад
│   │   ├── __init__.py       ← factory get_backend()
│   │   ├── mlx_backend.py    ← Apple Silicon (Metal)
│   │   └── fw_backend.py     ← Windows/Linux (CUDA/CPU)
│   ├── diarization.py        ← pyannote 3.3+, diar-cache
│   ├── alignment.py
│   ├── exporters.py
│   └── config.py
├── bin/
│   ├── svc-*.sh                              ← Mac launchd сервис
│   ├── com.muraveika.transcriber.plist.template
│   └── windows/                              ← Windows Task Scheduler сервис
│       ├── setup.ps1                         ← venv + deps + CUDA auto-detect
│       ├── install-service.ps1
│       ├── _runner.ps1                       ← wrapper для редиректа stdout
│       └── restart/uninstall/status/logs.ps1
├── tests/
│   ├── conftest.py
│   ├── test_alignment.py
│   ├── test_device.py
│   ├── test_exporters.py
│   └── fixtures/sample_30s.wav
├── cache/        # gitignored (transcribe partial + diar cache)
├── outputs/      # gitignored
└── logs/         # gitignored (логи сервиса)
```

---

## Правила работы

- **Одна активная фича за раз.** Не начинай вторую, пока первая не в статусе `passing` или `blocked`.
- **Не помечай фичу `passing`, если код просто написан** — нужна реально прошедшая верификация и зафиксированные доказательства.
- **Не переписывай feature_list.json, чтобы скрыть незавершённую работу.**
- **Не ослабляй тесты, чтобы фича «выглядела готовой».**
- **Не меняй правила верификации молча по ходу разработки.**

## Definition Of Done

Фича считается завершённой, только если **все** условия выполнены:
- целевое поведение реализовано,
- требуемая верификация **реально запускалась**,
- доказательства зафиксированы в `feature_list.json` или `claude-progress.md`,
- репозиторий остаётся запускаемым через `./init.sh`.

---

## Принципы кода

1. **Async где это естественно** (Gradio сам асинхронный); тяжёлые модели — синхронные с прогресс-колбэком.
2. **Типизация.** Все публичные функции с type hints, pydantic-модели для конфигов.
3. **Без god-классов.** Бизнес-логика в `src/`, `app.py` — только UI-склейка.
4. **Обработка ошибок:**
   - Битый файл / неподдерживаемый формат → понятная ошибка в UI.
   - Отсутствие HF-токена → подсказка как получить.
   - OOM → совет уменьшить размер модели.
5. **Комментарии в коде** — на русском, объясняют **почему**, а не **что**. Технические термины (segment, embedding, beam) оставляем на английском.
6. **Секреты только в `~/.config/transcriber/config.json` или env.** Никаких хардкодов токенов.
7. **Тесты:** alignment и exporters — детерминированные юнит-тесты на синтетике; для интеграционного теста нужен короткий WAV (положить в `tests/fixtures/`).

---

## Подводные камни (читать перед работой)

- **pyannote 3.3+ переименовала `use_auth_token` → `token`** при `Pipeline.from_pretrained`. В diarization.py мы пробуем сначала новый, потом старый параметр для совместимости.
- **pyannote возвращает `DiarizeOutput`**, а не `Annotation`. Аннотация — внутри `.speaker_diarization`. Берём через `getattr(annotation, 'speaker_diarization', annotation)` (back-compat со старыми версиями).
- **pyannote/speaker-diarization-3.1 требует accept трёх gated моделей** на HuggingFace под аккаунтом:
  - `pyannote/speaker-diarization-3.1`
  - `pyannote/segmentation-3.0`
  - `pyannote/speaker-diarization-community-1`  ← это часто забывают, оно даёт 403
- **Память на длинных файлах:** pyannote держит файл целиком в RAM (~3 ГБ на час 16kHz mono). Для 3-часового файла надо ≥8 ГБ свободной памяти. На 8-ГБ Mac мы выгружаем MLX-кэш между transcribe и diarize (`mlx.metal.clear_cache()` в app.py).
- **MLX недоступен вне Apple Silicon** — на Windows/Linux код автоматически уходит на `faster-whisper`. Если на Mac arm64 mlx-whisper не установлен (например после ручного pip install), `device.get_whisper_backend()` тихо вернёт `faster-whisper` (CPU+int8) — медленно. Проверить: `python -c "from src.device import get_device_info; print(get_device_info())"`.
- **CUDA torch на Windows ставится отдельно.** Стандартный `pip install torch` тянет CPU-сборку. Для GPU нужен индекс `--index-url https://download.pytorch.org/whl/cu121`. `setup.ps1` детектит `nvidia-smi` и предлагает поставить автоматически.
- **SRT/VTT таймкоды:** формат разный (`,` vs `.` в миллисекундах). Не путать.

---

## Перед завершением сессии

1. Обнови `claude-progress.md`.
2. Обнови `feature_list.json`.
3. Заполни `session-handoff.md`.
4. Закоммить, когда репозиторий в безопасном состоянии.

См. также `clean-state-checklist.md`.

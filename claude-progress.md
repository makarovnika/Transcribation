# claude-progress.md — журнал сессий

> Хронологический журнал. Новые записи — снизу. Каждая запись: дата, что
> сделано, что подтверждено (evidence), что следующее, известные блокеры.

---

## Session 001 — 2026-05-25 (инициализация проекта)

**Сделано:**
- Создан skeleton проекта в `/Users/muraveika/transcriber/`.
- Заведены harness-файлы по шаблону walkinglabs/learn-harness-engineering:
  `CLAUDE.md`, `AGENTS.md`, `init.sh`, `feature_list.json`,
  `session-handoff.md`, `clean-state-checklist.md`, `evaluator-rubric.md`,
  `claude-progress.md` (этот файл).
- Заведён `pyproject.toml` с зависимостями из ТЗ §5.
- Заведён `.gitignore` (cache/, outputs/, .env, .venv, __pycache__).

**Подтверждено (evidence):**
- Структура файлов соответствует ТЗ §4 и шаблону harness.
- Зависимости в pyproject.toml совпадают со списком ТЗ §5.

**Следующий шаг:**
- Начать F01-device (минимальный модуль с тестом).
- Затем по порядку из ТЗ §11: device → audio_utils → transcription → diarization → alignment → exporters → config → app.

**Блокеры:**
- Нет (ещё не доходили до моделей и HF-токена).

**Заметки:**
- На Apple Silicon faster-whisper НЕ может использовать MPS (CTranslate2). Только CPU+int8.
- pyannote/speaker-diarization-3.1 требует acceptance условий на HF под пользовательским аккаунтом.

---

## Session 002 — 2026-05-25 (полная реализация MVP)

**Сделано (все модули из ТЗ §11):**
- `src/device.py` — `get_device_info()`, инвариант whisper=cpu+int8.
- `src/audio_utils.py` — `prepare_audio()` через ffmpeg subprocess (mp3/wav/.../mp4/...) → WAV 16 kHz mono. Short-circuit когда вход уже подходит.
- `src/transcription.py` — обёртка над `faster_whisper.WhisperModel`. Ленивый импорт. Возвращает `(Iterator[Segment], TranscriptionMeta)`. Прогресс по `end / duration`.
- `src/diarization.py` — обёртка над `pyannote.audio.Pipeline`. MPS если есть. Понятные ошибки `HFAuthError` на 401/403.
- `src/alignment.py` — sweep-line max-overlap, `UNKNOWN_SPEAKER` для несовпадений.
- `src/exporters.py` — `to_txt/srt/vtt/json/md` + `write_*` обёртки. SRT (`,` в ms) и VTT (`.` в ms) — разные.
- `src/config.py` — `get_hf_token()` с приоритетом explicit > env > файл; `save_hf_token()` атомарно + chmod 0600.
- `app.py` — Gradio UI со всеми элементами ТЗ §3.1, прогресс с фазами prepare/transcribe/diarize/export, экспорт сразу в 5 форматов в `outputs/`.
- `README.md` — установка, HF-токен, perf-табличка, 5 подводных камней.

**Тесты:**
- `tests/test_device.py` — 4 теста на инварианты.
- `tests/test_alignment.py` — 10 тестов (empty, inside, overlap-max, outside, touch boundary, multiple, unsorted, gaps, equal-overlap tiebreaker).
- `tests/test_exporters.py` — 12 тестов (timestamp форматы, txt группировка спикеров, srt/vtt placeholder, json roundtrip с UTF-8, md заголовки).

**Подтверждено (evidence):**
- `python3 -m py_compile` зелёный на всех файлах.
- Ключевые ассерты `test_alignment.py` и `test_exporters.py` прогнаны вручную через `python3 -c`. Все проходят.
- `config.py` прогнан с изолированным HOME — порядок приоритета токена работает корректно.

**Что НЕ подтверждено (требует окружения):**
- Полный `pytest tests/` — нужен установленный pytest (доустановится через `./init.sh`).
- Реальная транскрибация / диаризация — нужен `faster-whisper`, `pyannote.audio`, модели, HF-токен.
- UI smoke — нужен `gradio` и реальный аудиофайл.

**Следующий шаг (Session 003):**
1. Запустить `./init.sh` (ffmpeg + uv venv + pip install -e .[dev] + pytest smoke).
2. Прогнать `pytest tests/` целиком, убедиться что все тесты зелёные.
3. Положить короткий 2-голосный WAV в `tests/fixtures/sample_30s.wav` (можно сгенерить из любого диалога).
4. Получить HF-токен, принять условия pyannote/speaker-diarization-3.1.
5. `python app.py` → smoke в браузере → отметить F08 как `passing`.
6. Тогда F10 (интеграционный тест) и можно ставить galочки в ТЗ §10 (приёмка).

**Блокеры:**
- Нет блокеров для запуска init.sh.
- HF-токен — задача пользователя (см. README §3).

# session-handoff.md — короткая передача между сессиями

> 5–10 строк. То, что нужно знать следующему агенту/себе через сутки.

---

## State: 2026-05-25 (после Session 002)

**Что готово (код написан + синтаксис ок):**
- Все модули из ТЗ §11: device, audio_utils, transcription, diarization, alignment, exporters, config, app.py.
- README с инструкциями.
- Тесты: test_device (4), test_alignment (10), test_exporters (12).

**Что подтверждено фактически (passing):**
- F05-alignment, F06-exporters, F07-config — прогнаны вручную через `python3 -c`, все assert зелёные.

**Что в работе (in_progress, ждёт окружения):**
- F01..F04, F08, F09 — синтаксис ок, но реальный запуск требует ffmpeg + venv + ML-либ + HF-токен.

**Что сломано:**
- Ничего. Все py_compile зелёные.

**Следующий шаг:**
1. `brew install ffmpeg` если ещё нет.
2. `./init.sh` (поставит venv, deps, прогонит smoke-pytest).
3. Положить `tests/fixtures/sample_30s.wav` (короткий 2-голосный wav).
4. Получить HF-токен + accept pyannote terms.
5. `python app.py` → загрузить wav → проверить все 5 экспортов.

**Блокеры:**
- Нет, всё в руках пользователя.

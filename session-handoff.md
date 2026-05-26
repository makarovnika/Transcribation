# session-handoff.md — короткая передача между сессиями

> 5–10 строк. То, что нужно знать следующему агенту/себе через сутки.

---

## State: 2026-05-26 (после Session 007 — v2 завершён + code review)

**Что готово end-to-end (passing):**
- MVP v1 (F01-F09).
- v2 P0/P1: F11 переименование спикеров + .meta.json sidecar, F12 slug naming
  ({YYYY-MM-DD}_{slug}_{hash6}), F13 светлая тема + ETA + 4-этапная прогресс-строка,
  F14 история встреч (Dropdown + load + delete), F17 HTML transcript с кликабельными
  таймкодами и JS-подключением к gr.Audio плееру, F18 поиск с <mark>-подсветкой,
  F23 YAML frontmatter в MD, F24 системные уведомления через osascript,
  F26 ротация outputs/cache + UI accordion, F27 psutil для RAM (cross-platform),
  F28 версия pyannote в diar-cache, F29 soft/hard limit длительности.
- Task #31: sequential pipeline через subprocess — освобождает MLX-память
  ДО загрузки pyannote. Это критичный fix для 8 ГБ Mac, без него medium+diarize
  падали с jetsam.
- Code-review (07-я итерация) — обнаружил stderr deadlock в subprocess
  (stderr=PIPE без чтения → 64 KB buffer → block) и зафиксил через stderr=STDOUT
  + объединённый read loop с накоплением recent_lines для error.
- Статический plist удалён — единственный source of truth теперь
  bin/com.muraveika.transcriber.plist.template, svc-install рендерит из него.

**Что в коде есть, но e2e ждёт прогона пользователя (in_progress):**
- F15 шумоподавление (галка в UI + ffmpeg -af loudnorm,afftdn).
- F16 запись с микрофона (gr.Audio sources=microphone, приоритет drop zone).
- F19 Ollama summary (src/summarize.py + кнопка «Получить резюме»;
  нужен запущенный ollama serve + llama3.1:8b).
- F20 word-level timestamps (галка в UI; в subprocess проброс через
  --word-timestamps int).
- F21 large-v3-turbo (model в SUPPORTED_MODELS; soft-block <10 ГБ).
- F22 batch (отдельный accordion с file_count='multiple').
- F25 model comparison (отдельный accordion с CheckboxGroup + Dataframe).

**Тесты:** 132/132 pytest.
**Сервис:** работает на http://127.0.0.1:7860, HTTP 200, KMP_DUPLICATE_LIB_OK
встроен в env (исправлен OMP Error #15 при ctranslate2+torch одновременно).
**GitHub:** https://github.com/makarovnika/Transcribation, последний commit
после code-review fixes (на момент handoff в момент написания).

**Что-важно знать следующему агенту:**
- 8 ГБ Mac жёсткий предел: medium+diarize работает ТОЛЬКО через subprocess
  (Task #31). small+diarize тоже через subprocess, чтобы не ловить jetsam.
- gr.HTML preview требует TRANSCRIPT_CSS_JS на странице (вставлен в начало UI).
  Если кто-то будет менять preview — нужно держать те же класс-нэймы
  (.turn, .speaker, .time, .text) иначе JS click-to-seek сломается.
- TranscriptionMeta теперь не @dataclass(frozen=True) — это был баг, который
  скрывал detected_language=null в subprocess. Не делайте обратно frozen.

---

## State: 2026-05-26 (после Session 003-004)

**Что готово фактически и работает end-to-end:**
- F01-F08 все `passing`. 27/27 pytest зелёные.
- macOS: рабочий сервис под launchd (PID 71518 на момент записи). 60-мин файл `Гороховая улица_17.mp3` → 5 экспортов в `outputs/` с метками SPEAKER_xx.
- Cross-platform backend: MLX на Mac, faster-whisper на Windows/Linux (см. `src/whisper_backends/`).
- GitHub: https://github.com/makarovnika/Transcribation (private).
- Windows-сервис: Task Scheduler через `bin/windows/*.ps1` (не тестирован end-to-end на самой Windows — ждёт развёртки на рабочем ПК пользователя).

**Что в работе:**
- F18 — стриминговый экспорт SRT/TXT (pending, UX-улучшение, не блокер).

**Что критически НЕ протестировано:**
- Windows e2e — install-service.ps1, setup.ps1 не запускались на реальной Windows-машине.
  CUDA-сборка torch на RTX 3050 Ti — не подтверждено что работает.
- Fix через wrapper `_runner.ps1` (вместо `cmd /c`) — теоретически правильный,
  но без проверки на Windows не доказано.

**Что сломано:**
- Ничего (на Mac работает).

**Следующий шаг для Mac:**
- Опционально: стриминговый экспорт SRT (F18).

**Следующий шаг для Windows (когда дойдут руки):**
1. На рабочем ПК: `winget install Gyan.FFmpeg Python.Python.3.12 Git.Git`.
2. `git clone https://github.com/makarovnika/Transcribation.git transcriber && cd transcriber`.
3. `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`.
4. `.\bin\windows\setup.ps1` (детектит nvidia-smi → предложит cu121).
5. Если не сделано в шаге 4: `pip install --upgrade --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu121`.
6. Проверка: `python -c "import torch; print(torch.cuda.is_available())"` → должно быть True.
7. `.\bin\windows\install-service.ps1`.
8. http://127.0.0.1:7860 → ввести HF токен (тот же что на Mac).

**Блокеры:**
- Доступ к рабочему ПК для проверки Windows flow.

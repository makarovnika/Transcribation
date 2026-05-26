# session-handoff.md — короткая передача между сессиями

> 5–10 строк. То, что нужно знать следующему агенту/себе через сутки.

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

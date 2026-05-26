# Локальный транскрибатор (offline)

Перевод аудио/видео в текст с разделением по спикерам и субтитрами.
Работает **полностью локально** на Mac с Apple Silicon (M1/M2/M3/M4).
Никаких облачных API.

Стек: `faster-whisper` (CTranslate2) + `pyannote.audio` 3.1 + `Gradio`.

---

## 1. Системные зависимости

```bash
brew install ffmpeg
```

Проверь: `ffmpeg -version` должно отвечать.

## 2. Установка Python-окружения

Используем `uv` — быстрый менеджер пакетов. Если его нет:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Дальше:

```bash
cd /путь/к/transcriber
uv venv                                  # создаёт .venv
source .venv/bin/activate
uv pip install -e ".[dev]"               # ставит проект и dev-зависимости
```

Альтернатива без `uv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 3. HuggingFace токен (нужен только для диаризации)

Диаризация (разделение по спикерам) использует gated-модель
`pyannote/speaker-diarization-3.1`. Без токена транскрибация работает,
но все реплики будут идти от `UNKNOWN`.

1. Зарегистрируйся на https://huggingface.co.
2. Прими условия модели:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0 (используется внутри)
3. Создай Read-токен: https://huggingface.co/settings/tokens.
4. Передай токен в приложение одним из способов (порядок приоритета):
   - В поле UI «HF Token» (флаг «Запомнить» сохранит в `~/.config/transcriber/config.json`).
   - Через переменную окружения: `export HF_TOKEN=hf_...`.

## 4. Запуск

### Ручной (для разработки)

```bash
# Из активированного venv:
./init.sh        # smoke-проверка окружения
python app.py
```

В браузере откроется `http://127.0.0.1:7860`. Если закрыть терминал — сервис умрёт.

### Как фоновый сервис (рекомендуется для повседневного использования)

Через launchd Agent — сервис запускается при логине, перезапускается при падении,
логи в `logs/`:

```bash
bin/svc-install.sh    # установить и запустить
bin/svc-status.sh     # проверить статус
bin/svc-logs.sh       # tail логов (Ctrl+C выйти)
bin/svc-restart.sh    # перезапустить (например после изменения кода)
bin/svc-uninstall.sh  # снять
```

После `svc-install.sh` сервис всегда доступен на `http://127.0.0.1:7860`,
пока ты залогинен на машине. Терминал закрывать можно.

**Когда нужен restart**: после правки кода в `src/` или `app.py` запусти
`bin/svc-restart.sh` — launchd убьёт старый процесс и поднимет новый с новым кодом.

UI:
- Перетащи аудио/видео в зону загрузки.
- Выбери модель (`large-v3` — лучшее качество, дольше; `small` — быстрее, хуже).
- Включи диаризацию, если нужны метки спикеров.
- Жми «Транскрибировать».
- Когда готово — скачай результат в 5 форматах: `.txt`, `.srt`, `.vtt`, `.json`, `.md`.

---

## Перенос на другой Mac

Полный flow с нуля.

### 1. Упаковать проект на старом Mac

Без `.venv` (Python окружение не переносимо между машинами), без артефактов:

```bash
cd ~ && tar \
  --exclude='transcriber/.venv' \
  --exclude='transcriber/__pycache__' \
  --exclude='transcriber/.pytest_cache' \
  --exclude='transcriber/outputs' \
  --exclude='transcriber/logs/*.log' \
  --exclude='transcriber/cache/*' \
  -czf ~/transcriber.tgz transcriber
```

Перенеси `transcriber.tgz` на новый Mac (AirDrop / scp / iCloud).

**Опционально:** можно перенести кэш моделей `~/.cache/huggingface/` — это
1.5–5 ГБ, но сэкономит минуты на скачивании при первом запуске.

### 2. На новом Mac — системные зависимости

```bash
# Homebrew (если нет)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# ffmpeg + uv
brew install ffmpeg uv
```

### 3. Развернуть проект

```bash
cd ~ && tar -xzf transcriber.tgz && cd transcriber

uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

./init.sh    # smoke pytest должен пройти
```

### 4. HuggingFace токен

Один из вариантов:

- **Вариант A**: открой UI → введи токен в поле «HF Token» → галка «Запомнить».
  Сохранится в `~/.config/transcriber/config.json` с правами 0600.
- **Вариант B**: перенеси `~/.config/transcriber/config.json` со старого Mac.

Если новый Mac залогинен под тем же HuggingFace-аккаунтом — условия трёх
gated моделей (`pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`,
`pyannote/speaker-diarization-community-1`) уже приняты, ничего нажимать не надо.

### 5. Запустить как фоновый сервис

```bash
bin/svc-install.sh
```

Plist рендерится из шаблона `bin/com.muraveika.transcriber.plist.template` — пути
к проекту и `HOME` подставляются автоматически из текущего окружения. Поэтому
скрипт работает на любом username, не только `muraveika`.

Если хочешь именно ручной запуск (без launchd) — просто `python app.py`.

---

## Поддерживаемые форматы

**Аудио:** mp3, wav, m4a, flac, ogg, opus, aac.
**Видео** (аудиодорожка извлекается через ffmpeg): mp4, mov, mkv, avi, webm.

Длительность — без ограничений (тестировалось до 3 ч).

---

## Производительность (ориентир для M2 Pro, 16 ГБ)

| Размер модели | Время на 1 час аудио (без диаризации) |
|---------------|----------------------------------------|
| `tiny`        | ~3–5 мин                              |
| `small`       | ~10–15 мин                            |
| `medium`      | ~30–40 мин                            |
| `large-v3`    | ~2–3 ч (×0.3 realtime, лучшее качество)|

Диаризация (pyannote) добавит ~6 мин/час аудио на MPS.

**Пиковая RAM на 1-часовом файле:** до 12 ГБ при `large-v3` + диаризация.

---

## Подводные камни

### 1. faster-whisper не работает на MPS

CTranslate2 (бэкенд faster-whisper) пока не поддерживает Metal. Поэтому
транскрибация идёт на CPU + int8. На Apple Silicon это всё равно быстро
благодаря Accelerate framework — не паникуй, что «не на GPU».

### 2. pyannote 401/403 на первом запуске

Значит: либо токен невалидный, либо не приняты условия модели.
Проверь оба пункта на https://huggingface.co/pyannote/speaker-diarization-3.1.

### 3. OOM на длинных файлах

Pyannote держит весь файл в RAM как тензор. Для 3-часового аудио нужно
≥8 ГБ свободной памяти. Если упало — выбери `medium` вместо `large-v3`
или режь файл на части.

### 4. Первый запуск долгий

Первый запуск с конкретной моделью качает её с HuggingFace (large-v3 ≈ 3 ГБ).
Дальше — кэш в `~/.cache/huggingface/`.

### 5. Видео без аудиодорожки

Падает в `prepare_audio` с понятной ошибкой «файл не содержит аудио-дорожки».

---

## Тесты

```bash
# Быстрые smoke-тесты (alignment, exporters, device, config):
pytest tests/ -q

# Все тесты, включая интеграционные (нужны модели и тестовый wav):
pytest tests/ -m "" -q
```

Тесты помечены маркером `slow`, если требуют сети/моделей —
по умолчанию они не запускаются.

---

## Структура проекта

```
transcriber/
├── app.py                    # Gradio UI, точка входа
├── pyproject.toml            # зависимости
├── init.sh                   # стандартный старт + smoke-тесты
├── CLAUDE.md / AGENTS.md     # инструкции для агента
├── feature_list.json         # источник правды по статусу фич
├── claude-progress.md        # журнал сессий
├── src/
│   ├── device.py             # MPS/CPU detection
│   ├── audio_utils.py        # ffmpeg → wav 16 kHz mono
│   ├── transcription.py      # faster-whisper wrapper
│   ├── diarization.py        # pyannote wrapper
│   ├── alignment.py          # max-overlap матчинг
│   ├── exporters.py          # txt/srt/vtt/json/md
│   └── config.py             # HF-токен
├── tests/                    # юнит-тесты
├── cache/                    # промежуточные результаты (gitignored)
└── outputs/                  # готовые транскрипции (gitignored)
```

---

## Что НЕ делает (по ТЗ)

- ❌ Не упаковывается в .app/.dmg.
- ❌ Не использует платные API.
- ❌ Не редактирует транскрипцию в UI (только просмотр + экспорт).
- ❌ Не хранит историю в БД — всё файлами в `outputs/`.

Эти фичи можно добавить позже отдельными итерациями.

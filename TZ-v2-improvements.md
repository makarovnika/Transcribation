# ТЗ v2 — улучшения транскрибатора

> Это техническое задание для Claude Code (или другого агента), работающего в
> репозитории `transcriber/`. Документ написан в стиле существующих
> `CLAUDE.md`/`feature_list.json` и предполагает, что агент уже умеет работать
> по Operating Loop (читает `claude-progress.md` → выбирает фичу → коммитит).
>
> **Принцип неизменен:** одна активная фича за раз, никаких массовых
> переписываний, верификация реально запускается, evidence фиксируется в
> `feature_list.json`. См. CLAUDE.md §«Правила работы» и §«Definition Of Done».

---

## 0. Контекст и цель

Уровень функциональности v1 закрыт: MVP работает end-to-end (F01–F08 passing).
Цель v2 — превратить инструмент из «работающего CLI с UI» в **рабочий
повседневный инструмент для транскрибации встреч**.

Заказчик использует продукт так: записал встречу команды → загрузил файл →
получил **читаемый** транскрипт с **именами** спикеров и **резюме** → положил
в свои рабочие заметки (Obsidian/Notion/Word).

Поэтому фокус v2:
1. **Спикеры с человеческими именами** (а не `SPEAKER_00`).
2. **Резюме встречи + action items** (короткая выжимка вместо 10 страниц сырого текста).
3. **История встреч прямо в UI** (без хождения в Finder за файлами).
4. **Осмысленные имена файлов** (а не `transcript_1779779882.json`).
5. **Современный читабельный UI** (тёмная тема, иерархия, аудио-плеер с переходом по таймстемпам, поиск).
6. **Поддержка повседневного workflow**: запись с микрофона, шумоподавление, batch, уведомления.

---

## 1. Глобальные изменения архитектуры

Эти изменения затрагивают несколько фич, поэтому документируются отдельно.

### 1.1 Расширение формата результата (transcript_id, метаданные встречи)

Сейчас единица результата — анонимный набор файлов `transcript_<unix>.{txt,srt,...}`
в `outputs/`. v2 вводит понятие **«сессия транскрипции»** (meeting / recording).

**Новая модель (sidecar метаданные):**

Рядом с каждой группой `<stem>.{txt,srt,...}` живёт `<stem>.meta.json` со схемой:

```json
{
  "schema_version": 2,
  "id": "2026-05-26_sync-komandy",
  "title": "Sync команды",
  "created_at": "2026-05-26T10:42:18Z",
  "source_file": {"name": "meeting.mp4", "size_bytes": 41234567},
  "duration_sec": 2823.4,
  "model_size": "medium",
  "detected_language": "ru",
  "diarized": true,
  "speakers": {
    "SPEAKER_00": {"display_name": "Никита", "speech_seconds": 1180.3, "turns": 142},
    "SPEAKER_01": {"display_name": "Артём", "speech_seconds": 873.0, "turns": 98}
  },
  "summary": { "tldr": "...", "action_items": ["...", "..."], "decisions": ["..."] },
  "files": { "txt": "...txt", "srt": "...srt", "vtt": "...vtt", "json": "...json", "md": "...md" }
}
```

Все экспорты (F11+) перечитывают `.meta.json` и подставляют `display_name` вместо
машинных `SPEAKER_XX`.

### 1.2 Управление состоянием сессии в Gradio

Gradio `gr.State` хранит ссылку на `MeetingSession` (in-memory обёртка над
`.meta.json` + aligned segments). После транскрибации в state кладётся всё, что
нужно для re-export без повторного прогона моделей.

### 1.3 Опциональная локальная LLM для резюме

Резюме делается **отдельным шагом** после транскрибации (не в основном pipeline,
чтобы не блокировать). Берём Ollama HTTP API (`http://127.0.0.1:11434`) с моделью
по умолчанию `llama3.1:8b` (есть в большинстве установок Ollama). Если Ollama не
запущен — кнопка резюме выдаёт понятную ошибку. Cloud-API (OpenAI/Anthropic) НЕ
используем — оффлайн остаётся принципом продукта.

### 1.4 Совместимость со старыми `outputs/`

Существующие `transcript_<unix>.*` файлы продолжают работать (читаются как
«legacy meetings»: `id` = timestamp, `title` = «Без названия», `speakers` =
пустой dict). При первом открытии legacy-сессии можно предложить миграцию
(сгенерить `.meta.json`).

---

## 2. Перечень фич (для feature_list.json)

Ниже спецификации каждой новой фичи. Формат тот же, что в текущем
`feature_list.json` (id, title, priority, status, depends_on, spec, verification,
evidence). Поле `status` для всех новых — `not_started`.

Полный обновлённый JSON приведён в §3 ниже.

---

### F11 — Переименование спикеров в UI и применение ко всем экспортам

**Priority:** P0
**Depends on:** F08-gradio-ui

**Spec:**

1. После завершения транскрибации в UI появляется секция «Спикеры». Это таблица:
   - колонка «Метка» — `SPEAKER_00`, `SPEAKER_01`, … (read-only).
   - колонка «Имя» — `gr.Textbox` (пустой = оставить системную метку).
   - колонка «Статистика» — `речь: 42% · 142 реплики` (рассчитывается из aligned
     segments: для каждой метки суммируем `sum(end-start)` и `count`).
2. Под таблицей кнопка «Применить и пересохранить» — пересчитывает `aligned`
   с подменой `speaker` через `display_name`, пишет 5 файлов заново в те же пути.
3. `display_name` сохраняется в `<stem>.meta.json` (sidecar из §1.1).
4. Маппинг применяется **во всех экспортах**: TXT (`[Никита]:`), SRT/VTT
   (`[Никита]: ...`), MD (`## Никита`), JSON (`"speaker": "Никита"`, плюс новое
   поле `"speaker_id": "SPEAKER_00"` для трассировки).
5. Превью в UI обновляется немедленно (`_run_pipeline` возвращает уже с применёнными именами).
6. Edge cases: пустое поле имени → оставляем `SPEAKER_XX`. Повторяющиеся имена
   допускаются (две метки можно объединить в одного спикера) — это **фича** для
   случая, когда pyannote разделил одного человека на двух из-за смены аудио-канала.

**Файлы:**
- Новые: `src/speakers.py` (модель Speaker, вычисление статистики, применение маппинга).
- Изменения: `app.py` (новый блок UI + handler), `src/exporters.py` (везде брать
  speaker через resolver, а не напрямую), новый `<stem>.meta.json` рядом с экспортами.

**Verification:**
- Unit-тест `tests/test_speakers.py`: расчёт статистики на синтетике, применение
  маппинга, edge case пустого имени.
- E2E ручной: загрузил файл → получил `SPEAKER_00..01` → ввёл «Никита» и «Артём»
  → нажал «Применить» → открыл `.txt` и `.md` → имена везде заменены.
- `pytest tests/` — все старые тесты остаются зелёными (особенно exporters).

**Evidence (заполнить после прогона):**
- Скриншот UI с заполненной таблицей спикеров.
- Diff одного из экспортов до/после применения маппинга.

---

### F12 — Название встречи и осмысленные имена файлов

**Priority:** P0
**Depends on:** F11-speaker-renaming (использует общий sidecar `.meta.json`).

**Spec:**

1. В UI над зоной загрузки — поле `gr.Textbox(label="Название встречи")`,
   placeholder «напр. Планирование Q3». Пустое поле допустимо.
2. Имя файла-выхода: `{YYYY-MM-DD}_{slug_title}_{short_hash}.{ext}`.
   - `YYYY-MM-DD` — день старта транскрибации (локальное время).
   - `slug_title` — `re.sub(r'[^\w\-]+', '-', title.lower()).strip('-')[:60]`.
     Если пусто → `untitled`.
   - `short_hash` — первые 6 символов `audio_fingerprint` (защита от коллизий,
     если за день несколько встреч с одним названием).
   - `ext` — `txt|srt|vtt|json|md|meta.json`.
3. Старые `transcript_<unix>` остаются как fallback при отсутствии `title`
   (миграционная совместимость).
4. `title` пишется в `.meta.json.title`.

**Файлы:**
- Изменения: `app.py` (новое поле + передача в `_run_pipeline` + расчёт `stem`),
  `src/exporters.py` (никаких — экспорт принимает готовый путь).
- Новые: `src/naming.py` (slugify + сборка stem; вынести из app, чтобы юнит-тестить).

**Verification:**
- Unit `tests/test_naming.py`: ASCII/Cyrillic/спецсимволы → корректный slug.
  Кейсы: `"Sync команды!"` → `"sync-komandy"` (учти, что non-ASCII нужно
  транслитерировать или оставить как есть — выбери одну стратегию и
  задокументируй; рекомендую оставлять Unicode word chars, без транслитерации,
  то есть результат будет `"sync-команды"`).
- E2E: ввёл «Sync команды» 26 мая → получил `2026-05-26_sync-команды_a1b2c3.txt`.

---

### F13 — Тёмная тема и переработка иерархии UI

**Priority:** P0
**Depends on:** F08-gradio-ui

**Spec:**

1. Подключить кастомную тему Gradio:
   ```python
   theme = gr.themes.Soft(
       primary_hue="indigo",
       neutral_hue="slate",
       font=gr.themes.GoogleFont("Inter"),
   ).set(
       body_background_fill="*neutral_950",
       background_fill_primary="*neutral_900",
       block_background_fill="*neutral_900",
       block_border_color="*neutral_800",
       button_primary_background_fill="*primary_600",
   )
   ```
2. Reshape основной layout по мокапу `outputs/ui-mockup.html`:
   - Шапка: лого-плашка + название + статус-пиллы (device / token / RAM).
   - Левая колонка (`scale=2`): Drop zone, поле «Название встречи», Модель +
     Язык в одну строку, чекбоксы (Диаризация / Шумоподавление / Резюме),
     `gr.Accordion("Продвинутые настройки", open=False)` со spec'ами num_speakers
     и HF Token, **крупный primary CTA**.
   - Правая колонка (`scale=3`): прогресс-карточка с этапами и ETA, таблица
     спикеров (после F11), `gr.Tabs(["Транскрипт", "Резюме", "Action items"])`,
     downloads с primary-кнопкой `MD`.
3. Прогресс — показать **все 4 этапа** одной строкой
   (`✓ Подготовка · ● Whisper · ○ Диаризация · ○ Экспорт`) с подсветкой текущего.
   Заодно посчитать ETA (см. F13.1 ниже).
4. Empty state: при `audio_in=None` правая колонка показывает плашку
   «Загрузите файл слева, чтобы начать».
5. Error state: ошибка идёт **не в `status` textbox**, а в отдельный `gr.Markdown`
   с красной плашкой и иконкой (`🚫`).
6. Success state: зелёная плашка `✓ Готово за 3:42 · 87 реплик · 4 спикера`.

**F13.1 (под-фича) — оценка ETA:**

В `_make_progress` хранить скользящее среднее времени на чанк и каждое
обновление прогресса добавлять в `desc` строку `(осталось ~M:SS)`. Формула:

```python
elapsed = time.time() - t0_stage
done_fraction = (idx + 1) / chunks_total
remaining_fraction = 1 - done_fraction
eta_sec = elapsed / max(done_fraction, 1e-6) * remaining_fraction
```

**Файлы:**
- Изменения: `app.py` целиком переработать `build_ui()` (это самая большая часть).
- Новые: возможно `src/ui_components.py` если будет много кастома (не обязательно).

**Verification:**
- Запустить `python app.py`, открыть в браузере, **сделать скриншот** до/после.
- Прогнать full e2e на коротком файле и убедиться, что прогресс показывает
  этапы и ETA.
- Состояния empty/error/success визуально проверены.

**Evidence:**
- 2 скриншота (старый/новый UI) → в `claude-progress.md`.

---

### F14 — История встреч в UI

**Priority:** P0
**Depends on:** F11 (схема `.meta.json`), F12 (имена файлов).

**Spec:**

1. Под левой колонкой блок `gr.Accordion("История встреч", open=True)`.
2. Сканировать `outputs/*.meta.json` (или legacy `transcript_*` без `.meta.json`),
   сортировать по `created_at` desc, показать 5 последних. Кнопка «Показать все N».
3. Каждый элемент: название, дата («сегодня» / «вчера» / `DD MMM`), длительность,
   число спикеров. При клике загружает сессию в правую колонку (re-render без
   повторного прогона моделей — берём из `.meta.json` + соответствующего
   `.json`-экспорта).
4. На каждом элементе — `⋯`-меню: «Открыть», «Удалить», «Открыть в Finder/Explorer».
5. Удаление сносит все файлы группы (`<stem>.*`) с подтверждением.

**Файлы:**
- Новые: `src/history.py` (scan, load, delete).
- Изменения: `app.py` (новый блок UI + handlers).

**Verification:**
- Unit `tests/test_history.py`: сканирование 3 синтетических `.meta.json` →
  правильный порядок и парсинг.
- E2E: после транскрибации новый файл появляется в списке без рестарта.
- Удаление работает и не оставляет orphan-файлов.

---

### F15 — Шумоподавление и нормализация громкости

**Priority:** P0
**Depends on:** F02-audio-utils

**Spec:**

1. В UI чекбокс «Шумоподавление» (по умолчанию **выкл**, чтобы не ломать существующее поведение).
2. При включении `prepare_audio()` добавляет в ffmpeg-цепочку:
   - `-af loudnorm=I=-16:LRA=11:TP=-1.5` — приводим громкость к стандарту
     EBU R128, помогает Whisper'у на слишком тихих/громких записях.
   - `-af afftdn=nf=-25` — мягкое спектральное шумоподавление. Применяем **только**
     если чекбокс включён (агрессивная фильтрация может убить тихих спикеров).
3. Полная команда (когда оба фильтра включены):
   `-af "loudnorm=I=-16:LRA=11:TP=-1.5,afftdn=nf=-25"`
4. Логировать в status «Применён loudnorm + afftdn».

**Файлы:**
- Изменения: `src/audio_utils.py` (`prepare_audio()` принимает `denoise: bool = False`),
  `app.py` (новый чекбокс).

**Verification:**
- Ручное сравнение на одном файле с шумом: транскрипт без denoise vs с denoise.
- Длительность остаётся той же (`probe_duration` до и после).
- Pytest старые остаются зелёными.

---

### F16 — Запись с микрофона прямо в UI

**Priority:** P1
**Depends on:** F08

**Spec:**

1. Добавить второй источник аудио в UI: рядом с drop zone — `gr.Audio(sources=["microphone"], type="filepath", label="Записать с микрофона")`.
2. Если оба источника заполнены — приоритет у drop zone (с warning в логах).
3. Записанный файл — WAV 44.1 kHz mono — корректно проходит `prepare_audio`
   (он сам ресемплит в 16 kHz).
4. Кнопка «Стоп» записи возвращает путь к временному файлу; pipeline стартует
   так же, как для drag-and-drop.

**Файлы:**
- Изменения: `app.py` (новый input + логика выбора источника в `_run_pipeline`).

**Verification:**
- Записать 30 сек в браузере → получить транскрипт → файлы в `outputs/`.

---

### F17 — Аудио-плеер с переходом по таймстемпам

**Priority:** P1
**Depends on:** F11 (превью с известным speaker), F12 (стабильное имя файла).

**Spec:**

1. После транскрибации показать `gr.Audio(value=audio_path, label="Прослушать")`.
2. Превью транскрипта рендерить как `gr.HTML` (не `gr.Textbox`) с кликабельными
   таймкодами:
   ```html
   <div class="turn">
     <span class="time" data-sec="42.5">00:00:42</span>
     <span class="speaker">[Никита]:</span>
     <span class="text">Окей, начнём…</span>
   </div>
   ```
3. JS-обработчик (через `gr.HTML` + inline `<script>`) при клике на `.time`:
   `document.querySelector('audio').currentTime = parseFloat(e.target.dataset.sec)`.
4. Текущий проигрываемый сегмент подсвечивать (опционально, P2): на `timeupdate`
   находить сегмент, у которого `start <= currentTime < end`, добавлять ему
   класс `.active`.

**Подводные камни:**
- Gradio изолирует HTML в iframe; селектор аудио надо искать в parent.
  Если выйдет проблемно — рендерить и audio внутри того же gr.HTML.

**Файлы:**
- Новые: `src/ui_html.py` (рендеринг турнов как HTML).
- Изменения: `app.py` (preview_out = gr.HTML вместо gr.Textbox).

**Verification:**
- E2E: загрузил → кликнул на таймкод второй реплики → плеер перематывает.
- Кросс-браузер: Chrome + Safari + Firefox.

---

### F18 — Поиск по транскрипту

**Priority:** P1
**Depends on:** F17

**Spec:**

1. Над превью — `gr.Textbox(label="🔍 Поиск", placeholder="искать по тексту")`.
2. На каждое изменение — JS-фильтрация (через `gr.HTML` + inline JS): скрыть
   `.turn`, в которых нет матча, в остальных обернуть совпадения в `<mark>`.
3. Регистр игнорировать. Регулярки — нет (только substring).
4. Счётчик `Найдено: N совпадений` справа от поля.

**Файлы:**
- Изменения: `app.py`, `src/ui_html.py` (добавить data-text атрибут для быстрого поиска).

**Verification:**
- Ручной: ввести «дедлайн» → подсветка работает, не-совпадающие реплики скрыты.

---

### F19 — Локальная суммаризация через Ollama

**Priority:** P1
**Depends on:** F11

**Spec:**

1. В UI кнопка «Получить резюме» (под транскриптом) + таб «Резюме». Без авто-запуска.
2. При клике — POST на `http://127.0.0.1:11434/api/generate` с моделью
   `llama3.1:8b` (имя в env `OLLAMA_MODEL`, дефолт `llama3.1:8b`).
3. Prompt:
   ```
   Ты помогаешь сократить транскрипт встречи. На вход — реплики с метками
   спикеров. На выход — JSON со следующими полями:
     - "tldr": один абзац (3-5 предложений), о чём была встреча.
     - "decisions": список принятых решений.
     - "action_items": список вида "{кто}: {что сделать} [{дедлайн или null}]".
   Отвечай только JSON, без markdown-fences.

   Транскрипт:
   {full_text}
   ```
4. Парсить ответ через `json.loads`, при ошибке — показать сырой ответ и сообщить
   о проблеме.
5. Результат сохранять в `.meta.json.summary` и рендерить в табе «Резюме».
6. Если Ollama не отвечает (`ConnectionError` за 5 сек) — показать инструкцию
   «Поставь Ollama: `brew install ollama && ollama pull llama3.1`».
7. Прогресс через `gr.Progress` (Ollama стримит — можно собирать через
   `stream=True` и обновлять текст).

**Файлы:**
- Новые: `src/summarize.py` (HTTP-клиент Ollama, парсинг, fallback).
- Изменения: `app.py` (кнопка + handler + таб).
- Опц. в `pyproject.toml`: добавить `httpx>=0.27` (или использовать stdlib `urllib`).

**Verification:**
- Если Ollama стоит — ручной прогон на коротком транскрипте → JSON распарсился,
  поля заполнены.
- Mock-тест на HTTP (`tests/test_summarize.py`) — отдельный.

---

### F20 — Word-level timestamps

**Priority:** P1
**Depends on:** F03-transcription

**Spec:**

1. В `whisper_backends/fw_backend.py`: добавить `word_timestamps=True` в вызов
   `handle.transcribe(...)`. Достать `s.words` из faster-whisper сегментов.
2. В `whisper_backends/mlx_backend.py`: добавить `word_timestamps=True` в
   `mlx_whisper.transcribe`. Достать `result["segments"][i]["words"]`.
3. Расширить `Segment` (в `src/transcription.py`):
   ```python
   @dataclass(frozen=True)
   class Word:
       start: float
       end: float
       text: str
       probability: float | None = None

   @dataclass(frozen=True)
   class Segment:
       start: float
       end: float
       text: str
       words: tuple[Word, ...] = ()
   ```
4. Опциональный чекбокс «Слова с таймкодами» в UI (по умолчанию **off**, потому
   что увеличивает память + время на 5–10%).
5. Когда включено: в `.json` экспорте добавлять массив `words` в каждом сегменте.
6. Alignment продолжает работать как раньше (на уровне сегментов).

**Файлы:**
- Изменения: `src/transcription.py`, `src/whisper_backends/*.py`,
  `src/exporters.py` (JSON), `src/alignment.py` (тип AlignedSegment расширить
  опциональным `words`), `app.py`.

**Verification:**
- Unit на synthetic Segment с `words=(...)` → корректно экспортируется в JSON.
- E2E: включить чекбокс → в JSON выхода у сегментов есть массив words с
  ненулевыми start/end.

---

### F21 — Опция модели `large-v3-turbo`

**Priority:** P1
**Depends on:** F03

**Spec:**

1. В `src/transcription.py`: добавить `"large-v3-turbo"` в `SUPPORTED_MODELS` и `WhisperModelName`.
2. В `src/whisper_backends/mlx_backend.py`: `"large-v3-turbo": "mlx-community/whisper-large-v3-turbo"`.
3. В `src/whisper_backends/fw_backend.py`: faster-whisper понимает `"large-v3-turbo"` нативно начиная с 1.0.4+ — проверь версию в pyproject.toml.
4. В UI добавить в dropdown с пометкой «⚡ быстрее, качество ~ large-v3».
5. Soft-block в `app.py` (по аналогии с large-v3 на <12 ГБ) — turbo тоже жирная,
   но меньше large-v3. Порог можно поднять до `_TOTAL_RAM_GB < 10`.

**Verification:**
- Прогнать короткий файл (30 сек) на turbo → время ≤ small × 2, качество читаемо.

---

### F22 — Batch-обработка

**Priority:** P2
**Depends on:** F11–F14

**Spec:**

1. Принимать `gr.File(file_count="multiple")`.
2. Очередь: обрабатывать файлы по одному, в `gr.Progress` показывать
   `файл K из N`. Каждый файл — отдельная сессия (свой `.meta.json`, свой stem).
3. Если один из файлов падает — продолжать с остальными, ошибка отображается в
   итоговом статусе.
4. После завершения — все сессии видны в Истории (F14).

**Verification:**
- E2E: загрузить 2 коротких файла → оба попали в `outputs/` со своими stem'ами.

---

### F23 — Markdown с YAML frontmatter (для Obsidian/Notion)

**Priority:** P2
**Depends on:** F11

**Spec:**

В `exporters.to_md(...)`:

```yaml
---
title: Sync команды
date: 2026-05-26
duration: 47m 03s
speakers: [Никита, Артём, Мария]
model: medium
language: ru
---
```

И дальше существующий формат `## SPEAKER\n(time) text`.

**Verification:**
- Открыть полученный `.md` в Obsidian: frontmatter распознан, шапка со
  свойствами видна.
- Unit-тест на парсинг frontmatter (yaml.safe_load на первом блоке `---...---`).

---

### F24 — Системное уведомление по завершении

**Priority:** P2
**Depends on:** F08

**Spec:**

После успешной транскрибации длиннее 5 минут — показать notification:

- **macOS:** `osascript -e 'display notification "..." with title "..."'`.
- **Windows:** PowerShell `New-BurntToastNotification` (если есть модуль), иначе
  `[System.Windows.Forms.MessageBox]::Show(...)` (синхронный, лучше пропустить).
- Альтернатива (cross-platform): `plyer` библиотека. Опционально.

**Verification:**
- Ручной: запустить 6-мин файл → по окончании всплыло уведомление.

---

### F25 — Сравнение моделей бок о бок (dev-feature)

**Priority:** P2
**Depends on:** F03

**Spec:**

1. Отдельная страница `/compare` в Gradio (`gr.TabbedInterface`).
2. Принимает короткий файл (≤2 мин принудительно через soft-block).
3. Прогоняет tiny + small + medium + large-v3 (опционально turbo) последовательно.
4. Показывает 3-колонную таблицу: модель | время | первые 200 символов
   транскрипта.

**Verification:**
- Прогнать на 1-мин фрагменте → 4 строки таблицы заполнены.

---

### F26 — Ротация `outputs/` и `cache/`

**Priority:** P2
**Depends on:** none

**Spec:**

1. В `src/config.py` добавить настройку `retention_days: int = 60` и `max_entries: int = 100`.
2. При старте `app.py` (lifespan startup) запустить `cleanup_old_outputs()`:
   - Удалить группы файлов старше `retention_days`.
   - Если групп >`max_entries` — удалить самые старые до лимита.
3. В UI Accordion «Управление местом» с кнопкой «Очистить старые» (запускает то же руками) и счётчиком `outputs: 1.2 ГБ · 27 файлов · cache: 340 МБ`.
4. Логировать удалённое в `logs/cleanup.log`.

**Verification:**
- Создать 5 фейковых stem'ов с разными `mtime` → запустить cleanup с
  retention_days=30 → удалены те, что старше.

---

### F27 — Кросс-платформенный `_total_ram_gb`

**Priority:** P0 (bugfix)
**Depends on:** none

**Spec:**

Текущий `_total_ram_gb` в `app.py` использует macOS-only `sysctl hw.memsize`. На
Windows возвращает 0.0 → `_LOW_RAM` всегда False → soft-block large-v3 не сработает.

Заменить на:
```python
def _total_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().total / (1024**3)
    except ImportError:
        # fallback на sysctl для случая когда psutil не установлен
        if sys.platform == "darwin":
            ...  # текущий код
        return 0.0
```

`psutil` уже косвенно тянется (torch её требует на некоторых платформах), но
лучше добавить в `pyproject.toml` явно.

**Verification:**
- `python -c "from app import _total_ram_gb; print(_total_ram_gb())"` на Mac и
  на Windows — обе платформы возвращают разумное значение.

---

### F28 — Версионирование кэша диаризации

**Priority:** P1 (bugfix)
**Depends on:** F04

**Spec:**

Сейчас ключ кэша диаризации:
`diar_<fingerprint>_n<num>_min<min>_max<max>.json`

Не учитывается версия pyannote-pipeline. Если обновили pyannote и схема меток
поменялась — старый кэш будет ошибочно reused.

Заменить ключ на:
`diar_<fingerprint>_<pipeline_version>_n<num>_min<min>_max<max>.json`

где `pipeline_version` = `pyannote.__version__` или хэш от
`PIPELINE_NAME + pyannote.__version__`.

**Verification:**
- Unit-тест: два вызова `_diar_cache_path` с разными версиями → разные пути.

---

### F29 — Ограничение размера/длительности входного файла

**Priority:** P1 (reliability)
**Depends on:** F02

**Spec:**

1. В `app.py._run_pipeline` после `prepare_audio` проверить
   `prepared.duration_sec`. Если >4 часов — soft-warning «файл длиннее 4ч,
   pyannote может упасть по памяти; продолжить?». Не блокируем, но
   предупреждаем.
2. Жёсткий блок при >8 часов (на сегодня нет ни одной разумной встречи
   длиннее).

**Verification:**
- Подать фейковый 9-часовой файл → блок с понятной ошибкой.

---

## 3. Обновлённый feature_list.json (готов к копированию)

Скопируй в `feature_list.json`, оставив существующие F01–F10 как `passing`.

```json
{
  "$schema_note": "Статусы: not_started | in_progress | passing | blocked.",
  "features": [
    {"id": "F01-device", "status": "passing", "...": "оставить как было"},
    {"id": "F02-audio-utils", "status": "passing", "...": "оставить как было"},
    {"id": "F03-transcription", "status": "passing", "...": "оставить как было"},
    {"id": "F04-diarization", "status": "passing", "...": "оставить как было"},
    {"id": "F05-alignment", "status": "passing", "...": "оставить как было"},
    {"id": "F06-exporters", "status": "passing", "...": "оставить как было"},
    {"id": "F07-config", "status": "passing", "...": "оставить как было"},
    {"id": "F08-gradio-ui", "status": "passing", "...": "оставить как было"},
    {"id": "F09-readme", "status": "in_progress", "...": "оставить как было"},
    {"id": "F10-integration-test", "status": "not_started", "...": "оставить как было"},

    {
      "id": "F11-speaker-renaming",
      "title": "Переименование спикеров в UI",
      "priority": "P0",
      "status": "not_started",
      "depends_on": ["F08-gradio-ui"],
      "spec": "См. TZ-v2-improvements.md §F11. Таблица спикеров, .meta.json sidecar, применение ко всем экспортам.",
      "verification": "tests/test_speakers.py + e2e: до/после применения маппинга.",
      "evidence": null
    },
    {
      "id": "F12-meeting-naming",
      "title": "Название встречи + осмысленные имена файлов",
      "priority": "P0",
      "status": "not_started",
      "depends_on": ["F11-speaker-renaming"],
      "spec": "См. TZ-v2-improvements.md §F12. Поле title, slug, формат {date}_{slug}_{hash}.",
      "verification": "tests/test_naming.py на кириллице/спецсимволах + e2e.",
      "evidence": null
    },
    {
      "id": "F13-ui-redesign",
      "title": "Тёмная тема и иерархия UI",
      "priority": "P0",
      "status": "not_started",
      "depends_on": ["F08-gradio-ui"],
      "spec": "См. TZ-v2-improvements.md §F13. gr.themes.Soft + indigo, reshape layout по outputs/ui-mockup.html, ETA в прогрессе, состояния empty/error/success.",
      "verification": "Скриншоты до/после в claude-progress.md + e2e на коротком файле.",
      "evidence": null
    },
    {
      "id": "F14-meeting-history",
      "title": "История встреч в UI",
      "priority": "P0",
      "status": "not_started",
      "depends_on": ["F11-speaker-renaming", "F12-meeting-naming"],
      "spec": "См. TZ-v2-improvements.md §F14. Сканирование outputs/*.meta.json, открытие/удаление, поддержка legacy без .meta.json.",
      "verification": "tests/test_history.py + e2e: новый файл появляется без рестарта.",
      "evidence": null
    },
    {
      "id": "F15-audio-preprocessing",
      "title": "Шумоподавление и loudnorm",
      "priority": "P0",
      "status": "not_started",
      "depends_on": ["F02-audio-utils"],
      "spec": "См. TZ-v2-improvements.md §F15. ffmpeg loudnorm + afftdn по чекбоксу.",
      "verification": "Ручное сравнение на шумном файле + старые pytest зелёные.",
      "evidence": null
    },
    {
      "id": "F27-cross-platform-ram",
      "title": "psutil вместо sysctl для _total_ram_gb",
      "priority": "P0",
      "status": "not_started",
      "depends_on": [],
      "spec": "См. TZ-v2-improvements.md §F27. psutil.virtual_memory().total, fallback на sysctl.",
      "verification": "Запуск на Windows и Mac, оба возвращают ненулевое значение.",
      "evidence": null
    },
    {
      "id": "F28-diar-cache-versioning",
      "title": "Версия pyannote в ключе кэша диаризации",
      "priority": "P1",
      "status": "not_started",
      "depends_on": ["F04-diarization"],
      "spec": "См. TZ-v2-improvements.md §F28. Добавить pyannote.__version__ в имя cache-файла.",
      "verification": "Unit-тест на функцию _diar_cache_path.",
      "evidence": null
    },
    {
      "id": "F29-duration-limit",
      "title": "Soft-warning на длинных файлах",
      "priority": "P1",
      "status": "not_started",
      "depends_on": ["F02-audio-utils"],
      "spec": "См. TZ-v2-improvements.md §F29. Warning при >4ч, hard-block при >8ч.",
      "verification": "Подать фейковый длинный файл → корректное поведение.",
      "evidence": null
    },
    {
      "id": "F16-mic-recording",
      "title": "Запись с микрофона",
      "priority": "P1",
      "status": "not_started",
      "depends_on": ["F08-gradio-ui"],
      "spec": "См. TZ-v2-improvements.md §F16. gr.Audio(sources=microphone).",
      "verification": "E2E: запись 30 сек → транскрипт.",
      "evidence": null
    },
    {
      "id": "F17-audio-player",
      "title": "Аудио-плеер + переход по таймстемпам",
      "priority": "P1",
      "status": "not_started",
      "depends_on": ["F11-speaker-renaming"],
      "spec": "См. TZ-v2-improvements.md §F17. gr.HTML с кликабельными .time, JS перематывает audio.",
      "verification": "E2E: клик на таймкод → плеер на нужной секунде. Chrome+Safari.",
      "evidence": null
    },
    {
      "id": "F18-transcript-search",
      "title": "Поиск по транскрипту",
      "priority": "P1",
      "status": "not_started",
      "depends_on": ["F17-audio-player"],
      "spec": "См. TZ-v2-improvements.md §F18. JS-фильтрация турнов + подсветка mark.",
      "verification": "Ручной: ввести слово → подсветка работает.",
      "evidence": null
    },
    {
      "id": "F19-ollama-summary",
      "title": "Локальная суммаризация через Ollama",
      "priority": "P1",
      "status": "not_started",
      "depends_on": ["F11-speaker-renaming"],
      "spec": "См. TZ-v2-improvements.md §F19. HTTP-вызов Ollama, парсинг JSON, fallback.",
      "verification": "tests/test_summarize.py с mock + e2e если Ollama стоит.",
      "evidence": null
    },
    {
      "id": "F20-word-timestamps",
      "title": "Word-level timestamps",
      "priority": "P1",
      "status": "not_started",
      "depends_on": ["F03-transcription"],
      "spec": "См. TZ-v2-improvements.md §F20. word_timestamps=True в обоих бэкендах.",
      "verification": "Unit + e2e: words[] непустой в JSON.",
      "evidence": null
    },
    {
      "id": "F21-turbo-model",
      "title": "Опция large-v3-turbo",
      "priority": "P1",
      "status": "not_started",
      "depends_on": ["F03-transcription"],
      "spec": "См. TZ-v2-improvements.md §F21. mlx-community/whisper-large-v3-turbo + faster-whisper нативно.",
      "verification": "E2E: 30-сек файл, time ≤ small × 2.",
      "evidence": null
    },
    {
      "id": "F22-batch",
      "title": "Batch-обработка нескольких файлов",
      "priority": "P2",
      "status": "not_started",
      "depends_on": ["F11", "F12", "F14"],
      "spec": "См. TZ-v2-improvements.md §F22.",
      "verification": "E2E: 2 файла → обе сессии в outputs/.",
      "evidence": null
    },
    {
      "id": "F23-md-frontmatter",
      "title": "MD с YAML frontmatter",
      "priority": "P2",
      "status": "not_started",
      "depends_on": ["F11"],
      "spec": "См. TZ-v2-improvements.md §F23.",
      "verification": "Файл открывается в Obsidian, метаданные распознаны.",
      "evidence": null
    },
    {
      "id": "F24-notifications",
      "title": "Системное уведомление по завершении",
      "priority": "P2",
      "status": "not_started",
      "depends_on": ["F08"],
      "spec": "См. TZ-v2-improvements.md §F24.",
      "verification": "E2E: длинный файл → notification.",
      "evidence": null
    },
    {
      "id": "F25-model-comparison",
      "title": "Сравнение моделей бок о бок",
      "priority": "P2",
      "status": "not_started",
      "depends_on": ["F03-transcription"],
      "spec": "См. TZ-v2-improvements.md §F25.",
      "verification": "E2E: 1-мин файл → 4 строки таблицы.",
      "evidence": null
    },
    {
      "id": "F26-rotation",
      "title": "Ротация outputs и cache",
      "priority": "P2",
      "status": "not_started",
      "depends_on": [],
      "spec": "См. TZ-v2-improvements.md §F26.",
      "verification": "Unit-тест на cleanup_old_outputs с фейковыми mtime.",
      "evidence": null
    }
  ]
}
```

---

## 4. Порядок реализации (граф зависимостей)

Граф зависимостей для P0+P1 (P0 → P1 → P2 строго):

```
F27-cross-platform-ram (bugfix, изолировано)
    ↓
F13-ui-redesign  ────────┐
F15-audio-preprocessing  │  (изолировано от F11/F12, но visible в новом UI)
                         │
F11-speaker-renaming     │
    ↓                    │
F12-meeting-naming       │
    ↓                    │
F14-meeting-history ←────┤
                         │
F28-diar-cache-versioning (bugfix)
F29-duration-limit (reliability)

→ дальше P1:
F17-audio-player → F18-transcript-search
F16-mic-recording (изолировано)
F19-ollama-summary
F20-word-timestamps
F21-turbo-model
```

**Рекомендуемый порядок коммитов (по одной фиче, как в CLAUDE.md):**

1. **F27** — самая дешёвая, отвязная от UI. 30 мин работы.
2. **F11** — фундамент для F12, F14, F17. Самый ценный шаг.
3. **F12** — поверх F11, быстрый.
4. **F13** — масштабный, но изолированный (визуал). Можно делать параллельно с
   F14, если разнести коммиты.
5. **F14** — поверх F11+F12.
6. **F15** — изолированно.
7. **F28, F29** — bugfix'ы, по дороге.
8. **F16, F17, F18, F19, F20, F21** — P1, в любом порядке (минимум зависимостей).
9. **P2** — когда руки дойдут.

---

## 5. Правила работы (повторение из CLAUDE.md)

- **Одна активная фича за раз.** Не открывать F12, пока F11 не `passing`.
- **Перед фичей** — прочитать `claude-progress.md` и `feature_list.json`.
- **Перед каждым коммитом** — `pytest tests/ -q` зелёный.
- **После фичи** — обновить `feature_list.json` (статус + evidence),
  обновить `claude-progress.md` (новая сессия), `session-handoff.md`.
- **Никаких массовых рефакторингов**. Если фича требует переписать модуль —
  заведи отдельную фичу с обоснованием.
- **На все новые модули** (`src/speakers.py`, `src/history.py`, `src/naming.py`,
  `src/summarize.py`) — pytest. Минимум — 3 кейса (happy path + edge + error).

---

## 6. Что НЕ делаем в v2 (явно out of scope)

- Облачные API (OpenAI Whisper API, Anthropic Claude и т.п.) — продукт остаётся
  оффлайн.
- БД для истории встреч (SQLite/Postgres) — файлы в `outputs/` достаточно.
- Real-time транскрибация в реальном времени (live captions) — это другой
  pipeline, отдельный проект.
- Мульти-пользовательский режим, аутентификация — продукт single-user.
- Электрон-сборка `.app/.exe` — Gradio-сервиса в браузере достаточно.

Эти ограничения зафиксированы в README §«Что НЕ делает (по ТЗ)» — не нарушать.

---

## 7. Definition of Done для v2 в целом

v2 считается релизной, когда:
- Все P0 фичи (F11, F12, F13, F14, F15, F27) — `passing` с evidence.
- Все P1 фичи — `passing` ИЛИ `blocked` с задокументированным блокером.
- `README.md` обновлён под новый UI (раздел «UI» переписан).
- Скриншоты нового UI добавлены в README.
- `pytest tests/ -q` зелёный.
- E2E на реальной встрече прогнан (60-минутный файл с 3+ спикерами,
  переименование, резюме, экспорт MD с frontmatter, история встреч работает).

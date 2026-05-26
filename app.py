"""Gradio UI для транскрибатора.

Запуск: `python app.py` → открыть http://127.0.0.1:7860

Архитектура UI:
- Один большой Pipeline-обработчик `transcribe_pipeline()`, который последовательно
  делает: prepare_audio → transcribe → (опц.) diarize → align → export_all.
- Gradio gr.Progress прокидывается через progress_callback в наши модули, чтобы
  индикатор крутился в реальном времени.
- Результаты экспортируются сразу во все 5 форматов, файлы лежат в `outputs/`,
  пользователь может скачать их по кнопкам gr.File.

Почему не разносим на handler-per-format:
- Транскрибация — самое долгое (десятки минут). Делать её отдельно при каждом
  «скачать в JSON» — кощунство. Поэтому считаем один раз, кэшируем результат
  в state и переиспользуем для всех форматов.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# Без line_buffering логи Python копятся в буфере и в фоновом режиме не видны
# до завершения процесса. Для отладки UI нам нужен flush на каждой строке.
sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

# Полноценные логи: INFO в stdout. Подавляем шумные либы.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("transcriber.ui")

import gradio as gr  # type: ignore[import-not-found]  # noqa: E402

from src import (
    alignment,
    audio_utils,
    config,
    device,
    diarization,
    exporters,
    transcription,
)
# F11: модули для переименования спикеров и sidecar meta.json.
# meta переименован в meta_mod, чтобы не конфликтовать с локальной переменной meta
# (TranscriptionMeta), которую возвращает transcription.transcribe().
from src import meta as meta_mod
from src import naming  # F12: slug + build_stem
from src import rotation  # F26: ротация outputs/cache
from src import speakers as speakers_mod

OUTPUTS_DIR = Path(__file__).parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


# ---------- F11 helpers: таблица спикеров и пересохранение ----------

def _build_speaker_rows(
    aligned: list[alignment.AlignedSegment],
    saved_mapping: dict[str, str] | None = None,
) -> list[list[str]]:
    """Готовит rows для gr.Dataframe из aligned-сегментов.

    Каждая строка: [label, display_name, "речь: 42% · 142 реплики"].
    Сортируется по убыванию длительности речи (полезнее для пользователя).
    """
    stats = speakers_mod.compute_stats(aligned)
    if not stats:
        return []
    total = sum(s.speech_seconds for s in stats) or 1.0
    mapping = saved_mapping or {}
    rows: list[list[str]] = []
    for s in stats:
        pct = 100.0 * s.speech_seconds / total
        rows.append([
            s.label,
            mapping.get(s.label, ""),
            f"речь: {pct:.0f}% · {s.turns} реплик",
        ])
    return rows


def _session_state(
    aligned: list[alignment.AlignedSegment],
    stem_path: Path,
    meta_obj: meta_mod.MeetingMeta,
) -> dict[str, Any]:
    """In-memory snapshot для повторного экспорта без прогона моделей.

    Хранит aligned (с ИСХОДНЫМИ SPEAKER_XX, не переименованными), путь к stem
    и meta-объект. Передаётся в gr.State между call'ами.
    """
    return {
        "stem_path": str(stem_path),
        "aligned": [
            {"start": s.start, "end": s.end, "text": s.text, "speaker": s.speaker}
            for s in aligned
        ],
        # MeetingMeta сериализуем через to_dict, чтобы gr.State мог его pickle'ить.
        "meta": meta_obj.to_dict(),
    }


def _restore_aligned(state_dict: dict[str, Any]) -> list[alignment.AlignedSegment]:
    """Обратно из state в AlignedSegment-ы."""
    return [
        alignment.AlignedSegment(
            start=float(s["start"]),
            end=float(s["end"]),
            text=str(s["text"]),
            speaker=str(s["speaker"]),
        )
        for s in state_dict.get("aligned", [])
    ]


def _write_all_exports(
    aligned: list[alignment.AlignedSegment],
    base: Path,
    meta_dict: dict[str, Any],
    detected_language: str | None,
    title_for_md: str,
    speakers_map: dict[str, str] | None = None,
    md_frontmatter: dict[str, Any] | None = None,  # F23
) -> tuple[Path, Path, Path, Path, Path]:
    """Записывает все 5 файлов одним вызовом. Используется и при первом экспорте,
    и при повторном после переименования спикеров (F11 §2).

    md_frontmatter (F23): YAML frontmatter для MD-экспорта. Поля title/date/
    duration_sec/model/language. Если None — MD без frontmatter (legacy).
    """
    txt_path = exporters.write_txt(aligned, base.with_suffix(".txt"), speakers_map=speakers_map)
    srt_path = exporters.write_srt(aligned, base.with_suffix(".srt"), speakers_map=speakers_map)
    vtt_path = exporters.write_vtt(aligned, base.with_suffix(".vtt"), speakers_map=speakers_map)
    # detected_language передан явно, чтобы не зависеть от meta_dict в подписи MD.
    json_path = exporters.write_json(
        aligned, base.with_suffix(".json"),
        meta=meta_dict, speakers_map=speakers_map,
    )
    md_path = exporters.write_md(
        aligned, base.with_suffix(".md"),
        title=title_for_md, speakers_map=speakers_map,
        frontmatter=md_frontmatter,
    )
    return txt_path, srt_path, vtt_path, json_path, md_path


def _total_ram_gb() -> float:
    """Сколько физической RAM на машине. Кросс-платформенно.

    Нужно чтобы подсказать пользователю безопасную конфигурацию: large-v3 +
    диаризация одновременно требует ~5-6 ГБ и на 8-ГБ машине стабильно ловит
    OOM-kill (jetsam на macOS, OutOfMemoryError на Windows).

    Стратегия:
    1. psutil.virtual_memory().total — основной путь, работает на macOS / Windows / Linux.
    2. sysctl hw.memsize — fallback только на macOS если psutil не установлен
       (psutil в наших зависимостях, но мало ли — старая среда, ручная сборка).
    3. 0.0 — если ничего не сработало. Тогда _LOW_RAM = False, защиты не сработают,
       но и приложение не упадёт.
    """
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass  # пробуем fallback ниже

    if sys.platform == "darwin":
        import subprocess
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                check=True, capture_output=True, text=True,
            )
            return int(out.stdout.strip()) / (1024 ** 3)
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
            pass

    # На Windows без psutil — sysctl тоже нет. На Linux то же самое.
    # Возвращаем 0.0 — _LOW_RAM=False, soft-block отключён. Юзер сам разберётся.
    return 0.0


# Порог «низкой» RAM, ниже которого мы переключаем дефолты на лёгкий пресет.
# 12 ГБ — комфортный минимум для large-v3 + диаризации (по ТЗ §7 рекомендовано 16+).
LOW_RAM_THRESHOLD_GB = 12.0

# F21: для turbo-модели порог ниже — она легче large-v3, но не настолько как small.
TURBO_RAM_THRESHOLD_GB = 10.0

# F29: лимиты по длительности файла.
# Soft warning — pyannote держит wav в RAM целиком, на 4+ ч риск OOM растёт.
# Hard block — на 8+ ч мы не видели ни одной разумной встречи; почти наверняка
# это ошибка (зацикленная запись, перепутанный файл).
DURATION_SOFT_WARNING_SEC = 4 * 3600
DURATION_HARD_BLOCK_SEC = 8 * 3600

_TOTAL_RAM_GB = _total_ram_gb()
_LOW_RAM = 0 < _TOTAL_RAM_GB < LOW_RAM_THRESHOLD_GB

# Под 8-ГБ Mac дефолты должны быть консервативные. Иначе пользователь жмёт кнопку
# и ловит OOM-kill через минуту — мы это уже наблюдали.
DEFAULT_MODEL_FOR_UI = "small" if _LOW_RAM else transcription.DEFAULT_MODEL
DEFAULT_DIARIZE_FOR_UI = not _LOW_RAM


# F26: при старте app.py делаем мягкую уборку устаревших групп.
# По дефолту храним 60 дней / 100 групп. Если пользователь хочет другое —
# поправит DEFAULT_RETENTION_DAYS/DEFAULT_MAX_ENTRIES в src/rotation.py.
def _startup_cleanup() -> None:
    try:
        for d in (OUTPUTS_DIR, CACHE_DIR):
            res = rotation.cleanup_outputs(d)
            if res.removed_groups:
                log.info(
                    "rotation %s: removed %d groups (%s)",
                    d.name, res.removed_groups, rotation.human_size(res.removed_bytes),
                )
    except Exception as e:
        # Уборка не должна валить запуск сервиса.
        log.warning("startup cleanup failed (non-fatal): %s", e)


_startup_cleanup()


# ---------- Pipeline ----------

def _run_pipeline(
    audio_file: str | None,
    model_size: str,
    do_diarize: bool,
    num_speakers: int | float | None,
    language_choice: str,
    hf_token_input: str,
    save_token: bool,
    meeting_title: str = "",   # F12: название встречи для slug
    denoise: bool = False,     # F15: шумоподавление + нормализация громкости
    word_timestamps: bool = False,  # F20: word-level timestamps в JSON
    progress: gr.Progress = gr.Progress(),
) -> tuple[str, str, str | None, str | None, str | None, str | None, str | None]:
    """Возвращает кортеж для gr.outputs:
    (preview_text, status, txt_path, srt_path, vtt_path, json_path, md_path).

    Для gr.File outputs возвращаем None если файла нет — пустая строка интерпретируется
    Gradio как «текущая директория» и приводит к IsADirectoryError при кэшировании.
    """
    log.info(
        "click: file=%r model=%s diarize=%s num_speakers=%s lang=%s token_in=%s save=%s",
        audio_file, model_size, do_diarize, num_speakers, language_choice,
        bool(hf_token_input), save_token,
    )

    if not audio_file:
        return ("", "Ошибка: файл не выбран.", None, None, None, None, None, [], {})

    # Soft-block для тяжёлых пресетов на машинах с малым RAM.
    # Не запускаем — потому что OOM-kill происходит молча (jetsam), без feedback
    # пользователю, и просто «теряет» прогресс. Лучше явно сказать и предложить.
    if _LOW_RAM and model_size == "large-v3":
        return (
            "",
            (
                f"⚠️ У тебя {_TOTAL_RAM_GB:.1f} ГБ RAM, а large-v3 + MLX требует ~5 ГБ "
                f"только под веса.\nНа машинах <{LOW_RAM_THRESHOLD_GB:.0f} ГБ macOS "
                "jetsam убьёт процесс сразу после загрузки модели.\n\n"
                "Что делать:\n"
                "  • выбери модель `small`, `medium` или `large-v3-turbo`, или\n"
                "  • выключи диаризацию и перезапусти приложение, чтобы освободить ~1.5 ГБ "
                "под pyannote, или\n"
                "  • закрой Chrome/IDE и попробуй `medium`."
            ),
            None, None, None, None, None, [], {},
        )
    # F21: turbo тоже тяжёлая, но легче large-v3. Порог 10 ГБ.
    if 0 < _TOTAL_RAM_GB < TURBO_RAM_THRESHOLD_GB and model_size == "large-v3-turbo":
        return (
            "",
            (
                f"⚠️ У тебя {_TOTAL_RAM_GB:.1f} ГБ RAM. large-v3-turbo требует "
                f"~{TURBO_RAM_THRESHOLD_GB:.0f} ГБ свободной памяти.\n"
                "Возьми `small` или `medium`."
            ),
            None, None, None, None, None, [], {},
        )
    if _LOW_RAM and do_diarize and model_size in ("medium", "large-v3-turbo"):
        log.warning("%s+diarize on low RAM (%.1f GB) — risky", model_size, _TOTAL_RAM_GB)

    # 0. Сохраняем токен, если просили — независимо от итога транскрибации.
    hf_token = hf_token_input.strip() if hf_token_input else None
    if save_token and hf_token:
        try:
            config.save_hf_token(hf_token)
        except Exception as e:  # сохранение токена — не блокер для транскрибации
            log.warning("save_hf_token failed: %s", e)

    effective_hf_token = hf_token or config.get_hf_token()
    log.info("hf_token resolved: %s", "yes" if effective_hf_token else "no")

    if do_diarize and not effective_hf_token:
        return (
            "",
            (
                "Ошибка: для диаризации нужен HuggingFace токен.\n"
                "Получи на huggingface.co/settings/tokens и прими условия модели "
                "pyannote/speaker-diarization-3.1."
            ),
            None, None, None, None, None, [], {},
        )

    # Прогресс-колбэк, который пишет в Gradio. Подписываем этапы по доле.
    # Этапы: prepare 0..0.05, transcribe 0.05..0.7, diarize 0.7..0.95, export 0.95..1.0
    def _make_progress(stage_lo: float, stage_hi: float, label: str):
        def cb(stage: str, fraction: float, note: str) -> None:
            value = stage_lo + (stage_hi - stage_lo) * fraction
            progress(value, desc=f"{label}: {note}")
        return cb

    prepared: audio_utils.PreparedAudio | None = None
    status_msgs: list[str] = []
    t0 = time.time()

    try:
        progress(0.0, desc="Подготовка аудио…")
        prepared = audio_utils.prepare_audio(audio_file, denoise=denoise)
        denoise_note = " + loudnorm/afftdn" if denoise else ""
        status_msgs.append(
            f"[ok] аудио {prepared.duration_sec:.1f}s "
            f"({prepared.sample_rate} Hz, {prepared.channels} ch){denoise_note}"
        )

        # F29: проверка длительности.
        # Hard block — точно отказываем (вероятно ошибка пользователя).
        # Soft warning — добавляем строку в статус, продолжаем работу.
        if prepared.duration_sec > DURATION_HARD_BLOCK_SEC:
            hours = prepared.duration_sec / 3600
            return (
                "",
                (
                    f"⛔ Файл {hours:.1f} ч — это больше hard-limit "
                    f"({DURATION_HARD_BLOCK_SEC / 3600:.0f} ч).\n"
                    "Скорее всего это ошибка (зацикленная запись или перепутанный файл).\n"
                    "Если это реально нужная запись — разбей её на куски через ffmpeg."
                ),
                None, None, None, None, None, [], {},
            )
        if prepared.duration_sec > DURATION_SOFT_WARNING_SEC:
            hours = prepared.duration_sec / 3600
            status_msgs.append(
                f"[warn] длинный файл ({hours:.1f} ч) — pyannote может упасть по памяти "
                f"на {_TOTAL_RAM_GB:.0f} ГБ RAM"
            )

        # Транскрибация
        lang_arg: str | None
        lang_arg = None if language_choice == "auto" else language_choice
        ws_iter, meta = transcription.transcribe(
            prepared.path,
            model_size=model_size,  # type: ignore[arg-type]
            language=lang_arg,
            audio_duration=prepared.duration_sec,
            progress_callback=_make_progress(0.05, 0.7, "Транскрибация"),
            cache_dir=CACHE_DIR,    # промежуточный дамп + resume на сбое
            word_timestamps=word_timestamps,  # F20
        )
        ws_segments = list(ws_iter)  # потребляем итератор полностью
        status_msgs.append(
            f"[ok] транскрибация: {len(ws_segments)} сегментов, "
            f"язык={meta.detected_language or 'n/a'}"
        )

        # === Освобождение памяти перед диаризацией (нужно для 8-16 ГБ Mac) ===
        # mlx-whisper держит модель в Metal-кэше между вызовами, и если сразу
        # начать грузить pyannote, оба набора весов уживутся в одной unified
        # memory → OOM-kill (мы это наблюдали с large-v3). Полноценное
        # освобождение требует subprocess (см. задача #19), но даже clear_cache +
        # gc.collect снимает несколько сотен МБ — для 8 ГБ это спасает.
        if do_diarize:
            del ws_iter  # закроет генератор и его closure (модель Whisper)
            import gc
            gc.collect()
            # mlx.metal — только на Apple Silicon. На Windows/Linux пропускаем тихо,
            # чтобы не засорять лог warning'ом на каждом запросе. faster-whisper
            # держит модель в CUDA/CPU памяти, и она освобождается при del выше.
            if sys.platform == "darwin":
                try:
                    import mlx.core as mx  # type: ignore[import-not-found]
                    mx.metal.clear_cache()
                    log.info("freed mlx Metal cache before diarization")
                except ImportError:
                    pass  # MLX не установлен — значит fallback на faster-whisper, нечего чистить
                except Exception as e:
                    log.warning("could not clear mlx cache: %s", e)

        # Диаризация (опционально)
        sp_segments: list[diarization.SpeakerSegment] = []
        if do_diarize:
            ns_int: int | None = None
            if num_speakers is not None and float(num_speakers) > 0:
                ns_int = int(num_speakers)
            assert effective_hf_token is not None  # отсеяли выше
            sp_segments = diarization.diarize(
                prepared.path,
                hf_token=effective_hf_token,
                num_speakers=ns_int,
                progress_callback=_make_progress(0.7, 0.95, "Диаризация"),
                cache_dir=CACHE_DIR,  # diar-кэш — спасает от повтора 9-мин pyannote
            )
            status_msgs.append(
                f"[ok] диаризация: {len({s.speaker for s in sp_segments})} спикеров, "
                f"{len(sp_segments)} turns"
            )
        else:
            status_msgs.append("[skip] диаризация выключена")

        # Alignment
        progress(0.95, desc="Сопоставление спикеров…")
        aligned = alignment.align_speakers_with_segments(ws_segments, sp_segments)

        # F12: формируем stem из названия встречи + slug + хэш файла.
        # Если title пустой — slug=untitled, поведение совместимо со старыми
        # стартами (просто менее читаемое имя). Хэш гарантирует уникальность.
        # Берём первые 12 символов fingerprint для надёжности (build_stem обрежет до 6).
        fingerprint = transcription._audio_fingerprint(prepared.path)
        stem = naming.build_stem(meeting_title, fingerprint)
        base = OUTPUTS_DIR / stem

        # Метаданные для JSON-экспорта (внутри файла транскрипции).
        meta_dict = {
            "model_size": meta.model_size,
            "detected_language": meta.detected_language,
            "duration": meta.duration,
            "chunks_total": meta.chunks_total,
            "resumed_from_chunk": meta.resumed_from_chunk,
            "diarized": do_diarize,
        }
        # F11: пишем все 5 экспортов БЕЗ speakers_map (имена ещё не введены).
        # Когда пользователь введёт имена и нажмёт «Применить и пересохранить» —
        # вызовется _apply_speaker_names, который перезапишет файлы с speakers_map.
        title_for_md = meeting_title or f"Transcript ({meta.detected_language or '?'})"
        # F23: YAML frontmatter для MD. duration берём из prepared (то что покажет
        # пользователю), а не из meta.duration (внутренний считает Whisper).
        md_frontmatter = {
            "title": meeting_title or "",
            "date": time.strftime("%Y-%m-%d"),
            "duration_sec": prepared.duration_sec,
            "model": meta.model_size,
            "language": meta.detected_language or "",
        }
        txt_path, srt_path, vtt_path, json_path, md_path = _write_all_exports(
            aligned, base, meta_dict, meta.detected_language, title_for_md,
            speakers_map=None,
            md_frontmatter=md_frontmatter,
        )

        # F11: sidecar .meta.json — единый источник правды по «человеческим» данным.
        # Сейчас display_name пустые; пользователь заполнит через UI-таблицу.
        stats_list = speakers_mod.compute_stats(aligned)
        speakers_meta_dict = {
            s.label: meta_mod.SpeakerMeta(
                display_name="",
                speech_seconds=s.speech_seconds,
                turns=s.turns,
            )
            for s in stats_list
        }
        meeting_meta = meta_mod.MeetingMeta(
            id=stem,
            title=meeting_title or "",  # F12: пишем введённое название в sidecar
            created_at=meta_mod.utc_now_iso(),
            source_file={"name": Path(audio_file).name},
            duration_sec=float(prepared.duration_sec),
            model_size=meta.model_size,
            detected_language=meta.detected_language,
            diarized=do_diarize,
            speakers=speakers_meta_dict,
            files={
                "txt": str(txt_path),
                "srt": str(srt_path),
                "vtt": str(vtt_path),
                "json": str(json_path),
                "md": str(md_path),
            },
        )
        meta_mod.save_meta(base, meeting_meta)

        progress(1.0, desc="Готово")

        # Preview — рендерим TXT (пока без переименования).
        preview = exporters.to_txt(aligned)
        elapsed = time.time() - t0
        status_msgs.append(f"[done] {elapsed:.1f}s")

        speaker_rows = _build_speaker_rows(aligned)
        session = _session_state(aligned, base, meeting_meta)

        return (
            preview,
            "\n".join(status_msgs),
            str(txt_path),
            str(srt_path),
            str(vtt_path),
            str(json_path),
            str(md_path),
            speaker_rows,   # F11: для gr.Dataframe со спикерами
            session,        # F11: для gr.State (re-export без прогона моделей)
        )

    except audio_utils.FFmpegMissingError as e:
        log.exception("ffmpeg missing")
        return ("", f"Ошибка: {e}", None, None, None, None, None, [], {})
    except audio_utils.UnsupportedFormatError as e:
        log.exception("unsupported format")
        return ("", f"Ошибка формата: {e}", None, None, None, None, None, [], {})
    except diarization.HFAuthError as e:
        log.exception("HF auth")
        return ("", f"Ошибка HuggingFace: {e}", None, None, None, None, None, [], {})
    except FileNotFoundError as e:
        log.exception("file not found")
        return ("", f"Файл не найден: {e}", None, None, None, None, None, [], {})
    except Exception as e:
        # Любая нелокализованная ошибка — в UI с полным traceback.
        log.exception("pipeline failed")
        tb = traceback.format_exc(limit=8)
        return ("", f"Неожиданная ошибка: {e}\n\n{tb}", None, None, None, None, None, [], {})
    finally:
        if prepared is not None:
            audio_utils.cleanup(prepared)


def _render_storage_status() -> str:
    """F26: красивая строка с занимаемым местом для UI accordion."""
    out_bytes, out_files = rotation.folder_stats(OUTPUTS_DIR)
    cache_bytes, cache_files = rotation.folder_stats(CACHE_DIR)
    return (
        f"📂 **outputs/** — {rotation.human_size(out_bytes)} · {out_files} файлов  \n"
        f"📦 **cache/** — {rotation.human_size(cache_bytes)} · {cache_files} файлов  \n"
        f"_Авто-ротация: {rotation.DEFAULT_RETENTION_DAYS} дней / "
        f"{rotation.DEFAULT_MAX_ENTRIES} групп._"
    )


# ---------- F11: re-export после переименования спикеров ----------

def _apply_speaker_names(
    rows: list[list[str]] | Any,
    state: dict[str, Any],
) -> tuple[str, str, str | None, str | None, str | None, str | None, str | None]:
    """Перезаписать 5 экспортов с подменой SPEAKER_XX на введённые имена + обновить .meta.json.

    rows: список [label, name, stats_str] из gr.Dataframe.
    state: dict с aligned, stem_path, meta.

    Возвращает: (preview, status, txt, srt, vtt, json, md).
    """
    # gr.Dataframe может прислать pandas.DataFrame или list[list[str]] — нормализуем.
    if rows is None or (hasattr(rows, "empty") and rows.empty):
        return ("", "Нет данных. Сначала запусти транскрибацию.", None, None, None, None, None)

    if hasattr(rows, "values"):
        rows_list = rows.values.tolist()
    else:
        rows_list = list(rows)

    if not state or "aligned" not in state:
        return ("", "Состояние сессии пустое. Запусти транскрибацию заново.",
                None, None, None, None, None)

    # Собираем mapping из rows. Пустые имена → не маппим (фолбэк на SPEAKER_XX).
    raw_mapping: dict[str, str] = {}
    for row in rows_list:
        if not row or len(row) < 2:
            continue
        label = str(row[0]).strip()
        name = str(row[1]).strip() if row[1] is not None else ""
        if label:
            raw_mapping[label] = name
    mapping = speakers_mod.normalize_mapping(raw_mapping)
    log.info("apply speaker names: %d mappings", len(mapping))

    # Восстанавливаем aligned и пути.
    aligned = _restore_aligned(state)
    base = Path(state["stem_path"])
    meta_obj = meta_mod.MeetingMeta.from_dict(state.get("meta", {}))

    # Обновляем display_name в meeting_meta.
    for label, spk in meta_obj.speakers.items():
        spk.display_name = mapping.get(label, "")

    # Перезаписываем 5 файлов. meta_dict для JSON-экспорта берём из MeetingMeta.
    inner_meta = {
        "model_size": meta_obj.model_size,
        "detected_language": meta_obj.detected_language,
        "duration": meta_obj.duration_sec,
        "diarized": meta_obj.diarized,
    }
    title_for_md = meta_obj.title or f"Transcript ({meta_obj.detected_language or '?'})"
    # F23: при повторном экспорте сохраняем frontmatter с теми же полями.
    md_frontmatter = {
        "title": meta_obj.title or "",
        "date": (meta_obj.created_at or "")[:10],  # YYYY-MM-DD из ISO 'YYYY-MM-DDTHH:MM:SSZ'
        "duration_sec": meta_obj.duration_sec,
        "model": meta_obj.model_size,
        "language": meta_obj.detected_language or "",
    }
    try:
        txt_path, srt_path, vtt_path, json_path, md_path = _write_all_exports(
            aligned, base, inner_meta, meta_obj.detected_language, title_for_md,
            speakers_map=mapping or None,
            md_frontmatter=md_frontmatter,
        )
    except Exception as e:
        log.exception("re-export failed")
        return ("", f"Ошибка при пересохранении: {e}", None, None, None, None, None)

    # Обновляем meta.json sidecar.
    meta_obj.files = {
        "txt": str(txt_path), "srt": str(srt_path), "vtt": str(vtt_path),
        "json": str(json_path), "md": str(md_path),
    }
    try:
        meta_mod.save_meta(base, meta_obj)
    except OSError as e:
        log.warning("save_meta failed: %s", e)
        # Не фатально — экспорты записаны.

    # Превью с применёнными именами.
    preview = exporters.to_txt(aligned, speakers_map=mapping or None)
    applied = ", ".join(f"{k}→{v}" for k, v in mapping.items()) or "пусто"
    status = f"✓ Пересохранено с маппингом: {applied}"

    return (
        preview, status,
        str(txt_path), str(srt_path), str(vtt_path), str(json_path), str(md_path),
    )


# ---------- UI ----------

def _masked_token(token: str) -> str:
    """Замаскированный вид токена для отображения: первые 6 + последние 4."""
    if not token:
        return ""
    if len(token) <= 10:
        return "•" * len(token)
    return f"{token[:6]}…{token[-4:]}"


def _token_status_line() -> str:
    """Markdown-строка про сохранённый HF-токен для шапки UI.

    Считывается на каждый build_ui() — если пользователь удалил/обновил токен,
    обновится после рестарта сервиса.
    """
    saved = config.get_hf_token()
    if saved:
        return f"🔑 **HF токен:** сохранён (`{_masked_token(saved)}`)."
    return "🔑 **HF токен:** не сохранён — диаризация работать не будет."


def build_ui() -> gr.Blocks:
    dev_info = device.get_device_info()

    ram_line = (
        f"**RAM:** {_TOTAL_RAM_GB:.1f} ГБ"
        + (
            f" — _мало для large-v3 (нужно ≥{LOW_RAM_THRESHOLD_GB:.0f} ГБ). "
            "Дефолты выставлены на `small` без диаризации._"
            if _LOW_RAM
            else ""
        )
    )
    token_line = _token_status_line()

    with gr.Blocks(title="Локальный транскрибатор") as demo:
        gr.Markdown(
            f"""
            # Локальный транскрибатор (offline)

            mlx-whisper + pyannote.audio. Всё считается на вашем Mac.

            **Устройство:** pyannote → `{dev_info.pyannote_device}`,
            whisper → `{dev_info.whisper_backend}` (Metal через MLX).
            {ram_line}
            {token_line}
            _{dev_info.note}_
            """
        )

        with gr.Row():
            with gr.Column(scale=2):
                # F12: название встречи — над зоной загрузки, как требует ТЗ.
                # Пустое поле допустимо: тогда slug = "untitled", файлы всё равно
                # уникальны благодаря хэшу аудио в имени.
                title_in = gr.Textbox(
                    label="Название встречи (опционально)",
                    placeholder="напр. Планирование Q3",
                    value="",
                )
                audio_in = gr.File(
                    label="Аудио или видео (mp3/wav/m4a/flac/ogg/opus/aac/mp4/mov/mkv/avi/webm)",
                    file_types=[
                        ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac",
                        ".mp4", ".mov", ".mkv", ".avi", ".webm",
                    ],
                    type="filepath",
                )
                model_in = gr.Dropdown(
                    label="Модель Whisper",
                    choices=list(transcription.SUPPORTED_MODELS),
                    value=DEFAULT_MODEL_FOR_UI,
                )
                language_in = gr.Dropdown(
                    label="Язык",
                    choices=["auto", "ru", "en"],
                    value="auto",
                )
                with gr.Row():
                    diarize_in = gr.Checkbox(
                        label="Диаризация (разделение по спикерам)",
                        value=DEFAULT_DIARIZE_FOR_UI,
                    )
                    num_speakers_in = gr.Number(
                        label="Сколько спикеров (0 = авто)",
                        value=0,
                        precision=0,
                    )
                # F15: шумоподавление + нормализация громкости.
                # По умолчанию выключено — на чистых записях afftdn может убрать
                # тихих спикеров; включай только для реально шумных файлов.
                denoise_in = gr.Checkbox(
                    label="Шумоподавление + нормализация громкости",
                    value=False,
                    info="ffmpeg loudnorm + afftdn. Помогает Whisper'у на шумных/тихих записях. +~50% времени конвертации.",
                )
                # F20: word-level timestamps. Off by default — +5-10% времени и памяти.
                word_ts_in = gr.Checkbox(
                    label="Слова с таймкодами (word-level timestamps)",
                    value=False,
                    info="Каждое слово получит свой start/end в .json. Замедляет на 5-10%.",
                )
                with gr.Accordion("HuggingFace токен (нужен для диаризации)", open=False):
                    has_token = config.has_hf_token()
                    saved_preview = _masked_token(config.get_hf_token() or "")
                    gr.Markdown(
                        "Токен берётся в порядке: поле ниже → env `HF_TOKEN` → "
                        "`~/.config/transcriber/config.json`.\n\n"
                        + (
                            f"✅ Сохранённый токен: `{saved_preview}`\n\n"
                            "_Поле ниже можно оставить пустым — токен возьмётся "
                            "из файла. Заполни, только если хочешь сменить токен._"
                            if has_token
                            else "⚠️ Сохранённого токена нет — введи в поле ниже "
                                 "и поставь галку «Запомнить»."
                        )
                    )
                    token_in = gr.Textbox(
                        label="HF Token",
                        placeholder="hf_… (оставь пустым если уже сохранён)",
                        type="password",
                    )
                    save_token_in = gr.Checkbox(
                        label="Запомнить токен в ~/.config/transcriber/config.json",
                        value=True,
                    )
                    if has_token:
                        clear_token_btn = gr.Button(
                            "Удалить сохранённый токен",
                            variant="stop",
                            size="sm",
                        )
                        clear_token_status = gr.Markdown("")

                        def _clear_token() -> str:
                            try:
                                config.save_hf_token("")
                                log.info("hf_token cleared from config")
                                return (
                                    "🗑 Токен удалён из `~/.config/transcriber/config.json`. "
                                    "Перезагрузи страницу, чтобы шапка обновилась."
                                )
                            except Exception as e:
                                log.exception("clear_token failed")
                                return f"Ошибка при удалении: {e}"

                        clear_token_btn.click(fn=_clear_token, outputs=[clear_token_status])

                run_btn = gr.Button("Транскрибировать", variant="primary")

            with gr.Column(scale=3):
                status_out = gr.Textbox(label="Статус", lines=6, interactive=False)
                preview_out = gr.Textbox(label="Результат", lines=20, interactive=False)

        # F11: in-memory снапшот сессии (aligned + meta), чтобы re-export не требовал
        # повторного прогона моделей. gr.State хранит произвольный dict между call'ами.
        session_state = gr.State({})

        # F11: таблица спикеров со статистикой.
        gr.Markdown("### Спикеры")
        gr.Markdown(
            "_Заполни имена и нажми «Применить и пересохранить» — все 5 файлов "
            "перезапишутся с новыми именами. Пустые поля оставят машинную метку. "
            "Одинаковые имена для разных меток = объединение спикера (полезно, если "
            "pyannote разделил одного человека)._"
        )
        speakers_table = gr.Dataframe(
            headers=["Метка", "Имя", "Статистика"],
            datatype=["str", "str", "str"],
            col_count=(3, "fixed"),
            interactive=True,
            wrap=True,
        )
        apply_btn = gr.Button("Применить и пересохранить", variant="secondary")

        gr.Markdown("### Скачать результат")
        with gr.Row():
            txt_out = gr.File(label="TXT")
            srt_out = gr.File(label="SRT")
            vtt_out = gr.File(label="VTT")
            json_out = gr.File(label="JSON")
            md_out = gr.File(label="MD")

        # F26: управление местом — accordion (по умолчанию свёрнут).
        with gr.Accordion("Управление местом (outputs/ и cache/)", open=False):
            storage_status = gr.Markdown(_render_storage_status())
            cleanup_btn = gr.Button("Очистить старые", size="sm")

            def _do_cleanup() -> str:
                """Запустить ротацию вручную и обновить статус."""
                try:
                    res_out = rotation.cleanup_outputs(OUTPUTS_DIR)
                    res_cache = rotation.cleanup_outputs(CACHE_DIR)
                    total_removed = res_out.removed_groups + res_cache.removed_groups
                    total_bytes = res_out.removed_bytes + res_cache.removed_bytes
                    log.info(
                        "manual cleanup: outputs -%d (%d B), cache -%d (%d B)",
                        res_out.removed_groups, res_out.removed_bytes,
                        res_cache.removed_groups, res_cache.removed_bytes,
                    )
                    return (
                        f"Удалено {total_removed} групп ({rotation.human_size(total_bytes)}).\n\n"
                        + _render_storage_status()
                    )
                except Exception as e:
                    log.exception("manual cleanup failed")
                    return f"Ошибка при уборке: {e}\n\n" + _render_storage_status()

            cleanup_btn.click(fn=_do_cleanup, outputs=[storage_status])

        run_btn.click(
            fn=_run_pipeline,
            inputs=[
                audio_in,
                model_in,
                diarize_in,
                num_speakers_in,
                language_in,
                token_in,
                save_token_in,
                title_in,    # F12: meeting_title
                denoise_in,  # F15: denoise flag
                word_ts_in,  # F20: word-level timestamps
            ],
            outputs=[
                preview_out,
                status_out,
                txt_out,
                srt_out,
                vtt_out,
                json_out,
                md_out,
                speakers_table,  # F11: rows для таблицы спикеров
                session_state,   # F11: dict со snapshot сессии
            ],
        )

        apply_btn.click(
            fn=_apply_speaker_names,
            inputs=[speakers_table, session_state],
            outputs=[
                preview_out, status_out,
                txt_out, srt_out, vtt_out, json_out, md_out,
            ],
        )

    return demo


def main() -> None:
    host = os.environ.get("GRADIO_HOST", "127.0.0.1")
    port = int(os.environ.get("GRADIO_PORT", "7860"))
    demo = build_ui()
    # show_error=True — стек ошибки в UI, удобно при отладке.
    # max_threads=4 — pyannote и whisper и так блокирующие; параллельных запросов
    # с одного браузера обычно нет.
    demo.queue(max_size=8).launch(
        server_name=host,
        server_port=port,
        show_error=True,
        inbrowser=True,
    )


if __name__ == "__main__":
    main()

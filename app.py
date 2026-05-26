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

OUTPUTS_DIR = Path(__file__).parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


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

_TOTAL_RAM_GB = _total_ram_gb()
_LOW_RAM = 0 < _TOTAL_RAM_GB < LOW_RAM_THRESHOLD_GB

# Под 8-ГБ Mac дефолты должны быть консервативные. Иначе пользователь жмёт кнопку
# и ловит OOM-kill через минуту — мы это уже наблюдали.
DEFAULT_MODEL_FOR_UI = "small" if _LOW_RAM else transcription.DEFAULT_MODEL
DEFAULT_DIARIZE_FOR_UI = not _LOW_RAM


# ---------- Pipeline ----------

def _run_pipeline(
    audio_file: str | None,
    model_size: str,
    do_diarize: bool,
    num_speakers: int | float | None,
    language_choice: str,
    hf_token_input: str,
    save_token: bool,
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
        return ("", "Ошибка: файл не выбран.", None, None, None, None, None)

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
                "  • выбери модель `small` или `medium`, или\n"
                "  • выключи диаризацию и перезапусти приложение, чтобы освободить ~1.5 ГБ "
                "под pyannote, или\n"
                "  • закрой Chrome/IDE и попробуй `medium`."
            ),
            None, None, None, None, None,
        )
    if _LOW_RAM and do_diarize and model_size == "medium":
        log.warning("medium+diarize on low RAM (%.1f GB) — risky", _TOTAL_RAM_GB)

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
            None, None, None, None, None,
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
        prepared = audio_utils.prepare_audio(audio_file)
        status_msgs.append(
            f"[ok] аудио {prepared.duration_sec:.1f}s "
            f"({prepared.sample_rate} Hz, {prepared.channels} ch)"
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

        # Экспорт во все форматы. Имя по timestamp — чтобы файлы не перезаписывали друг друга.
        stem = f"transcript_{int(time.time())}"
        base = OUTPUTS_DIR / stem

        # Метаданные для JSON-экспорта. Поля синхронизированы с TranscriptionMeta
        # из mlx-версии transcription.py (language_probability там нет — убран,
        # т.к. mlx-whisper его не возвращает; вместо него поля chunks_total и
        # resumed_from_chunk показывают как считалось).
        meta_dict = {
            "model_size": meta.model_size,
            "detected_language": meta.detected_language,
            "duration": meta.duration,
            "chunks_total": meta.chunks_total,
            "resumed_from_chunk": meta.resumed_from_chunk,
            "diarized": do_diarize,
        }
        txt_path = exporters.write_txt(aligned, base.with_suffix(".txt"))
        srt_path = exporters.write_srt(aligned, base.with_suffix(".srt"))
        vtt_path = exporters.write_vtt(aligned, base.with_suffix(".vtt"))
        json_path = exporters.write_json(aligned, base.with_suffix(".json"), meta=meta_dict)
        md_path = exporters.write_md(aligned, base.with_suffix(".md"),
                                     title=f"Transcript ({meta.detected_language or '?'})")

        progress(1.0, desc="Готово")

        # Preview — рендерим TXT-результат прямо в UI.
        preview = exporters.to_txt(aligned)
        elapsed = time.time() - t0
        status_msgs.append(f"[done] {elapsed:.1f}s")

        return (
            preview,
            "\n".join(status_msgs),
            str(txt_path),
            str(srt_path),
            str(vtt_path),
            str(json_path),
            str(md_path),
        )

    except audio_utils.FFmpegMissingError as e:
        log.exception("ffmpeg missing")
        return ("", f"Ошибка: {e}", None, None, None, None, None)
    except audio_utils.UnsupportedFormatError as e:
        log.exception("unsupported format")
        return ("", f"Ошибка формата: {e}", None, None, None, None, None)
    except diarization.HFAuthError as e:
        log.exception("HF auth")
        return ("", f"Ошибка HuggingFace: {e}", None, None, None, None, None)
    except FileNotFoundError as e:
        log.exception("file not found")
        return ("", f"Файл не найден: {e}", None, None, None, None, None)
    except Exception as e:
        # Любая нелокализованная ошибка — в UI с полным traceback.
        log.exception("pipeline failed")
        tb = traceback.format_exc(limit=8)
        return ("", f"Неожиданная ошибка: {e}\n\n{tb}", None, None, None, None, None)
    finally:
        if prepared is not None:
            audio_utils.cleanup(prepared)


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

        gr.Markdown("### Скачать результат")
        with gr.Row():
            txt_out = gr.File(label="TXT")
            srt_out = gr.File(label="SRT")
            vtt_out = gr.File(label="VTT")
            json_out = gr.File(label="JSON")
            md_out = gr.File(label="MD")

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
            ],
            outputs=[
                preview_out,
                status_out,
                txt_out,
                srt_out,
                vtt_out,
                json_out,
                md_out,
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

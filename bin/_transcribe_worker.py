"""Изолированный subprocess для транскрибации (Task #31, F26b).

Зачем отдельный процесс:
- mlx-whisper + pyannote.audio + torch в одном процессе пиково едят ~3-4 ГБ.
- На 8 ГБ Mac (вместе с macOS, Chrome, IDE) это даёт jetsam-OOM.
- В одном процессе del/gc.collect/mx.metal.clear_cache НЕ освобождает unified
  memory полностью — Metal allocator держит пулы.
- Запуск whisper в отдельном Python-процессе → его смерть освобождает ВСЁ.
  Дальше pyannote стартует с чистого листа в основном процессе.

API:
  python _transcribe_worker.py \\
      --audio /path/to/audio.wav \\
      --model medium \\
      --language ru \\
      --chunk-seconds 600 \\
      --cache-dir /path/to/cache \\
      --word-timestamps 0 \\
      --output /tmp/result.json

Output (output JSON):
  {
    "segments": [{"start", "end", "text", "words"?}, ...],
    "language": "ru",
    "duration": 3600.0,
    "model_size": "medium",
    "chunks_total": 6,
    "resumed_from_chunk": 0
  }

Прогресс:
  stdout получает строки `[idx/total] note` после каждого чанка — основной
  процесс может их читать и обновлять Gradio progress.

Ошибки:
  Любая ошибка → stderr + exit code != 0. Основной процесс показывает в UI.
"""

from __future__ import annotations

# КРИТИЧНО: KMP_DUPLICATE_LIB_OK ДО любого ML импорта — иначе OMP Error #15.
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Делаем src/ доступным для импорта без установки пакета.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe worker (subprocess)")
    parser.add_argument("--audio", required=True, help="WAV 16 kHz mono")
    parser.add_argument("--model", required=True, help="tiny/base/small/medium/large-v3/large-v3-turbo")
    parser.add_argument("--language", default=None, help="ru/en/None")
    parser.add_argument("--chunk-seconds", type=int, default=600)
    parser.add_argument("--cache-dir", default=None)
    # Принимаем как int (0/1). type=int парсит «0» → 0, «1» → 1, и `bool(0)=False`.
    # Раньше был implicit cast str→bool, который давал True для любой непустой строки.
    parser.add_argument("--word-timestamps", type=int, default=0, choices=[0, 1])
    parser.add_argument("--output", required=True, help="path to JSON with result")
    args = parser.parse_args()

    # Импорт transcribe — внутрь main, чтобы при argparse-ошибке не тянуть тяжесть.
    # Также — проверим что mlx_whisper доступен (subprocess может быть запущен
    # не из venv, например с системным python — тогда понятная ошибка лучше
    # чем загадочный ImportError позже).
    try:
        from src.transcription import transcribe
    except ImportError as e:
        print(f"[error] не могу импортировать transcribe: {e}", file=sys.stderr, flush=True)
        print("[error] subprocess запущен не из venv? sys.executable=" + sys.executable,
              file=sys.stderr, flush=True)
        return 1

    try:
        # progress_callback в transcribe принимает (stage, fraction, note).
        # Мы используем его, чтобы понять когда заканчивается чанк, и печатаем
        # '[chunk] idx/total' — родитель парсит это для Gradio progress.
        chunks_total_holder = {"value": 0}
        last_chunk_idx = {"value": -1}

        def _progress_cb(stage: str, fraction: float, note: str) -> None:
            # transcription.transcribe вызывает callback с stage='transcribe_chunk_done'
            # после каждого чанка (см. src/transcription.py). Мы используем это
            # как сигнал «чанк готов». chunks_total получим из meta после старта.
            if stage == "transcribe_chunk_done":
                total = chunks_total_holder["value"] or 1
                idx = int(round(fraction * total))
                if idx > last_chunk_idx["value"]:
                    last_chunk_idx["value"] = idx
                    print(f"[chunk] {idx}/{total}", flush=True)

        ws_iter, meta = transcribe(
            args.audio,
            model_size=args.model,  # type: ignore[arg-type]
            language=args.language if args.language and args.language != "auto" else None,
            chunk_seconds=args.chunk_seconds,
            cache_dir=args.cache_dir,
            word_timestamps=bool(args.word_timestamps),
            progress_callback=_progress_cb,
        )
        # Теперь когда meta готова — знаем сколько всего чанков.
        chunks_total_holder["value"] = meta.chunks_total

        segments = []
        for s in ws_iter:
            seg_dict = {
                "start": float(s.start),
                "end": float(s.end),
                "text": s.text,
            }
            # words — кортеж dataclasses; asdict сериализует.
            if s.words:
                seg_dict["words"] = [asdict(w) for w in s.words]
            segments.append(seg_dict)

        result = {
            "segments": segments,
            "language": meta.detected_language,
            "duration": meta.duration,
            "model_size": meta.model_size,
            "chunks_total": meta.chunks_total,
            "resumed_from_chunk": meta.resumed_from_chunk,
        }

        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8",
        )
        print(f"[done] {len(segments)} сегментов → {args.output}", flush=True)
        return 0

    except Exception as e:  # noqa: BLE001
        # Записываем ошибку и в stderr (для родителя), и в выходной JSON
        # (на случай если родитель ждёт файл независимо от exit code).
        err_msg = f"{type(e).__name__}: {e}"
        print(f"[error] {err_msg}", file=sys.stderr, flush=True)
        try:
            Path(args.output).write_text(
                json.dumps({"error": err_msg}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())

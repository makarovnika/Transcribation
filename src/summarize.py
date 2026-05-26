"""Локальное резюмирование транскрипта через Ollama (F19 ТЗ v2).

Зачем:
- Whisper выдаёт сырые реплики, человек должен их пересказать.
- Ollama даёт LLM локально, без облака и токенов — это вписывается в принцип
  «никаких облачных API» (см. ТЗ §1.3, §7 v1).

Контракт:
- Вход: список AlignedSegment + опциональный speakers_map.
- Выход: SummaryResult с полями tldr / decisions / action_items.
- Ошибки: SummarizeError — единая для всех сетевых/парсинг проблем,
  с понятным сообщением что делать.

Зависимости: httpx (уже в venv через huggingface_hub).

Тесты: mock httpx-ответа в test_summarize.py. Реальный Ollama не требуется
для CI — только для e2e локально.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .alignment import AlignedSegment

log = logging.getLogger("transcriber.summarize")

OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1:8b"

# Лимит на длину prompt — слишком большие транскрипты ломают контекст LLM.
# 100 KB — это ~25 тыс. токенов на ~100к символов; llama 3.1 8b держит 128k токенов,
# но качество резко падает. Если транскрипт длиннее — обрезаем с конца.
MAX_TRANSCRIPT_CHARS = 100_000

DEFAULT_TIMEOUT_SEC = 120.0  # на длинном транскрипте LLM отвечает >30 сек.


class SummarizeError(Exception):
    """Базовая ошибка модуля. Сообщение — в UI как есть, на русском."""


@dataclass(frozen=True)
class SummaryResult:
    """Распарсенный ответ Ollama.

    tldr — один абзац-резюме.
    decisions — список строк-решений.
    action_items — список строк формата '{кто}: {что} [дедлайн]'.
    raw_text — оригинальный ответ модели (на случай если парсинг частично сломался).
    """

    tldr: str
    decisions: tuple[str, ...] = ()
    action_items: tuple[str, ...] = ()
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tldr": self.tldr,
            "decisions": list(self.decisions),
            "action_items": list(self.action_items),
        }


_PROMPT_TEMPLATE = """Ты помогаешь сократить транскрипт встречи. На вход — реплики с метками
спикеров. На выход — JSON со следующими полями:
  - "tldr": один абзац (3-5 предложений), о чём была встреча.
  - "decisions": список принятых решений.
  - "action_items": список вида "{{кто}}: {{что сделать}} [{{дедлайн или null}}]".
Отвечай только JSON, без markdown-fences.

Транскрипт:
{transcript}"""


def _resolve_speaker(speaker: str, mapping: dict[str, str] | None) -> str:
    """Display-имя для prompt — берём mapping если есть, иначе оригинальную метку."""
    if mapping:
        v = mapping.get(speaker, "")
        if v and v.strip():
            return v.strip()
    return speaker


def render_transcript(
    segments: list[AlignedSegment],
    speakers_map: dict[str, str] | None = None,
    max_chars: int = MAX_TRANSCRIPT_CHARS,
) -> str:
    """Сжать сегменты в plain-text reply-style для prompt LLM.

    Формат: `[Никита]: текст`. Соседние реплики одного спикера объединяем —
    меньше токенов на меток.
    """
    lines: list[str] = []
    current_speaker: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if current_speaker is not None and buf:
            lines.append(f"[{current_speaker}]: {' '.join(buf).strip()}")

    for seg in segments:
        display = _resolve_speaker(seg.speaker, speakers_map)
        if display != current_speaker:
            flush()
            current_speaker = display
            buf = []
        t = seg.text.strip()
        if t:
            buf.append(t)
    flush()

    text = "\n".join(lines)
    if len(text) > max_chars:
        # Обрезаем с КОНЦА — начало встречи обычно содержит представление,
        # это полезно для понимания контекста. Лучше пропустить хвост.
        text = text[:max_chars]
        # Не режем посреди реплики — обрезаем по последнему \n.
        last_nl = text.rfind("\n")
        if last_nl > 0:
            text = text[:last_nl]
        text += "\n[...остальные реплики обрезаны из-за лимита контекста]"
    return text


def _extract_json(raw: str) -> dict[str, Any]:
    """Достать JSON-объект из ответа LLM. Терпимо к markdown-fences и шуму.

    1. Пробуем json.loads напрямую.
    2. Иначе ищем первое `{...}` с балансировкой скобок.
    3. Иначе SummarizeError.
    """
    raw = raw.strip()
    # Срезать ```json ... ``` если модель вернула fence.
    if raw.startswith("```"):
        raw = re.sub(r"^```(json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Поиск первого сбалансированного { ... }.
    start = raw.find("{")
    if start < 0:
        raise SummarizeError("Ответ LLM не содержит JSON")
    depth = 0
    for i in range(start, len(raw)):
        c = raw[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = raw[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as e:
                    raise SummarizeError(
                        f"Не смог распарсить JSON из ответа LLM: {e}"
                    ) from e
    raise SummarizeError("В ответе LLM незакрытая JSON-скобка")


def _to_str_list(v: Any) -> tuple[str, ...]:
    """LLM иногда возвращает не строки, а dict с полями — нормализуем."""
    if isinstance(v, list):
        out: list[str] = []
        for item in v:
            if isinstance(item, str):
                out.append(item.strip())
            elif isinstance(item, dict):
                # На случай {who, what, due_date}.
                parts = [str(item.get(k, "")) for k in ("who", "what", "due_date")
                         if item.get(k)]
                out.append(" — ".join(parts) if parts else json.dumps(item, ensure_ascii=False))
            else:
                out.append(str(item))
        return tuple(s for s in out if s)
    if isinstance(v, str):
        # Иногда LLM возвращает многострочный текст вместо списка.
        return tuple(line.strip("- ").strip() for line in v.split("\n") if line.strip())
    return ()


def parse_summary(raw_text: str) -> SummaryResult:
    """Прогон JSON-парсинга + нормализация полей."""
    data = _extract_json(raw_text)
    if not isinstance(data, dict):
        raise SummarizeError("Корень JSON — не объект")

    tldr_raw = data.get("tldr", "")
    tldr = str(tldr_raw).strip() if tldr_raw else ""
    decisions = _to_str_list(data.get("decisions"))
    action_items = _to_str_list(data.get("action_items"))

    return SummaryResult(
        tldr=tldr,
        decisions=decisions,
        action_items=action_items,
        raw_text=raw_text,
    )


def summarize_transcript(
    segments: list[AlignedSegment],
    *,
    speakers_map: dict[str, str] | None = None,
    model: str | None = None,
    host: str = OLLAMA_HOST,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> SummaryResult:
    """End-to-end: рендер транскрипта → POST в Ollama → парс JSON.

    Raises:
        SummarizeError: если Ollama не отвечает / отдаёт не-JSON / etc.
            Сообщение пригодно для прямого вывода в UI.
    """
    if not segments:
        raise SummarizeError("Транскрипт пуст — резюмировать нечего.")

    chosen_model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL
    prompt = _PROMPT_TEMPLATE.format(
        transcript=render_transcript(segments, speakers_map),
    )

    # httpx — синхронный, без stream'а ради простоты. На длинном транскрипте
    # ждём timeout сек. Gradio progress снаружи покажет что мы «крутимся».
    import httpx
    log.info("summarize: model=%s host=%s prompt_chars=%d", chosen_model, host, len(prompt))
    try:
        resp = httpx.post(
            f"{host}/api/generate",
            json={"model": chosen_model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
    except httpx.ConnectError as e:
        raise SummarizeError(
            "Ollama не отвечает на 127.0.0.1:11434.\n"
            "Поставь и запусти:\n"
            "  brew install ollama (или скачать с ollama.com)\n"
            "  ollama serve\n"
            f"  ollama pull {chosen_model}"
        ) from e
    except httpx.TimeoutException as e:
        raise SummarizeError(
            f"Ollama не ответил за {timeout:.0f} сек. "
            "Возможно, модель ещё грузится — попробуй через минуту."
        ) from e
    except httpx.HTTPError as e:
        raise SummarizeError(f"Сетевая ошибка к Ollama: {e}") from e

    if resp.status_code != 200:
        raise SummarizeError(
            f"Ollama ответил HTTP {resp.status_code}: {resp.text[:300]}"
        )

    try:
        payload = resp.json()
    except json.JSONDecodeError as e:
        raise SummarizeError(f"Ollama вернул не-JSON: {e}") from e

    raw_text = str(payload.get("response", "")).strip()
    if not raw_text:
        raise SummarizeError("Ollama вернул пустой response.")

    return parse_summary(raw_text)

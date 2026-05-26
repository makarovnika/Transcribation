"""Хранение пользовательских настроек: пока только HuggingFace токен.

Где храним и почему:
- ~/.config/transcriber/config.json — XDG-подобное место, не в репо.
- Права файла 0600 — токен не должен читаться другими пользователями системы.

Порядок поиска токена (в порядке убывания приоритета):
1. Аргумент функции — для теста/админ-режима.
2. ENV: HF_TOKEN — удобно для разовых запусков.
3. config.json — то, что юзер один раз ввёл в UI.
4. None — UI должен показать пользователю, что токена нет.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "transcriber"
CONFIG_FILE = CONFIG_DIR / "config.json"

HF_TOKEN_ENV_VAR = "HF_TOKEN"


def _ensure_config_dir() -> None:
    """Создаём ~/.config/transcriber, если ещё нет. mode 0700 — только владельцу."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # На некоторых FS (например, exfat) chmod не работает — это не критично.
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def load_config() -> dict[str, str]:
    """Прочитать config.json. Если нет / битый — вернуть {}.

    Сознательно «прощаем» битый файл: для оффлайн-инструмента важнее не упасть
    при старте, чем строго валидировать схему. Юзер просто введёт токен заново.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            # Преобразуем значения в str — на случай старого формата.
            return {str(k): str(v) for k, v in data.items() if v is not None}
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(data: dict[str, str]) -> None:
    """Перезаписать config.json. Делает chmod 0600."""
    _ensure_config_dir()
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # rename атомарный — защищает от полуписанного файла при kill -9.
    tmp.replace(CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass


def get_hf_token(explicit: str | None = None) -> str | None:
    """Вернуть HF-токен по правилам приоритета. None если нигде нет."""
    if explicit:
        return explicit.strip() or None

    env = os.environ.get(HF_TOKEN_ENV_VAR)
    if env:
        return env.strip() or None

    cfg = load_config()
    tok = cfg.get("hf_token")
    return tok.strip() if tok else None


def save_hf_token(token: str) -> None:
    """Сохранить токен в config.json. Пустой токен — стираем поле."""
    cfg = load_config()
    token = (token or "").strip()
    if token:
        cfg["hf_token"] = token
    else:
        cfg.pop("hf_token", None)
    save_config(cfg)


def has_hf_token() -> bool:
    return get_hf_token() is not None

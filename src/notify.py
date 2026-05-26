"""Системные уведомления (F24 ТЗ v2).

Cross-platform минимум:
- macOS: osascript (встроен в систему).
- Windows: PowerShell + BurntToast если есть, иначе тихо пропустить.
- Linux: notify-send если есть.

Никаких внешних либ (plyer не ставим, чтобы не плодить зависимости).
Уведомление best-effort — если ничего не сработало, тихо игнорируем.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger("transcriber.notify")


def _shell_escape_double_quotes(s: str) -> str:
    """Заэскейпить двойные кавычки и backslash для безопасного встраивания в shell-команду."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, message: str) -> bool:
    """Послать системное уведомление. Возвращает True если получилось.

    Не блокирует, тайм-аут 5 сек. На любой ошибке тихо возвращает False —
    notification это nice-to-have, не должна валить pipeline.
    """
    if not title and not message:
        return False

    try:
        if sys.platform == "darwin":
            return _notify_macos(title, message)
        if sys.platform.startswith("win"):
            return _notify_windows(title, message)
        # Linux/прочее.
        return _notify_linux(title, message)
    except Exception as e:
        log.debug("notify failed: %s", e)
        return False


def _notify_macos(title: str, message: str) -> bool:
    if not shutil.which("osascript"):
        return False
    # AppleScript принимает строки в двойных кавычках. Escape тоже двойные.
    t = _shell_escape_double_quotes(title)
    m = _shell_escape_double_quotes(message)
    script = f'display notification "{m}" with title "{t}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True, timeout=5, capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _notify_windows(title: str, message: str) -> bool:
    if not shutil.which("powershell"):
        return False
    # BurntToast — лучший вариант (toast notifications), но требует pip install.
    # Сначала пробуем его, потом fallback на простой messagebox через PowerShell.
    t = _shell_escape_double_quotes(title)
    m = _shell_escape_double_quotes(message)

    # Best-effort: проверяем BurntToast, если есть — используем.
    ps_burnt = (
        f'if (Get-Module -ListAvailable BurntToast) {{ '
        f'Import-Module BurntToast; '
        f'New-BurntToastNotification -Text "{t}", "{m}" '
        f'}}'
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_burnt],
            check=True, timeout=5, capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _notify_linux(title: str, message: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.run(
            ["notify-send", title, message],
            check=True, timeout=5, capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False

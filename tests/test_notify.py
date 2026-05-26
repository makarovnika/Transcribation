"""Тесты для F24 — системные уведомления."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.notify import _shell_escape_double_quotes, notify


def test_shell_escape() -> None:
    assert _shell_escape_double_quotes('hello') == 'hello'
    assert _shell_escape_double_quotes('say "hi"') == 'say \\"hi\\"'
    assert _shell_escape_double_quotes('back\\slash') == 'back\\\\slash'


def test_notify_empty_returns_false() -> None:
    assert notify("", "") is False


def test_notify_macos_called(monkeypatch) -> None:
    """На darwin вызывается osascript."""
    monkeypatch.setattr("sys.platform", "darwin")
    with patch("src.notify.shutil.which", return_value="/usr/bin/osascript"):
        with patch("subprocess.run", return_value=MagicMock()) as mock_run:
            assert notify("title", "msg") is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "osascript"
            assert args[1] == "-e"


def test_notify_fallback_on_missing_osascript(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    with patch("src.notify.shutil.which", return_value=None):
        assert notify("title", "msg") is False


def test_notify_does_not_raise_on_error(monkeypatch) -> None:
    """notify не должен валить вызывающий код, даже если subprocess упал."""
    monkeypatch.setattr("sys.platform", "darwin")
    import subprocess

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "osascript")

    with patch("src.notify.shutil.which", return_value="/usr/bin/osascript"):
        with patch("subprocess.run", side_effect=boom):
            # Не должно raise, должно вернуть False.
            assert notify("title", "msg") is False


def test_notify_linux(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    with patch("src.notify.shutil.which", return_value="/usr/bin/notify-send"):
        with patch("subprocess.run", return_value=MagicMock()) as mock_run:
            assert notify("title", "msg") is True
            args = mock_run.call_args[0][0]
            assert args[0] == "notify-send"

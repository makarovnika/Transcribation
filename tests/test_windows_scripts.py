"""Регрессии Windows-установщика: кодировка, настоящий парсер и запуск без загрузки ML."""

from __future__ import annotations

import codecs
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "bin" / "windows").glob("*.ps1"))
POWERSHELL = shutil.which("powershell.exe" if os.name == "nt" else "pwsh")


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_windows_script_encoding(script: Path) -> None:
    data = script.read_bytes()
    text = data.decode("utf-8-sig")
    if not text.isascii():
        assert data.startswith(codecs.BOM_UTF8), "PowerShell 5.1 needs a BOM for Unicode"


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_windows_script_parses(script: Path) -> None:
    # ParseFile читает реальные байты: ParseInput(Get-Content -Encoding UTF8)
    # скрывал бы исходную ошибку декодирования в Windows PowerShell 5.1.
    env = {**os.environ, "TRANSCRIBER_TEST_SCRIPT": str(script)}
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
            "$tokens = $null; $parseErrors = $null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:TRANSCRIBER_TEST_SCRIPT, [ref]$tokens, [ref]$parseErrors) | Out-Null; "
            "if ($parseErrors.Count) { $parseErrors | Out-String | Write-Output; exit 1 }",
        ],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows executables and paths")
@pytest.mark.parametrize("failure", ["", "venv", "install", "smoke"])
def test_setup_control_flow(tmp_path: Path, failure: str) -> None:
    """Настоящий PowerShell + Python venv; внешние загрузки заменены заглушками."""
    project = tmp_path / "project with spaces"
    windows = project / "bin" / "windows"
    windows.mkdir(parents=True)
    shutil.copy2(ROOT / "bin" / "windows" / "setup.ps1", windows)
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "commands.jsonl"
    driver = commands / "driver.py"
    driver.write_text(
        "import json, os, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['TRANSCRIBER_TEST_LOG'], 'a') as f:\n"
        "    f.write(json.dumps(args) + '\\n')\n"
        "failure = os.environ['TRANSCRIBER_TEST_FAILURE']\n"
        "if args[0] == 'python':\n"
        "    print('Python 3.12.10')\n"
        "elif args[:2] == ['uv', 'venv']:\n"
        "    if failure == 'venv': sys.exit(7)\n"
        "    subprocess.run([sys.executable, '-m', 'venv', '.venv'], check=True)\n"
        "elif args[:3] == ['uv', 'pip', 'install']:\n"
        "    if failure == 'install': sys.exit(8)\n"
        "    site = pathlib.Path('.venv/Lib/site-packages')\n"
        "    (site / 'torch.py').write_text(\n"
        "        '__version__ = \"test\"\\nclass cuda:\\n'\n"
        "        '    @staticmethod\\n    def is_available(): return True\\n')\n"
        "    (site / 'pytest.py').write_text(\n"
        "        'import sys\\nsys.exit(' + ('9' if failure == 'smoke' else '0') + ')\\n')\n"
        "else: sys.exit(99)\n",
        encoding="utf-8",
    )
    for command in ("python", "uv"):
        (commands / f"{command}.cmd").write_text(
            f'@"{sys.executable}" "%~dp0driver.py" {command} %*\n@exit /b %errorlevel%\n',
            encoding="utf-8",
        )
    for command in ("ffmpeg", "nvidia-smi"):
        (commands / f"{command}.cmd").write_text("@echo test\n@exit /b 0\n")
    env = {
        **os.environ,
        "TRANSCRIBER_TEST_LOG": str(log),
        "TRANSCRIBER_TEST_FAILURE": failure,
    }
    # Исключаем установленные uv/Python, чтобы проверялся именно выбранный кандидат.
    env["PATH"] = str(commands) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(windows / "setup.ps1")],
        env=env, cwd=tmp_path, capture_output=True, timeout=120,
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert ["uv", "venv", "--python", "python", "--seed"] in calls
    installs = [call for call in calls if call[:3] == ["uv", "pip", "install"]]
    if failure == "venv":
        assert not installs
    else:
        assert len(installs) == 1
        assert installs[0][3:] == ["--python", ".venv\\Scripts\\python.exe", "-e", ".[dev]"]
        # pip действительно доступен в созданном окружении для опциональной CUDA-ветки.
        pip_check = subprocess.run(
            [str(project / ".venv" / "Scripts" / "python.exe"), "-m", "pip", "--version"],
            capture_output=True, timeout=30,
        )
        assert pip_check.returncode == 0
    assert (result.returncode == 0) == (failure == ""), output
    assert ("=== setup.ps1 OK ===" in output) == (failure == ""), output

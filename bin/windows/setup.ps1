# setup.ps1 — установка окружения транскрибатора на Windows.
#
# Что делает:
#   1) Проверяет наличие Python 3.10-3.13, ffmpeg, uv. Помогает поставить, если чего нет.
#   2) Создаёт .venv и ставит зависимости через uv.
#   3) Запускает smoke pytest.
#
# Запуск:
#   Открой PowerShell в папке проекта и:
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
#     .\bin\windows\setup.ps1
#
# Если на машине есть NVIDIA GPU и хочешь CUDA-ускорение для faster-whisper —
# после setup поставь CUDA-сборку torch (см. README раздел Windows).

$ErrorActionPreference = "Stop"

# Перейти в корень проекта (script is in bin/windows/, root is 2 levels up).
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $projectRoot
Write-Host "[..] project root: $projectRoot"

# --- 1. ffmpeg ---
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "[!!] ffmpeg не найден."
    Write-Host "    Поставь через winget: winget install Gyan.FFmpeg"
    Write-Host "    Или скачай с https://www.gyan.dev/ffmpeg/builds/ и добавь в PATH."
    exit 1
}
Write-Host "[ok] ffmpeg: $(ffmpeg -version | Select-Object -First 1)"

# --- 2. Python ---
$pythonCmd = $null
foreach ($cmd in @("python", "py", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $version = & $cmd --version 2>&1
        if ($version -match "Python (3\.(1[0-3]))") {
            $pythonCmd = $cmd
            Write-Host "[ok] $cmd: $version"
            break
        }
    }
}
if (-not $pythonCmd) {
    Write-Host "[!!] Не найден Python 3.10-3.13."
    Write-Host "    Поставь через winget: winget install Python.Python.3.12"
    Write-Host "    Или скачай с https://www.python.org/downloads/"
    exit 1
}

# --- 3. uv ---
$useUv = $true
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[..] uv не найден, ставлю через pip…"
    & $pythonCmd -m pip install --user --upgrade uv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[warn] uv не установился. Использую pip напрямую."
        $useUv = $false
    } else {
        $userBase = & $pythonCmd -c "import site; print(site.USER_BASE)"
        $env:Path = "$userBase\Scripts;$env:Path"
        Write-Host "[ok] uv установлен в $userBase\Scripts"
    }
}

# --- 4. venv ---
if (-not (Test-Path ".venv")) {
    Write-Host "[..] создаю .venv"
    if ($useUv) {
        uv venv
    } else {
        & $pythonCmd -m venv .venv
    }
}

# --- 5. Активация и установка зависимостей ---
$venvPython = Join-Path ".venv\Scripts" "python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[!!] venv создан, но $venvPython не найден"
    exit 1
}

Write-Host "[..] ставлю зависимости (это долго, тянет torch + faster-whisper + pyannote)…"
if ($useUv) {
    uv pip install -e ".[dev]"
} else {
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -e ".[dev]"
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!!] установка зависимостей упала. Посмотри вывод выше."
    exit 1
}
Write-Host "[ok] зависимости поставлены"

# --- 6. Smoke pytest ---
Write-Host "[..] прогоняю smoke-тесты"
& $venvPython -m pytest tests/ -q --no-header
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!!] smoke-тесты упали — посмотри вывод"
    exit 1
}
Write-Host "[ok] smoke-тесты пройдены"

Write-Host ""
Write-Host "=== setup.ps1 OK ==="
Write-Host ""
Write-Host "Запуск приложения вручную:"
Write-Host "  .\.venv\Scripts\python.exe -u app.py"
Write-Host ""
Write-Host "Установка как фонового сервиса (Task Scheduler):"
Write-Host "  .\bin\windows\install-service.ps1"

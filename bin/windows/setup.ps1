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

# --- 6. CUDA detection (для GPU-ускорения) ---
# По умолчанию pip ставит CPU-сборку torch на Windows. Если есть NVIDIA GPU,
# нужно переустановить torch с CUDA-индекса. Делаем это автоматически — иначе
# юзер с RTX «молча» получит CPU и удивится почему медленно.
$hasNvidia = $false
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "[..] nvidia-smi найден, проверяю CUDA capability…"
    $smiOut = & nvidia-smi 2>&1 | Out-String
    if ($smiOut -match "CUDA Version: (\d+)\.(\d+)") {
        $cudaMajor = [int]$Matches[1]
        Write-Host "[ok] NVIDIA driver CUDA: $($Matches[0])"
        $hasNvidia = $true
        # cu121 требует driver ≥ 530.x (CUDA 12.1+). cu118 — driver ≥ 450 (CUDA 11.8).
        if ($cudaMajor -ge 12) {
            $cudaWheelIdx = "https://download.pytorch.org/whl/cu121"
            $cudaTag = "cu121"
        } else {
            $cudaWheelIdx = "https://download.pytorch.org/whl/cu118"
            $cudaTag = "cu118"
        }
    } else {
        Write-Host "[warn] не смог распарсить CUDA version из nvidia-smi"
    }
} else {
    Write-Host "[..] nvidia-smi не найден — GPU нет или драйверы не установлены. Остаёмся на CPU."
}

# Проверим уже-установленный torch — может быть это CPU-сборка, нужно переставить.
$torchInfo = & $venvPython -c "import torch; print(torch.__version__); print(torch.cuda.is_available())" 2>&1
$torchVer, $cudaAvailable = $torchInfo -split "`n"
Write-Host "[..] torch=$torchVer, cuda.is_available()=$cudaAvailable"

if ($hasNvidia -and $cudaAvailable -ne "True") {
    Write-Host ""
    Write-Host "[!] У тебя есть NVIDIA GPU, но torch установлен в CPU-режиме."
    Write-Host "    Чтобы транскрибация шла на GPU, нужно переставить torch с CUDA-индекса ($cudaTag)."
    $answer = Read-Host "Поставить CUDA-сборку torch сейчас? Это снова потянет ~2 ГБ. [Y/n]"
    if ($answer -ne "n" -and $answer -ne "N") {
        Write-Host "[..] ставлю torch+torchaudio для $cudaTag"
        & $venvPython -m pip install --upgrade --force-reinstall torch torchaudio --index-url $cudaWheelIdx
        if ($LASTEXITCODE -eq 0) {
            $check = & $venvPython -c "import torch; print(torch.cuda.is_available())"
            if ($check -eq "True") {
                Write-Host "[ok] torch теперь видит CUDA"
            } else {
                Write-Host "[warn] torch установлен, но cuda.is_available()=False."
                Write-Host "       Проверь nvidia-smi → Driver Version ≥ 530.x для cu121."
            }
        } else {
            Write-Host "[warn] установка CUDA-сборки упала. Останешься на CPU."
        }
    } else {
        Write-Host "[skip] остаёшься на CPU. Поставить позже: pip install --upgrade --force-reinstall torch torchaudio --index-url $cudaWheelIdx"
    }
}

# --- 7. Smoke pytest ---
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

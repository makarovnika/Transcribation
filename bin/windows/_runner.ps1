# _runner.ps1 — wrapper, который Task Scheduler запускает вместо python.
#
# Зачем wrapper, а не прямой `python -u app.py`:
#   Task Scheduler не умеет напрямую перенаправлять stdout/stderr в файлы.
#   Вариант `cmd /c "..." > log.txt 2> err.txt` ломается на путях с кириллицей
#   (cmd кодирует аргументы в ANSI), что блокировало пользователей с именами
#   типа C:\Users\Никита\... Поэтому редирект делаем тут, через PowerShell.
#
# Что делает:
#   1) Находит .venv\Scripts\python.exe в корне проекта (2 уровня выше скрипта).
#   2) Подтягивает HF_TOKEN из ~/.config/transcriber/config.json в env, если он там есть
#      (нужно, чтобы huggingface_hub скачивал модели без 401).
#   3) Запускает app.py с редиректом stdout/stderr в logs/.
#
# Не вызывать руками — это служебный скрипт. Запускается из install-service.ps1.

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$appPy = Join-Path $projectRoot "app.py"
$logsDir = Join-Path $projectRoot "logs"

if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$outLog = Join-Path $logsDir "transcriber.out.log"
$errLog = Join-Path $logsDir "transcriber.err.log"

# Best-effort: подгружаем HF_TOKEN из config.json в env. Не критично, если файла нет —
# app.py всё равно прочитает токен через src/config.py.
$cfgPath = Join-Path $env:USERPROFILE ".config\transcriber\config.json"
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content -Raw -Path $cfgPath | ConvertFrom-Json
        if ($cfg.hf_token) { $env:HF_TOKEN = $cfg.hf_token }
    } catch {
        # битый JSON — игнорируем, не блокируем запуск сервиса
    }
}

# Стартуем python с редиректом. -RedirectStandardOutput/-Error пишут построчно,
# никакого cmd /c. Путь $venvPython подаётся как $args[0] — PowerShell корректно
# сохраняет UTF-16, экранирование не нужно.
& $venvPython -u $appPy *>&1 | Tee-Object -FilePath $outLog

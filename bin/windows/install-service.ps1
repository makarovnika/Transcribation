# install-service.ps1 — регистрирует транскрибатор как Task Scheduler задачу.
#
# Эквивалент launchd Agent на Mac:
#   - стартует при логине пользователя;
#   - перезапускается при крахе (RestartCount=999, RestartInterval=1 мин);
#   - пишет логи в logs/transcriber.out.log (через _runner.ps1);
#   - не требует прав администратора.
#
# Идемпотентно: если задача уже существует, она перерегистрируется.
#
# Реализационная заметка:
#   Раньше тут был `cmd /c "...python.exe... > log 2> err"`, но это ломается
#   на путях с кириллицей (cmd кодирует аргументы в ANSI/OEM, теряются символы).
#   Теперь Action вызывает PowerShell, который запускает _runner.ps1 — он
#   делает redirect через Tee-Object, PowerShell корректно работает с UTF-16.

$ErrorActionPreference = "Stop"

$taskName = "transcriber"
$taskPath = "\transcriber\"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runner = Join-Path $projectRoot "bin\windows\_runner.ps1"
$logsDir = Join-Path $projectRoot "logs"

if (-not (Test-Path $venvPython)) {
    Write-Host "[!!] venv не найден ($venvPython). Сначала .\bin\windows\setup.ps1"
    exit 1
}
if (-not (Test-Path $runner)) {
    Write-Host "[!!] _runner.ps1 не найден ($runner). Pull последние изменения."
    exit 1
}
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

# Удалить старую регистрацию если есть.
$existing = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[..] снимаю старую регистрацию"
    Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false
}

# Прибить любой работающий python app.py — иначе будут драться за порт 7860.
# Берём всё что слушает 7860, не только наш venv (мог быть запущен из другого чекаута).
$pidsOnPort = (Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue).OwningProcess
foreach ($pid in $pidsOnPort) {
    if ($pid) {
        Write-Host "[..] прибиваю процесс PID=$pid (слушает порт 7860)"
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
}

# Действие: PowerShell без profile (быстрее старт), с bypass policy (чтобы
# _runner.ps1 запустился без отдельного Set-ExecutionPolicy), запускает наш runner.
# -ExecutionPolicy Bypass влияет только на этот процесс, не на систему.
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $projectRoot

# Триггер: при логине текущего пользователя.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Настройки: рестарт при ошибке, без таймаута, разрешать запуск на батарее.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew

# Принципал: запуск от имени текущего пользователя, без admin elevation.
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -TaskPath $taskPath `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal | Out-Null

Write-Host "[ok] задача зарегистрирована: $taskPath$taskName"

# Стартуем сразу, не ждём логина.
Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath
Start-Sleep -Seconds 5  # PowerShell + python + load weights — медленнее cmd

$task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath
$state = $task.State
Write-Host "[..] state = $state"
if ($state -eq "Running") {
    Write-Host "[ok] сервис запущен"
} else {
    Write-Host "[warn] сервис ещё не в Running — посмотри logs/transcriber.out.log"
}

Write-Host ""
Write-Host "URL:     http://127.0.0.1:7860"
Write-Host "Status:  .\bin\windows\status.ps1"
Write-Host "Логи:    .\bin\windows\logs.ps1"
Write-Host "Restart: .\bin\windows\restart-service.ps1"
Write-Host "Снять:   .\bin\windows\uninstall-service.ps1"

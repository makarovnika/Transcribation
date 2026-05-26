# install-service.ps1 — регистрирует транскрибатор как Task Scheduler задачу.
#
# Эквивалент launchd Agent на Mac:
#   - стартует при логине пользователя;
#   - перезапускается при крахе (RestartCount=999, RestartInterval=1 мин);
#   - пишет логи в logs/transcriber.{out,err}.log;
#   - не требует прав администратора (TaskPath под текущего юзера).
#
# Идемпотентно: если задача уже существует, она перерегистрируется.
#
# Запуск:
#   .\bin\windows\install-service.ps1

$ErrorActionPreference = "Stop"

$taskName = "transcriber"
$taskPath = "\transcriber\"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$appPy = Join-Path $projectRoot "app.py"
$logsDir = Join-Path $projectRoot "logs"

if (-not (Test-Path $venvPython)) {
    Write-Host "[!!] venv не найден ($venvPython). Сначала .\bin\windows\setup.ps1"
    exit 1
}
if (-not (Test-Path $appPy)) {
    Write-Host "[!!] app.py не найден ($appPy)"
    exit 1
}
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

# Удалить старую регистрацию если есть.
$existing = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[..] снимаю старую регистрацию"
    Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false
}

# Прибить любой запущенный руками python app.py — иначе будут драться за порт 7860.
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.MainModule -and ($_.MainModule.FileName -eq $venvPython)
} | ForEach-Object {
    Write-Host "[..] прибиваю старый процесс PID=$($_.Id)"
    Stop-Process -Id $_.Id -Force
}

# Команда: python -u app.py, с stdout/stderr в logs/.
# Используем cmd /c для редиректа, так как Action не умеет напрямую writer-ом stdout.
$stdoutLog = Join-Path $logsDir "transcriber.out.log"
$stderrLog = Join-Path $logsDir "transcriber.err.log"

# cmd /c "..."  → передаёт всю строку cmd'у; > и 2> работают.
$argument = "/c `"`"$venvPython`" -u `"$appPy`" > `"$stdoutLog`" 2> `"$stderrLog`"`""

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument $argument `
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
Start-Sleep -Seconds 3

$task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath
$state = $task.State
Write-Host "[..] state = $state"
if ($state -eq "Running") {
    Write-Host "[ok] сервис запущен"
} else {
    Write-Host "[warn] сервис ещё не в Running — посмотри logs/transcriber.err.log"
}

Write-Host ""
Write-Host "URL:     http://127.0.0.1:7860"
Write-Host "Status:  .\bin\windows\status.ps1"
Write-Host "Логи:    .\bin\windows\logs.ps1"
Write-Host "Restart: .\bin\windows\restart-service.ps1"
Write-Host "Снять:   .\bin\windows\uninstall-service.ps1"

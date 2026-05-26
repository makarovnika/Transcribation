# status.ps1 — короткий статус сервиса.

$taskName = "transcriber"
$taskPath = "\transcriber\"

$task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "X Сервис НЕ установлен. .\bin\windows\install-service.ps1 — поставить."
    exit 1
}

Write-Host "Задача: $($task.TaskPath)$($task.TaskName)"
Write-Host "State:  $($task.State)"

if ($task.State -eq "Running") {
    Write-Host "URL:    http://127.0.0.1:7860"

    # Последний запуск + код выхода (полезно если рестарт идёт в цикле).
    $info = Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $taskPath
    Write-Host "LastRun:    $($info.LastRunTime)"
    Write-Host "LastResult: $($info.LastTaskResult)  (0 = OK)"
    Write-Host "NumRuns:    $($info.NumberOfMissedRuns) пропущенных запусков"
} else {
    Write-Host "Сервис не работает. .\bin\windows\logs.ps1 — посмотреть лог."
}

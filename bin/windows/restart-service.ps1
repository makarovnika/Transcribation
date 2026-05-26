# restart-service.ps1 — перезапустить сервис (применить изменения в коде).

$ErrorActionPreference = "Stop"

$taskName = "transcriber"
$taskPath = "\transcriber\"

$task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "[!!] сервис не установлен. Сначала: .\bin\windows\install-service.ps1"
    exit 1
}

if ($task.State -eq "Running") {
    Write-Host "[..] stop"
    Stop-ScheduledTask -TaskName $taskName -TaskPath $taskPath
    # Подождём пока процесс реально умрёт. Stop-ScheduledTask асинхронный.
    $i = 0
    while ((Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath).State -ne "Ready") {
        if (++$i -ge 20) { break }
        Start-Sleep -Milliseconds 500
    }
}

Write-Host "[..] start"
Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath
Start-Sleep -Seconds 3

$task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath
if ($task.State -eq "Running") {
    Write-Host "[ok] сервис перезапущен"
} else {
    Write-Host "[warn] state = $($task.State) — проверь .\bin\windows\logs.ps1"
}

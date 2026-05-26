# uninstall-service.ps1 — снять Task Scheduler-задачу транскрибатора.

$ErrorActionPreference = "Stop"

$taskName = "transcriber"
$taskPath = "\transcriber\"

$task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if ($task) {
    if ($task.State -eq "Running") {
        Write-Host "[..] останавливаю задачу"
        Stop-ScheduledTask -TaskName $taskName -TaskPath $taskPath
        Start-Sleep -Seconds 1
    }
    Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false
    Write-Host "[ok] задача $taskPath$taskName удалена"
} else {
    Write-Host "[skip] задача не зарегистрирована"
}

Write-Host "Готово. Сервис больше не запускается автоматически."

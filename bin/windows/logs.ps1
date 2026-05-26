# logs.ps1 — стрим логов сервиса (аналог tail -F).
# Прерывание — Ctrl+C.

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$outLog = Join-Path $projectRoot "logs\transcriber.out.log"
$errLog = Join-Path $projectRoot "logs\transcriber.err.log"

if (-not (Test-Path $outLog) -and -not (Test-Path $errLog)) {
    Write-Host "[!!] логи ещё не созданы. Сервис не запускался?"
    exit 1
}

Write-Host "=== Логи: $projectRoot\logs (Ctrl+C чтобы выйти) ==="

# Стрим обоих файлов одновременно через PowerShell job — каждый Get-Content -Wait
# свой поток. Не идеально (нет лейбла откуда строка), но просто.
$ErrorActionPreference = "Stop"
if (Test-Path $outLog) {
    Start-Job -ScriptBlock { Get-Content -Wait -Path $args[0] } -ArgumentList $outLog | Out-Null
}
if (Test-Path $errLog) {
    Start-Job -ScriptBlock { Get-Content -Wait -Path $args[0] } -ArgumentList $errLog | Out-Null
}

try {
    while ($true) {
        Get-Job | Receive-Job
        Start-Sleep -Milliseconds 500
    }
} finally {
    Get-Job | Stop-Job -PassThru | Remove-Job
}

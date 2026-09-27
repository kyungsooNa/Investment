param(
    [string]$PythonPath = (Join-Path $env:USERPROFILE "anaconda3\envs\py310\python.exe"),
    [int]$RestartDelaySeconds = 30
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$entrypoint = Join-Path $projectRoot "main.py"
$watchdogLogDir = Join-Path $projectRoot "logs\common"
$watchdogLog = Join-Path $watchdogLogDir "watchdog.log"

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python executable not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
    throw "Application entrypoint not found: $entrypoint"
}

$env:OPEN_BROWSER = "0"
New-Item -ItemType Directory -Path $watchdogLogDir -Force | Out-Null
Set-Location -LiteralPath $projectRoot

while ($true) {
    & $PythonPath $entrypoint
    $exitCode = $LASTEXITCODE
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
    Add-Content `
        -LiteralPath $watchdogLog `
        -Encoding UTF8 `
        -Value "$timestamp Investment app exited with code $exitCode. Restarting in $RestartDelaySeconds seconds."
    Start-Sleep -Seconds $RestartDelaySeconds
}

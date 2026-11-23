$ErrorActionPreference = 'Stop'
$ecgRoot = $PSScriptRoot
$ecgScript = Join-Path $ecgRoot 'run_server.py'
$ecgListeners = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
if (-not $ecgListeners) {
    Write-Host 'No ECG service is listening on port 8766.'
    exit 0
}
foreach ($ecgListener in $ecgListeners) {
    $ecgProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($ecgListener.OwningProcess)"
    if ($ecgProcess.CommandLine -and $ecgProcess.CommandLine.Contains($ecgScript)) {
        Stop-Process -Id $ecgProcess.ProcessId
        Write-Host 'ECG local service stopped. Recorded data remains on disk.'
    } else {
        throw 'Port 8766 belongs to an unrecognized process; it was not stopped.'
    }
}

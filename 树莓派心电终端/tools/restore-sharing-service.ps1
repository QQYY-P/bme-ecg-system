$ErrorActionPreference = 'Stop'
$ecgEvidence = Join-Path $PSScriptRoot '..\evidence\09_connection_20261027'
New-Item -ItemType Directory -Force -Path $ecgEvidence | Out-Null
$ecgReport = [ordered]@{
    checked_at = (Get-Date).ToString('o')
    action = 'Start the existing Windows Internet Connection Sharing service'
    service = 'SharedAccess'
    status_before = $null
    status_after = $null
    error = $null
}
try {
    $ecgService = Get-Service -Name SharedAccess
    $ecgReport.status_before = $ecgService.Status.ToString()
    if ($ecgService.Status -ne 'Running') {
        Start-Service -Name SharedAccess
        $ecgService.WaitForStatus('Running', [TimeSpan]::FromSeconds(20))
    }
    $ecgReport.status_after = (Get-Service -Name SharedAccess).Status.ToString()
} catch {
    $ecgReport.error = $_.Exception.Message
} finally {
    $ecgReport | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ecgEvidence 'sharing-service-result.json') -Encoding UTF8
}
if ($ecgReport.error) { exit 1 }

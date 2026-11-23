$ErrorActionPreference = 'Stop'
$ecgEvidence = Join-Path $PSScriptRoot '..\evidence\09_connection_20261027'
New-Item -ItemType Directory -Force -Path $ecgEvidence | Out-Null
$ecgResult = [ordered]@{ checked_at=(Get-Date).ToString('o'); before=@(); actions=@(); after=@(); error=$null }
try {
    $ecgPrivate = Get-NetAdapter -InterfaceIndex 7
    $ecgPublic = Get-NetAdapter -Name 'WLAN'
    if ($ecgPrivate.InterfaceDescription -ne 'Realtek PCIe GbE Family Controller' -or $ecgPrivate.Status -ne 'Up' -or $ecgPublic.Status -ne 'Up') {
        throw 'The expected active Ethernet and WLAN adapters were not found; no sharing change made.'
    }
    $ecgManager = New-Object -ComObject HNetCfg.HNetShare
    $ecgEntries = @(foreach ($ecgConnection in $ecgManager.EnumEveryConnection()) {
        $ecgProps = $ecgManager.NetConnectionProps($ecgConnection)
        $ecgConfig = $ecgManager.INetSharingConfigurationForINetConnection($ecgConnection)
        [pscustomobject]@{ Name=$ecgProps.Name; Guid=$ecgProps.Guid; Config=$ecgConfig }
    })
    $ecgResult.before = @($ecgEntries | ForEach-Object { [pscustomobject]@{Name=$_.Name;Guid=$_.Guid;Enabled=$_.Config.SharingEnabled;Type=$_.Config.SharingConnectionType} })
    $ecgResult | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $ecgEvidence 'direct-sharing-result.json') -Encoding UTF8
    $ecgPublicEntry = @($ecgEntries | Where-Object { [guid]$_.Guid -eq [guid]$ecgPublic.InterfaceGuid })
    $ecgPrivateEntry = @($ecgEntries | Where-Object { [guid]$_.Guid -eq [guid]$ecgPrivate.InterfaceGuid })
    if ($ecgPublicEntry.Count -ne 1 -or $ecgPrivateEntry.Count -ne 1) { throw 'Could not identify the two sharing connections uniquely.' }
    foreach ($ecgEntry in $ecgEntries) {
        if ($ecgEntry.Config.SharingEnabled -and [guid]$ecgEntry.Guid -ne [guid]$ecgPublic.InterfaceGuid -and [guid]$ecgEntry.Guid -ne [guid]$ecgPrivate.InterfaceGuid) {
            throw 'Another adapter already uses sharing. Preserving it and stopping without changes.'
        }
    }
    if ($ecgPublicEntry[0].Config.SharingEnabled -and $ecgPublicEntry[0].Config.SharingConnectionType -ne 0) { throw 'WLAN is already a private shared connection; preserving it.' }
    if ($ecgPrivateEntry[0].Config.SharingEnabled -and $ecgPrivateEntry[0].Config.SharingConnectionType -ne 1) { throw 'Ethernet is already a public shared connection; preserving it.' }
    if (-not $ecgPublicEntry[0].Config.SharingEnabled) {
        $ecgPublicEntry[0].Config.EnableSharing(0)
        $ecgResult.actions += 'Enabled existing WLAN as the public Internet sharing connection'
    }
    if (-not $ecgPrivateEntry[0].Config.SharingEnabled) {
        $ecgPrivateEntry[0].Config.EnableSharing(1)
        $ecgResult.actions += 'Enabled existing Ethernet as the private shared connection'
    }
    Start-Service -Name SharedAccess
    $ecgResult.after = @($ecgEntries | ForEach-Object { [pscustomobject]@{Name=$_.Name;Guid=$_.Guid;Enabled=$_.Config.SharingEnabled;Type=$_.Config.SharingConnectionType} })
} catch {
    $ecgResult.error = $_.Exception.Message
} finally {
    $ecgResult | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $ecgEvidence 'direct-sharing-result.json') -Encoding UTF8
}
if ($ecgResult.error) { exit 1 }

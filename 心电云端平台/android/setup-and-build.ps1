param(
    [switch]$AcceptSdkLicense,
    [string]$SdkPath = 'D:\workspace\bme-android-sdk',
    [string]$JdkPath = '',
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$projectRoot = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'work') | Out-Null
Start-Transcript -Path (Join-Path $PSScriptRoot 'work\setup-build.log') -Append | Out-Null

if (-not $AcceptSdkLicense) {
    throw 'Read https://developer.android.com/studio#command-tools and accept the Android SDK license before using -AcceptSdkLicense.'
}
if (-not $JdkPath) {
    $candidates = @($env:JAVA_HOME, 'E:\tool\Pycharm\PyCharm 2026.1.1\jbr')
    $JdkPath = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'bin\javac.exe')) } | Select-Object -First 1
}
if (-not $JdkPath -or -not (Test-Path -LiteralPath (Join-Path $JdkPath 'bin\javac.exe'))) {
    throw 'JDK not found. Run with -JdkPath pointing to a JDK 17 or newer.'
}
if (-not $PythonPath) {
    $localPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython) { $PythonPath = $localPython }
    else { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}
$sdkRoot = [System.IO.Path]::GetFullPath($SdkPath)
New-Item -ItemType Directory -Force -Path $sdkRoot | Out-Null
$env:JAVA_HOME = $JdkPath
$env:ANDROID_HOME = $sdkRoot
$env:ANDROID_SDK_ROOT = $sdkRoot
$env:PATH = (Join-Path $JdkPath 'bin') + ';' + $env:PATH
$sdkManager = Join-Path $sdkRoot 'cmdline-tools\latest\bin\sdkmanager.bat'

if (-not (Test-Path -LiteralPath $sdkManager)) {
    $fileName = 'commandlinetools-win-15859902_latest.zip'
    $archive = Join-Path $sdkRoot $fileName
    $expected = '90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a'
    if (-not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $expected) {
        Write-Host 'Downloading official Android command-line tools (156 MB)...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri ('https://dl.google.com/android/repository/' + $fileName) -OutFile $archive -TimeoutSec 900
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $expected) {
        throw 'SDK SHA-256 does not match the official download page. Extraction stopped.'
    }
    Write-Host 'Verified SDK checksum. Extracting compiler tools...'
    $staging = Join-Path $sdkRoot 'extracted-command-tools'
    Expand-Archive -LiteralPath $archive -DestinationPath $staging -Force
    $latest = Join-Path $sdkRoot 'cmdline-tools\latest'
    New-Item -ItemType Directory -Force -Path $latest | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $staging 'cmdline-tools') | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $latest -Recurse -Force
    }
}
Write-Host 'Installing Android 35 platform and Build Tools 35.0.0 under the approved SDK license...'
& $PythonPath (Join-Path $PSScriptRoot 'install_sdk.py') --sdk $sdkRoot --accept-sdk-license
if ($LASTEXITCODE -ne 0) { throw 'Android SDK installation failed. Check network access to dl.google.com, then rerun.' }
Write-Host 'Building and verifying the signed ECG Records APK...'
& $PythonPath (Join-Path $PSScriptRoot 'build.py') --sdk $sdkRoot --jdk $JdkPath --build-root (Join-Path $sdkRoot 'app-build')
if ($LASTEXITCODE -ne 0) { throw 'APK build failed. See the compiler error above.' }
Write-Host ''
Write-Host ('APK ready: ' + (Join-Path $PSScriptRoot 'dist\ecg-records-1.0.0.apk')) -ForegroundColor Green
Write-Host 'Keep android\work\signing private and backed up; future updates must use the same signing key.'
Stop-Transcript | Out-Null

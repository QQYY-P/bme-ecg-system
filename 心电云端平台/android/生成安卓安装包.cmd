@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-and-build.ps1" -AcceptSdkLicense
if errorlevel 1 (
  echo.
  echo Build failed. Keep this window open and copy the error for diagnosis.
  pause
  exit /b 1
)
echo.
echo APK is ready in the dist folder beside this script.
pause

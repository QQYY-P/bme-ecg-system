@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "%~dp0cloud_link.py" --host 62.234.150.104 %*
pause

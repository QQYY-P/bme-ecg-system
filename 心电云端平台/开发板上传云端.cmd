@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "%~dp0pi_link.py" --destination-port 8767
pause

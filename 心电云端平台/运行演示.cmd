@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" demo.py --seconds 30 --live
pause

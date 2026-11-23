@echo off
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" "%~dp0auto_link.py"
start "" "http://127.0.0.1:8768/"

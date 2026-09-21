@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\play_neural.py %*
if errorlevel 1 pause

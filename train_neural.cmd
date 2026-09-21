@echo off
cd /d "%~dp0"
if exist "build\neural\local\latest.pt" (
  ".venv\Scripts\python.exe" -m uno.neural.training --resume build\neural\local\latest.pt --output build\neural\local %*
) else (
  ".venv\Scripts\python.exe" -m uno.neural.training --output build\neural\local %*
)
if errorlevel 1 pause

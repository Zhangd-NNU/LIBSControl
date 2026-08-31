@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Runtime is not installed. Please run install.bat first.
  pause
  exit /b 1
)
start "LIBS Vision Control" ".venv\Scripts\pythonw.exe" main.py

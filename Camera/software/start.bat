@echo off
setlocal
cd /d "%~dp0"
set "CAMERA_PYTHON=python"
where python >nul 2>nul
if errorlevel 1 (
  set "CAMERA_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  if not exist "%CAMERA_PYTHON%" (
    echo [ERROR] Python 3.10 or newer was not found.
    echo Install Python x64, then run: python -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)
"%CAMERA_PYTHON%" -c "import PIL" >nul 2>nul
if errorlevel 1 (
  set "CAMERA_CODEX_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  if exist "%CAMERA_CODEX_PYTHON%" (
    "%CAMERA_CODEX_PYTHON%" -c "import PIL" >nul 2>nul
    if not errorlevel 1 set "CAMERA_PYTHON=%CAMERA_CODEX_PYTHON%"
  )
)
"%CAMERA_PYTHON%" -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing required Python package Pillow...
  "%CAMERA_PYTHON%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
  )
)
"%CAMERA_PYTHON%" main.py
if errorlevel 1 pause

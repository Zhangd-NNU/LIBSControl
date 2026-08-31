@echo off
setlocal
cd /d "%~dp0"
echo [1/3] Creating isolated Python environment...
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto :error

echo [2/3] Installing Python dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [3/3] Installing RealSense Python binding...
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)"
if not errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install "vendor\pyrealsense2-2.58.1.10581-cp314-cp314-win_amd64.whl"
) else (
  ".venv\Scripts\python.exe" -m pip install "pyrealsense2==2.58.1.10581"
)
if errorlevel 1 goto :error
echo.
echo Installation complete. Double-click run.bat to start.
pause
exit /b 0

:error
echo.
echo Installation failed. Check the network, Python version, and error message above.
pause
exit /b 1

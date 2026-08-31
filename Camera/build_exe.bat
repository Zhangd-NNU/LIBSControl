@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --windowed --name LIBS-Camera-Control --collect-all PIL main.py
if errorlevel 1 exit /b 1
echo Built: dist\LIBS-Camera-Control\LIBS-Camera-Control.exe
pause

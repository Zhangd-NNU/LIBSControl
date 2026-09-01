$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$packageName = "LIBSDepthCamera_Debug"
$buildRoot = Join-Path ([System.IO.Path]::GetTempPath()) "LIBSDepthCameraDebugBuild"
$distRoot = Join-Path $PSScriptRoot "dist"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Missing .venv. Run install.bat first."
}

& $pythonExe -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed." }

& $pythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --console `
    --name $packageName `
    --distpath $distRoot `
    --workpath (Join-Path $buildRoot "work") `
    --specpath $buildRoot `
    --collect-all pyrealsense2 `
    (Join-Path $projectRoot "main.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$zipPath = Join-Path $PSScriptRoot "LIBSDepthCamera_Debug_win-x64.zip"
Compress-Archive -LiteralPath (Join-Path $distRoot $packageName) -DestinationPath $zipPath -CompressionLevel Optimal -Force
Write-Host "Debug package created: $zipPath"

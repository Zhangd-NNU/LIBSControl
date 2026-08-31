$ErrorActionPreference = "Stop"
$debugRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Split-Path -Parent $debugRoot
Set-Location $project

python -m PyInstaller `
  --noconfirm `
  --clean `
  --console `
  --onedir `
  --name LIBSStageControl_Debug `
  --distpath "$debugRoot\dist_debug" `
  --workpath "$debugRoot\build_debug" `
  --specpath "$debugRoot" `
  --add-binary "vendor_reference\ftcoreimc_win_v2.3.0.0n\ftcoreimc\lib\x64\ftcoreimc.dll;vendor_reference\ftcoreimc_win_v2.3.0.0n\ftcoreimc\lib\x64" `
  --add-data "README.md;." `
  --add-data "$debugRoot\DEBUG_TEST_GUIDE.txt;." `
  main.py

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

Write-Host "Debug package: $debugRoot\dist_debug\LIBSStageControl_Debug"

# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('vendor_reference/ftcoreimc_win_v2.3.0.0n/ftcoreimc/lib/x64/ftcoreimc.dll', 'vendor_reference/ftcoreimc_win_v2.3.0.0n/ftcoreimc/lib/x64')],
    datas=[('README.md', '.'), ('DEBUG_TEST_GUIDE.txt', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LIBSStageControl_Debug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LIBSStageControl_Debug',
)

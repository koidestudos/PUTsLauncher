# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PUTs Launcher (Windows .exe)."""

from pathlib import Path

block_cipher = None
root = Path(SPECPATH)
icon_path = root / "launcher" / "assets" / "icon.ico"

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "launcher" / "assets"), "launcher/assets"),
    ],
    hiddenimports=[
        "customtkinter",
        "minecraft_launcher_lib",
        "minecraft_launcher_lib.forge",
        "minecraft_launcher_lib.command",
        "minecraft_launcher_lib.microsoft_account",
        "PIL",
        "requests",
        "launcher",
        "launcher.ui.app",
        "launcher.core.launch",
        "launcher.auth.microsoft",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PUTsLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)

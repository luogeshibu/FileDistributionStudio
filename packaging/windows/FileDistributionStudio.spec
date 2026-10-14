# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
ROOT = Path(SPEC).resolve().parents[2]
RES = ROOT / "resources"
a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(RES / "assets"), "resources/assets"),
        (str(RES / "icons"), "resources/icons"),
    ],
    hiddenimports=["PySide6.QtCore","PySide6.QtGui","PySide6.QtWidgets","paramiko","psutil"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["_context_attributes"], noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="FileDistributionStudio", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(RES / "assets" / "logo.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FileDistributionStudio")

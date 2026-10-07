# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
ROOT = Path(SPEC).resolve().parents[2]
# The application only uses Impacket for SMB/NTLM identity checks and the
# authenticated WKSSVC hostname lookup.  Collecting the whole package also
# pulls in optional example tools (notably ntlmrelayx), and some Impacket
# wheels do not contain every example-package __init__.py that PyInstaller
# expects while building the PYZ archive.
IMPACKET_HIDDEN = [
    "impacket",
    "impacket.ntlm",
    "impacket.smbconnection",
    "impacket.smb3structs",
    "impacket.nt_errors",
    "impacket.dcerpc",
    "impacket.dcerpc.v5",
    "impacket.dcerpc.v5.transport",
    "impacket.dcerpc.v5.wkst",
]
RES = ROOT / "resources"
a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(RES / "assets"), "resources/assets"),
        (str(RES / "icons"), "resources/icons"),
        (str(RES / "scripts"), "resources/scripts"),
    ],
    hiddenimports=["PySide6.QtCore","PySide6.QtGui","PySide6.QtWidgets","paramiko","psutil","winrm"] + IMPACKET_HIDDEN,
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

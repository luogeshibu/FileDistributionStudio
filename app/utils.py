from pathlib import Path
import hashlib
import os
import re
import socket

_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/]*(.*)$")

def sha256_file(path: Path, chunk_size=4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def validate_windows_target_path(target_path: str, *, allow_unc: bool = True) -> str:
    """Validate and normalize a remote Windows absolute path without converting it to SMB.

    WinRM mode works with the target machine's real path directly (C:\\..., D:\\..., ...).
    UNC is optionally accepted for explicit network destinations, subject to the remote account's access.
    """
    raw = (target_path or "").strip().replace("/", "\\")
    if not raw:
        raise ValueError("目标目录不能为空。")
    if raw.startswith("\\\\"):
        if not allow_unc:
            raise ValueError(f"当前模式不支持 UNC 目标目录：{target_path}")
        parts = [x for x in raw.strip("\\").split("\\") if x]
        if len(parts) < 2:
            raise ValueError(f"UNC 路径必须至少包含服务器和共享名：{target_path}")
        return raw.rstrip("\\") or raw
    m = _DRIVE_RE.match(raw)
    if not m or raw[2:3] != "\\":
        raise ValueError(f"目标目录必须是绝对 Windows 路径，例如 E:\\temp：{target_path}")
    drive, rest = m.groups()
    rest = rest.replace("/", "\\").lstrip("\\")
    base = f"{drive.upper()}:\\"
    return base if not rest else base + rest

def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

def safe_rel(rel: str) -> str:
    rel = rel.replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    return "/".join(parts)

def human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.1f} {u}"
        x /= 1024
    return f"{n} B"

def reverse_dns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""

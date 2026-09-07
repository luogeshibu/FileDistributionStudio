from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime


@dataclass
class HostConnectivity:
    host: str
    ping_ok: bool
    smb_port_ok: bool
    rdp_port_ok: bool
    winrm_5985_ok: bool
    winrm_5986_ok: bool
    online_status: str
    message: str

    @property
    def winrm_port_ok(self) -> bool:
        return self.winrm_5985_ok or self.winrm_5986_ok


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def _ping(host: str, timeout_ms: int = 900) -> bool:
    try:
        if os.name == "nt":
            cmd = ["ping", "-n", "1", "-w", str(max(200, timeout_ms)), host]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", host]
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=max(2.0, timeout_ms / 1000 + 1.5),
            encoding="utf-8", errors="ignore",
        )
        return p.returncode == 0
    except Exception:
        return False


def test_host_connectivity(host: str, timeout: float = 0.55) -> HostConnectivity:
    # Ping 只作为一个信号，不作为“在线”的唯一判断依据。
    ping_ok = _ping(host, int(max(0.25, timeout) * 1000))
    smb = _tcp_open(host, 445, timeout)
    rdp = _tcp_open(host, 3389, timeout)
    w5985 = _tcp_open(host, 5985, timeout)
    w5986 = _tcp_open(host, 5986, timeout)

    any_tcp = smb or rdp or w5985 or w5986
    if w5985 or w5986:
        status = "ONLINE_WINRM"
        label = "在线（WinRM 可达）"
    elif smb:
        status = "ONLINE_SMB"
        label = "在线（SMB 可达）"
    elif ping_ok or any_tcp:
        status = "ONLINE"
        label = "在线"
    else:
        status = "OFFLINE"
        label = "离线 / 不可达"

    details = [
        f"状态：{label}",
        f"Ping：{'通过' if ping_ok else '无响应'}",
        f"SMB 445：{'开放' if smb else '未开放/不可达'}",
        f"RDP 3389：{'开放' if rdp else '未开放/不可达'}",
        f"WinRM 5985：{'开放' if w5985 else '未开放/不可达'}",
        f"WinRM 5986：{'开放' if w5986 else '未开放/不可达'}",
    ]
    return HostConnectivity(host, ping_ok, smb, rdp, w5985, w5986, status, "\n".join(details))


def status_label(status: str) -> str:
    return {
        "UNTESTED": "未测试",
        "ONLINE_WINRM": "在线（WinRM 可达）",
        "ONLINE_SMB": "在线（SMB 可达）",
        "ONLINE": "在线",
        "OFFLINE": "离线 / 不可达",
    }.get(status or "UNTESTED", status or "未测试")


def smb_status_label(status: str) -> str:
    return {
        "UNTESTED": "未测试",
        "WRITABLE": "可分发",
        "FAILED": "SMB 测试失败",
    }.get(status or "UNTESTED", status or "未测试")


def winrm_status_label(status: str) -> str:
    return {
        "UNTESTED": "未测试",
        "WRITABLE": "可分发",
        "FAILED": "WinRM 测试失败",
    }.get(status or "UNTESTED", status or "未测试")

from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import psutil

from ..models import DiscoveryResult
from .smb_identity import smb_ntlm_identity, windows_netwksta_identity

WINDOWS_PORTS = (445, 3389, 5985, 5986)
MAX_SCAN_ADDRESSES = 4096


def local_ipv4_networks():
    out = []
    seen = set()
    for ifname, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family != socket.AF_INET or not a.address or a.address.startswith("127.") or not a.netmask:
                continue
            try:
                iface = ipaddress.IPv4Interface(f"{a.address}/{a.netmask}")
                key = (ifname, a.address, str(iface.network))
                if key not in seen:
                    seen.add(key)
                    out.append(key)
            except Exception:
                pass
    return out


def normalize_networks(value) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[;,\r\n\s]+", value)
    else:
        raw = list(value or [])
    result = []
    for item in raw:
        item = str(item).strip()
        if not item:
            continue
        net = ipaddress.ip_network(item, strict=False)
        if net.version != 4:
            raise ValueError(f"当前版本仅支持 IPv4 网段：{item}")
        canonical = str(net)
        if canonical not in result:
            result.append(canonical)
    return result


def _port_open(ip: str, port: int, timeout: float):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def _clean_hostname(value: str) -> str:
    value = (value or "").strip().strip(".")
    if not value:
        return ""
    # PTR may return an FQDN. For inventory we keep the returned value as-is,
    # but remove surrounding whitespace/trailing dot.
    return value


def _reverse_dns(ip: str) -> str:
    try:
        return _clean_hostname(socket.gethostbyaddr(ip)[0])
    except Exception:
        return ""


def _run_hidden(args: list[str], timeout: float) -> str:
    if os.name != "nt":
        return ""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=False,
            timeout=max(1.0, timeout),
            creationflags=creationflags,
        )
    except Exception:
        return ""
    raw = (p.stdout or b"") + b"\n" + (p.stderr or b"")
    for enc in ("utf-8", "gbk", "cp936", "cp1252"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def _nbtstat_name(ip: str, timeout: float) -> str:
    """Best-effort NetBIOS name lookup.

    NetBIOS computer names are traditionally limited to 15 characters, so this
    result is treated as an unverified hint and should be upgraded with WinRM
    when an exact Windows computer name is required.
    """
    text = _run_hidden(["nbtstat", "-A", ip], timeout=max(2.0, timeout * 5))
    if not text:
        return ""
    candidates = []
    for line in text.splitlines():
        if "<00>" not in line.upper():
            continue
        m = re.search(r"^\s*([^\s<]{1,63})\s+<00>\s+(.+)$", line, re.IGNORECASE)
        if not m:
            continue
        name = _clean_hostname(m.group(1))
        tail = m.group(2).upper()
        if not name:
            continue
        # Prefer UNIQUE workstation records over GROUP/workgroup records.
        score = 0
        if "UNIQUE" in tail or "唯一" in tail:
            score += 10
        if "GROUP" in tail or "组" in tail:
            score -= 10
        candidates.append((score, name))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _ping_resolved_name(ip: str, timeout: float) -> str:
    """Use Windows resolver as a last no-credential hint.

    This can still resolve via DNS/NetBIOS and therefore is not considered
    authoritative.
    """
    if os.name != "nt":
        return ""
    ms = max(250, int(timeout * 1000))
    text = _run_hidden(["ping", "-a", "-n", "1", "-w", str(ms), ip], timeout=max(2.0, timeout * 5))
    if not text:
        return ""
    # English: Pinging HOST [1.2.3.4] with 32 bytes of data:
    # Chinese Windows still contains the bracketed IP in the first line.
    for line in text.splitlines()[:4]:
        if f"[{ip}]" not in line:
            continue
        before = line.split(f"[{ip}]", 1)[0].strip()
        tokens = before.split()
        if not tokens:
            continue
        candidate = _clean_hostname(tokens[-1])
        if candidate and candidate != ip:
            return candidate
    return ""


def quick_hostname(ip: str, timeout: float, smb_open: bool = False) -> tuple[str, str, str]:
    """Return ``(hostname, source, note)`` without using credentials.

    For routed Windows networks the most useful no-credential signal is often
    TCP/445 itself: DNS PTR can be absent, UDP/137 NetBIOS can be filtered, and
    WinRM can be disabled.  When SMB is reachable we therefore ask the target
    SMB service for its NTLM challenge identity first.
    """
    smb_note = ""
    if smb_open:
        # First use the native Windows workstation-management API.  Level 100
        # returns the target's local Computer Name and is especially useful in
        # routed networks where reverse DNS and NetBIOS name service are absent.
        identity = windows_netwksta_identity(ip)
        if identity.hostname:
            return identity.hostname, "NETAPI_WKSTA", identity.note

        # If NetAPI is unavailable/blocked, obtain the server-published identity
        # from the SMB2/3 NTLM Type-2 challenge over the already-open TCP 445.
        identity = smb_ntlm_identity(ip, timeout=max(1.2, timeout * 3))
        smb_note = identity.note
        if identity.hostname:
            return identity.hostname, "SMB_NTLM", identity.note

    name = _reverse_dns(ip)
    if name:
        note = "DNS PTR 反向解析结果，尚未验证目标 Windows 的实际 Computer Name。"
        if len(name.split(".", 1)[0]) == 15:
            note += " 名称主体长度为 15，若现场主机名更长，建议执行深度验证。"
        return name, "DNS_PTR", note

    name = _nbtstat_name(ip, timeout)
    if name:
        note = "通过 NetBIOS 名称表识别，属于快速识别结果。"
        if len(name) >= 15:
            note += " NetBIOS 名称可能受 15 字符限制而被截断，建议执行深度验证。"
        return name, "NETBIOS", note

    name = _ping_resolved_name(ip, timeout)
    if name:
        return name, "WINDOWS_RESOLVER", "通过 Windows 名称解析获得，尚未通过目标机命令验证。"

    note = "未获取到计算机名。"
    if smb_open:
        note += " TCP 445 可达，但 SMB/NTLM 未返回可用名称；"
        if smb_note:
            note += f"快速 SMB 识别信息：{smb_note} "
    note += "可使用‘深度验证主机名’，优先通过 SMB/WKSSVC（445）读取 Computer Name，必要时再回退 WinRM。"
    return "", "", note


def scan_host(ip: str, timeout: float, network: str = "") -> DiscoveryResult | None:
    ports = {p: _port_open(ip, p, timeout) for p in WINDOWS_PORTS}
    if not any(ports.values()):
        return None
    hostname, source, note = quick_hostname(ip, timeout, smb_open=ports[445])
    return DiscoveryResult(
        host=ip,
        hostname=hostname,
        port_445=ports[445],
        port_3389=ports[3389],
        port_5985=ports[5985],
        port_5986=ports[5986],
        network=network,
        hostname_source=source,
        hostname_verified=(source == "NETAPI_WKSTA"),
        hostname_note=note,
    )


def scan_networks(networks, timeout: float = 0.45, workers: int = 64,
                  on_result=None, on_progress=None, cancelled=None):
    networks = normalize_networks(networks)
    if not networks:
        raise ValueError("请至少填写一个 IPv4 CIDR 网段。")

    targets = []
    seen_ips = set()
    for cidr in networks:
        net = ipaddress.ip_network(cidr, strict=False)
        for addr in net.hosts():
            ip = str(addr)
            if ip not in seen_ips:
                seen_ips.add(ip)
                targets.append((ip, cidr))
    if len(targets) > MAX_SCAN_ADDRESSES:
        raise ValueError(
            f"当前扫描范围合计包含 {len(targets)} 个地址。"
            f"界面单次扫描上限为 {MAX_SCAN_ADDRESSES} 个地址，请拆分为更小的网段后重试。"
        )

    total = len(targets)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {ex.submit(scan_host, ip, timeout, cidr): (ip, cidr) for ip, cidr in targets}
        for fut in as_completed(futures):
            if cancelled and cancelled():
                for f in futures:
                    f.cancel()
                break
            done += 1
            try:
                r = fut.result()
            except Exception:
                r = None
            if r and on_result:
                on_result(r)
            if on_progress:
                on_progress(done, total)


def scan_network(cidr: str, timeout: float = 0.45, workers: int = 64,
                 on_result=None, cancelled=None):
    return scan_networks([cidr], timeout, workers, on_result=on_result, cancelled=cancelled)

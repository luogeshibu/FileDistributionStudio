from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SmbIdentity:
    hostname: str = ""
    dns_hostname: str = ""
    netbios_hostname: str = ""
    domain: str = ""
    source: str = ""
    note: str = ""


def _decode_utf16(value) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, tuple):
            value = value[1]
        if isinstance(value, str):
            return value.rstrip("\x00").strip()
        return bytes(value).decode("utf-16le", errors="ignore").rstrip("\x00").strip()
    except Exception:
        return ""


def _short_dns_name(value: str) -> str:
    value = (value or "").strip().strip(".")
    return value.split(".", 1)[0] if value else ""



def windows_netwksta_identity(host: str) -> SmbIdentity:
    """Read the remote Windows Computer Name through NetWkstaGetInfo level 100.

    This uses the Windows NetAPI32 workstation-management API available on the
    machine running File Distribution Studio.  It is intentionally best-effort
    and is only attempted for hosts whose TCP/445 is already known reachable.
    No password is supplied by this function.
    """
    import os
    if os.name != "nt":
        return SmbIdentity(note="Windows NetAPI 仅能在 Windows 客户端执行。")

    try:
        import ctypes
        from ctypes import wintypes

        class WKSTA_INFO_100(ctypes.Structure):
            _fields_ = [
                ("wki100_platform_id", wintypes.DWORD),
                ("wki100_computername", wintypes.LPWSTR),
                ("wki100_langroup", wintypes.LPWSTR),
                ("wki100_ver_major", wintypes.DWORD),
                ("wki100_ver_minor", wintypes.DWORD),
            ]

        netapi = ctypes.WinDLL("Netapi32.dll")
        netapi.NetWkstaGetInfo.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)
        ]
        netapi.NetWkstaGetInfo.restype = wintypes.DWORD
        netapi.NetApiBufferFree.argtypes = [ctypes.c_void_p]
        netapi.NetApiBufferFree.restype = wintypes.DWORD

        buf = ctypes.c_void_p()
        # NetWkstaGetInfo accepts a remote server string.  Windows environments
        # commonly accept an IP here as well, e.g. \\172.16.21.115.
        server = rf"\\{host}"
        status = int(netapi.NetWkstaGetInfo(server, 100, ctypes.byref(buf)))
        if status != 0 or not buf.value:
            return SmbIdentity(note=f"Windows NetWkstaGetInfo(100) 未返回名称，状态码：{status}。")
        try:
            info = ctypes.cast(buf, ctypes.POINTER(WKSTA_INFO_100)).contents
            hostname = (info.wki100_computername or "").strip().rstrip(".")
            domain = (info.wki100_langroup or "").strip()
            if not hostname or hostname == host:
                return SmbIdentity(note="Windows NetWkstaGetInfo(100) 未返回可用 Computer Name。")
            return SmbIdentity(
                hostname=hostname,
                netbios_hostname=hostname,
                domain=domain,
                source="NETAPI_WKSTA",
                note=(
                    "通过 Windows NetWkstaGetInfo(100) 从目标工作站服务读取 Computer Name；"
                    "无需 WinRM，也未提供密码。"
                ),
            )
        finally:
            netapi.NetApiBufferFree(buf)
    except Exception as e:
        return SmbIdentity(note=f"Windows NetAPI 名称读取失败：{e}")

def smb_ntlm_identity(host: str, timeout: float = 2.0) -> SmbIdentity:
    """Best-effort SMB2/3 NTLM challenge fingerprint without credentials.

    Modern Windows SMB servers normally include the NetBIOS/DNS computer name
    in the NTLM TargetInfo AV pairs. This is useful when DNS PTR is missing and
    NetBIOS UDP/137 cannot cross the routed network. No password is sent.

    This result is a protocol identity hint, not the same as executing
    ``hostname`` on the remote computer, so it remains marked unverified.
    """
    try:
        from impacket import ntlm
        from impacket.smbconnection import SMBConnection, SMB_DIALECT
        from impacket.smb3structs import (
            SMB2SessionSetup,
            SMB2_SESSION_SETUP,
            SMB2SessionSetup_Response,
        )
        from impacket.nt_errors import STATUS_MORE_PROCESSING_REQUIRED
    except Exception as e:
        return SmbIdentity(note=f"SMB 名称指纹组件不可用：{e}")

    conn = None
    try:
        conn = SMBConnection(host, host, sess_port=445, timeout=max(1.0, float(timeout)))
        if conn.getDialect() == SMB_DIALECT:
            return SmbIdentity(note="目标仅协商到 SMB1，当前快速 SMB 名称指纹仅处理 SMB2/SMB3。")

        # Follow Impacket's own SMB relay client negotiate flow: send the raw
        # NTLM Type-1 token in SMB2 SESSION_SETUP and parse the returned
        # STATUS_MORE_PROCESSING_REQUIRED buffer as the NTLM Type-2 challenge.
        negotiate = ntlm.getNTLMSSPType1("", "", signingRequired=False, use_ntlmv2=True).getData()

        client = conn.getSMBServer()
        session_setup = SMB2SessionSetup()
        session_setup["Flags"] = 0
        session_setup["SecurityBufferLength"] = len(negotiate)
        session_setup["Buffer"] = negotiate

        packet = client.SMB_PACKET()
        packet["Command"] = SMB2_SESSION_SETUP
        packet["Data"] = session_setup
        packet_id = client.sendSMB(packet)
        answer = client.recvSMB(packet_id)
        if not answer.isValidAnswer(STATUS_MORE_PROCESSING_REQUIRED):
            return SmbIdentity(note="SMB 服务器未返回可解析的 NTLM Challenge。")
        try:
            client._Session["SessionID"] = answer["SessionID"]
        except Exception:
            pass

        response = SMB2SessionSetup_Response(answer["Data"])
        token = bytes(response["Buffer"])
        pos = token.find(b"NTLMSSP\x00")
        if pos > 0:
            token = token[pos:]
        if not token.startswith(b"NTLMSSP\x00"):
            return SmbIdentity(note="SMB NTLM Challenge 中未找到 NTLMSSP 数据。")

        challenge = ntlm.NTLMAuthChallenge()
        challenge.fromString(token)
        av = ntlm.AV_PAIRS(challenge["TargetInfoFields"])

        dns_host = _decode_utf16(av[ntlm.NTLMSSP_AV_DNS_HOSTNAME])
        nb_host = _decode_utf16(av[ntlm.NTLMSSP_AV_HOSTNAME])
        dns_domain = _decode_utf16(av[ntlm.NTLMSSP_AV_DNS_DOMAINNAME])
        nb_domain = _decode_utf16(av[ntlm.NTLMSSP_AV_DOMAINNAME])
        hostname = _short_dns_name(dns_host) or nb_host
        if not hostname:
            return SmbIdentity(note="SMB/NTLM 指纹可访问，但目标未公布计算机名称字段。")

        note = "通过目标主机 TCP 445 的 SMB/NTLM Challenge 直接读取计算机身份信息；无需 WinRM、无需密码。"
        if dns_host:
            note += f" DNS 主机名：{dns_host}。"
        if nb_host and nb_host.casefold() != hostname.casefold():
            note += f" NetBIOS 名称：{nb_host}。"
        note += " 该结果来自目标协议响应，可信度高，但仍可用 SMB/WKSSVC 或 WinRM 做命令级验证。"
        return SmbIdentity(
            hostname=hostname,
            dns_hostname=dns_host,
            netbios_hostname=nb_host,
            domain=dns_domain or nb_domain,
            source="SMB_NTLM",
            note=note,
        )
    except Exception as e:
        return SmbIdentity(note=f"SMB/NTLM 名称指纹失败：{e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def split_windows_username(value: str) -> tuple[str, str]:
    """Return (domain, user) for DOMAIN\\user, user@domain or plain user."""
    value = (value or "").strip()
    if "\\" in value:
        domain, user = value.split("\\", 1)
        return domain.strip(), user.strip()
    if "@" in value:
        user, domain = value.split("@", 1)
        return domain.strip(), user.strip()
    return "", value


def verify_hostname_smb_rpc(host: str, username: str, password: str, timeout: float = 6.0) -> tuple[str, str]:
    """Verify exact Windows Computer Name through SMB/WKSSVC over TCP 445.

    Requires credentials accepted by the target SMB service. It does not need
    WinRM to be enabled. Returns ``(hostname, note)`` and raises on failure.
    """
    from impacket.smbconnection import SMBConnection
    from impacket.dcerpc.v5 import transport, wkst

    domain, user = split_windows_username(username)
    if not user:
        raise ValueError("SMB 深度验证需要 Windows 用户名。")

    conn = SMBConnection(host, host, sess_port=445, timeout=max(2.0, float(timeout)))
    try:
        conn.login(user, password, domain)

        # If the authenticated NTLM exchange already yielded a full server name,
        # keep it as a fallback. WKSSVC is preferred because it is the remote
        # workstation service's ComputerName value.
        auth_name = ""
        try:
            auth_name = _short_dns_name(conn.getServerDNSHostName()) or (conn.getServerName() or "").strip()
            if auth_name == host:
                auth_name = ""
        except Exception:
            pass

        rpc = transport.DCERPCTransportFactory(r"ncacn_np:445[\pipe\wkssvc]")
        rpc.set_smb_connection(conn)
        dce = rpc.get_dce_rpc()
        try:
            dce.connect()
            dce.bind(wkst.MSRPC_UUID_WKST)
            resp = wkst.hNetrWkstaGetInfo(dce, 100)
            raw = resp["WkstaInfo"]["WkstaInfo100"]["wki100_computername"]
            hostname = str(raw).rstrip("\x00").strip()
        finally:
            try:
                dce.disconnect()
            except Exception:
                pass

        hostname = hostname or auth_name
        if not hostname:
            raise RuntimeError("WKSSVC 未返回 Windows Computer Name。")
        return hostname, "已通过 SMB/WKSSVC（TCP 445）读取目标 Windows 的 Computer Name 并验证；不依赖 WinRM。"
    finally:
        try:
            conn.logoff()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

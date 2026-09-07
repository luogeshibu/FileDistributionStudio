from __future__ import annotations

"""Windows-protected credential storage for File Distribution Studio.

Passwords are intentionally never written to settings.json, SQLite, JSONL or audit logs.
On Windows we use the native Credential Manager (CredRead/CredWrite) with a generic
credential.  On non-Windows platforms the store reports unavailable; this keeps the
source tree importable/testable without weakening the Windows security contract.
"""

from dataclasses import dataclass
import os
import re


DEFAULT_TARGET = "FileDistributionStudio:WinRM:Default"
HOST_TARGET_PREFIX = "FileDistributionStudio:WinRM:Host:"


@dataclass(frozen=True)
class StoredCredential:
    username: str
    password: str


def host_target(host: str) -> str:
    # Credential Manager target names are opaque strings, but normalize whitespace and
    # avoid control characters so accidental malformed host text cannot create odd keys.
    safe = re.sub(r"[\x00-\x1f\x7f]+", "", (host or "").strip())
    return HOST_TARGET_PREFIX + safe


def is_available() -> bool:
    return os.name == "nt"


def _api():
    if os.name != "nt":
        raise RuntimeError("Windows 凭据管理器仅在 Windows 上可用。")

    import ctypes
    from ctypes import wintypes

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    class CREDENTIAL_ATTRIBUTEW(ctypes.Structure):
        _fields_ = [
            ("Keyword", wintypes.LPWSTR),
            ("Flags", wintypes.DWORD),
            ("ValueSize", wintypes.DWORD),
            ("Value", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    PCREDENTIAL_ATTRIBUTEW = ctypes.POINTER(CREDENTIAL_ATTRIBUTEW)

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", PCREDENTIAL_ATTRIBUTEW),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    PCREDENTIALW = ctypes.POINTER(CREDENTIALW)
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)

    advapi32.CredWriteW.argtypes = [PCREDENTIALW, wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None

    return ctypes, wintypes, advapi32, CREDENTIALW, PCREDENTIALW, CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE, ERROR_NOT_FOUND


def write(target: str, username: str, password: str) -> None:
    """Save one credential in Windows Credential Manager.

    The password is stored as an opaque UTF-16LE credential blob protected by Windows.
    """
    if not target:
        raise ValueError("凭据目标不能为空。")
    if not username:
        raise ValueError("用户名不能为空。")
    if password is None:
        raise ValueError("密码不能为空。")

    ctypes, wintypes, advapi32, CREDENTIALW, _, CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE, _ = _api()
    blob = str(password).encode("utf-16-le")
    # Generic credentials have a bounded blob size; a Windows account password is far
    # smaller in normal operation.  Fail clearly instead of silently truncating.
    if len(blob) > 2560:
        raise ValueError("密码长度超过 Windows 凭据管理器允许范围。")
    blob_buf = (ctypes.c_ubyte * max(1, len(blob)))()
    if blob:
        ctypes.memmove(blob_buf, blob, len(blob))

    cred = CREDENTIALW()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = str(target)
    cred.Comment = "File Distribution Studio WinRM credential"
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(blob_buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = str(username)

    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        err = ctypes.get_last_error()
        raise OSError(err, f"保存 Windows 凭据失败，错误码={err}")


def read(target: str) -> StoredCredential | None:
    if not target:
        return None
    ctypes, _, advapi32, _, PCREDENTIALW, CRED_TYPE_GENERIC, _, ERROR_NOT_FOUND = _api()
    out = PCREDENTIALW()
    if not advapi32.CredReadW(str(target), CRED_TYPE_GENERIC, 0, ctypes.byref(out)):
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return None
        raise OSError(err, f"读取 Windows 凭据失败，错误码={err}")
    try:
        cred = out.contents
        username = cred.UserName or ""
        if cred.CredentialBlob and cred.CredentialBlobSize:
            raw = ctypes.string_at(cred.CredentialBlob, int(cred.CredentialBlobSize))
            password = raw.decode("utf-16-le")
        else:
            password = ""
        return StoredCredential(username=username, password=password)
    finally:
        advapi32.CredFree(ctypes.cast(out, ctypes.c_void_p))


def delete(target: str) -> None:
    if not target:
        return
    ctypes, _, advapi32, _, _, CRED_TYPE_GENERIC, _, ERROR_NOT_FOUND = _api()
    if not advapi32.CredDeleteW(str(target), CRED_TYPE_GENERIC, 0):
        err = ctypes.get_last_error()
        if err != ERROR_NOT_FOUND:
            raise OSError(err, f"删除 Windows 凭据失败，错误码={err}")

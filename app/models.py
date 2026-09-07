from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import uuid


@dataclass
class HostRecord:
    id: Optional[int]
    name: str
    host: str
    group_name: str = "默认"
    target_mode: str = "WINRM"
    # 兼容旧数据库字段。v0.5.0 起目标目录属于“分发映射”，不再作为主机属性使用。
    default_target: str = ""
    os_hint: str = ""
    last_seen: str = ""
    notes: str = ""
    hostname_source: str = ""
    hostname_verified: int = 0
    hostname_note: str = ""
    online_status: str = "UNTESTED"
    ping_ok: int = 0
    smb_port_ok: int = 0
    rdp_port_ok: int = 0
    winrm_port_ok: int = 0
    smb_status: str = "UNTESTED"
    winrm_status: str = "UNTESTED"
    last_test_at: str = ""


@dataclass
class ManifestEntry:
    absolute_path: Path
    relative_path: str
    size: int
    sha256: str = ""


@dataclass
class DistributionMapping:
    """一条“源 -> 目标目录”分发映射。

    密码仅用于本次进程内的 SFTP 拉取，不应写入持久化日志。
    """
    source_type: str
    source_path: str
    target_path: str
    source_kind: str = "FILE"  # FILE | DIR
    folder_mode: str = "CONTENTS"  # CONTENTS | SELF
    mapping_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    sftp_host: str = ""
    sftp_port: int = 22
    sftp_username: str = ""
    sftp_password: str = ""

    def display_source(self) -> str:
        if self.source_type == "SFTP":
            return f"{self.sftp_host}:{self.source_path}"
        return self.source_path

    def safe_dict(self) -> dict:
        return {
            "mapping_id": self.mapping_id,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "source_kind": self.source_kind,
            "folder_mode": self.folder_mode,
            "sftp_host": self.sftp_host,
            "sftp_port": self.sftp_port,
            "sftp_username": self.sftp_username,
        }


@dataclass
class PreparedMapping:
    mapping: DistributionMapping
    manifest: list[ManifestEntry]
    manifest_sha256: str
    source_display: str = ""

    @property
    def file_count(self) -> int:
        return len(self.manifest)

    @property
    def total_bytes(self) -> int:
        return sum(int(x.size) for x in self.manifest)


@dataclass
class DiscoveryResult:
    host: str
    hostname: str
    port_445: bool
    port_3389: bool
    port_5985: bool
    port_5986: bool
    network: str = ""
    hostname_source: str = ""
    hostname_verified: bool = False
    hostname_note: str = ""

    @property
    def status(self) -> str:
        if self.port_445 and (self.port_3389 or self.port_5985 or self.port_5986):
            return "Likely Windows"
        if self.port_445:
            return "SMB"
        if self.port_3389 or self.port_5985 or self.port_5986:
            return "Likely Windows"
        return "Online / Unknown"

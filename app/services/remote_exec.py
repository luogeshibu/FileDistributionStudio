"""Retired WinRM compatibility surface.

The ``codex/windows-agent`` branch has one supported Windows transport:
the FDS Agent HTTPS service.  This small module intentionally keeps only the
data/helpers needed by old settings and dormant UI objects so importing an
old saved layout remains harmless.  It must never create a WinRM session.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RemoteActionPlan:
    """Legacy shape retained for settings/task deserialization only."""

    enabled: bool = False
    use_https: bool = False
    port: int = 0
    username: str = ""
    password: str = ""
    command_timeout: int = 30
    read_timeout_sec: int = 40
    command_workdir: str = ""
    command_execution_mode: str = ""
    kill_processes: list[str] = field(default_factory=list)
    pre_commands: list[str] = field(default_factory=list)
    post_commands: list[str] = field(default_factory=list)
    post_on_failure: bool = False


class WinRMExecutor:
    """Fail closed: the Agent branch never opens a WinRM connection."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "当前 Windows Agent 分支已移除 WinRM 传输。"
            "请在目标主机安装 FDS Agent，并使用 Agent HTTPS 配对。"
        )


def qualify_windows_username(username: str, host_name: str = "") -> str:
    """Preserve the old formatting helper without implying WinRM support."""

    return (username or "").strip()


def split_items(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = str(value or "").replace("\n", ";").split(";")
    return [str(item).strip() for item in raw if str(item).strip()]


def split_commands(value: str | None) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def describe_winrm_failure(host: str, plan: RemoteActionPlan | None, error) -> str:
    return (
        f"[{host}] WinRM 已从当前 Agent 分支移除，未建立远程连接。"
        "请在目标主机安装 FDS Agent，并使用 Agent HTTPS 配对。"
    )

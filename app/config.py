from dataclasses import dataclass, asdict, field
import json
from .paths import settings_path, cache_dir, audit_dir


@dataclass
class AppSettings:
    max_concurrency: int = 4
    socket_timeout: float = 0.45
    discovery_workers: int = 64
    discovery_ranges: list[str] = field(default_factory=list)
    retry_count: int = 1
    verify_sha256: bool = True
    backup_existing: bool = True
    preflight_check: bool = True
    cache_path: str = str(cache_dir())
    audit_path: str = str(audit_dir())
    default_backup_root: str = ""
    min_free_space_margin_mb: int = 256
    winrm_use_https: bool = False
    winrm_port: int = 5985
    winrm_default_username: str = ""
    remember_winrm_default_credential: bool = True
    winrm_host_usernames: dict[str, str] = field(default_factory=dict)
    winrm_command_workdir: str = ""
    winrm_remote_actions_enabled: bool = False
    winrm_pre_commands_text: str = ""
    winrm_kill_processes: list[str] = field(default_factory=list)
    winrm_post_commands_text: str = ""
    winrm_post_on_failure: bool = True
    winrm_command_execution_mode: str = "INTERACTIVE"
    distribution_target_selection_initialized: bool = False
    distribution_target_checks: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "AppSettings":
        p = settings_path()
        if not p.exists():
            obj = cls()
            obj.save()
            return obj
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            base = asdict(cls())
            base.update({k: v for k, v in raw.items() if k in base})
            if not isinstance(base.get("discovery_ranges"), list):
                base["discovery_ranges"] = []
            if not isinstance(base.get("winrm_host_usernames"), dict):
                base["winrm_host_usernames"] = {}
            if not isinstance(base.get("winrm_kill_processes"), list):
                base["winrm_kill_processes"] = []
            if not isinstance(base.get("distribution_target_checks"), dict):
                base["distribution_target_checks"] = {}
            else:
                base["distribution_target_checks"] = {
                    str(k): bool(v) for k, v in base["distribution_target_checks"].items()
                    if str(k).strip()
                }
            if base.get("winrm_command_execution_mode") not in ("INTERACTIVE", "WINRM_BACKGROUND"):
                base["winrm_command_execution_mode"] = "INTERACTIVE"
            return cls(**base)
        except Exception:
            return cls()

    def save(self) -> None:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

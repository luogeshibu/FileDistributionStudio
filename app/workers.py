from pathlib import Path
from dataclasses import replace
from datetime import datetime, timezone
import getpass
import platform
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from PySide6.QtCore import QThread, Signal

from .models import HostRecord, DistributionMapping, PreparedMapping
from .version import APP_VERSION
from .config import AppSettings
from .utils import human_bytes
from .services.manifest import build_manifest, manifest_digest
from .services.sftp_source import SftpSource
from .services.winrm_distribution import distribute_plan_to_host_winrm, test_winrm_targets
from .services.remote_exec import RemoteActionPlan, WinRMExecutor, qualify_windows_username, describe_winrm_failure
from .services.discovery import scan_networks
from .services.smb_identity import verify_hostname_smb_rpc
from .services import audit
from .services.host_status import test_host_connectivity
from . import db


class DiscoveryThread(QThread):
    result_found = Signal(object)
    log = Signal(str)
    progress = Signal(int, int)
    completed = Signal(str)

    def __init__(self, cidrs: list[str], timeout: float, workers: int):
        super().__init__()
        self.cidrs = list(cidrs)
        self.timeout = timeout
        self.workers = workers
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            self.log.emit("正在扫描：" + ", ".join(self.cidrs))
            scan_networks(
                self.cidrs,
                self.timeout,
                self.workers,
                on_result=lambda r: self.result_found.emit(r),
                on_progress=lambda done, total: self.progress.emit(done, total),
                cancelled=self._cancel.is_set,
            )
            self.completed.emit("Cancelled" if self._cancel.is_set() else "Completed")
        except Exception as e:
            self.log.emit(f"主机发现失败：{e}")
            self.completed.emit("Failed")


class DiscoveryReconcileThread(QThread):
    """Re-check saved hosts that were not rediscovered in the current scan.

    The scan itself intentionally only returns Windows-like hosts.  For an IP that
    used to exist in the host library but is missing from the new scan, this worker
    distinguishes "reachable but no Windows signals" from simple offline/unreachable
    so the UI can make a safe cleanup suggestion without deleting anything silently.
    """
    completed = Signal(object)  # list[(host, HostConnectivity)]

    def __init__(self, hosts: list[str], timeout: float, max_workers: int = 16):
        super().__init__()
        self.hosts = list(dict.fromkeys(hosts))
        self.timeout = timeout
        self.max_workers = max(1, min(32, max_workers))

    def run(self):
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {ex.submit(test_host_connectivity, h, self.timeout): h for h in self.hosts}
            for fut in as_completed(futures):
                h = futures[fut]
                try:
                    results.append((h, fut.result()))
                except Exception:
                    results.append((h, None))
        self.completed.emit(results)


class HostnameVerificationThread(QThread):
    result = Signal(str, str, str, bool, str)  # host, hostname, source, verified, message
    progress = Signal(int, int)
    log = Signal(str)
    completed = Signal(int, int)  # success, failed

    def __init__(self, hosts: list[str], username: str, password: str,
                 use_https: bool = False, port: int = 5985,
                 max_workers: int = 8, method: str = "AUTO"):
        super().__init__()
        self.hosts = list(dict.fromkeys(hosts))
        self.username = username
        self.password = password
        self.use_https = use_https
        self.port = port
        self.method = (method or "AUTO").upper()
        self.max_workers = max(1, min(16, max_workers))
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def _verify_smb(self, host: str):
        try:
            hostname, note = verify_hostname_smb_rpc(
                host, self.username, self.password, timeout=8.0
            )
            if not hostname:
                return host, "", "SMB_RPC", False, "SMB/WKSSVC 未返回 Windows Computer Name。"
            return host, hostname, "SMB_RPC", True, note
        except Exception as e:
            return host, "", "SMB_RPC", False, str(e)

    def _verify_winrm(self, host: str):
        plan = RemoteActionPlan(
            enabled=True,
            use_https=self.use_https,
            port=self.port,
            username=self.username,
            password=self.password,
            command_timeout=20,
        )
        try:
            r = WinRMExecutor(host, plan).run_cmd("hostname")
            if r["exit_code"] != 0:
                detail = r["stderr"] or r["stdout"] or f"退出码={r['exit_code']}"
                return host, "", "WINRM", False, detail
            lines = [x.strip() for x in (r["stdout"] or "").splitlines() if x.strip()]
            hostname = lines[0] if lines else ""
            if not hostname:
                return host, "", "WINRM", False, "远程 hostname 未返回名称。"
            return host, hostname, "WINRM", True, "已通过目标 Windows 主机的 hostname 命令验证。"
        except Exception as e:
            return host, "", "WINRM", False, str(e)

    def _verify_one(self, host: str):
        if self.method == "SMB":
            return self._verify_smb(host)
        if self.method == "WINRM":
            return self._verify_winrm(host)

        # AUTO 仅用于“主机名识别”。SMB/WKSSVC 在很多现场无需 WinRM 即可得到精确
        # Computer Name，因此优先尝试；它不会参与正式文件分发、备份或远程控制。
        smb_result = self._verify_smb(host)
        if smb_result[3]:
            return smb_result
        winrm_result = self._verify_winrm(host)
        if winrm_result[3]:
            return winrm_result
        message = (
            f"SMB/WKSSVC 验证失败：{smb_result[4]} | "
            f"WinRM 验证失败：{winrm_result[4]}"
        )
        return host, "", "AUTO", False, message

    def run(self):
        success = failed = done = 0
        total = len(self.hosts)
        method_text = {
            "AUTO": "自动识别（SMB/WKSSVC 优先，WinRM 备用；仅用于主机名）",
            "SMB": "SMB/WKSSVC（TCP 445）",
            "WINRM": "WinRM hostname",
        }.get(self.method, self.method)
        self.log.emit(f"开始主机名深度验证，共 {total} 台主机；方式：{method_text}。")
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, total))) as ex:
            futures = {ex.submit(self._verify_one, host): host for host in self.hosts}
            for fut in as_completed(futures):
                if self._cancel.is_set():
                    for f in futures:
                        f.cancel()
                    break
                host, hostname, source, verified, message = fut.result()
                done += 1
                if verified:
                    success += 1
                else:
                    failed += 1
                self.result.emit(host, hostname, source, verified, message)
                self.progress.emit(done, total)
        self.completed.emit(success, failed)


class HostStatusTestThread(QThread):
    result = Signal(str, object)  # host, HostConnectivity
    completed = Signal(int, int)  # online, offline

    def __init__(self, hosts: list[str], timeout: float = 0.55, max_workers: int = 16):
        super().__init__()
        self.hosts = list(dict.fromkeys(hosts))
        self.timeout = timeout
        self.max_workers = max(1, min(32, max_workers))

    def _one(self, host: str):
        return host, test_host_connectivity(host, self.timeout)

    def run(self):
        online = offline = 0
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(self.hosts)))) as ex:
            futures = [ex.submit(self._one, h) for h in self.hosts]
            for fut in as_completed(futures):
                host, result = fut.result()
                if result.online_status == "OFFLINE":
                    offline += 1
                else:
                    online += 1
                self.result.emit(host, result)
        self.completed.emit(online, offline)


class WinRMTargetTestThread(QThread):
    result = Signal(str, bool, str)  # host, success, message
    completed = Signal(int, int)

    def __init__(self, hosts: list[str], target_paths: list[str] | str,
                 credentials: dict[str, tuple[str, str, str]], use_https: bool = False,
                 port: int = 5985, max_workers: int = 8):
        super().__init__()
        self.hosts = list(dict.fromkeys(hosts))
        if isinstance(target_paths, str):
            target_paths = [target_paths]
        self.target_paths = list(dict.fromkeys(x.strip() for x in target_paths if x and x.strip()))
        self.credentials = dict(credentials or {})
        self.use_https = bool(use_https)
        self.port = int(port)
        self.max_workers = max(1, min(16, max_workers))

    def _one(self, host: str):
        username, password, source = self.credentials.get(host, ("", "", "未设置"))
        if not username or not password:
            return host, False, f"{source}缺少用户名或密码。"
        try:
            resolved_username = qualify_windows_username(username, "")
            plan = RemoteActionPlan(
                enabled=False, use_https=self.use_https, port=self.port,
                username=resolved_username, password=password, command_timeout=30,
            )
            r = test_winrm_targets(host, self.target_paths, plan)
            return host, True, f"{r['message']} 凭据来源：{source}；账号：{resolved_username}。"
        except Exception as e:
            return host, False, f"凭据来源：{source}；账号：{username}。{e}"

    def run(self):
        success = failed = 0
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(self.hosts)))) as ex:
            futures = [ex.submit(self._one, h) for h in self.hosts]
            for fut in as_completed(futures):
                host, ok, msg = fut.result()
                if ok:
                    success += 1
                else:
                    failed += 1
                self.result.emit(host, ok, msg)
        self.completed.emit(success, failed)


class HostEnvironmentCheckThread(QThread):
    """Read-only Windows environment inspection via WinRM."""
    result = Signal(str, bool, object)
    completed = Signal(int, int)

    def __init__(self, hosts: list[HostRecord], credentials: dict[str, tuple[str, str, str]],
                 use_https: bool = False, port: int = 5985, max_workers: int = 8):
        super().__init__()
        self.hosts=list(hosts or [])
        self.credentials=dict(credentials or {})
        self.use_https=bool(use_https); self.port=int(port)
        self.max_workers=max(1,min(16,int(max_workers or 8)))

    def _one(self, hrec: HostRecord):
        username,password,source=self.credentials.get(hrec.host,("","","未设置"))
        if not username or not password:
            return hrec.host,False,{"error":f"{source}缺少用户名或密码。"}
        try:
            username=qualify_windows_username(username,hrec.name)
            plan=RemoteActionPlan(enabled=False,use_https=self.use_https,port=self.port,
                                  username=username,password=password,command_timeout=30)
            ps=r'''$ErrorActionPreference='Stop'
$profiles=@()
try {
  $profiles=@(Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object {
    [pscustomobject]@{Name=[string]$_.Name;Enabled=[bool]$_.Enabled;DefaultInboundAction=[string]$_.DefaultInboundAction;DefaultOutboundAction=[string]$_.DefaultOutboundAction}
  })
} catch { $profiles=@() }
$svc=Get-Service -Name W32Time -ErrorAction SilentlyContinue
$tz=''
try { $tz=(Get-TimeZone).Id } catch { $tz=[TimeZoneInfo]::Local.Id }
[pscustomobject]@{
  UtcNow=[DateTimeOffset]::UtcNow.ToString('o')
  TimeZone=$tz
  TimeService=if($null -eq $svc){'NotFound'}else{[string]$svc.Status}
  FirewallProfiles=$profiles
} | ConvertTo-Json -Depth 5 -Compress'''
            t0=datetime.now(timezone.utc)
            rr=WinRMExecutor(hrec.host,plan).run_ps(ps)
            t1=datetime.now(timezone.utc)
            if int(rr.get('exit_code',1)) != 0:
                raise RuntimeError(rr.get('stderr') or rr.get('stdout') or f"退出码={rr.get('exit_code')}")
            payload=json.loads((rr.get('stdout') or '').strip())
            raw=str(payload.get('UtcNow') or '').replace('Z','+00:00')
            remote=datetime.fromisoformat(raw)
            if remote.tzinfo is None:
                remote=remote.replace(tzinfo=timezone.utc)
            midpoint=t0+(t1-t0)/2
            drift=(remote.astimezone(timezone.utc)-midpoint).total_seconds()
            profiles=payload.get('FirewallProfiles') or []
            if isinstance(profiles,dict): profiles=[profiles]
            if not profiles:
                fw='无法读取'
            else:
                enabled=[str(x.get('Name','?')) for x in profiles if bool(x.get('Enabled'))]
                disabled=[str(x.get('Name','?')) for x in profiles if not bool(x.get('Enabled'))]
                if not enabled: fw='全部关闭'
                elif not disabled: fw='全部开启'
                else: fw='部分开启（' + ','.join(enabled) + '）'
            return hrec.host,True,{
                'time_drift_seconds':round(drift,3), 'remote_utc':remote.astimezone(timezone.utc).isoformat(),
                'local_utc':midpoint.isoformat(), 'time_zone':str(payload.get('TimeZone') or '未知'),
                'time_service':str(payload.get('TimeService') or '未知'), 'firewall_profiles':profiles,
                'firewall_summary':fw, 'credential_source':source,
            }
        except Exception as exc:
            return hrec.host,False,{'error':str(exc),'credential_source':source}

    def run(self):
        success=failed=0
        with ThreadPoolExecutor(max_workers=min(self.max_workers,max(1,len(self.hosts)))) as ex:
            futures=[ex.submit(self._one,h) for h in self.hosts]
            for fut in as_completed(futures):
                host,ok,data=fut.result()
                if ok: success+=1
                else: failed+=1
                self.result.emit(host,ok,data)
        self.completed.emit(success,failed)


class DistributionThread(QThread):
    log = Signal(str)
    prepared = Signal(str, int, int)  # task_id, files, bytes
    host_status = Signal(str, str, str)  # host, status, detail
    mapping_status = Signal(str, str, str)  # mapping_id, status, detail
    file_progress = Signal(str, int, int, str, str)
    completed = Signal(str, int, int)  # status, success, failed

    def __init__(self, mappings: list[DistributionMapping], hosts: list[HostRecord],
                 credentials: dict[str, tuple[str, str, str]], settings: AppSettings,
                 remote_plan: RemoteActionPlan | None = None,
                 backup_root_path: str = ""):
        super().__init__()
        self.mappings = list(mappings)
        self.hosts = hosts
        self.credentials = dict(credentials or {})
        self.settings = settings
        self.remote_plan = remote_plan or RemoteActionPlan(enabled=False)
        self.backup_root_path = backup_root_path or ""
        self._cancel = threading.Event()
        self.task_id = "DIST-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]

    def cancel(self):
        self._cancel.set()
        audit.operation(self.settings.audit_path, "TASK", "CANCEL_REQUEST", "CANCELLED",
                        "用户请求取消分发任务。", task_id=self.task_id)

    def _log(self, text):
        self.log.emit(text)
        try:
            audit.task_log(self.settings.audit_path, self.task_id, text)
        except Exception:
            pass


    def _credential_for(self, hrec: HostRecord) -> tuple[str, str, str]:
        username, password, source = self.credentials.get(hrec.host, ("", "", "未设置"))
        username = qualify_windows_username(username, hrec.name)
        if not username or not password:
            raise RuntimeError(f"{hrec.host} 缺少 WinRM 凭据（{source}）。")
        return username, password, source

    def _precheck_winrm_auth(self):
        """在生成本地/SFTP 清单之前先验证所有目标主机的 WinRM 会话。

        业务操作从 v0.6.0 起只走 WinRM。主机发现可以继续使用 Ping/445/3389/DNS/SMB
        等辅助信号，但这些辅助通道绝不会参与文件写入、备份、服务或进程控制。
        """
        if not self.hosts:
            raise RuntimeError("没有目标主机。")

        failures = []

        def one(hrec):
            resolved_username, password, source = self._credential_for(hrec)
            plan = replace(
                self.remote_plan,
                enabled=False,
                username=resolved_username,
                password=password,
            )
            try:
                result = WinRMExecutor(hrec.host, plan).test()
                if int(result.get("exit_code", 1)) != 0:
                    detail = result.get("stderr") or result.get("stdout") or f"退出码={result.get('exit_code')}"
                    raise RuntimeError(describe_winrm_failure(hrec.host, plan, detail))
                return hrec, True, resolved_username, source, ""
            except Exception as exc:
                msg = str(exc)
                if not msg.startswith("WinRM"):
                    msg = describe_winrm_failure(hrec.host, plan, exc)
                return hrec, False, resolved_username, source, msg

        self._log("开始 WinRM 前置认证检查：只有全部目标主机认证通过后，才生成文件清单并进入分发。")
        with ThreadPoolExecutor(max_workers=min(max(1, self.settings.max_concurrency), max(1, len(self.hosts)))) as ex:
            futures = [ex.submit(one, hrec) for hrec in self.hosts]
            for fut in as_completed(futures):
                hrec, ok, resolved_username, source, message = fut.result()
                if ok:
                    self.host_status.emit(hrec.host, "READY", "WinRM 认证通过")
                    self._log(f"[{hrec.host}] WinRM 认证通过；凭据来源：{source}；实际认证账号：{resolved_username}")
                    audit.operation(
                        self.settings.audit_path, "WINRM", "AUTH_PREFLIGHT", "SUCCESS",
                        "正式分发前 WinRM 身份验证通过。", task_id=self.task_id, host=hrec.host,
                        details={"username": resolved_username, "auth_source": source, "port": self.remote_plan.port,
                                 "https": self.remote_plan.use_https},
                    )
                else:
                    self.host_status.emit(hrec.host, "FAILED", "WinRM 认证失败")
                    self._log(f"[{hrec.host}] WinRM 前置认证失败；凭据来源：{source}；实际认证账号：{resolved_username}；{message}")
                    failures.append((hrec.host, message))
                    audit.operation(
                        self.settings.audit_path, "WINRM", "AUTH_PREFLIGHT", "FAILED",
                        message, task_id=self.task_id, host=hrec.host,
                        details={"username": resolved_username, "auth_source": source, "port": self.remote_plan.port,
                                 "https": self.remote_plan.use_https},
                    )

        if failures:
            summary = "；".join(f"{host}: {msg}" for host, msg in failures[:3])
            if len(failures) > 3:
                summary += f"；另有 {len(failures)-3} 台失败"
            raise RuntimeError(
                "WinRM 前置认证未全部通过，任务已在读取/上传任何分发源之前停止。"
                f" 失败主机数={len(failures)}。{summary}"
            )

    def _prepare_mapping(self, mapping: DistributionMapping) -> PreparedMapping:
        if mapping.source_type == "LOCAL":
            source = Path(mapping.source_path)
            if not source.exists():
                raise FileNotFoundError(f"分发源不存在：{source}")
            audit.operation(
                self.settings.audit_path, "SOURCE", "LOCAL_SELECTED", "SUCCESS",
                "使用本地文件/目录作为分发源。", task_id=self.task_id,
                subject=str(source), details=mapping.safe_dict(),
            )
        elif mapping.source_type == "SFTP":
            staged_root = Path(self.settings.cache_path) / self.task_id / mapping.mapping_id
            staged_root.mkdir(parents=True, exist_ok=True)
            self._log(f"正在从 SFTP 拉取：{mapping.display_source()} → {staged_root}")
            audit.operation(
                self.settings.audit_path, "SOURCE", "SFTP_PULL_START", "SUCCESS",
                "开始从 SFTP 拉取分发映射源。", task_id=self.task_id,
                subject=mapping.source_path,
                details=mapping.safe_dict(),
            )
            sftp = SftpSource(
                mapping.sftp_host, int(mapping.sftp_port or 22),
                mapping.sftp_username, mapping.sftp_password,
            )
            source = sftp.download(mapping.source_path, staged_root, self._log)
            audit.operation(
                self.settings.audit_path, "SOURCE", "SFTP_PULL_FINISH", "SUCCESS",
                "SFTP 分发源已完整拉取到本机缓存。", task_id=self.task_id,
                subject=str(source), details={"mapping_id": mapping.mapping_id},
            )
        else:
            raise ValueError(f"不支持的来源类型：{mapping.source_type}")

        actual_kind = "DIR" if source.is_dir() else "FILE"
        if mapping.source_kind != actual_kind:
            audit.operation(
                self.settings.audit_path, "MAPPING", "SOURCE_KIND_CORRECT", "SUCCESS",
                "实际源类型与配置不一致，已按实际文件系统类型执行。", task_id=self.task_id,
                subject=mapping.mapping_id, details={"configured": mapping.source_kind, "actual": actual_kind},
            )
            mapping.source_kind = actual_kind
            if actual_kind == "FILE":
                mapping.folder_mode = "CONTENTS"
        self._log(f"正在生成映射清单：{mapping.display_source()} → {mapping.target_path}")
        if mapping.source_kind == "DIR":
            self._log("目录策略：合并覆盖（安全）——源目录同名文件强制覆盖、缺少文件新增；目标目录额外文件保留，不执行删除。")
        manifest = build_manifest(source, verify_sha256=self.settings.verify_sha256, progress=self._log)
        if mapping.source_kind == "DIR" and mapping.folder_mode == "SELF":
            prefix = source.name
            for entry in manifest:
                entry.relative_path = f"{prefix}/{entry.relative_path}" if entry.relative_path else prefix
        digest = manifest_digest(manifest)
        return PreparedMapping(mapping=mapping, manifest=manifest, manifest_sha256=digest,
                               source_display=str(source))

    def _check_collisions(self, prepared: list[PreparedMapping]):
        import ntpath
        seen = {}
        for pm in prepared:
            base = pm.mapping.target_path.rstrip("\\/")
            for entry in pm.manifest:
                rel = entry.relative_path.replace("/", "\\")
                final_display = base + "\\" + rel
                final = ntpath.normcase(ntpath.normpath(final_display))
                if final in seen:
                    other = seen[final]
                    raise RuntimeError(
                        "检测到目标文件冲突，已阻止分发：\n"
                        f"{final_display}\n"
                        f"来源 1：{other}\n来源 2：{entry.absolute_path}"
                    )
                seen[final] = str(entry.absolute_path)

    def run(self):
        verification_mode = "SHA256" if self.settings.verify_sha256 else "SIZE"
        task_created = False
        try:
            if not self.mappings:
                raise RuntimeError("没有配置任何分发映射。")
            source_summary = f"{len(self.mappings)} 条分发映射"
            target_summary = "多目标目录" if len({m.target_path for m in self.mappings}) > 1 else self.mappings[0].target_path
            db.create_task(
                self.task_id, "MULTI", source_summary, target_summary, len(self.hosts),
                backup_root=self.backup_root_path, verification_mode=verification_mode,
                audit_path=self.settings.audit_path, operator=getpass.getuser(),
                workstation=platform.node(), app_version=APP_VERSION,
            )
            task_created = True
            audit.start_task(
                self.settings.audit_path, self.task_id,
                {
                    "source_type": "MULTI",
                    "mappings": [m.safe_dict() for m in self.mappings],
                    "hosts": [{"name": h.name, "host": h.host, "group": h.group_name} for h in self.hosts],
                    "backup_existing": self.settings.backup_existing,
                    "backup_root": self.backup_root_path,
                    "verification_mode": verification_mode,
                    "retry_count": self.settings.retry_count,
                    "max_concurrency": self.settings.max_concurrency,
                    "preflight_check": self.settings.preflight_check,
                    "remote_actions_enabled": self.remote_plan.enabled,
                    "transport": "WINRM",
                    "winrm_port": self.remote_plan.port,
                    "winrm_https": self.remote_plan.use_https,
                    "operator": getpass.getuser(), "workstation": platform.node(),
                    "app_version": APP_VERSION,
                },
            )

            # 先验证 WinRM 身份，不让用户在认证失败时还等待本地/SFTP 清单和 SHA256。
            self._precheck_winrm_auth()

            prepared = []
            for mapping in self.mappings:
                if self._cancel.is_set():
                    raise RuntimeError("用户已取消任务")
                self.mapping_status.emit(mapping.mapping_id, "PREPARING", "正在生成清单")
                pm = self._prepare_mapping(mapping)
                if not pm.manifest:
                    raise RuntimeError(f"分发映射没有可分发文件：{mapping.display_source()}")
                prepared.append(pm)
                self.mapping_status.emit(mapping.mapping_id, "READY", f"{pm.file_count} 个文件 / {pm.total_bytes} 字节")
                db.record_task_mapping(
                    self.task_id, mapping.mapping_id, mapping.source_type,
                    mapping.display_source(), mapping.target_path, mapping.source_kind,
                    mapping.folder_mode, pm.file_count, pm.total_bytes, pm.manifest_sha256,
                )

            self._check_collisions(prepared)
            total_files = sum(pm.file_count for pm in prepared)
            total_bytes = sum(pm.total_bytes for pm in prepared)
            # 整个任务的指纹使用每条映射指纹和目标路径组合，确保目标映射变化也能被审计识别。
            import hashlib
            h = hashlib.sha256()
            for pm in prepared:
                h.update(pm.mapping.mapping_id.encode("utf-8"))
                h.update(pm.mapping.target_path.encode("utf-8"))
                h.update(pm.manifest_sha256.encode("ascii"))
            task_digest = h.hexdigest()
            db.update_task_manifest(self.task_id, total_files, total_bytes, task_digest)
            # 保留传统 manifest.json，同时写入“目标路径感知”的映射清单摘要。
            flat = []
            for pm in prepared:
                for e in pm.manifest:
                    flat.append(e)
            audit.write_manifest(self.settings.audit_path, self.task_id, flat, task_digest)
            audit.write_mapping_plan(self.settings.audit_path, self.task_id, prepared, task_digest)
            audit.operation(
                self.settings.audit_path, "VERIFY", "MAPPING_PLAN", "SUCCESS",
                "多源多目标分发计划已生成并完成冲突检查。", task_id=self.task_id,
                subject=task_digest,
                details={"mapping_count": len(prepared), "file_count": total_files,
                         "total_bytes": total_bytes,
                         "mappings": [{**pm.mapping.safe_dict(), "file_count": pm.file_count,
                                       "total_bytes": pm.total_bytes,
                                       "manifest_sha256": pm.manifest_sha256} for pm in prepared]},
            )
            self._log(f"分发计划：{len(prepared)} 条映射，{total_files} 个文件，共 {total_bytes} 字节")
            self._log(f"任务计划指纹 SHA256：{task_digest}")
            self.prepared.emit(self.task_id, total_files, total_bytes)

            success = failed = 0
            with ThreadPoolExecutor(max_workers=max(1, self.settings.max_concurrency)) as ex:
                futures = {}
                for hrec in self.hosts:
                    self.host_status.emit(hrec.host, "WAITING", "")
                    username, password, source = self._credential_for(hrec)
                    host_plan = replace(self.remote_plan, username=username, password=password)
                    fut = ex.submit(
                        distribute_plan_to_host_winrm,
                        self.task_id, hrec, prepared, username, password,
                        self.settings.verify_sha256, self.settings.backup_existing,
                        self.settings.retry_count, self._cancel.is_set,
                        lambda host, msg: self._log(f"[{host}] {msg}"),
                        lambda host, i, total, rel, status: self.file_progress.emit(host, i, total, rel, status),
                        host_plan, self.backup_root_path, self.settings.audit_path,
                        self.settings.preflight_check, self.settings.min_free_space_margin_mb,
                    )
                    futures[fut] = hrec

                for fut in as_completed(futures):
                    r = fut.result()
                    if r["status"] == "SUCCESS":
                        success += 1
                    else:
                        failed += 1
                    detail = (f"新增={r['new']} 更新={r['updated']} 跳过={r['skipped']} 失败={r['failed']}")
                    if r.get("error"):
                        detail += f" | {r['error']}"
                    self.host_status.emit(r["host"], r["status"], detail)

            if self._cancel.is_set():
                status = "CANCELLED"
            elif failed:
                status = "PARTIAL_FAILED" if success else "FAILED"
            else:
                status = "SUCCESS"
            db.finish_task(self.task_id, status, success, failed)
            for pm in prepared:
                self.mapping_status.emit(pm.mapping.mapping_id, status, f"主机成功 {success}/{len(self.hosts)}，失败 {failed}")
            audit.finish_task(
                self.settings.audit_path, self.task_id,
                {"status": status, "success_hosts": success, "failed_hosts": failed,
                 "host_count": len(self.hosts), "mapping_count": len(prepared),
                 "file_count": total_files, "total_bytes": total_bytes,
                 "manifest_sha256": task_digest, "backup_root": self.backup_root_path,
                 "verification_mode": verification_mode},
            )
            self.completed.emit(status, success, failed)
        except Exception as e:
            self.log.emit(f"分发任务失败：{e}")
            status = "CANCELLED" if self._cancel.is_set() else "FAILED"
            if task_created:
                try:
                    db.finish_task(self.task_id, status, 0, len(self.hosts))
                except Exception:
                    pass
            try:
                audit.operation(self.settings.audit_path, "TASK", "EXCEPTION", status,
                                str(e), task_id=self.task_id)
                audit.finish_task(self.settings.audit_path, self.task_id,
                                  {"status": status, "success_hosts": 0,
                                   "failed_hosts": len(self.hosts), "error": str(e)})
            except Exception:
                pass
            self.completed.emit(status, 0, len(self.hosts))


class DryRunThread(DistributionThread):
    """Read-only execution-plan validation.

    Dry Run deliberately never uploads, replaces, kills, starts/stops, backs up, or
    launches Version Checker.  It may read local/SFTP sources to build the manifest,
    and uses WinRM only for authentication and read-only remote path / free-space
    checks.  Target paths that do not yet exist are reported as warnings because the
    real distribution path is allowed to create them during its write preflight.
    """
    dry_completed = Signal(object)

    def __init__(self, mappings: list[DistributionMapping], hosts: list[HostRecord],
                 credentials: dict[str, tuple[str, str, str]], settings: AppSettings,
                 remote_plan: RemoteActionPlan | None = None, backup_root_path: str = "",
                 version_config: dict | None = None):
        super().__init__(mappings, hosts, credentials, settings,
                         remote_plan or RemoteActionPlan(enabled=False), backup_root_path)
        self.task_id = "DRYRUN-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        self.version_config = dict(version_config or {})

    def cancel(self):
        self._cancel.set()
        audit.operation(self.settings.audit_path, "PREFLIGHT", "DRY_RUN_CANCEL_REQUEST", "CANCELLED",
                        "用户请求取消 Dry Run。", task_id=self.task_id)

    @staticmethod
    def _target_bytes(prepared: list[PreparedMapping]) -> dict[str, int]:
        totals: dict[str, int] = {}
        for pm in prepared:
            key = (pm.mapping.target_path or "").strip()
            totals[key] = totals.get(key, 0) + int(pm.total_bytes)
        return totals

    def _check_host(self, hrec: HostRecord, prepared: list[PreparedMapping]) -> dict:
        username, password, source = self._credential_for(hrec)
        plan = replace(self.remote_plan, enabled=False, username=username, password=password)
        executor = WinRMExecutor(hrec.host, plan)
        issues: list[str] = []
        warnings: list[str] = []
        details: list[str] = []

        try:
            r = executor.test()
            if int(r.get("exit_code", 1)) != 0:
                raise RuntimeError(r.get("stderr") or r.get("stdout") or f"退出码={r.get('exit_code')}")
            details.append(f"WinRM认证通过（{source}）")
        except Exception as exc:
            return {"host": hrec.host, "name": hrec.name or hrec.host, "status": "FAILED",
                    "issues": [describe_winrm_failure(hrec.host, plan, exc)], "warnings": [], "details": []}

        target_totals = self._target_bytes(prepared)
        margin = max(0, int(self.settings.min_free_space_margin_mb)) * 1024 * 1024
        for target, bytes_needed in target_totals.items():
            try:
                info = executor.path_info(target)
                if info.get("exists"):
                    if not info.get("is_dir"):
                        issues.append(f"目标路径已存在但不是目录：{target}")
                    else:
                        details.append(f"目标目录存在：{target}")
                else:
                    warnings.append(f"目标目录不存在，正式执行时将尝试创建：{target}")
                free = executor.free_space(target)
                if free is not None:
                    required = int(bytes_needed) + margin
                    # 使用目标目录内置备份时，同盘至少再预留一份源文件规模。
                    if self.settings.backup_existing and not (self.backup_root_path or "").strip():
                        required += int(bytes_needed)
                    if free < required:
                        issues.append(
                            f"目标磁盘空间不足：{target} 可用 {human_bytes(free)}，预计至少需要 {human_bytes(required)}"
                        )
                    else:
                        details.append(f"目标盘空间通过：{target} 可用 {human_bytes(free)}")
            except Exception as exc:
                issues.append(f"读取目标路径失败：{target}；{exc}")

        if self.settings.backup_existing and (self.backup_root_path or "").strip():
            root = self.backup_root_path.strip()
            try:
                info = executor.path_info(root)
                if info.get("exists") and not info.get("is_dir"):
                    issues.append(f"备份根路径已存在但不是目录：{root}")
                elif info.get("exists"):
                    details.append(f"备份根目录存在：{root}")
                else:
                    warnings.append(f"备份根目录不存在，正式执行时将自动创建：{root}")
                free = executor.free_space(root)
                if free is not None:
                    backup_required = sum(int(pm.total_bytes) for pm in prepared) + margin
                    if free < backup_required:
                        issues.append(
                            f"备份盘空间不足：{root} 可用 {human_bytes(free)}，预计至少需要 {human_bytes(backup_required)}"
                        )
                    else:
                        details.append(f"备份盘空间通过：{root} 可用 {human_bytes(free)}")
            except Exception as exc:
                issues.append(f"读取备份根目录失败：{root}；{exc}")

        if self.remote_plan.enabled and (self.remote_plan.command_workdir or "").strip():
            wd = self.remote_plan.command_workdir.strip()
            try:
                info = executor.path_info(wd)
                if not info.get("exists") or not info.get("is_dir"):
                    issues.append(f"CMD 工作目录不存在或不是目录：{wd}")
                else:
                    details.append(f"CMD 工作目录存在：{wd}")
            except Exception as exc:
                issues.append(f"读取 CMD 工作目录失败：{wd}；{exc}")

        if self.version_config.get("enabled"):
            exe = str(self.version_config.get("exe_path") or "").strip()
            wd = str(self.version_config.get("workdir") or "").strip()
            out = str(self.version_config.get("output_dir") or "").strip()
            for label, path, expect_dir in (("Version Checker 程序", exe, False),
                                            ("Version Checker 工作目录", wd, True),
                                            ("Version Checker CSV目录", out, True)):
                if not path:
                    issues.append(f"{label}未配置")
                    continue
                try:
                    info = executor.path_info(path)
                    if not info.get("exists"):
                        issues.append(f"{label}不存在：{path}")
                    elif bool(info.get("is_dir")) != bool(expect_dir):
                        issues.append(f"{label}类型不正确：{path}")
                    else:
                        details.append(f"{label}存在：{path}")
                except Exception as exc:
                    issues.append(f"读取{label}失败：{path}；{exc}")

        status = "FAILED" if issues else ("WARNING" if warnings else "SUCCESS")
        return {"host": hrec.host, "name": hrec.name or hrec.host, "status": status,
                "issues": issues, "warnings": warnings, "details": details}

    def run(self):
        summary = {"task_id": self.task_id, "status": "FAILED", "success": 0,
                   "warning": 0, "failed": 0, "hosts": [], "file_count": 0,
                   "total_bytes": 0, "mapping_count": len(self.mappings), "error": ""}
        try:
            if not self.mappings:
                raise RuntimeError("没有配置任何启用的分发映射。")
            self._log("Dry Run 开始：只做读取与检查，不上传、不覆盖、不备份、不结束进程、不执行 CMD、不启动 Version Checker。")
            prepared: list[PreparedMapping] = []
            for mapping in self.mappings:
                if self._cancel.is_set():
                    raise RuntimeError("用户已取消 Dry Run")
                self.mapping_status.emit(mapping.mapping_id, "PREPARING", "Dry Run 生成清单")
                pm = self._prepare_mapping(mapping)
                if not pm.manifest:
                    raise RuntimeError(f"分发映射没有可分发文件：{mapping.display_source()}")
                prepared.append(pm)
                self.mapping_status.emit(mapping.mapping_id, "READY", f"Dry Run：{pm.file_count} 个文件 / {human_bytes(pm.total_bytes)}")
            self._check_collisions(prepared)
            summary["file_count"] = sum(pm.file_count for pm in prepared)
            summary["total_bytes"] = sum(pm.total_bytes for pm in prepared)
            self.prepared.emit(self.task_id, summary["file_count"], summary["total_bytes"])
            self._log(f"Dry Run 分发计划：{len(prepared)} 条映射，{summary['file_count']} 个文件，共 {human_bytes(summary['total_bytes'])}。")

            with ThreadPoolExecutor(max_workers=min(max(1, self.settings.max_concurrency), max(1, len(self.hosts)))) as ex:
                futures = {ex.submit(self._check_host, h, prepared): h for h in self.hosts}
                for fut in as_completed(futures):
                    if self._cancel.is_set():
                        break
                    result = fut.result()
                    summary["hosts"].append(result)
                    st = result["status"]
                    if st == "SUCCESS":
                        summary["success"] += 1
                        detail = "预演通过"
                    elif st == "WARNING":
                        summary["warning"] += 1
                        detail = "；".join(result["warnings"][:2]) or "有警告"
                    else:
                        summary["failed"] += 1
                        detail = "；".join(result["issues"][:2]) or "预演失败"
                    self.host_status.emit(result["host"], st, detail)
                    for item in result["details"]:
                        self._log(f"[{result['host']}] ✓ {item}")
                    for item in result["warnings"]:
                        self._log(f"[{result['host']}] ⚠ {item}")
                    for item in result["issues"]:
                        self._log(f"[{result['host']}] ✗ {item}")

            if self._cancel.is_set():
                summary["status"] = "CANCELLED"
            elif summary["failed"]:
                summary["status"] = "FAILED"
            elif summary["warning"]:
                summary["status"] = "WARNING"
            else:
                summary["status"] = "SUCCESS"
            audit.operation(
                self.settings.audit_path, "PREFLIGHT", "DRY_RUN", summary["status"],
                "Dry Run 执行计划检查完成。", task_id=self.task_id,
                details={k: v for k, v in summary.items() if k != "hosts"},
            )
        except Exception as exc:
            summary["error"] = str(exc)
            summary["status"] = "CANCELLED" if self._cancel.is_set() else "FAILED"
            self._log(f"Dry Run 失败：{exc}")
            audit.operation(self.settings.audit_path, "PREFLIGHT", "DRY_RUN", summary["status"],
                            str(exc), task_id=self.task_id)
        self.dry_completed.emit(summary)


class BackupThread(DistributionThread):
    """Standalone backup for the currently enabled distribution mappings.

    The local/SFTP source is used only to build the same target-file manifest that a
    distribution would use.  No file is uploaded or replaced.  For each target host,
    only an existing remote file that corresponds to a manifest entry is copied to the
    backup location.
    """
    completed = Signal(str, int, int)

    def __init__(self, mappings: list[DistributionMapping], hosts: list[HostRecord],
                 credentials: dict[str, tuple[str, str, str]], settings: AppSettings,
                 connection_plan: RemoteActionPlan | None = None, backup_root_path: str = "",
                 remote_paths: list[str] | None = None):
        super().__init__(mappings, hosts, credentials, settings,
                         connection_plan or RemoteActionPlan(enabled=False), backup_root_path)
        self.remote_paths = list(remote_paths or [])
        self.task_id = "BACKUP-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]

    @staticmethod
    def _remote_final_path(mapping: DistributionMapping, relative_path: str) -> str:
        import ntpath
        base = mapping.target_path.rstrip("\\/")
        rel = (relative_path or "").replace("/", "\\")
        return ntpath.normpath(base + "\\" + rel) if rel else ntpath.normpath(base)

    def _backup_destination(self, source_path: str) -> str:
        import ntpath
        src = ntpath.normpath(source_path)
        if self.backup_root_path:
            drive, tail = ntpath.splitdrive(src)
            drive_name = drive.rstrip(":\\/") or "ROOT"
            tail = tail.lstrip("\\/")
            return ntpath.join(self.backup_root_path, self.task_id, drive_name, tail)
        parent = ntpath.dirname(src)
        name = ntpath.basename(src)
        return ntpath.join(parent, ".fds_backup", self.task_id, name)

    def run(self):
        try:
            if not self.mappings and not self.remote_paths:
                raise RuntimeError("没有指定任何需要备份的目标文件。")
            prepared=[]
            if not self.remote_paths:
                for mapping in self.mappings:
                    if self._cancel.is_set(): break
                    pm=self._prepare_mapping(mapping)
                    prepared.append(pm)
                    self._log(f"备份清单已生成：{mapping.target_path}，{pm.file_count} 个目标文件。")
            else:
                self._log(f"单独备份文件清单：{len(self.remote_paths)} 个目标路径。")
            if self._cancel.is_set():
                self.completed.emit("CANCELLED",0,0); return

            success=failed=0
            def one(hrec):
                username,password,source=self._credential_for(hrec)
                plan=replace(self.remote_plan,enabled=False,username=username,password=password)
                ex=WinRMExecutor(hrec.host,plan)
                self.host_status.emit(hrec.host,"RUNNING","备份中")
                if self.backup_root_path:
                    import ntpath
                    task_backup_root = ntpath.join(self.backup_root_path, self.task_id)
                    ex.ensure_directory(task_backup_root)
                    self._log(f"[{hrec.host}] 备份目录已就绪：{task_backup_root}（不存在自动创建，已存在直接使用）")
                copied=skipped=0
                targets=list(self.remote_paths)
                if not targets:
                    for pm in prepared:
                        for entry in pm.manifest:
                            targets.append(self._remote_final_path(pm.mapping,entry.relative_path))
                for remote in targets:
                    if self._cancel.is_set(): return hrec,"CANCELLED",copied,skipped,"已取消"
                    info=ex.path_info(remote)
                    if not info.get("exists"):
                        skipped+=1
                        self._log(f"[{hrec.host}] 跳过（目标不存在）：{remote}")
                        continue
                    dest=self._backup_destination(remote)
                    if info.get("is_dir"):
                        ex.copy_remote_directory(remote,dest,overwrite=True)
                        copied+=1
                        self._log(f"[{hrec.host}] 已递归备份目录：{remote} → {dest}")
                    else:
                        ex.copy_remote_file(remote,dest,overwrite=True)
                        copied+=1
                        self._log(f"[{hrec.host}] 已备份文件：{remote} → {dest}")
                return hrec,"SUCCESS",copied,skipped,""

            with ThreadPoolExecutor(max_workers=max(1,self.settings.max_concurrency)) as pool:
                futures=[pool.submit(one,h) for h in self.hosts]
                for fut in as_completed(futures):
                    try:
                        h,status,copied,skipped,msg=fut.result()
                    except Exception as e:
                        failed+=1; self._log(f"备份主机失败：{e}"); continue
                    if status=="SUCCESS":
                        success+=1
                        detail=f"备份={copied}，远端不存在/跳过={skipped}"
                        self.host_status.emit(h.host,"SUCCESS",detail)
                        audit.operation(self.settings.audit_path,"BACKUP","STANDALONE","SUCCESS","单独备份完成。",task_id=self.task_id,host=h.host,details={"copied":copied,"skipped":skipped,"backup_root":self.backup_root_path})
                    elif status=="CANCELLED":
                        self.host_status.emit(h.host,"CANCELLED",msg)
                    else:
                        failed+=1; self.host_status.emit(h.host,"FAILED",msg)
            status="CANCELLED" if self._cancel.is_set() else ("SUCCESS" if failed==0 else ("PARTIAL_FAILED" if success else "FAILED"))
            self.completed.emit(status,success,failed)
        except Exception as e:
            self._log(f"备份任务失败：{e}")
            self.completed.emit("CANCELLED" if self._cancel.is_set() else "FAILED",0,len(self.hosts))


class VersionCheckThread(QThread):
    log = Signal(str)
    host_status = Signal(str, str, str)
    completed = Signal(str, int, int, str)

    def __init__(self, hosts: list[HostRecord], credentials: dict[str, tuple[str, str, str]], settings: AppSettings,
                 connection_plan: RemoteActionPlan, exe_path: str, workdir: str, output_dir: str,
                 save_button: str, timeout_seconds: int, collect_excel: bool, close_after: bool,
                 local_result_root: str):
        super().__init__()
        self.hosts=list(hosts); self.credentials=dict(credentials or {}); self.settings=settings
        self.connection_plan=connection_plan; self.exe_path=exe_path; self.workdir=workdir; self.output_dir=output_dir
        self.save_button=save_button; self.timeout_seconds=max(15,int(timeout_seconds or 120))
        self.collect_excel=bool(collect_excel); self.close_after=bool(close_after)
        self.local_result_root=local_result_root
        self.task_id='VERCHK-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]
        self._cancel=threading.Event()

    def cancel(self): self._cancel.set()
    def _log(self,text): self.log.emit(text)

    def _credential_for(self,hrec):
        username,password,source=self.credentials.get(hrec.host,("","","未设置"))
        username=qualify_windows_username(username,hrec.name)
        if not username or not password: raise RuntimeError(f"{hrec.host} 缺少 WinRM 凭据（{source}）。")
        return username,password,source

    def run(self):
        base=Path(self.local_result_root).expanduser()/self.task_id
        base.mkdir(parents=True,exist_ok=True)
        success=failed=0
        def one(hrec):
            if self._cancel.is_set(): return hrec,'CANCELLED','已取消',''
            try:
                username,password,source=self._credential_for(hrec)
                plan=replace(self.connection_plan,enabled=False,username=username,password=password)
                ex=WinRMExecutor(hrec.host,plan)
                self.host_status.emit(hrec.host,'RUNNING','Version Checker 启动中')
                self._log(f"[{hrec.host}] 启动 Version Checker；凭据来源：{source}")
                result=ex.run_version_checker_export(self.exe_path,self.workdir,self.output_dir,self.save_button,self.timeout_seconds,self.close_after)
                remote_csv=result['csv_path']; local=''
                self._log(f"[{hrec.host}] Save 已触发（{result.get('save_method') or 'unknown'}），Information/OK 已处理（{result.get('ok_method') or 'unknown'}）。")
                if self.collect_excel:
                    safe_host=(hrec.name or "UNKNOWN_HOST").replace('\\','_').replace('/','_').replace(':','_')
                    safe_ip=(hrec.host or "UNKNOWN_IP").replace('\\','_').replace('/','_').replace(':','_')
                    filename=Path(remote_csv.replace('\\','/')).name or 'version_checker_result.csv'
                    # 回收结果直接放到用户选择的本机结果目录根目录，并同时带主机名和 IP，便于多机现场核对。
                    local_root=Path(self.local_result_root).expanduser()
                    local_root.mkdir(parents=True,exist_ok=True)
                    local_path=local_root/f"{safe_host}_{safe_ip}_{filename}"
                    downloaded=ex.download_file(remote_csv,local_path)
                    remote_size=int(result.get('size',0) or 0)
                    if not downloaded.exists():
                        raise RuntimeError(f"CSV 回收后本机文件不存在：{downloaded}")
                    local_size=downloaded.stat().st_size
                    if remote_size and local_size != remote_size:
                        raise RuntimeError(f"CSV 回收大小校验失败：远端={remote_size} 字节，本机={local_size} 字节；{downloaded}")
                    local=str(downloaded.resolve())
                    self._log(f"[{hrec.host}] CSV 已回收到本机：{local}")
                    self._log(f"[{hrec.host}] 本机文件已确认：{local_size} 字节。")
                else:
                    self._log(f"[{hrec.host}] CSV 已生成：{remote_csv}")
                audit.operation(self.settings.audit_path,'VERSION_CHECK','EXPORT','SUCCESS','Version Checker 导出完成。',task_id=self.task_id,host=hrec.host,subject=remote_csv,details={'local_result':local})
                return hrec,'SUCCESS',f"版本检查完成；CSV={'已回收' if self.collect_excel else remote_csv}",local
            except Exception as e:
                msg=str(e); self._log(f"[{hrec.host}] Version Checker 失败：{msg}")
                audit.operation(self.settings.audit_path,'VERSION_CHECK','EXPORT','FAILED',msg,task_id=self.task_id,host=hrec.host)
                return hrec,'FAILED',msg,''
        with ThreadPoolExecutor(max_workers=max(1,self.settings.max_concurrency)) as pool:
            futs=[pool.submit(one,h) for h in self.hosts]
            for fut in as_completed(futs):
                h,status,detail,local=fut.result(); self.host_status.emit(h.host,status,detail)
                if status=='SUCCESS': success+=1
                elif status=='FAILED': failed+=1
        status='CANCELLED' if self._cancel.is_set() else ('SUCCESS' if failed==0 else ('PARTIAL_FAILED' if success else 'FAILED'))
        self.completed.emit(status,success,failed,str(Path(self.local_result_root).expanduser().resolve()) if self.collect_excel else '')

class RemoteFileOperationThread(QThread):
    """远程文件管理独立操作线程。

    只服务“远程文件”人工运维模块，不参与文件分发、备份、Dry Run、版本检查等任务流程。
    """
    progress = Signal(int, int, str)
    log = Signal(str)
    completed = Signal(object)

    def __init__(self, context: dict, operation: str, *, use_https=False, port=5985,
                 remote_path="", local_path="", paths=None, new_name=""):
        super().__init__()
        self.context = dict(context or {})
        self.operation = str(operation or "").upper()
        self.use_https = bool(use_https)
        self.port = int(port)
        self.remote_path = str(remote_path or "")
        self.local_path = str(local_path or "")
        self.paths = list(paths or [])
        self.new_name = str(new_name or "")

    def _executor(self):
        plan = RemoteActionPlan(
            enabled=False,
            use_https=self.use_https,
            port=self.port,
            username=self.context.get("username", ""),
            password=self.context.get("password", ""),
            command_timeout=120,
        )
        return WinRMExecutor(self.context.get("host", ""), plan)

    def _upload_one(self, executor, source: Path, remote_dir: str, counters: dict):
        import ntpath
        if source.is_file():
            dest = ntpath.join(remote_dir, source.name)
            self.log.emit(f"上传：{source} -> {dest}")
            executor.upload_file(source, dest)
            counters["done"] += 1
            self.progress.emit(counters["done"], counters["total"], source.name)
            return
        base_dest = ntpath.join(remote_dir, source.name)
        executor.ensure_directory(base_dest)
        for item in sorted(source.rglob("*")):
            rel = item.relative_to(source)
            remote_item = ntpath.join(base_dest, *rel.parts)
            if item.is_dir():
                executor.ensure_directory(remote_item)
            elif item.is_file():
                self.log.emit(f"上传：{item} -> {remote_item}")
                executor.upload_file(item, remote_item)
                counters["done"] += 1
                self.progress.emit(counters["done"], counters["total"], item.name)

    @staticmethod
    def _count_local_files(paths):
        total = 0
        for raw in paths:
            p = Path(raw)
            if p.is_file():
                total += 1
            elif p.is_dir():
                total += sum(1 for x in p.rglob("*") if x.is_file())
        return total

    def _download_remote_tree(self, executor, remote_source: str, local_dir: Path, counters: dict):
        import ntpath
        info = executor.path_info(remote_source)
        if not info.get("exists"):
            raise FileNotFoundError(f"远程路径不存在：{remote_source}")
        if not info.get("is_dir"):
            dest = local_dir / ntpath.basename(remote_source)
            self.log.emit(f"下载：{remote_source} -> {dest}")
            executor.download_file(remote_source, dest)
            counters["done"] += 1
            self.progress.emit(counters["done"], max(1, counters["total"]), ntpath.basename(remote_source))
            return
        root_name = ntpath.basename(remote_source.rstrip("\\")) or remote_source[:2].replace(":", "")
        target_root = local_dir / root_name
        target_root.mkdir(parents=True, exist_ok=True)
        stack = [(remote_source, target_root)]
        while stack:
            current_remote, current_local = stack.pop()
            current_local.mkdir(parents=True, exist_ok=True)
            for entry in executor.list_directory_entries(current_remote):
                if entry.get("is_dir"):
                    child_local = current_local / entry.get("name", "")
                    stack.append((entry.get("path", ""), child_local))
                else:
                    dest = current_local / entry.get("name", "")
                    self.log.emit(f"下载：{entry.get('path','')} -> {dest}")
                    executor.download_file(entry.get("path", ""), dest)
                    counters["done"] += 1
                    self.progress.emit(counters["done"], max(1, counters["total"]), entry.get("name", ""))

    def _count_remote_files(self, executor, remote_source: str) -> int:
        info = executor.path_info(remote_source)
        if not info.get("exists"):
            return 0
        if not info.get("is_dir"):
            return 1
        total = 0
        stack = [remote_source]
        while stack:
            current = stack.pop()
            for entry in executor.list_directory_entries(current):
                if entry.get("is_dir"):
                    stack.append(entry.get("path", ""))
                else:
                    total += 1
        return total

    def run(self):
        host = self.context.get("host", "")
        try:
            executor = self._executor()
            if self.operation == "LIST":
                if self.remote_path:
                    data = executor.list_directory_entries(self.remote_path)
                    payload = {"ok": True, "kind": "entries", "path": self.remote_path, "data": data}
                else:
                    data = executor.list_drives()
                    try:
                        known_folders = executor.list_known_folders()
                    except Exception:
                        # Known Folders are optional shortcuts. Drive browsing
                        # must remain available even on incompatible targets.
                        known_folders = []
                    payload = {"ok": True, "kind": "drives", "path": "", "data": data, "known_folders": known_folders}
                self.completed.emit(payload); return

            if self.operation == "UPLOAD":
                import ntpath
                valid = [Path(p) for p in self.paths if Path(p).exists()]
                total = self._count_local_files(valid)
                counters = {"done": 0, "total": max(1, total)}
                executor.ensure_directory(self.remote_path)
                for p in valid:
                    self._upload_one(executor, p, self.remote_path, counters)
                self.completed.emit({"ok": True, "kind": "upload", "count": counters["done"], "path": self.remote_path}); return

            if self.operation == "DOWNLOAD":
                local_dir = Path(self.local_path)
                local_dir.mkdir(parents=True, exist_ok=True)
                total = 0
                for remote in self.paths:
                    total += self._count_remote_files(executor, remote)
                counters = {"done": 0, "total": max(1, total)}
                for remote in self.paths:
                    self._download_remote_tree(executor, remote, local_dir, counters)
                self.completed.emit({"ok": True, "kind": "download", "count": counters["done"], "path": str(local_dir)}); return

            if self.operation == "MKDIR":
                executor.ensure_directory(self.remote_path)
                self.completed.emit({"ok": True, "kind": "mkdir", "path": self.remote_path}); return

            if self.operation == "DELETE":
                for remote in self.paths:
                    executor.remove_path(remote)
                self.completed.emit({"ok": True, "kind": "delete", "count": len(self.paths)}); return

            if self.operation == "RENAME":
                executor.rename_path(self.remote_path, self.new_name)
                self.completed.emit({"ok": True, "kind": "rename"}); return

            raise ValueError(f"未知远程文件操作：{self.operation}")
        except Exception as exc:
            self.completed.emit({"ok": False, "kind": self.operation.lower(), "host": host, "error": str(exc)})

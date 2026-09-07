from pathlib import Path
from dataclasses import replace
from datetime import datetime
import getpass
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from PySide6.QtCore import QThread, Signal

from .models import HostRecord, DistributionMapping, PreparedMapping
from .version import APP_VERSION
from .config import AppSettings
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
                self.mapping_status.emit(pm.mapping.mapping_id, status, "任务已结束")
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


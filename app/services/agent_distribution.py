from __future__ import annotations

import ntpath
import time
from pathlib import Path

from .. import db
from ..models import HostRecord
from ..utils import safe_rel
from .agent_client import AgentClient, AgentEndpoint
from . import audit


def _join_remote(base: str, *parts: str) -> str:
    out = (base or "").strip().replace("/", "\\")
    for part in parts:
        value = (part or "").replace("/", "\\").strip("\\")
        if value:
            out = ntpath.join(out, value)
    return ntpath.normpath(out)


def _backup_path(target_root: str, custom_root: str, task_id: str, relative: str, host: str) -> str:
    if (custom_root or "").strip():
        base = custom_root.strip()
        if base.startswith("\\\\"):
            safe_host = host.replace("\\", "_").replace("/", "_").replace(":", "_")
            base = _join_remote(base, task_id, safe_host)
        else:
            base = _join_remote(base, task_id)
    else:
        base = _join_remote(target_root, ".fds_backup", task_id)
    drive, tail = ntpath.splitdrive(target_root.replace("/", "\\"))
    identity = [drive.rstrip(":\\").upper() or "ROOT"] + [x for x in tail.strip("\\").split("\\") if x]
    return _join_remote(base, *identity, *safe_rel(relative).split("/"))


def _client(host_record: HostRecord, token: str) -> AgentClient:
    return AgentClient(AgentEndpoint(
        host=host_record.host,
        port=int(host_record.agent_port or 8743),
        token=token,
        certificate_sha256=host_record.agent_certificate_sha256,
        timeout=10,
    ))


def distribute_plan_to_host_agent(
    task_id: str,
    host_record: HostRecord,
    prepared_mappings,
    token: str,
    verify_sha256: bool,
    backup_existing: bool,
    retry_count: int,
    cancel_cb,
    log_cb,
    file_progress_cb=None,
    byte_progress_cb=None,
    remote_plan=None,
    backup_root_path: str = "",
    audit_root: str = "",
    preflight_check: bool = True,
    min_free_space_margin_mb: int = 256,
):
    """Run the file-only workflow over Agent HTTPS.

    Arbitrary CMDs are intentionally rejected here.  The Agent branch exposes
    only the controlled process-tree and file operations; it never falls back
    to WinRM or executes an arbitrary command string.
    """
    host = host_record.host
    db.start_host(task_id, host, host_record.name)
    transferred = new_files = updated_files = skipped_files = failed_files = 0
    total_files = sum(len(pm.manifest) for pm in prepared_mappings)
    total_bytes = sum(pm.total_bytes for pm in prepared_mappings)
    done_files = completed_bytes = 0
    last_error = ""
    client = None
    plan = remote_plan
    try:
        if not token:
            raise RuntimeError("Agent 配对令牌未设置。")
        if plan and getattr(plan, "enabled", False):
            if getattr(plan, "pre_commands", None) or getattr(plan, "post_commands", None):
                raise RuntimeError("FDS Agent 不执行任意 CMD；当前分支已移除 WinRM，请使用 Agent 支持的进程操作或重启操作。")
        client = _client(host_record, token)
        client.status()
        audit.operation(audit_root, "AGENT", "HOST_START", "SUCCESS",
                        "开始 FDS Agent HTTPS 文件分发工作流。", task_id=task_id, host=host,
                        details={"mapping_count": len(prepared_mappings), "port": host_record.agent_port})
        if plan and getattr(plan, "enabled", False):
            for image_name in getattr(plan, "kill_processes", []) or []:
                log_cb(host, f"Agent 正在结束目标进程树：{image_name}")
                client.kill_process(image_name)

        for pm in prepared_mappings:
            mapping = pm.mapping
            log_cb(host, f"开始 Agent 映射：{mapping.display_source()} → {mapping.target_path}")
            for entry in pm.manifest:
                if cancel_cb():
                    raise RuntimeError("用户已取消任务")
                done_files += 1
                rel = safe_rel(entry.relative_path)
                dest = _join_remote(mapping.target_path, *rel.split("/"))
                source_hash = (entry.sha256 or "").lower()
                existing = client.inspect_file(dest, include_sha256=verify_sha256)
                action = "UPDATE" if existing.get("exists") else "NEW"
                if existing.get("exists") and backup_existing:
                    backup = _backup_path(mapping.target_path, backup_root_path, task_id, rel, host)
                    try:
                        backup_info = client.backup_file(dest, backup)
                        check = client.inspect_file(backup, include_sha256=verify_sha256)
                        if int(check.get("size", -1)) != int(existing.get("size", -2)):
                            raise IOError("Agent 备份文件大小校验不一致")
                        if verify_sha256 and (check.get("sha256") or "").lower() != (existing.get("sha256") or "").lower():
                            raise IOError("Agent 备份文件 SHA256 校验不一致")
                        db.record_backup(task_id, host, dest, dest, backup, existing.get("size", 0),
                                         check.get("sha256") or existing.get("sha256", ""), "SUCCESS")
                    except Exception as exc:
                        db.record_backup(task_id, host, dest, dest, backup, existing.get("size", 0),
                                         existing.get("sha256", ""), "FAILED", str(exc))
                        failed_files += 1
                        last_error = f"备份失败 {dest}：{exc}"
                        db.record_file(task_id, host, rel, action, entry.size, source_hash, "", "FAILED",
                                       last_error, mapping_id=mapping.mapping_id,
                                       source_path=str(entry.absolute_path), target_path=dest)
                        log_cb(host, last_error)
                        if file_progress_cb:
                            file_progress_cb(host, done_files, total_files, dest, "FAILED")
                        continue

                error = ""
                success = False
                for attempt in range(max(0, int(retry_count)) + 1):
                    try:
                        log_cb(host, f"Agent HTTPS 上传：{entry.absolute_path.name} → {dest}")
                        base_bytes = completed_bytes
                        client.upload_file(
                            entry.absolute_path, dest,
                            progress=(lambda sent, _size: byte_progress_cb(host, base_bytes + sent, total_bytes, dest))
                            if byte_progress_cb else None,
                        )
                        final_info = client.inspect_file(dest, include_sha256=verify_sha256)
                        if not final_info.get("exists") or int(final_info.get("size", -1)) != int(entry.size):
                            raise IOError("Agent 上传完成后文件大小校验不一致")
                        if verify_sha256 and (final_info.get("sha256") or "").lower() != source_hash:
                            raise IOError("Agent 上传完成后 SHA256 校验不一致")
                        transferred += entry.size
                        completed_bytes += int(entry.size)
                        if byte_progress_cb:
                            byte_progress_cb(host, completed_bytes, total_bytes, dest)
                        if action == "NEW":
                            new_files += 1
                        else:
                            updated_files += 1
                        db.record_file(task_id, host, rel, action, entry.size, source_hash,
                                       final_info.get("sha256", ""), "SUCCESS",
                                       mapping_id=mapping.mapping_id,
                                       source_path=str(entry.absolute_path), target_path=dest)
                        if file_progress_cb:
                            file_progress_cb(host, done_files, total_files, dest, "SUCCESS")
                        success = True
                        break
                    except Exception as exc:
                        error = str(exc)
                        if attempt < int(retry_count):
                            time.sleep(0.6 * (attempt + 1))
                if not success:
                    failed_files += 1
                    last_error = f"分发失败 {dest}：{error}"
                    db.record_file(task_id, host, rel, action, entry.size, source_hash, "", "FAILED", error,
                                   mapping_id=mapping.mapping_id,
                                   source_path=str(entry.absolute_path), target_path=dest)
                    log_cb(host, last_error)
                    if file_progress_cb:
                        file_progress_cb(host, done_files, total_files, dest, "FAILED")

        status = "SUCCESS" if failed_files == 0 else "FAILED"
        db.finish_host(task_id, host, status, transferred, new_files, updated_files, skipped_files, failed_files, last_error)
        audit.operation(audit_root, "AGENT", "HOST_FINISH", status,
                        "FDS Agent 文件分发工作流结束。", task_id=task_id, host=host,
                        details={"transport": "AGENT_HTTPS", "new": new_files, "updated": updated_files,
                                 "failed": failed_files})
        return {"host": host, "status": status, "new": new_files, "updated": updated_files,
                "skipped": skipped_files, "failed": failed_files, "transferred": transferred,
                "error": last_error}
    except Exception as exc:
        last_error = str(exc)
        db.finish_host(task_id, host, "FAILED", transferred, new_files, updated_files, skipped_files,
                       failed_files or 1, last_error)
        audit.operation(audit_root, "AGENT", "HOST_FINISH", "FAILED", last_error,
                        task_id=task_id, host=host, details={"transport": "AGENT_HTTPS"})
        return {"host": host, "status": "FAILED", "new": new_files, "updated": updated_files,
                "skipped": skipped_files, "failed": failed_files or 1, "transferred": transferred,
                "error": last_error}

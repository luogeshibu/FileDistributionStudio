from __future__ import annotations

import ntpath
import time
from dataclasses import replace
from pathlib import Path

from .. import db
from ..utils import safe_rel, human_bytes
from .remote_exec import RemoteActionPlan, WinRMExecutor, qualify_windows_username, describe_winrm_failure
from . import audit


def _join_remote(base: str, *parts: str) -> str:
    out = (base or "").strip().replace("/", "\\")
    for part in parts:
        p = (part or "").replace("/", "\\").strip("\\")
        if p:
            out = ntpath.join(out, p)
    return ntpath.normpath(out)


def _path_identity(target_path: str) -> list[str]:
    raw = (target_path or "").strip().replace("/", "\\")
    drive, tail = ntpath.splitdrive(raw)
    if drive:
        if drive.startswith("\\"):
            prefix = drive.strip("\\").replace("\\", "_")
        else:
            prefix = drive.rstrip(":\\").upper()
    else:
        prefix = "ROOT"
    parts = [x for x in tail.strip("\\").split("\\") if x]
    return [prefix] + parts


def _backup_file_path(target_root: str, custom_backup_root: str, task_id: str, rel: str, host: str) -> str:
    if (custom_backup_root or "").strip():
        custom = custom_backup_root.strip()
        if custom.startswith("\\\\"):
            safe_host = host.replace("\\", "_").replace("/", "_").replace(":", "_")
            base = _join_remote(custom, task_id, safe_host)
        else:
            base = _join_remote(custom, task_id)
        return _join_remote(base, *_path_identity(target_root), *safe_rel(rel).split("/"))
    return _join_remote(target_root, ".fds_backup", task_id, *safe_rel(rel).split("/"))


def _record_check(task_id, host, rel, stage, algorithm, expected, actual,
                  status, details="", audit_root=""):
    db.record_verification(task_id, host, rel, stage, algorithm,
                           expected, actual, status, details)
    audit.operation(
        audit_root, "VERIFY", stage, status,
        details or f"{algorithm} 校验：{status}",
        task_id=task_id, host=host, subject=rel,
        details={"algorithm": algorithm, "expected": expected, "actual": actual},
    )


def _test_connection(executor: WinRMExecutor, task_id: str, host: str, audit_root: str, log_cb):
    scheme = "HTTPS" if executor.plan.use_https else "HTTP"
    log_cb(host, f"正在连接 WinRM {scheme}:{executor.plan.port}")
    try:
        r = executor.test()
    except Exception as e:
        message = describe_winrm_failure(host, executor.plan, e)
        audit.operation(
            audit_root, "WINRM", "CONNECT", "FAILED", message,
            task_id=task_id, host=host,
            details={"https": executor.plan.use_https, "port": executor.plan.port},
        )
        raise RuntimeError(message) from e
    status = "SUCCESS" if r["exit_code"] == 0 else "FAILED"
    db.record_action(task_id, host, "PRE", "WINRM_TEST", "echo FDS_REMOTE_OK && ver",
                     status, r["exit_code"], r["stdout"], r["stderr"], r["duration_ms"])
    audit.operation(
        audit_root, "WINRM", "CONNECT", status,
        "WinRM 连接与身份验证测试。", task_id=task_id, host=host,
        details={"https": executor.plan.use_https, "port": executor.plan.port,
                 "exit_code": r["exit_code"], "duration_ms": r["duration_ms"]},
    )
    if r["exit_code"] != 0:
        detail = r["stderr"] or r["stdout"] or f"退出码={r['exit_code']}"
        raise RuntimeError(describe_winrm_failure(host, executor.plan, detail))
    log_cb(host, "WinRM 连接成功；文件传输和远程操作将统一通过 WinRM 完成。")


def test_winrm_targets(host: str, target_paths: list[str], plan: RemoteActionPlan) -> dict:
    executor = WinRMExecutor(host, plan)
    try:
        r = executor.test()
    except Exception as e:
        raise RuntimeError(describe_winrm_failure(host, plan, e)) from e
    if r["exit_code"] != 0:
        detail = r["stderr"] or r["stdout"] or f"退出码={r['exit_code']}"
        raise RuntimeError(describe_winrm_failure(host, plan, detail))
    checked = []
    for target in target_paths:
        executor.test_write_path(target)
        checked.append(target)
    identity_lines = [x.strip() for x in (r.get("stdout") or "").splitlines() if x.strip() and x.strip() != "FDS_REMOTE_OK"]
    identity = " / ".join(identity_lines[:2])
    suffix = f" 远端身份：{identity}。" if identity else ""
    if checked:
        message = f"WinRM 连接、身份验证及 {len(checked)} 个目标目录写入测试通过。{suffix}"
    else:
        message = f"WinRM 连接和身份验证通过；当前未添加分发映射，因此未执行目标目录写入测试。{suffix}"
    return {
        "host": host,
        "status": "SUCCESS",
        "targets": checked,
        "message": message,
    }


def _preflight_mapping(executor: WinRMExecutor, task_id: str, host: str, pm,
                       backup_existing: bool, backup_root_path: str,
                       margin_mb: int, audit_root: str, log_cb):
    target_root = pm.mapping.target_path
    temp_root = _join_remote(target_root, ".fds_tmp", task_id, pm.mapping.mapping_id)
    log_cb(host, f"预检查目标目录：{target_root}")
    audit.operation(audit_root, "PREFLIGHT", "START", "SUCCESS", "开始 WinRM 目标目录预检查。",
                    task_id=task_id, host=host, subject=target_root)

    try:
        executor.test_write_path(target_root)
        _record_check(task_id, host, target_root, "TARGET_WRITE_PERMISSION", "WINRM_WRITE_PROBE",
                      "可写", "可写", "SUCCESS", "目标目录 WinRM 写入探针通过。", audit_root)
    except Exception as e:
        _record_check(task_id, host, target_root, "TARGET_WRITE_PERMISSION", "WINRM_WRITE_PROBE",
                      "可写", "不可写", "FAILED", str(e), audit_root)
        raise RuntimeError(f"目标目录不可写：{target_root}；{e}")

    executor.ensure_directory(temp_root)

    if backup_existing:
        if (backup_root_path or "").strip():
            custom = backup_root_path.strip()
            if custom.startswith("\\\\"):
                safe_host = host.replace("\\", "_").replace("/", "_").replace(":", "_")
                backup_probe_root = _join_remote(custom, task_id, safe_host, *_path_identity(target_root))
            else:
                backup_probe_root = _join_remote(custom, task_id, *_path_identity(target_root))
        else:
            backup_probe_root = _join_remote(target_root, ".fds_backup", task_id)
        try:
            executor.test_write_path(backup_probe_root)
            _record_check(task_id, host, backup_probe_root, "BACKUP_WRITE_PERMISSION", "WINRM_WRITE_PROBE",
                          "可写", "可写", "SUCCESS", "备份目录 WinRM 写入探针通过。", audit_root)
        except Exception as e:
            _record_check(task_id, host, backup_probe_root, "BACKUP_WRITE_PERMISSION", "WINRM_WRITE_PROBE",
                          "可写", "不可写", "FAILED", str(e), audit_root)
            raise RuntimeError(f"备份目录不可写：{backup_probe_root}；{e}")

    free = executor.free_space(target_root)
    if free is not None:
        margin = max(0, int(margin_mb)) * 1024 * 1024
        required = int(pm.total_bytes) + margin
        if backup_existing and not (backup_root_path or "").strip():
            required += int(pm.total_bytes)
        st = "SUCCESS" if free >= required else "FAILED"
        _record_check(task_id, host, target_root, "TARGET_FREE_SPACE", "BYTES",
                      str(required), str(free), st,
                      f"目标卷可用 {human_bytes(free)}，预计至少需要 {human_bytes(required)}。", audit_root)
        if free < required:
            raise RuntimeError(
                f"目标磁盘空间不足：{target_root} 可用 {human_bytes(free)}，预计需要 {human_bytes(required)}"
            )
    else:
        _record_check(task_id, host, target_root, "TARGET_FREE_SPACE", "BYTES",
                      "", "", "SKIPPED", "无法读取该路径的卷剩余空间，写入探针已通过。", audit_root)

    audit.operation(audit_root, "PREFLIGHT", "FINISH", "SUCCESS", "WinRM 目标目录预检查通过。",
                    task_id=task_id, host=host, subject=target_root)
    return temp_root


def _run_pre_actions(executor: WinRMExecutor, task_id: str, host: str,
                     plan: RemoteActionPlan, log_cb, audit_root: str):
    if not plan.enabled:
        return False
    audit.operation(audit_root, "WINRM", "PRE_START", "SUCCESS", "开始执行分发前远程操作。",
                    task_id=task_id, host=host,
                    details={"kill_processes": plan.kill_processes,
                             "pre_command_count": len(plan.pre_commands)})
    # ADMS/桌面程序更新场景优先结束已选客户端/GUI进程，再执行 sys_ctl stop
    # 等“分发前 CMD”。这样先释放前台文件占用，再停止平台后台组件，随后进入文件替换。
    for image_name in plan.kill_processes:
        log_cb(host, f"正在结束目标进程（如果存在）：{image_name}")
        executor.kill_process(task_id, image_name)
    for command in plan.pre_commands:
        wd_note = f"（工作目录：{plan.command_workdir}）" if plan.command_workdir else ""
        interactive = plan.command_execution_mode == "INTERACTIVE"
        mode_note = "登录桌面交互式" if interactive else "WinRM 后台"
        log_cb(host, f"执行分发前 CMD [{mode_note}]{wd_note}：{command}")
        if interactive:
            result = executor.run_interactive_audited(task_id, "PRE", "CMD_INTERACTIVE", command, workdir=plan.command_workdir)
            if result.get("interactive_user"):
                sid = result.get("interactive_session_id", -1)
                sid_note = f"（Session {sid}）" if isinstance(sid, int) and sid >= 0 else ""
                log_cb(host, f"分发前 CMD 已在目标机登录桌面用户 {result['interactive_user']}{sid_note} 会话中执行。")
        else:
            executor.run_audited(task_id, "PRE", "CMD", command, workdir=plan.command_workdir)
    return True


def _run_post_actions(executor: WinRMExecutor, task_id: str, host: str,
                      plan: RemoteActionPlan, log_cb, audit_root: str):
    if not plan.enabled:
        return []
    errors = []
    for command in plan.post_commands:
        try:
            wd_note = f"（工作目录：{plan.command_workdir}）" if plan.command_workdir else ""
            interactive = plan.command_execution_mode == "INTERACTIVE"
            mode_note = "登录桌面交互式" if interactive else "WinRM 后台"
            log_cb(host, f"执行分发后 CMD [{mode_note}]{wd_note}：{command}")
            if interactive:
                result = executor.run_interactive_audited(task_id, "POST", "CMD_INTERACTIVE", command, workdir=plan.command_workdir)
                if result.get("interactive_user"):
                    log_cb(host, f"分发后 CMD 已在目标机登录桌面用户 {result['interactive_user']} 会话中执行。")
            else:
                executor.run_audited(task_id, "POST", "CMD", command, workdir=plan.command_workdir)
        except Exception as e:
            errors.append(str(e))
            audit.operation(audit_root, "WINRM", "POST_CMD", "FAILED", str(e),
                            task_id=task_id, host=host, subject=command)
    return errors


def distribute_plan_to_host_winrm(
    task_id,
    host_record,
    prepared_mappings,
    username: str,
    password: str,
    verify_sha256: bool,
    backup_existing: bool,
    retry_count: int,
    cancel_cb,
    log_cb,
    file_progress_cb=None,
    remote_plan: RemoteActionPlan | None = None,
    backup_root_path: str = "",
    audit_root: str = "",
    preflight_check: bool = True,
    min_free_space_margin_mb: int = 256,
):
    host = host_record.host
    db.start_host(task_id, host, host_record.name)
    base_plan = remote_plan or RemoteActionPlan(enabled=False, username=username, password=password)
    # 每台主机都创建独立的计划副本，避免并发主机之间互相覆盖本地账号的 COMPUTER\user。
    raw_username = base_plan.username or username
    plan = replace(
        base_plan,
        username=qualify_windows_username(raw_username, host_record.name),
        password=base_plan.password or password,
    )

    transferred = new_files = updated_files = skipped_files = failed_files = 0
    last_error = ""
    distribution_success = False
    pre_actions_started = False
    total_files = sum(len(pm.manifest) for pm in prepared_mappings)
    done_files = 0
    temp_roots: list[str] = []
    executor = None

    audit.operation(
        audit_root, "DISTRIBUTION", "HOST_START", "SUCCESS",
        "开始 WinRM 多目标目录主机分发工作流。", task_id=task_id, host=host,
        details={"hostname": host_record.name, "mapping_count": len(prepared_mappings),
                 "transport": "WINRM", "port": plan.port, "https": plan.use_https},
    )

    try:
        if cancel_cb():
            raise RuntimeError("用户已取消任务")

        executor = WinRMExecutor(host, plan)
        _test_connection(executor, task_id, host, audit_root, log_cb)

        # 在任何结束进程之前，先验证所有真实 Windows 目标目录可写。
        if preflight_check:
            for pm in prepared_mappings:
                temp_roots.append(_preflight_mapping(
                    executor, task_id, host, pm, backup_existing, backup_root_path,
                    min_free_space_margin_mb, audit_root, log_cb,
                ))
        else:
            for pm in prepared_mappings:
                temp_root = _join_remote(pm.mapping.target_path, ".fds_tmp", task_id, pm.mapping.mapping_id)
                executor.ensure_directory(temp_root)
                temp_roots.append(temp_root)

        if cancel_cb():
            raise RuntimeError("用户已取消任务")

        pre_actions_started = bool(plan.enabled)
        _run_pre_actions(executor, task_id, host, plan, log_cb, audit_root)

        for pm, temp_root in zip(prepared_mappings, temp_roots):
            mapping = pm.mapping
            log_cb(host, f"开始映射：{mapping.display_source()} → {mapping.target_path}")
            audit.operation(
                audit_root, "DISTRIBUTION", "MAPPING_START", "SUCCESS",
                "开始执行 WinRM 分发映射。", task_id=task_id, host=host,
                subject=mapping.mapping_id, details=mapping.safe_dict(),
            )

            for entry in pm.manifest:
                if cancel_cb():
                    raise RuntimeError("用户已取消任务")
                done_files += 1
                rel = safe_rel(entry.relative_path)
                dest = _join_remote(mapping.target_path, *rel.split("/"))
                temp = _join_remote(temp_root, *rel.split("/"))
                source_hash = (entry.sha256 or "").lower()
                existing = executor.file_info(dest, include_sha256=verify_sha256)
                action = "UPDATE" if existing["exists"] else "NEW"

                audit.operation(
                    audit_root, "DISTRIBUTION", "FILE_BEGIN", "SUCCESS",
                    "开始处理文件。", task_id=task_id, host=host, subject=dest,
                    details={"mapping_id": mapping.mapping_id, "source": str(entry.absolute_path),
                             "target": dest, "action": action, "size": entry.size,
                             "transport": "WINRM"},
                )

                if existing["exists"] and verify_sha256 and existing["size"] == entry.size:
                    st = "SUCCESS" if existing["sha256"] == source_hash else "FAILED"
                    _record_check(task_id, host, dest, "EXISTING_SHA256", "SHA256",
                                  source_hash, existing["sha256"], st,
                                  "比较现有目标文件与源文件。", audit_root)
                    if existing["sha256"] == source_hash:
                        skipped_files += 1
                        db.record_file(
                            task_id, host, rel, "SAME", entry.size, source_hash,
                            existing["sha256"], "SKIPPED", mapping_id=mapping.mapping_id,
                            source_path=str(entry.absolute_path), target_path=dest,
                        )
                        if file_progress_cb:
                            file_progress_cb(host, done_files, total_files, dest, "SKIPPED")
                        continue

                if existing["exists"] and backup_existing:
                    backup = _backup_file_path(mapping.target_path, backup_root_path, task_id, rel, host)
                    try:
                        backup_info = executor.file_info(backup, include_sha256=verify_sha256)
                        if not backup_info["exists"]:
                            executor.copy_remote_file(dest, backup, overwrite=False)
                            backup_info = executor.file_info(backup, include_sha256=verify_sha256)
                        _record_check(task_id, host, dest, "BACKUP_SIZE", "SIZE",
                                      str(existing["size"]), str(backup_info["size"]),
                                      "SUCCESS" if existing["size"] == backup_info["size"] else "FAILED",
                                      "备份文件大小校验。", audit_root)
                        if backup_info["size"] != existing["size"]:
                            raise IOError("备份文件大小校验不一致")
                        if verify_sha256:
                            _record_check(task_id, host, dest, "BACKUP_SHA256", "SHA256",
                                          existing["sha256"], backup_info["sha256"],
                                          "SUCCESS" if existing["sha256"] == backup_info["sha256"] else "FAILED",
                                          "备份文件 SHA256 校验。", audit_root)
                            if existing["sha256"] != backup_info["sha256"]:
                                raise IOError("备份文件 SHA256 校验不一致")
                        db.record_backup(task_id, host, dest, dest, backup, existing["size"],
                                         backup_info["sha256"] or existing["sha256"], "SUCCESS")
                        audit.operation(audit_root, "BACKUP", "CREATE", "SUCCESS",
                                        "旧文件已通过 WinRM 备份并完成校验。", task_id=task_id,
                                        host=host, subject=dest,
                                        details={"backup_path": backup, "mapping_id": mapping.mapping_id})
                    except Exception as backup_error:
                        db.record_backup(task_id, host, dest, dest, backup, existing["size"],
                                         existing["sha256"], "FAILED", str(backup_error))
                        failed_files += 1
                        last_error = f"备份失败 {dest}：{backup_error}"
                        db.record_file(task_id, host, rel, action, entry.size, source_hash, "", "FAILED",
                                       last_error, mapping_id=mapping.mapping_id,
                                       source_path=str(entry.absolute_path), target_path=dest)
                        log_cb(host, last_error)
                        if file_progress_cb:
                            file_progress_cb(host, done_files, total_files, dest, "FAILED")
                        continue

                success = False
                err = ""
                for attempt in range(retry_count + 1):
                    try:
                        if cancel_cb():
                            raise RuntimeError("用户已取消任务")
                        try:
                            executor.remove_path(temp)
                        except Exception:
                            pass
                        log_cb(host, f"WinRM 上传：{entry.absolute_path.name} → {dest}")
                        executor.upload_file(entry.absolute_path, temp)
                        temp_info = executor.file_info(temp, include_sha256=verify_sha256)
                        _record_check(task_id, host, dest, "TEMP_SIZE", "SIZE",
                                      str(entry.size), str(temp_info["size"]),
                                      "SUCCESS" if temp_info["size"] == entry.size else "FAILED",
                                      "WinRM 临时文件大小校验。", audit_root)
                        if temp_info["size"] != entry.size:
                            raise IOError("传输完成后文件大小校验不一致")
                        if verify_sha256:
                            _record_check(task_id, host, dest, "TEMP_SHA256", "SHA256",
                                          source_hash, temp_info["sha256"],
                                          "SUCCESS" if temp_info["sha256"] == source_hash else "FAILED",
                                          "WinRM 临时文件 SHA256 校验。", audit_root)
                            if temp_info["sha256"] != source_hash:
                                raise IOError("传输完成后 SHA256 校验不一致")

                        executor.promote_temp_file(temp, dest)
                        final_info = executor.file_info(dest, include_sha256=verify_sha256)
                        _record_check(task_id, host, dest, "FINAL_SIZE", "SIZE",
                                      str(entry.size), str(final_info["size"]),
                                      "SUCCESS" if final_info["size"] == entry.size else "FAILED",
                                      "正式文件大小校验。", audit_root)
                        if final_info["size"] != entry.size:
                            raise IOError("正式文件替换后大小校验不一致")
                        if verify_sha256:
                            _record_check(task_id, host, dest, "FINAL_SHA256", "SHA256",
                                          source_hash, final_info["sha256"],
                                          "SUCCESS" if final_info["sha256"] == source_hash else "FAILED",
                                          "正式文件 SHA256 校验。", audit_root)
                            if final_info["sha256"] != source_hash:
                                raise IOError("正式文件替换后 SHA256 校验不一致")

                        transferred += entry.size
                        if action == "NEW":
                            new_files += 1
                        else:
                            updated_files += 1
                        db.record_file(task_id, host, rel, action, entry.size, source_hash,
                                       final_info["sha256"], "SUCCESS",
                                       mapping_id=mapping.mapping_id,
                                       source_path=str(entry.absolute_path), target_path=dest)
                        audit.operation(
                            audit_root, "DISTRIBUTION", "FILE_SUCCESS", "SUCCESS",
                            "文件通过 WinRM 分发并校验成功。", task_id=task_id, host=host,
                            subject=dest,
                            details={"mapping_id": mapping.mapping_id,
                                     "source_path": str(entry.absolute_path),
                                     "destination_path": dest, "size": entry.size,
                                     "source_sha256": source_hash,
                                     "target_sha256": final_info["sha256"],
                                     "attempt": attempt + 1, "transport": "WINRM"},
                        )
                        success = True
                        if file_progress_cb:
                            file_progress_cb(host, done_files, total_files, dest, "SUCCESS")
                        break
                    except Exception as e:
                        err = str(e)
                        audit.operation(audit_root, "DISTRIBUTION", "FILE_ATTEMPT_FAILED", "FAILED",
                                        err, task_id=task_id, host=host, subject=dest,
                                        details={"mapping_id": mapping.mapping_id,
                                                 "attempt": attempt + 1,
                                                 "max_attempts": retry_count + 1,
                                                 "transport": "WINRM"})
                        if attempt < retry_count:
                            time.sleep(0.6 * (attempt + 1))
                        else:
                            failed_files += 1
                            db.record_file(task_id, host, rel, action, entry.size, source_hash, "", "FAILED",
                                           err, mapping_id=mapping.mapping_id,
                                           source_path=str(entry.absolute_path), target_path=dest)
                            if file_progress_cb:
                                file_progress_cb(host, done_files, total_files, dest, "FAILED")
                if not success:
                    last_error = err
                    log_cb(host, f"分发失败 {dest}：{err}")

            audit.operation(audit_root, "DISTRIBUTION", "MAPPING_FINISH", "SUCCESS",
                            "WinRM 分发映射处理完成。", task_id=task_id, host=host,
                            subject=mapping.mapping_id, details=mapping.safe_dict())

        distribution_success = failed_files == 0
        if plan.enabled and (distribution_success or plan.post_on_failure):
            post_errors = _run_post_actions(executor, task_id, host, plan, log_cb, audit_root)
            if post_errors:
                last_error = (last_error + " | " if last_error else "") + " ; ".join(post_errors)
                failed_files = max(1, failed_files)
        elif plan.enabled:
            db.record_action(task_id, host, "POST", "SKIP_POST_ACTIONS", "", "SKIPPED", 0,
                             "由于文件分发失败，已跳过分发后操作。", "", 0)

        status = "SUCCESS" if failed_files == 0 else "FAILED"
        db.finish_host(task_id, host, status, transferred, new_files, updated_files,
                       skipped_files, failed_files, last_error)
        audit.operation(
            audit_root, "DISTRIBUTION", "HOST_FINISH", status,
            "WinRM 多目标目录主机分发工作流结束。", task_id=task_id, host=host,
            details={"mapping_count": len(prepared_mappings), "transferred_bytes": transferred,
                     "new_files": new_files, "updated_files": updated_files,
                     "skipped_files": skipped_files, "failed_files": failed_files,
                     "error": last_error, "transport": "WINRM"},
        )
        return {"host": host, "status": status, "transferred": transferred,
                "new": new_files, "updated": updated_files, "skipped": skipped_files,
                "failed": failed_files, "error": last_error}

    except Exception as e:
        last_error = str(e)
        log_cb(host, f"主机工作流失败：{last_error}")
        audit.operation(audit_root, "DISTRIBUTION", "HOST_EXCEPTION", "FAILED",
                        last_error, task_id=task_id, host=host,
                        details={"transport": "WINRM"})
        try:
            if plan.enabled and pre_actions_started and plan.post_on_failure:
                post_errors = _run_post_actions(executor, task_id, host, plan, log_cb, audit_root)
                if post_errors:
                    last_error += " | 分发后恢复：" + " ; ".join(post_errors)
        except Exception as recovery_error:
            last_error += f" | 分发后恢复失败：{recovery_error}"
        db.finish_host(task_id, host, "FAILED", transferred, new_files, updated_files,
                       skipped_files, max(1, failed_files), last_error)
        return {"host": host, "status": "FAILED", "transferred": transferred,
                "new": new_files, "updated": updated_files, "skipped": skipped_files,
                "failed": max(1, failed_files), "error": last_error}
    finally:
        # .fds_tmp 只是安全暂存区，不属于正式分发结果。无论成功或失败都尽力清理；
        # cleanup_staging_path 会只删除本任务的映射目录，并仅在父目录为空时删除 task/.fds_tmp。
        if executor is not None and temp_roots:
            cleanup_errors=[]
            for p in temp_roots:
                try:
                    executor.cleanup_staging_path(p)
                except Exception as cleanup_error:
                    cleanup_errors.append(f"{p}: {cleanup_error}")
            if cleanup_errors:
                try:
                    log_cb(host, "临时暂存目录清理未完全完成；可在确认无任务运行后手动删除残留 .fds_tmp。")
                    audit.operation(audit_root, "DISTRIBUTION", "TEMP_CLEANUP", "FAILED",
                                    "WinRM 临时暂存目录清理未完全完成。", task_id=task_id, host=host,
                                    details={"errors": cleanup_errors})
                except Exception:
                    pass
            else:
                try:
                    audit.operation(audit_root, "DISTRIBUTION", "TEMP_CLEANUP", "SUCCESS",
                                    "WinRM 临时暂存目录已清理。", task_id=task_id, host=host)
                except Exception:
                    pass

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any
import json
import logging
import getpass
import platform

from ..version import APP_VERSION

from ..paths import audit_dir
from .. import db

_LOCK = RLock()
_TASK_DIR_CACHE: dict[str, Path] = {}
_LOG = logging.getLogger("fds.audit")
_SENSITIVE_WORDS = ("password", "passwd", "pwd", "secret", "token", "credential", "authorization")


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _safe(value: Any, key: str = "") -> Any:
    k = key.lower()
    if any(word in k for word in _SENSITIVE_WORDS):
        return "***"
    if isinstance(value, dict):
        return {str(a): _safe(b, str(a)) for a, b in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


def _root(root: str | Path | None) -> Path:
    p = Path(root) if root else audit_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _append_text(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line.rstrip("\r\n") + "\n")


def task_dir(root: str | Path | None, task_id: str, created_at: str | None = None) -> Path:
    # Keep one task in one directory even when a deployment crosses midnight.
    with _LOCK:
        cached = _TASK_DIR_CACHE.get(task_id)
        if cached:
            cached.mkdir(parents=True, exist_ok=True)
            return cached
        day = (created_at or datetime.now().strftime("%Y-%m-%d"))[:10]
        p = _root(root) / "tasks" / day / task_id
        p.mkdir(parents=True, exist_ok=True)
        _TASK_DIR_CACHE[task_id] = p
        return p


def operation(
    root: str | Path | None,
    category: str,
    action: str,
    status: str = "SUCCESS",
    message: str = "",
    *,
    task_id: str = "",
    host: str = "",
    subject: str = "",
    details: dict | None = None,
) -> dict:
    payload = {
        "time": _now(),
        "operator": getpass.getuser(),
        "workstation": platform.node(),
        "app_version": APP_VERSION,
        "category": str(category or "GENERAL").upper(),
        "action": str(action or "EVENT").upper(),
        "status": str(status or "SUCCESS").upper(),
        "task_id": task_id or "",
        "host": host or "",
        "subject": subject or "",
        "message": message or "",
        "details": _safe(details or {}),
    }
    safe_payload = _safe(payload)
    try:
        db.record_audit_event(
            safe_payload["time"], safe_payload["category"], safe_payload["action"],
            safe_payload["status"], safe_payload["task_id"], safe_payload["host"],
            safe_payload["subject"], safe_payload["message"],
            json.dumps(safe_payload["details"], ensure_ascii=False, sort_keys=True),
            safe_payload["operator"], safe_payload["workstation"], safe_payload["app_version"],
        )
    except Exception:
        _LOG.exception("写入 SQLite 审计日志失败")

    root_path = _root(root)
    day = safe_payload["time"][:10]
    _append_jsonl(root_path / "operations" / f"{day}.jsonl", safe_payload)
    if task_id:
        tdir = task_dir(root_path, task_id, safe_payload["time"])
        _append_jsonl(tdir / "events.jsonl", safe_payload)
        line = (
            f"{safe_payload['time']} | {safe_payload['category']} | {safe_payload['action']} | "
            f"{safe_payload['status']} | host={safe_payload['host'] or '-'} | "
            f"{safe_payload['message']}"
        )
        _append_text(tdir / "distribution.log", line)
    _LOG.info(
        "%s | %s | %s | task=%s | host=%s | %s",
        safe_payload["category"], safe_payload["action"], safe_payload["status"],
        task_id or "-", host or "-", message,
    )
    return safe_payload


def task_log(root: str | Path | None, task_id: str, message: str, level: str = "INFO") -> None:
    payload = {
        "time": _now(),
        "operator": getpass.getuser(),
        "workstation": platform.node(),
        "app_version": APP_VERSION,
        "level": level.upper(),
        "message": str(message),
    }
    tdir = task_dir(root, task_id, payload["time"])
    _append_text(tdir / "distribution.log", f"{payload['time']} | {payload['level']} | {payload['message']}")


def start_task(root: str | Path | None, task_id: str, metadata: dict) -> Path:
    tdir = task_dir(root, task_id)
    safe = _safe({"task_id": task_id, "created_at": _now(), **metadata})
    with _LOCK:
        (tdir / "task.json").write_text(
            json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
    operation(root, "TASK", "CREATE", "SUCCESS", "已创建分发任务审计目录。",
              task_id=task_id, details={"audit_dir": str(tdir)})
    return tdir


def write_manifest(root: str | Path | None, task_id: str, entries, manifest_sha256: str) -> Path:
    data = {
        "task_id": task_id,
        "generated_at": _now(),
        "algorithm": "SHA256",
        "manifest_sha256": manifest_sha256,
        "file_count": len(entries),
        "total_bytes": sum(int(e.size) for e in entries),
        "files": [
            {
                "relative_path": e.relative_path,
                "size": int(e.size),
                "sha256": e.sha256,
            }
            for e in entries
        ],
    }
    path = task_dir(root, task_id) / "manifest.json"
    with _LOCK:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    operation(root, "VERIFY", "MANIFEST_SAVED", "SUCCESS", "已生成并保存本地文件清单。",
              task_id=task_id, subject=manifest_sha256,
              details={"file_count": data["file_count"], "total_bytes": data["total_bytes"], "path": str(path)})
    return path


def finish_task(root: str | Path | None, task_id: str, summary: dict) -> Path:
    payload = _safe({"task_id": task_id, "finished_at": _now(), **summary})
    path = task_dir(root, task_id) / "summary.json"
    with _LOCK:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    operation(root, "TASK", "FINISH", str(summary.get("status", "SUCCESS")),
              "分发任务已结束。", task_id=task_id, details=summary)
    return path


def write_mapping_plan(root: str | Path | None, task_id: str, prepared_mappings,
                       plan_sha256: str) -> Path:
    """Persist a target-aware distribution plan without any credentials."""
    mappings = []
    for pm in prepared_mappings:
        m = pm.mapping
        mappings.append({
            "mapping_id": m.mapping_id,
            "source_type": m.source_type,
            "source_path": m.display_source(),
            "target_path": m.target_path,
            "source_kind": m.source_kind,
            "folder_mode": m.folder_mode,
            "manifest_sha256": pm.manifest_sha256,
            "file_count": pm.file_count,
            "total_bytes": pm.total_bytes,
            "files": [
                {
                    "relative_path": e.relative_path,
                    "size": int(e.size),
                    "sha256": e.sha256,
                }
                for e in pm.manifest
            ],
        })
    data = {
        "task_id": task_id,
        "generated_at": _now(),
        "plan_sha256": plan_sha256,
        "mapping_count": len(mappings),
        "file_count": sum(x["file_count"] for x in mappings),
        "total_bytes": sum(x["total_bytes"] for x in mappings),
        "mappings": mappings,
    }
    path = task_dir(root, task_id) / "mapping_plan.json"
    with _LOCK:
        path.write_text(json.dumps(_safe(data), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    operation(
        root, "MAPPING", "PLAN_SAVED", "SUCCESS",
        "已保存多源多目标分发映射计划。", task_id=task_id,
        subject=plan_sha256,
        details={"mapping_count": data["mapping_count"], "file_count": data["file_count"],
                 "total_bytes": data["total_bytes"], "path": str(path)},
    )
    return path

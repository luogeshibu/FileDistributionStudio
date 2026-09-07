import sqlite3
from datetime import datetime

from .paths import db_path
from .models import HostRecord

SCHEMA = r'''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS hosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host TEXT NOT NULL UNIQUE,
    group_name TEXT NOT NULL DEFAULT 'Default',
    target_mode TEXT NOT NULL DEFAULT 'WINRM',
    default_target TEXT NOT NULL DEFAULT 'D:\ADMS',
    os_hint TEXT NOT NULL DEFAULT '',
    last_seen TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    hostname_source TEXT NOT NULL DEFAULT '',
    hostname_verified INTEGER NOT NULL DEFAULT 0,
    hostname_note TEXT NOT NULL DEFAULT '',
    online_status TEXT NOT NULL DEFAULT 'UNTESTED',
    ping_ok INTEGER NOT NULL DEFAULT 0,
    smb_port_ok INTEGER NOT NULL DEFAULT 0,
    rdp_port_ok INTEGER NOT NULL DEFAULT 0,
    winrm_port_ok INTEGER NOT NULL DEFAULT 0,
    smb_status TEXT NOT NULL DEFAULT 'UNTESTED',
    winrm_status TEXT NOT NULL DEFAULT 'UNTESTED',
    last_test_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_path TEXT NOT NULL,
    host_count INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    success_hosts INTEGER NOT NULL DEFAULT 0,
    failed_hosts INTEGER NOT NULL DEFAULT 0,
    finished_at TEXT NOT NULL DEFAULT '',
    backup_root TEXT NOT NULL DEFAULT '',
    verification_mode TEXT NOT NULL DEFAULT 'SHA256',
    manifest_sha256 TEXT NOT NULL DEFAULT '',
    audit_path TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    workstation TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_hosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    host TEXT NOT NULL,
    hostname TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    transferred_bytes INTEGER NOT NULL DEFAULT 0,
    new_files INTEGER NOT NULL DEFAULT 0,
    updated_files INTEGER NOT NULL DEFAULT 0,
    skipped_files INTEGER NOT NULL DEFAULT 0,
    failed_files INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    host TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    action TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    source_sha256 TEXT NOT NULL DEFAULT '',
    target_sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    mapping_id TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    target_path TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    mapping_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_path TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'FILE',
    folder_mode TEXT NOT NULL DEFAULT 'CONTENTS',
    file_count INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    manifest_sha256 TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    host TEXT NOT NULL,
    phase TEXT NOT NULL,
    action TEXT NOT NULL,
    command TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    exit_code INTEGER NOT NULL DEFAULT 0,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    host TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    original_path TEXT NOT NULL DEFAULT '',
    backup_path TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    host TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    expected_value TEXT NOT NULL DEFAULT '',
    actual_value TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    workstation TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_task_hosts_task ON task_hosts(task_id);
CREATE INDEX IF NOT EXISTS idx_task_files_task ON task_files(task_id);
CREATE INDEX IF NOT EXISTS idx_task_mappings_task ON task_mappings(task_id);
CREATE INDEX IF NOT EXISTS idx_task_actions_task ON task_actions(task_id);
CREATE INDEX IF NOT EXISTS idx_task_backups_task ON task_backups(task_id);
CREATE INDEX IF NOT EXISTS idx_task_verifications_task ON task_verifications(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_events(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_events(category);
'''


def _conn():
    c = sqlite3.connect(db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _ensure_column(c, table: str, column: str, ddl: str):
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)
        # Safe migration for users upgrading an existing fds.db.
        _ensure_column(c, "tasks", "backup_root", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "tasks", "verification_mode", "TEXT NOT NULL DEFAULT 'SHA256'")
        _ensure_column(c, "tasks", "manifest_sha256", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "tasks", "audit_path", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "tasks", "operator", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "tasks", "workstation", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "tasks", "app_version", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "audit_events", "operator", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "audit_events", "workstation", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "audit_events", "app_version", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "hosts", "hostname_source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "hosts", "hostname_verified", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(c, "hosts", "hostname_note", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "hosts", "online_status", "TEXT NOT NULL DEFAULT 'UNTESTED'")
        _ensure_column(c, "hosts", "ping_ok", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(c, "hosts", "smb_port_ok", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(c, "hosts", "rdp_port_ok", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(c, "hosts", "winrm_port_ok", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(c, "hosts", "smb_status", "TEXT NOT NULL DEFAULT 'UNTESTED'")
        _ensure_column(c, "hosts", "winrm_status", "TEXT NOT NULL DEFAULT 'UNTESTED'")
        _ensure_column(c, "hosts", "last_test_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "task_files", "mapping_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "task_files", "source_path", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, "task_files", "target_path", "TEXT NOT NULL DEFAULT ''")


def list_hosts():
    with _conn() as c:
        rows = c.execute("SELECT * FROM hosts ORDER BY group_name, name, host").fetchall()
    return [HostRecord(**dict(r)) for r in rows]


def upsert_host(host: HostRecord):
    with _conn() as c:
        c.execute(
            '''
            INSERT INTO hosts(name,host,group_name,target_mode,default_target,os_hint,last_seen,notes,
                              hostname_source,hostname_verified,hostname_note,online_status,ping_ok,
                              smb_port_ok,rdp_port_ok,winrm_port_ok,smb_status,winrm_status,last_test_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(host) DO UPDATE SET
              name=excluded.name,
              group_name=excluded.group_name,
              target_mode=excluded.target_mode,
              default_target=excluded.default_target,
              os_hint=excluded.os_hint,
              last_seen=excluded.last_seen,
              notes=excluded.notes,
              hostname_source=excluded.hostname_source,
              hostname_verified=excluded.hostname_verified,
              hostname_note=excluded.hostname_note,
              online_status=excluded.online_status,
              ping_ok=excluded.ping_ok,
              smb_port_ok=excluded.smb_port_ok,
              rdp_port_ok=excluded.rdp_port_ok,
              winrm_port_ok=excluded.winrm_port_ok,
              smb_status=excluded.smb_status,
              winrm_status=excluded.winrm_status,
              last_test_at=excluded.last_test_at
            ''',
            (host.name, host.host, host.group_name, host.target_mode, host.default_target,
             host.os_hint, host.last_seen, host.notes, host.hostname_source,
             int(bool(host.hostname_verified)), host.hostname_note, host.online_status,
             int(bool(host.ping_ok)), int(bool(host.smb_port_ok)), int(bool(host.rdp_port_ok)),
             int(bool(host.winrm_port_ok)), host.smb_status, host.winrm_status, host.last_test_at),
        )


def update_host_hostname(host: str, hostname: str, source: str, verified: bool, note: str = ""):
    with _conn() as c:
        c.execute(
            """UPDATE hosts SET name=?, hostname_source=?, hostname_verified=?, hostname_note=?, last_seen=?
               WHERE host=?""",
            (hostname or host, source or "", int(bool(verified)), note or "",
             datetime.now().isoformat(timespec="seconds"), host),
        )


def mark_host_hostname_verification_failed(host: str, note: str = ""):
    with _conn() as c:
        c.execute(
            """UPDATE hosts SET hostname_verified=0, hostname_note=?, last_seen=? WHERE host=?""",
            (note or "WinRM 主机名验证失败。", datetime.now().isoformat(timespec="seconds"), host),
        )


def delete_host(host_id: int):
    with _conn() as c:
        c.execute("DELETE FROM hosts WHERE id=?", (host_id,))


def create_task(task_id, source_type, source_path, target_path, host_count,
                status="PREPARING", backup_root="", verification_mode="SHA256", audit_path="",
                operator="", workstation="", app_version=""):
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            '''INSERT INTO tasks(task_id,created_at,source_type,source_path,target_path,host_count,status,
                                 backup_root,verification_mode,audit_path,operator,workstation,app_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (task_id, now, source_type, source_path, target_path, host_count, status,
             backup_root, verification_mode, audit_path, operator, workstation, app_version),
        )


def update_task_manifest(task_id, file_count, total_bytes, manifest_sha256=""):
    with _conn() as c:
        c.execute("UPDATE tasks SET file_count=?, total_bytes=?, manifest_sha256=? WHERE task_id=?",
                  (file_count, total_bytes, manifest_sha256, task_id))


def finish_task(task_id, status, success_hosts, failed_hosts):
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            "UPDATE tasks SET status=?, success_hosts=?, failed_hosts=?, finished_at=? WHERE task_id=?",
            (status, success_hosts, failed_hosts, now, task_id),
        )


def start_host(task_id, host, hostname=""):
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            '''INSERT INTO task_hosts(task_id,host,hostname,status,started_at)
               VALUES(?,?,?,?,?)''',
            (task_id, host, hostname, "RUNNING", now),
        )


def finish_host(task_id, host, status, transferred_bytes, new_files, updated_files,
                skipped_files, failed_files, error_message=""):
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            '''UPDATE task_hosts SET status=?,finished_at=?,transferred_bytes=?,new_files=?,
               updated_files=?,skipped_files=?,failed_files=?,error_message=?
               WHERE task_id=? AND host=?''',
            (status, now, transferred_bytes, new_files, updated_files, skipped_files,
             failed_files, error_message, task_id, host),
        )


def record_file(task_id, host, relative_path, action, size, source_sha256,
                target_sha256, status, error_message="", mapping_id="",
                source_path="", target_path=""):
    with _conn() as c:
        c.execute(
            """INSERT INTO task_files(task_id,host,relative_path,action,size,
               source_sha256,target_sha256,status,error_message,mapping_id,source_path,target_path)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, host, relative_path, action, size, source_sha256,
             target_sha256, status, error_message, mapping_id or "",
             source_path or "", target_path or ""),
        )


def record_task_mapping(task_id, mapping_id, source_type, source_path, target_path,
                        source_kind, folder_mode, file_count, total_bytes, manifest_sha256):
    with _conn() as c:
        c.execute(
            """INSERT INTO task_mappings(task_id,mapping_id,source_type,source_path,target_path,
               source_kind,folder_mode,file_count,total_bytes,manifest_sha256)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (task_id, mapping_id, source_type, source_path, target_path, source_kind,
             folder_mode, int(file_count), int(total_bytes), manifest_sha256 or ""),
        )


def update_host_connectivity(host: str, *, online_status: str, ping_ok: bool,
                             smb_port_ok: bool, rdp_port_ok: bool, winrm_port_ok: bool,
                             last_test_at: str):
    with _conn() as c:
        c.execute(
            """UPDATE hosts SET online_status=?, ping_ok=?, smb_port_ok=?, rdp_port_ok=?,
               winrm_port_ok=?, last_test_at=? WHERE host=?""",
            (online_status, int(bool(ping_ok)), int(bool(smb_port_ok)), int(bool(rdp_port_ok)),
             int(bool(winrm_port_ok)), last_test_at, host),
        )


def update_host_smb_status(host: str, smb_status: str, last_test_at: str):
    with _conn() as c:
        if smb_status == "WRITABLE":
            c.execute(
                "UPDATE hosts SET smb_status=?, online_status='ONLINE_SMB', smb_port_ok=1, last_test_at=? WHERE host=?",
                (smb_status, last_test_at, host),
            )
        else:
            c.execute("UPDATE hosts SET smb_status=?, last_test_at=? WHERE host=?",
                      (smb_status, last_test_at, host))


def update_host_winrm_status(host: str, winrm_status: str, last_test_at: str):
    with _conn() as c:
        if winrm_status == "WRITABLE":
            c.execute(
                "UPDATE hosts SET winrm_status=?, online_status='ONLINE_WINRM', winrm_port_ok=1, last_test_at=? WHERE host=?",
                (winrm_status, last_test_at, host),
            )
        else:
            c.execute("UPDATE hosts SET winrm_status=?, last_test_at=? WHERE host=?",
                      (winrm_status, last_test_at, host))


def record_action(task_id, host, phase, action, command, status, exit_code=0,
                  stdout="", stderr="", duration_ms=0):
    now = datetime.now().isoformat(timespec="seconds")
    stdout = (stdout or "")[-20000:]
    stderr = (stderr or "")[-20000:]
    with _conn() as c:
        c.execute(
            '''INSERT INTO task_actions(task_id,host,phase,action,command,status,exit_code,stdout,stderr,duration_ms,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (task_id, host, phase, action, command, status, int(exit_code or 0),
             stdout, stderr, int(duration_ms or 0), now),
        )


def record_backup(task_id, host, relative_path, original_path, backup_path,
                  size, sha256, status, error_message=""):
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            '''INSERT INTO task_backups(task_id,host,relative_path,original_path,backup_path,size,sha256,status,error_message,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (task_id, host, relative_path, original_path, backup_path, int(size or 0),
             sha256 or "", status, error_message or "", now),
        )


def record_verification(task_id, host, relative_path, stage, algorithm,
                        expected_value, actual_value, status, details=""):
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            '''INSERT INTO task_verifications(task_id,host,relative_path,stage,algorithm,
               expected_value,actual_value,status,details,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (task_id, host or "", relative_path or "", stage, algorithm,
             str(expected_value or ""), str(actual_value or ""), status, details or "", now),
        )


def record_audit_event(created_at, category, action, status, task_id="", host="",
                       subject="", message="", details_json="", operator="",
                       workstation="", app_version=""):
    with _conn() as c:
        c.execute(
            '''INSERT INTO audit_events(created_at,category,action,status,task_id,host,subject,message,details_json,operator,workstation,app_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (created_at, category, action, status, task_id or "", host or "",
             subject or "", message or "", details_json or "", operator or "",
             workstation or "", app_version or ""),
        )


def list_tasks(limit=300):
    with _conn() as c:
        return c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_task(task_id):
    with _conn() as c:
        return c.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()


def list_task_hosts(task_id):
    with _conn() as c:
        return c.execute("SELECT * FROM task_hosts WHERE task_id=? ORDER BY host", (task_id,)).fetchall()


def list_task_files(task_id):
    with _conn() as c:
        return c.execute("SELECT * FROM task_files WHERE task_id=? ORDER BY host, relative_path", (task_id,)).fetchall()



def list_task_mappings(task_id):
    with _conn() as c:
        return c.execute("SELECT * FROM task_mappings WHERE task_id=? ORDER BY id", (task_id,)).fetchall()

def list_task_actions(task_id):
    with _conn() as c:
        return c.execute("SELECT * FROM task_actions WHERE task_id=? ORDER BY id", (task_id,)).fetchall()


def list_task_backups(task_id):
    with _conn() as c:
        return c.execute("SELECT * FROM task_backups WHERE task_id=? ORDER BY host, relative_path", (task_id,)).fetchall()


def list_task_verifications(task_id):
    with _conn() as c:
        return c.execute("SELECT * FROM task_verifications WHERE task_id=? ORDER BY id", (task_id,)).fetchall()


def list_audit_events(limit=2000, category="", status="", keyword="", task_id=""):
    where = []
    args = []
    if category:
        where.append("category=?")
        args.append(category)
    if status:
        where.append("status=?")
        args.append(status)
    if task_id:
        where.append("task_id=?")
        args.append(task_id)
    if keyword:
        where.append("(task_id LIKE ? OR host LIKE ? OR subject LIKE ? OR message LIKE ? OR action LIKE ?)")
        k = f"%{keyword}%"
        args.extend([k, k, k, k, k])
    sql = "SELECT * FROM audit_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with _conn() as c:
        return c.execute(sql, tuple(args)).fetchall()

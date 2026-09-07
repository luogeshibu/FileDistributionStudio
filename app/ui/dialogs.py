from pathlib import Path
from .widgets import PasswordLineEdit
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QDialogButtonBox, QTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QLabel, QFrame, QCheckBox, QSpinBox, QMessageBox,
    QPushButton, QFileDialog, QApplication, QTreeWidget, QTreeWidgetItem, QAbstractItemView
)
from ..models import HostRecord
from .. import db
from ..utils import validate_windows_target_path, human_bytes
from ..resources import script_path
from ..services.remote_exec import RemoteActionPlan, WinRMExecutor, describe_winrm_failure
from .locale_zh import (status_text, action_text, phase_text, mode_text, group_text, source_text,
                        audit_category_text, verify_stage_text, hostname_source_text)


class HostEditDialog(QDialog):
    def __init__(self, parent=None, host: HostRecord | None = None):
        super().__init__(parent)
        self._original = host
        self.setWindowTitle("主机配置")
        self.resize(560, 390)

        self.name = QLineEdit(host.name if host else "")
        self.host = QLineEdit(host.host if host else "")
        self.group = QLineEdit(group_text(host.group_name) if host else "默认")
        self.mode = QComboBox()
        self.mode.addItem("WinRM（固定业务通道）", "WINRM")
        self.mode.setEnabled(False)
        self.business_channel = QLineEdit("WinRM（固定业务通道）")
        self.business_channel.setReadOnly(True)
        self.notes = QTextEdit(host.notes if host else "")

        source_value = hostname_source_text(host.hostname_source) if host else "手工维护"
        verify_value = "已验证" if (host and host.hostname_verified) else ("未验证" if host else "手工维护")
        self.hostname_source = QLineEdit(source_value)
        self.hostname_source.setReadOnly(True)
        self.hostname_verify = QLineEdit(verify_value)
        self.hostname_verify.setReadOnly(True)
        self.hostname_note = QLineEdit(host.hostname_note if host else "")
        self.hostname_note.setReadOnly(True)

        form = QFormLayout()
        form.addRow("名称 / 主机名", self.name)
        form.addRow("IP / 主机", self.host)
        form.addRow("名称来源", self.hostname_source)
        form.addRow("名称状态", self.hostname_verify)
        form.addRow("名称说明", self.hostname_note)
        form.addRow("分组", self.group)
        form.addRow("业务通道", self.business_channel)
        note = QLabel("v0.5.0 起目标目录由每次分发任务的“分发映射”指定，不再固定保存到主机属性。")
        note.setWordWrap(True); note.setStyleSheet("color:#667A8A;")
        form.addRow("目标目录", note)
        form.addRow("备注", self.notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def value(self):
        new_name = self.name.text().strip() or self.host.text().strip()
        original_name = self._original.name if self._original else ""
        changed_name = (not self._original) or (new_name != original_name)
        if changed_name:
            hostname_source = "MANUAL"
            hostname_verified = 1
            hostname_note = "名称由用户手工维护。"
        else:
            hostname_source = self._original.hostname_source if self._original else "MANUAL"
            hostname_verified = self._original.hostname_verified if self._original else 1
            hostname_note = self._original.hostname_note if self._original else "名称由用户手工维护。"
        return HostRecord(
            id=self._original.id if self._original else None,
            name=new_name,
            host=self.host.text().strip(),
            group_name=self.group.text().strip() or "默认",
            target_mode=self.mode.currentData(),
            default_target=self._original.default_target if self._original else "",
            os_hint=self._original.os_hint if self._original else "",
            last_seen=self._original.last_seen if self._original else "",
            notes=self.notes.toPlainText().strip(),
            hostname_source=hostname_source,
            hostname_verified=hostname_verified,
            hostname_note=hostname_note,
            online_status=self._original.online_status if self._original else "UNTESTED",
            ping_ok=self._original.ping_ok if self._original else 0,
            smb_port_ok=self._original.smb_port_ok if self._original else 0,
            rdp_port_ok=self._original.rdp_port_ok if self._original else 0,
            winrm_port_ok=self._original.winrm_port_ok if self._original else 0,
            smb_status=self._original.smb_status if self._original else "UNTESTED",
            winrm_status=self._original.winrm_status if self._original else "UNTESTED",
            last_test_at=self._original.last_test_at if self._original else "",
        )


class RemoteDriveQueryThread(QThread):
    """通过每台目标机自己的 WinRM 凭据读取远程盘符。"""
    completed = Signal(object)

    def __init__(self, contexts: list[dict], *, use_https=False, port=5985, max_workers=4):
        super().__init__()
        self.contexts = list(contexts or [])
        self.use_https = bool(use_https)
        self.port = int(port)
        self.max_workers = max(1, min(8, int(max_workers)))

    def run(self):
        results = {}
        errors = {}

        def one(ctx):
            host = ctx["host"]
            plan = RemoteActionPlan(
                enabled=False,
                use_https=self.use_https,
                port=self.port,
                username=ctx.get("username", ""),
                password=ctx.get("password", ""),
                command_timeout=25,
            )
            try:
                drives = WinRMExecutor(host, plan).list_drives()
                return host, drives, ""
            except Exception as exc:
                return host, [], describe_winrm_failure(host, plan, exc)

        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(self.contexts)))) as ex:
            futures = [ex.submit(one, ctx) for ctx in self.contexts]
            for fut in as_completed(futures):
                host, drives, error = fut.result()
                if error:
                    errors[host] = error
                else:
                    results[host] = drives
        self.completed.emit({"results": results, "errors": errors, "total": len(self.contexts)})


class RemoteDriveSelector(QFrame):
    """目标路径辅助控件：通过 WinRM 自动读取所选目标机盘符。"""
    def __init__(self, target_edit: QLineEdit, *, contexts=None, context_hint="", use_https=False, port=5985, parent=None):
        super().__init__(parent)
        self.target_edit = target_edit
        self.contexts = list(contexts or [])
        self.context_hint = context_hint or ""
        self.use_https = bool(use_https)
        self.port = int(port)
        self._thread = None
        self._drive_data = {}

        self.combo = QComboBox()
        self.combo.setMinimumWidth(330)
        self.refresh_btn = QPushButton("读取远程盘符")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#667A8A;")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.combo, 1)
        top.addWidget(self.refresh_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.status)

        self.combo.addItem("手动输入目标目录", "")
        self.combo.currentIndexChanged.connect(self._drive_selected)
        self.refresh_btn.clicked.connect(self.refresh_drives)

        if self.contexts:
            self.status.setText(f"已选择 {len(self.contexts)} 台目标主机，将通过 WinRM 自动读取盘符。")
            QTimer.singleShot(120, self.refresh_drives)
        else:
            self.refresh_btn.setEnabled(False)
            self.status.setText(self.context_hint or "未勾选目标主机或缺少可用 WinRM 凭据；仍可手动输入目标目录。")

    def refresh_drives(self):
        if not self.contexts or (self._thread and self._thread.isRunning()):
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("正在读取…")
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("正在通过 WinRM 读取远程盘符…", "")
        self.combo.blockSignals(False)
        self.status.setText(f"正在读取 {len(self.contexts)} 台目标主机的本地盘符，请稍候……")
        self._thread = RemoteDriveQueryThread(
            self.contexts, use_https=self.use_https, port=self.port,
            max_workers=min(4, len(self.contexts)),
        )
        self._thread.completed.connect(self._drives_loaded)
        self._thread.start()

    def _drives_loaded(self, payload):
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新盘符")
        results = payload.get("results", {})
        errors = payload.get("errors", {})
        total = int(payload.get("total", len(self.contexts)) or 0)

        drive_hosts = {}
        for host, drives in results.items():
            for d in drives:
                name = d.get("name", "")
                if not name:
                    continue
                drive_hosts.setdefault(name, []).append((host, d))

        current_drive = ""
        text = self.target_edit.text().strip()
        if len(text) >= 3 and text[1:3] in (":\\", ":/"):
            current_drive = text[:2].upper() + "\\"

        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("请选择远程盘符…", "")
        selectable_indexes = []
        for name in sorted(drive_hosts):
            entries = drive_hosts[name]
            count = len(entries)
            labels = sorted({(d.get("volume_label") or "").strip() for _, d in entries if (d.get("volume_label") or "").strip()})
            label = "/".join(labels[:2]) if labels else "无卷标"
            min_free = min((int(d.get("free_size", 0) or 0) for _, d in entries), default=0)
            min_total = min((int(d.get("total_size", 0) or 0) for _, d in entries), default=0)
            coverage = f"{count}/{total} 台" if total > 1 else ""
            prefix = "✓ " if count == total and total else ("⚠ " if total > 1 else "")
            cap = f"可用 {human_bytes(min_free)} / {human_bytes(min_total)}" if min_total else "容量未知"
            display = f"{prefix}{name}  {label}  {cap}"
            if coverage:
                display += f"  · {coverage}存在"
            self.combo.addItem(display, name)
            item_index = self.combo.count() - 1
            self.combo.setItemData(item_index, entries, Qt.UserRole + 1)  # 仅供诊断，不覆盖 currentData
            if total > 1 and count < total:
                model_item = self.combo.model().item(item_index)
                if model_item is not None:
                    model_item.setEnabled(False)
                    model_item.setToolTip("该盘符并非所有当前勾选目标主机都存在；同一映射会应用到全部目标主机，因此这里不允许直接选择。")
            else:
                selectable_indexes.append(item_index)

        if current_drive:
            idx = self.combo.findData(current_drive)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        elif len(selectable_indexes) == 1:
            # 只有一个对当前全部目标主机都安全可用的盘符时，直接替用户选择。
            self.combo.setCurrentIndex(selectable_indexes[0])
        self.combo.blockSignals(False)
        if self.combo.currentData():
            self._drive_selected(self.combo.currentIndex())

        ok = len(results)
        if errors:
            preview = "；".join(f"{h}: {msg}" for h, msg in list(errors.items())[:2])
            more = f"；另有 {len(errors)-2} 台失败" if len(errors) > 2 else ""
            self.status.setText(f"已读取 {ok}/{total} 台。带 ✓ 的盘符存在于全部目标机；带 ⚠ 的盘符只存在于部分主机。{preview}{more}")
        elif total > 1:
            self.status.setText("盘符读取完成。✓ 表示该盘符在所有已勾选目标主机上都存在；⚠ 表示只在部分主机上存在。正式分发前仍会逐台预检查。")
        else:
            self.status.setText("远程盘符读取完成。选择盘符后，程序会自动写入目标目录前缀；你仍可继续输入子目录。")

        if self.combo.count() <= 1:
            self.combo.addItem("未读取到可用本地盘符，请手动输入", "")

    def _drive_selected(self, index):
        drive = self.combo.itemData(index)
        if not isinstance(drive, str) or not drive:
            return
        old = self.target_edit.text().strip()
        suffix = ""
        if len(old) >= 3 and old[1:3] in (":\\", ":/"):
            suffix = old[3:].lstrip("\\/")
        new_value = drive + suffix.replace("/", "\\")
        self.target_edit.setText(new_value)
        self.target_edit.setFocus()
        self.target_edit.setCursorPosition(len(new_value))



class RemoteDirectoryQueryThread(QThread):
    """只读查询单台参考主机的远程盘符或某目录的一层子目录。"""
    completed = Signal(object)

    def __init__(self, context: dict, *, path="", use_https=False, port=5985):
        super().__init__()
        self.context = dict(context or {})
        self.path = (path or "").strip()
        self.use_https = bool(use_https)
        self.port = int(port)

    def run(self):
        host = self.context.get("host", "")
        plan = RemoteActionPlan(
            enabled=False,
            use_https=self.use_https,
            port=self.port,
            username=self.context.get("username", ""),
            password=self.context.get("password", ""),
            command_timeout=30,
        )
        try:
            executor = WinRMExecutor(host, plan)
            if self.path:
                data = executor.list_directories(self.path)
                kind = "directories"
            else:
                data = executor.list_drives()
                kind = "drives"
            self.completed.emit({
                "ok": True,
                "host": host,
                "kind": kind,
                "path": self.path,
                "data": data,
                "error": "",
            })
        except Exception as exc:
            self.completed.emit({
                "ok": False,
                "host": host,
                "kind": "directories" if self.path else "drives",
                "path": self.path,
                "data": [],
                "error": describe_winrm_failure(host, plan, exc),
            })


class RemotePathCoverageThread(QThread):
    """只读检查同一目标目录在所有当前目标主机上的存在情况。"""
    completed = Signal(object)

    def __init__(self, contexts: list[dict], *, path: str, use_https=False, port=5985, max_workers=4):
        super().__init__()
        self.contexts = list(contexts or [])
        self.path = (path or "").strip().replace("/", "\\")
        self.use_https = bool(use_https)
        self.port = int(port)
        self.max_workers = max(1, min(8, int(max_workers)))

    def run(self):
        results = {}
        errors = {}

        def one(ctx):
            host = ctx.get("host", "")
            plan = RemoteActionPlan(
                enabled=False,
                use_https=self.use_https,
                port=self.port,
                username=ctx.get("username", ""),
                password=ctx.get("password", ""),
                command_timeout=25,
            )
            try:
                info = WinRMExecutor(host, plan).directory_info(self.path)
                return host, info, ""
            except Exception as exc:
                return host, {}, describe_winrm_failure(host, plan, exc)

        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(self.contexts)))) as ex:
            futures = [ex.submit(one, ctx) for ctx in self.contexts]
            for fut in as_completed(futures):
                host, info, error = fut.result()
                if error:
                    errors[host] = error
                else:
                    results[host] = info
        self.completed.emit({
            "path": self.path,
            "results": results,
            "errors": errors,
            "total": len(self.contexts),
        })


class RemoteDirectoryBrowserDialog(QDialog):
    """在本 App 内通过 WinRM 只读浏览远程 Windows 目录树，并显示多主机路径覆盖。"""
    ROLE_PATH = Qt.UserRole + 21
    ROLE_KIND = Qt.UserRole + 22
    ROLE_LOADED = Qt.UserRole + 23

    def __init__(self, parent=None, *, contexts=None, initial_path="", use_https=False, port=5985):
        super().__init__(parent)
        self.contexts = list(contexts or [])
        self.initial_path = (initial_path or "").strip().replace("/", "\\")
        self.use_https = bool(use_https)
        self.port = int(port)
        self._thread = None
        self._coverage_thread = None
        self._loading_item = None
        self._coverage_path = ""
        self._coverage_payload = None
        self._pending_coverage_path = ""

        self.setWindowTitle("选择远程目标目录")
        self.resize(930, 720)

        self.host_combo = QComboBox()
        for i, ctx in enumerate(self.contexts):
            label = (ctx.get("name") or "").strip()
            host = (ctx.get("host") or "").strip()
            shown = f"{label}  ({host})" if label and label != host else host
            self.host_combo.addItem(shown or f"目标主机 {i+1}", i)

        self.path_edit = QLineEdit(self.initial_path)
        self.path_edit.setPlaceholderText(r"例如：D:\ADMS\bin")
        self.check_path_btn = QPushButton("检查全部目标主机")
        self.check_path_btn.setEnabled(bool(self.contexts))
        self.check_path_btn.setToolTip("只读检查当前路径在所有已勾选目标主机上是否已经存在，不创建目录、不写入文件。")

        self.coverage_summary = QLabel("")
        self.coverage_summary.setWordWrap(True)
        self.coverage_summary.setStyleSheet("color:#667A8A;")

        self.coverage_table = QTableWidget(len(self.contexts), 3)
        self.coverage_table.setHorizontalHeaderLabels(["目标主机", "主机 / IP", "当前路径状态"])
        self.coverage_table.verticalHeader().setVisible(False)
        self.coverage_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.coverage_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.coverage_table.setAlternatingRowColors(True)
        ch = self.coverage_table.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.Stretch)
        ch.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.coverage_table.setMaximumHeight(min(190, 58 + max(1, len(self.contexts)) * 30))
        for r, ctx in enumerate(self.contexts):
            name = (ctx.get("name") or ctx.get("host") or "").strip()
            host = (ctx.get("host") or "").strip()
            self.coverage_table.setItem(r, 0, QTableWidgetItem(name))
            self.coverage_table.setItem(r, 1, QTableWidgetItem(host))
            self.coverage_table.setItem(r, 2, QTableWidgetItem("未检查"))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["远程目录", "信息"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setUniformRowHeights(True)

        self.status = QLabel("目录树只浏览参考主机；当前路径可另外检查所有已勾选目标主机。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#667A8A;")
        self.multi_note = QLabel("")
        self.multi_note.setWordWrap(True)
        self.multi_note.setStyleSheet("color:#8A6D3B;")
        if len(self.contexts) > 1:
            self.multi_note.setText(
                f"当前分发范围是 {len(self.contexts)} 台目标主机。目录树仅用于参考主机浏览；最终这条映射会把同一个目标路径应用到全部 {len(self.contexts)} 台。"
            )
        elif len(self.contexts) == 1:
            self.multi_note.setText("当前分发范围是 1 台目标主机。")

        self.refresh_btn = QPushButton("刷新")
        self.select_btn = QPushButton("选择此目录")
        self.cancel_btn = QPushButton("取消")
        self.select_btn.setObjectName("Primary")

        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("参考主机"))
        host_row.addWidget(self.host_combo, 1)
        host_row.addWidget(self.refresh_btn)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("当前路径"))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.check_path_btn)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.select_btn)
        bottom.addWidget(self.cancel_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(host_row)
        lay.addWidget(self.multi_note)
        lay.addLayout(path_row)
        lay.addWidget(self.coverage_summary)
        lay.addWidget(self.coverage_table)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.status)
        lay.addLayout(bottom)

        self.host_combo.currentIndexChanged.connect(self._reference_host_changed)
        self.refresh_btn.clicked.connect(self.refresh_root)
        self.check_path_btn.clicked.connect(self._start_coverage_check)
        self.path_edit.editingFinished.connect(self._start_coverage_check)
        self.path_edit.textChanged.connect(self._coverage_path_edited)
        self.tree.itemExpanded.connect(self._item_expanded)
        self.tree.itemClicked.connect(self._item_clicked)
        self.tree.itemDoubleClicked.connect(self._item_double_clicked)
        self.select_btn.clicked.connect(self._accept_checked)
        self.cancel_btn.clicked.connect(self.reject)

        if not self.contexts:
            self.refresh_btn.setEnabled(False)
            self.select_btn.setEnabled(False)
            self.check_path_btn.setEnabled(False)
            self.coverage_summary.setText("当前没有可用目标主机/WinRM 凭据。")
            self.status.setText("当前没有可用目标主机/WinRM 凭据，无法浏览远程目录。")
        else:
            self._reset_coverage_rows("未检查")
            QTimer.singleShot(80, self.refresh_root)

    def _current_context(self):
        if not self.contexts:
            return None
        idx = self.host_combo.currentData()
        try:
            return self.contexts[int(idx)]
        except Exception:
            return self.contexts[0]

    def _reference_host_changed(self, *_):
        if self.contexts:
            self.refresh_root()

    def _set_busy(self, busy: bool, text=""):
        self.refresh_btn.setEnabled(not busy and bool(self.contexts))
        self.host_combo.setEnabled(not busy)
        if text:
            self.status.setText(text)

    def _start_query(self, path="", item=None):
        if self._thread and self._thread.isRunning():
            return
        ctx = self._current_context()
        if not ctx:
            return
        self._loading_item = item
        host = ctx.get("host", "")
        if path:
            self._set_busy(True, f"正在通过 WinRM 读取 {host}：{path}")
        else:
            self._set_busy(True, f"正在通过 WinRM 读取 {host} 的本地盘符……")
        self._thread = RemoteDirectoryQueryThread(
            ctx, path=path, use_https=self.use_https, port=self.port,
        )
        self._thread.completed.connect(self._query_completed)
        self._thread.start()

    def refresh_root(self):
        if not self.contexts:
            return
        self.tree.clear()
        loading = QTreeWidgetItem(["正在读取远程盘符……", ""])
        loading.setDisabled(True)
        self.tree.addTopLevelItem(loading)
        self._start_query("")

    def _add_dummy_child(self, item):
        dummy = QTreeWidgetItem(["展开以读取下一层……", ""])
        dummy.setData(0, self.ROLE_KIND, "placeholder")
        dummy.setDisabled(True)
        item.addChild(dummy)

    def _populate_drives(self, drives):
        self.tree.clear()
        current_drive = self.initial_path[:3].upper() if len(self.initial_path) >= 3 and self.initial_path[1:3] == ":\\" else ""
        target_item = None
        for d in drives or []:
            path = str(d.get("name", "") or "").replace("/", "\\")
            if not path:
                continue
            label = (d.get("volume_label") or "").strip() or "无卷标"
            total = int(d.get("total_size", 0) or 0)
            free = int(d.get("free_size", 0) or 0)
            info = f"{label} · 可用 {human_bytes(free)} / {human_bytes(total)}" if total else label
            item = QTreeWidgetItem([path, info])
            item.setData(0, self.ROLE_PATH, path)
            item.setData(0, self.ROLE_KIND, "drive")
            item.setData(0, self.ROLE_LOADED, False)
            self._add_dummy_child(item)
            self.tree.addTopLevelItem(item)
            if current_drive and path.upper() == current_drive:
                target_item = item
        if target_item is not None:
            self.tree.setCurrentItem(target_item)
            target_item.setExpanded(True)
        elif self.tree.topLevelItemCount() == 1:
            item = self.tree.topLevelItem(0)
            self.tree.setCurrentItem(item)
            self.path_edit.setText(item.data(0, self.ROLE_PATH) or "")
        if self.path_edit.text().strip():
            QTimer.singleShot(80, self._start_coverage_check)

    def _populate_directories(self, parent_item, directories):
        if parent_item is None:
            return
        parent_item.takeChildren()
        for d in directories or []:
            name = str(d.get("name", "") or "").strip()
            path = str(d.get("path", "") or "").strip().replace("/", "\\")
            if not name or not path:
                continue
            child = QTreeWidgetItem([name, "目录"])
            child.setData(0, self.ROLE_PATH, path)
            child.setData(0, self.ROLE_KIND, "directory")
            child.setData(0, self.ROLE_LOADED, False)
            self._add_dummy_child(child)
            parent_item.addChild(child)
        parent_item.setData(0, self.ROLE_LOADED, True)
        if not directories:
            empty = QTreeWidgetItem(["（无子目录）", ""])
            empty.setData(0, self.ROLE_KIND, "empty")
            empty.setDisabled(True)
            parent_item.addChild(empty)

    def _query_completed(self, payload):
        self._set_busy(False)
        item = self._loading_item
        self._loading_item = None
        if not payload.get("ok"):
            error = payload.get("error") or "未知错误"
            if item is not None:
                item.takeChildren()
                failed = QTreeWidgetItem(["（读取失败）", ""])
                failed.setDisabled(True)
                item.addChild(failed)
                item.setData(0, self.ROLE_LOADED, False)
            self.status.setText(error)
            return
        if payload.get("kind") == "drives":
            self._populate_drives(payload.get("data") or [])
            self.status.setText("参考主机盘符读取完成。展开盘符或目录时，只读取下一层。")
        else:
            self._populate_directories(item, payload.get("data") or [])
            self.status.setText(f"参考主机已读取：{payload.get('path') or ''}")

    def _item_expanded(self, item):
        kind = item.data(0, self.ROLE_KIND)
        loaded = bool(item.data(0, self.ROLE_LOADED))
        path = item.data(0, self.ROLE_PATH) or ""
        if kind not in ("drive", "directory") or loaded or not path:
            return
        item.takeChildren()
        loading = QTreeWidgetItem(["正在读取……", ""])
        loading.setDisabled(True)
        item.addChild(loading)
        self._start_query(path, item)

    def _item_clicked(self, item, _column):
        path = item.data(0, self.ROLE_PATH)
        if path:
            self.path_edit.setText(str(path))
            QTimer.singleShot(60, self._start_coverage_check)

    def _item_double_clicked(self, item, _column):
        path = item.data(0, self.ROLE_PATH)
        if path:
            item.setExpanded(True)

    def _coverage_path_edited(self, text):
        normalized = (text or "").strip().replace("/", "\\")
        if normalized != self._coverage_path:
            self._coverage_payload = None
            self.coverage_summary.setStyleSheet("color:#667A8A;")
            self.coverage_summary.setText(
                f"当前路径尚未检查全部目标主机。分发范围仍是 {len(self.contexts)} 台。" if self.contexts else ""
            )
            self._reset_coverage_rows("未检查")

    def _reset_coverage_rows(self, status):
        for r in range(self.coverage_table.rowCount()):
            item = QTableWidgetItem(status)
            self.coverage_table.setItem(r, 2, item)

    def _start_coverage_check(self):
        if not self.contexts:
            return
        path = self.path_edit.text().strip().replace("/", "\\")
        if not path:
            return
        try:
            validate_windows_target_path(path, allow_unc=False)
        except Exception:
            return
        if self._coverage_thread and self._coverage_thread.isRunning():
            self._pending_coverage_path = path
            return
        self._pending_coverage_path = ""
        self._reset_coverage_rows("检查中…")
        self.coverage_summary.setStyleSheet("color:#667A8A;")
        self.coverage_summary.setText(f"正在检查 {len(self.contexts)} 台目标主机：{path}")
        self.check_path_btn.setEnabled(False)
        self.check_path_btn.setText("正在检查…")
        self._coverage_thread = RemotePathCoverageThread(
            self.contexts, path=path, use_https=self.use_https, port=self.port,
            max_workers=min(4, len(self.contexts)),
        )
        self._coverage_thread.completed.connect(self._coverage_completed)
        self._coverage_thread.start()

    def _coverage_completed(self, payload):
        self.check_path_btn.setEnabled(bool(self.contexts))
        self.check_path_btn.setText("检查全部目标主机")
        path = (payload.get("path") or "").strip().replace("/", "\\")
        results = payload.get("results") or {}
        errors = payload.get("errors") or {}
        total = int(payload.get("total", len(self.contexts)) or 0)
        exists_count = 0
        missing_count = 0
        drive_missing_count = 0
        for r, ctx in enumerate(self.contexts):
            host = (ctx.get("host") or "").strip()
            cell = QTableWidgetItem()
            if host in errors:
                cell.setText("检查失败")
                cell.setToolTip(errors[host])
            else:
                info = results.get(host) or {}
                if info.get("exists"):
                    exists_count += 1
                    cell.setText("✓ 已存在")
                elif info.get("drive_exists"):
                    missing_count += 1
                    cell.setText("○ 目录未创建")
                    cell.setToolTip("目标盘符存在，但这个目录当前尚未创建。正式分发预检查会按现有逻辑尝试创建并验证可写。")
                else:
                    drive_missing_count += 1
                    cell.setText("⚠ 盘符不存在")
                    cell.setToolTip("目标主机没有这个盘符；若继续使用该路径，该主机正式分发会失败。")
            self.coverage_table.setItem(r, 2, cell)

        error_count = len(errors)
        self._coverage_path = path
        self._coverage_payload = {
            "path": path,
            "total": total,
            "exists": exists_count,
            "missing": missing_count,
            "drive_missing": drive_missing_count,
            "errors": error_count,
        }
        parts = [f"路径覆盖：{exists_count}/{total} 台已存在"]
        if missing_count:
            parts.append(f"{missing_count} 台目录未创建")
        if drive_missing_count:
            parts.append(f"{drive_missing_count} 台无该盘符")
        if error_count:
            parts.append(f"{error_count} 台检查失败")
        if total and exists_count == total:
            self.coverage_summary.setStyleSheet("color:#14866D; font-weight:600;")
            parts.append("这条映射会分发到以上全部目标主机")
        else:
            self.coverage_summary.setStyleSheet("color:#8A6D3B; font-weight:600;")
            parts.append("正式分发仍会对全部已勾选主机逐台预检查")
        self.coverage_summary.setText("；".join(parts) + "。")

        pending = self._pending_coverage_path
        self._pending_coverage_path = ""
        if pending and pending != path and pending == self.path_edit.text().strip().replace("/", "\\"):
            QTimer.singleShot(60, self._start_coverage_check)

    def _accept_checked(self):
        path = self.path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "远程目录", "请选择或输入一个远程目标目录。")
            return
        try:
            validate_windows_target_path(path, allow_unc=False)
        except Exception as exc:
            QMessageBox.warning(self, "远程目录", str(exc))
            return
        path = path.replace("/", "\\")
        payload = self._coverage_payload if self._coverage_path == path else None
        if payload and len(self.contexts) > 1:
            missing = int(payload.get("missing", 0) or 0)
            drive_missing = int(payload.get("drive_missing", 0) or 0)
            errors = int(payload.get("errors", 0) or 0)
            if drive_missing or missing or errors:
                detail = []
                if missing:
                    detail.append(f"{missing} 台目标机上该目录尚未创建")
                if drive_missing:
                    detail.append(f"{drive_missing} 台目标机没有对应盘符")
                if errors:
                    detail.append(f"{errors} 台目标机未能完成只读检查")
                text = (
                    "当前路径并非在所有目标主机上都已存在：\n" + "\n".join(f"• {x}" for x in detail) +
                    f"\n\n这条映射仍会应用到当前全部 {len(self.contexts)} 台目标主机。"
                    "目录未创建时，正式分发预检查会按现有逻辑尝试创建；盘符不存在的主机会失败。\n\n仍选择此路径吗？"
                )
                if QMessageBox.question(self, "确认多主机目标路径", text) != QMessageBox.Yes:
                    return
        self.path_edit.setText(path)
        self.accept()

    def selected_path(self):
        return self.path_edit.text().strip().replace("/", "\\")

    def reject(self):
        if (self._thread and self._thread.isRunning()) or (self._coverage_thread and self._coverage_thread.isRunning()):
            QMessageBox.information(self, "远程目录", "正在读取远程信息，请等待当前读取完成后再关闭窗口。")
            return
        super().reject()

    def closeEvent(self, event):
        if (self._thread and self._thread.isRunning()) or (self._coverage_thread and self._coverage_thread.isRunning()):
            event.ignore()
            return
        super().closeEvent(event)


class RemoteProcessQueryThread(QThread):
    """通过 WinRM 只读读取一个参考主机的当前进程列表。"""
    completed = Signal(object)

    def __init__(self, context: dict, *, use_https=False, port=5985):
        super().__init__()
        self.context = dict(context or {})
        self.use_https = bool(use_https)
        self.port = int(port)

    def run(self):
        host = self.context.get("host", "")
        plan = RemoteActionPlan(
            enabled=False,
            use_https=self.use_https,
            port=self.port,
            username=self.context.get("username", ""),
            password=self.context.get("password", ""),
            command_timeout=35,
        )
        try:
            data = WinRMExecutor(host, plan).list_processes()
            self.completed.emit({"ok": True, "host": host, "data": data, "error": ""})
        except Exception as exc:
            self.completed.emit({
                "ok": False,
                "host": host,
                "data": [],
                "error": describe_winrm_failure(host, plan, exc),
            })


class RemoteProcessBrowserDialog(QDialog):
    """通过 WinRM 读取参考主机进程，并按实际镜像名选择要结束的进程。"""
    COL_CHECK = 0
    COL_DESC = 1
    COL_IMAGE = 2
    COL_COUNT = 3
    COL_PIDS = 4
    COL_PATH = 5

    def __init__(self, parent=None, *, contexts=None, preselected=None, use_https=False, port=5985):
        super().__init__(parent)
        self.contexts = list(contexts or [])
        self.preselected = [x.strip() for x in (preselected or []) if str(x).strip()]
        self.use_https = bool(use_https)
        self.port = int(port)
        self._thread = None

        self.setWindowTitle("选择远程进程")
        self.resize(1040, 680)

        self.host_combo = QComboBox()
        for i, ctx in enumerate(self.contexts):
            name = (ctx.get("name") or "").strip()
            host = (ctx.get("host") or "").strip()
            shown = f"{name}  ({host})" if name and name != host else host
            self.host_combo.addItem(shown or f"目标主机 {i+1}", i)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索显示名称、exe、PID 或路径，例如 Graphic / sys_ / ADMS")
        self.refresh_btn = QPushButton("刷新进程")

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["选择", "显示名称", "镜像名（实际参数）", "实例数", "PID", "可执行路径"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_CHECK, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_DESC, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_IMAGE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_COUNT, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PIDS, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PATH, QHeaderView.Stretch)

        self.status = QLabel("进程读取只通过 WinRM 查询，不会在打开窗口时结束任何进程。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#667A8A;")
        self.note = QLabel(
            "注意：正式执行按“镜像名”调用 taskkill /F /T /IM。若同一 exe 有多个实例，选择一次会结束该 exe 的全部实例；"
            "多主机任务会在所有勾选目标主机上执行同一组镜像名。"
        )
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#8A6D3B;")

        self.select_visible_btn = QPushButton("全选当前筛选")
        self.clear_btn = QPushButton("取消全选")
        self.ok_btn = QPushButton("使用所选进程")
        self.ok_btn.setObjectName("Primary")
        self.cancel_btn = QPushButton("取消")

        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("参考主机"))
        host_row.addWidget(self.host_combo, 1)
        host_row.addWidget(self.refresh_btn)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索"))
        search_row.addWidget(self.search, 1)
        action_row = QHBoxLayout()
        action_row.addWidget(self.select_visible_btn)
        action_row.addWidget(self.clear_btn)
        action_row.addStretch(1)
        action_row.addWidget(self.ok_btn)
        action_row.addWidget(self.cancel_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(host_row)
        if len(self.contexts) > 1:
            multi = QLabel(f"当前共勾选 {len(self.contexts)} 台目标主机；这里只读取“参考主机”的进程，用于生成所有目标主机共用的结束进程列表。")
            multi.setWordWrap(True)
            multi.setStyleSheet("color:#8A6D3B;")
            lay.addWidget(multi)
        lay.addLayout(search_row)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.note)
        lay.addLayout(action_row)

        self.host_combo.currentIndexChanged.connect(self.refresh_processes)
        self.refresh_btn.clicked.connect(self.refresh_processes)
        self.search.textChanged.connect(self._apply_filter)
        self.select_visible_btn.clicked.connect(self._select_visible)
        self.clear_btn.clicked.connect(self._clear_all)
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

        if not self.contexts:
            self.refresh_btn.setEnabled(False)
            self.ok_btn.setEnabled(False)
            self.status.setText("当前没有可用目标主机/WinRM 凭据，无法读取远程进程。")
        else:
            QTimer.singleShot(80, self.refresh_processes)

    def _current_context(self):
        if not self.contexts:
            return None
        idx = self.host_combo.currentData()
        try:
            return self.contexts[int(idx)]
        except Exception:
            return self.contexts[0]

    def _set_busy(self, busy: bool, text=""):
        self.host_combo.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy and bool(self.contexts))
        if text:
            self.status.setText(text)

    def refresh_processes(self, *_):
        if not self.contexts or (self._thread and self._thread.isRunning()):
            return
        ctx = self._current_context()
        if not ctx:
            return
        host = ctx.get("host", "")
        self._set_busy(True, f"正在通过 WinRM 读取 {host} 的进程列表……")
        self._thread = RemoteProcessQueryThread(ctx, use_https=self.use_https, port=self.port)
        self._thread.completed.connect(self._processes_loaded)
        self._thread.start()

    def _processes_loaded(self, payload):
        self._set_busy(False)
        if not payload.get("ok"):
            self.table.setRowCount(0)
            self.status.setText(payload.get("error") or "读取远程进程失败。")
            return

        processes = list(payload.get("data") or [])
        # 与正式 taskkill /IM 语义一致：按镜像名聚合，而不是让用户误以为只结束一个 PID。
        grouped = {}
        for proc in processes:
            image = str(proc.get("image_name", "") or "").strip()
            if not image:
                continue
            key = image.lower()
            g = grouped.setdefault(key, {
                "image": image, "descriptions": [], "pids": [], "paths": [], "commands": []
            })
            desc = str(proc.get("description", "") or "").strip()
            path = str(proc.get("path", "") or "").strip()
            cmd = str(proc.get("command_line", "") or "").strip()
            pid = int(proc.get("pid", 0) or 0)
            if desc and desc not in g["descriptions"]:
                g["descriptions"].append(desc)
            if path and path not in g["paths"]:
                g["paths"].append(path)
            if cmd and cmd not in g["commands"]:
                g["commands"].append(cmd)
            if pid:
                g["pids"].append(pid)

        selected_lower = {x.lower() for x in self.preselected}
        rows = sorted(grouped.values(), key=lambda g: ((g["descriptions"][0] if g["descriptions"] else g["image"]).lower(), g["image"].lower()))
        present = {g["image"].lower() for g in rows}
        for old in self.preselected:
            if old.lower() not in present:
                rows.append({"image": old, "descriptions": ["已配置（当前未运行/未发现）"], "pids": [], "paths": [], "commands": []})

        self.table.setRowCount(len(rows))
        for r, g in enumerate(rows):
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if g["image"].lower() in selected_lower else Qt.Unchecked)
            self.table.setItem(r, self.COL_CHECK, check)
            desc = " / ".join(g["descriptions"][:2]) if g["descriptions"] else ""
            self.table.setItem(r, self.COL_DESC, QTableWidgetItem(desc))
            self.table.setItem(r, self.COL_IMAGE, QTableWidgetItem(g["image"]))
            self.table.setItem(r, self.COL_COUNT, QTableWidgetItem(str(len(g["pids"]))))
            pids = ", ".join(str(x) for x in sorted(g["pids"]))
            self.table.setItem(r, self.COL_PIDS, QTableWidgetItem(pids))
            paths = " | ".join(g["paths"][:2])
            path_item = QTableWidgetItem(paths)
            if g["commands"]:
                path_item.setToolTip("命令行：\n" + "\n".join(g["commands"][:5]))
            self.table.setItem(r, self.COL_PATH, path_item)
        self._apply_filter(self.search.text())
        self.status.setText(
            f"已读取 {payload.get('host') or ''}：{len(processes)} 个进程实例，按镜像名合并为 {len(grouped)} 项。"
        )

    def _apply_filter(self, text=""):
        needle = (text or "").strip().lower()
        for r in range(self.table.rowCount()):
            hay = " ".join(
                (self.table.item(r, c).text() if self.table.item(r, c) else "")
                for c in range(1, self.table.columnCount())
            ).lower()
            self.table.setRowHidden(r, bool(needle and needle not in hay))

    def _select_visible(self):
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            item = self.table.item(r, self.COL_CHECK)
            if item:
                item.setCheckState(Qt.Checked)

    def _clear_all(self):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, self.COL_CHECK)
            if item:
                item.setCheckState(Qt.Unchecked)

    def selected_image_names(self) -> list[str]:
        out = []
        seen = set()
        for r in range(self.table.rowCount()):
            check = self.table.item(r, self.COL_CHECK)
            image = self.table.item(r, self.COL_IMAGE)
            if not check or check.checkState() != Qt.Checked or not image:
                continue
            name = image.text().strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                out.append(name)
        return out

    def reject(self):
        if self._thread and self._thread.isRunning():
            QMessageBox.information(self, "远程进程", "正在读取远程进程，请等待当前读取完成后再关闭窗口。")
            return
        super().reject()

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            event.ignore()
            return
        super().closeEvent(event)




class MappingTargetDialog(QDialog):
    def __init__(self, parent=None, *, title="配置分发映射", target_path="", is_directory=False,
                 folder_mode="CONTENTS", remote_drive_contexts=None, remote_drive_hint="",
                 use_https=False, winrm_port=5985):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 390)
        self.remote_drive_contexts = list(remote_drive_contexts or [])
        self.remote_drive_hint = remote_drive_hint or ""
        self.use_https = bool(use_https)
        self.winrm_port = int(winrm_port)
        self.target = QLineEdit(target_path)
        self.target.setPlaceholderText(r"例如：D:\ADMS\dll、E:\NariTech\bin")
        self.mode = QComboBox()
        self.mode.addItem("仅复制目录内容到目标目录", "CONTENTS")
        self.mode.addItem("复制目录本身到目标目录", "SELF")
        idx = self.mode.findData(folder_mode)
        self.mode.setCurrentIndex(max(0, idx))
        self.mode.setEnabled(is_directory)
        scope_names = [((c.get("name") or c.get("host") or "").strip()) for c in self.remote_drive_contexts]
        scope_preview = "、".join(scope_names[:4])
        if len(scope_names) > 4:
            scope_preview += f" 等 {len(scope_names)} 台"
        if scope_names:
            note_text = f"当前已勾选 {len(scope_names)} 台目标主机；这条映射会把同一个目标路径同时应用到：{scope_preview}。"
        else:
            note_text = "目标目录属于本次分发映射；选择目标主机后，这条映射会应用到全部已勾选主机。"
        note = QLabel(note_text)
        note.setWordWrap(True); note.setStyleSheet("color:#667A8A;")
        self.drive_selector = RemoteDriveSelector(
            self.target, contexts=self.remote_drive_contexts, context_hint=self.remote_drive_hint,
            use_https=self.use_https, port=self.winrm_port, parent=self,
        )
        self.browse_remote_btn = QPushButton("浏览远程目录")
        self.browse_remote_btn.setEnabled(bool(self.remote_drive_contexts))
        self.browse_remote_btn.setToolTip("通过 WinRM 只读浏览参考目标主机的盘符和目录；不会创建或修改远程文件。")
        self.browse_remote_btn.clicked.connect(self._browse_remote_directory)
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.addWidget(self.target, 1)
        target_row.addWidget(self.browse_remote_btn)
        form = QFormLayout()
        form.addRow("远程盘符", self.drive_selector)
        form.addRow("目标目录", target_row)
        if is_directory:
            form.addRow("目录方式", self.mode)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_checked); buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(note); lay.addLayout(form); lay.addWidget(buttons)

    def _browse_remote_directory(self):
        if not self.remote_drive_contexts:
            QMessageBox.information(self, "远程目录", self.remote_drive_hint or "请先勾选至少一台具有可用 WinRM 凭据的目标主机。")
            return
        d = RemoteDirectoryBrowserDialog(
            self, contexts=self.remote_drive_contexts, initial_path=self.target.text().strip(),
            use_https=self.use_https, port=self.winrm_port,
        )
        if d.exec():
            self.target.setText(d.selected_path())
            self.target.setFocus()
            self.target.setCursorPosition(len(self.target.text()))

    def _accept_checked(self):
        if not self.target.text().strip():
            QMessageBox.warning(self, "分发映射", "目标目录不能为空。")
            return
        try:
            validate_windows_target_path(self.target.text().strip(), allow_unc=False)
        except Exception as e:
            QMessageBox.warning(self, "分发映射", str(e))
            return
        self.accept()

    def value(self):
        return {"target_path": self.target.text().strip(), "folder_mode": self.mode.currentData()}


class SftpMappingDialog(QDialog):
    def __init__(self, parent=None, *, initial=None, remote_drive_contexts=None, remote_drive_hint="",
                 use_https=False, winrm_port=5985):
        super().__init__(parent)
        initial = initial or {}
        self.setWindowTitle("添加 SFTP 分发映射")
        self.resize(760, 540)
        self.remote_drive_contexts = list(remote_drive_contexts or [])
        self.remote_drive_hint = remote_drive_hint or ""
        self.use_https = bool(use_https)
        self.winrm_port = int(winrm_port)
        self.host = QLineEdit(initial.get("host", ""))
        self.port = QSpinBox(); self.port.setRange(1,65535); self.port.setValue(int(initial.get("port",22)))
        self.username = QLineEdit(initial.get("username", ""))
        self.password = PasswordLineEdit(initial.get("password", ""))
        self.remote_path = QLineEdit(initial.get("source_path", ""))
        self.source_kind = QComboBox(); self.source_kind.addItem("远程文件", "FILE"); self.source_kind.addItem("远程目录", "DIR")
        idx = self.source_kind.findData(initial.get("source_kind", "FILE")); self.source_kind.setCurrentIndex(max(0,idx))
        self.target = QLineEdit(initial.get("target_path", "")); self.target.setPlaceholderText(r"例如：D:\ADMS\dll")
        self.folder_mode = QComboBox(); self.folder_mode.addItem("仅复制目录内容到目标目录", "CONTENTS"); self.folder_mode.addItem("复制目录本身到目标目录", "SELF")
        idx = self.folder_mode.findData(initial.get("folder_mode", "CONTENTS")); self.folder_mode.setCurrentIndex(max(0,idx))
        self.source_kind.currentIndexChanged.connect(lambda *_: self.folder_mode.setEnabled(self.source_kind.currentData()=="DIR"))
        self.folder_mode.setEnabled(self.source_kind.currentData()=="DIR")
        scope_names = [((c.get("name") or c.get("host") or "").strip()) for c in self.remote_drive_contexts]
        scope_text = f" 当前已勾选 {len(scope_names)} 台目标主机，这条映射会应用到全部这些主机。" if scope_names else ""
        note = QLabel("SFTP 密码只存在当前程序进程内，不写入 SQLite、JSONL 或任务审计文件。分发时先拉取到本机缓存，再通过 WinRM 向 Windows 目标主机分发。" + scope_text)
        note.setWordWrap(True); note.setStyleSheet("color:#667A8A;")
        self.drive_selector = RemoteDriveSelector(
            self.target, contexts=self.remote_drive_contexts, context_hint=self.remote_drive_hint,
            use_https=self.use_https, port=self.winrm_port, parent=self,
        )
        self.browse_remote_btn = QPushButton("浏览远程目录")
        self.browse_remote_btn.setEnabled(bool(self.remote_drive_contexts))
        self.browse_remote_btn.setToolTip("通过 WinRM 只读浏览参考目标主机目录。")
        self.browse_remote_btn.clicked.connect(self._browse_remote_directory)
        target_row = QHBoxLayout(); target_row.setContentsMargins(0,0,0,0); target_row.addWidget(self.target,1); target_row.addWidget(self.browse_remote_btn)
        form=QFormLayout(); form.addRow("SFTP 主机",self.host); form.addRow("端口",self.port); form.addRow("用户名",self.username); form.addRow("密码",self.password); form.addRow("远程路径",self.remote_path); form.addRow("远程源类型",self.source_kind); form.addRow("远程盘符",self.drive_selector); form.addRow("目标目录",target_row); form.addRow("目录方式",self.folder_mode)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Ok).setText("添加映射"); buttons.button(QDialogButtonBox.Cancel).setText("取消"); buttons.accepted.connect(self._accept_checked); buttons.rejected.connect(self.reject)
        lay=QVBoxLayout(self); lay.addWidget(note); lay.addLayout(form); lay.addWidget(buttons)

    def _browse_remote_directory(self):
        if not self.remote_drive_contexts:
            QMessageBox.information(self, "远程目录", self.remote_drive_hint or "请先勾选至少一台具有可用 WinRM 凭据的目标主机。")
            return
        d = RemoteDirectoryBrowserDialog(
            self, contexts=self.remote_drive_contexts, initial_path=self.target.text().strip(),
            use_https=self.use_https, port=self.winrm_port,
        )
        if d.exec():
            self.target.setText(d.selected_path())
            self.target.setFocus()
            self.target.setCursorPosition(len(self.target.text()))

    def _accept_checked(self):
        if not all([self.host.text().strip(), self.username.text().strip(), self.remote_path.text().strip(), self.target.text().strip()]):
            QMessageBox.warning(self,"SFTP 分发映射","SFTP 主机、用户名、远程路径和目标目录不能为空。")
            return
        try:
            validate_windows_target_path(self.target.text().strip(), allow_unc=False)
        except Exception as e:
            QMessageBox.warning(self,"SFTP 分发映射",str(e))
            return
        self.accept()

    def value(self):
        return {"host":self.host.text().strip(),"port":self.port.value(),"username":self.username.text().strip(),"password":self.password.text(),"source_path":self.remote_path.text().strip(),"source_kind":self.source_kind.currentData(),"target_path":self.target.text().strip(),"folder_mode":self.folder_mode.currentData() if self.source_kind.currentData()=="DIR" else "CONTENTS"}



class WinRMHostCredentialDialog(QDialog):
    """Configure one credential override for one or more target hosts.

    Password persistence is delegated to Windows Credential Manager by the main window;
    this dialog never writes credentials itself and never logs the password.
    """
    def __init__(self, parent=None, *, host_count: int = 1, username: str = "", password: str = "",
                 remember: bool = True):
        super().__init__(parent)
        self.setWindowTitle("设置 WinRM 自定义凭据")
        self.resize(590, 300)

        title = "当前主机" if host_count == 1 else f"选中的 {host_count} 台主机"
        note = QLabel(
            f"为{title}设置自定义 WinRM 凭据。自定义凭据会覆盖页面顶部的默认凭据。"
            "勾选“安全记住”后，密码只保存到当前 Windows 用户的 Windows 凭据管理器，"
            "不会写入 settings.json、SQLite、JSONL、审计日志或命令行。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#667A8A;")

        self.username = QLineEdit(username)
        self.username.setPlaceholderText(r"例如 ADMS、COMPUTER\ADMS、DOMAIN\deployuser")
        self.password = PasswordLineEdit(password)
        self.remember = QCheckBox("安全记住这些主机的凭据（Windows 凭据管理器）")
        self.remember.setChecked(bool(remember))

        form = QFormLayout()
        form.addRow("Windows 用户", self.username)
        form.addRow("密码", self.password)
        form.addRow("", self.remember)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存自定义凭据")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _accept_checked(self):
        if not self.username.text().strip():
            QMessageBox.warning(self, "WinRM 凭据", "用户名不能为空。")
            self.username.setFocus()
            return
        if not self.password.text():
            QMessageBox.warning(self, "WinRM 凭据", "密码不能为空。")
            self.password.setFocus()
            return
        self.accept()

    def value(self):
        return {
            "username": self.username.text().strip(),
            "password": self.password.text(),
            "remember": self.remember.isChecked(),
        }


class HostnameCredentialDialog(QDialog):
    """一次性主机名深度验证凭据。密码只存在当前进程内，不保存。"""
    def __init__(self, parent=None, title="主机名深度验证"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(590, 340)

        self.method = QComboBox()
        self.method.addItem("自动（SMB/WKSSVC 优先，WinRM 备用）", "AUTO")
        self.method.addItem("SMB/WKSSVC（TCP 445）", "SMB")
        self.method.addItem("WinRM hostname", "WINRM")

        self.username = QLineEdit()
        self.username.setPlaceholderText(r"例如：DOMAIN\deployuser、COMPUTER\Administrator 或本地管理员")
        self.password = PasswordLineEdit()
        self.https = QCheckBox("使用 HTTPS")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(5985)
        self.https.toggled.connect(lambda checked: self.port.setValue(5986 if checked else 5985))
        self.method.currentIndexChanged.connect(self._method_changed)

        note = QLabel(
            "验证只针对你明确选择的主机。自动模式会优先通过 SMB/WKSSVC（TCP 445）读取目标 Windows "
            "Computer Name；即使目标机没有开启 WinRM 也可以验证。只有 SMB 验证失败时才尝试 WinRM。"
            "用户名和密码仅保存在当前进程内，不写入 SQLite、JSONL 或审计日志。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#667A8A;")

        form = QFormLayout()
        form.addRow("验证方式", self.method)
        form.addRow("Windows 用户名", self.username)
        form.addRow("密码", self.password)
        form.addRow("WinRM HTTPS", self.https)
        form.addRow("WinRM 端口", self.port)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("开始验证")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self._method_changed()

    def _method_changed(self, *_):
        method = self.method.currentData()
        enabled = method in ("AUTO", "WINRM")
        self.https.setEnabled(enabled)
        self.port.setEnabled(enabled)

    def _accept_checked(self):
        if not self.username.text().strip() or not self.password.text():
            self.username.setFocus()
            return
        self.accept()

    def value(self):
        return {
            "method": self.method.currentData(),
            "username": self.username.text().strip(),
            "password": self.password.text(),
            "use_https": self.https.isChecked(),
            "port": self.port.value(),
        }


# 兼容旧代码引用；新代码统一使用 HostnameCredentialDialog。
WinRMCredentialDialog = HostnameCredentialDialog


def _table(rows, cols, headers, transformers=None):
    t = QTableWidget()
    t.setColumnCount(len(cols))
    t.setHorizontalHeaderLabels(headers)
    t.setRowCount(len(rows))
    transformers = transformers or {}
    for r, row in enumerate(rows):
        for c, key in enumerate(cols):
            value = row[key]
            if key in transformers:
                value = transformers[key](value)
            t.setItem(r, c, QTableWidgetItem(str(value if value is not None else "")))
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    return t


class TaskDetailDialog(QDialog):
    def __init__(self, task_id: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"任务详情 - {task_id}")
        self.resize(1280, 760)

        lay = QVBoxLayout(self)
        task = db.get_task(task_id)
        if task:
            summary = QLabel(
                f"任务：{task['task_id']}    状态：{status_text(task['status'])}    "
                f"来源：{source_text(task['source_type'])}    目标：{task['target_path']}\n"
                f"备份根目录：{task['backup_root'] or '目标目录\\.fds_backup'}    "
                f"校验：{task['verification_mode']}    清单指纹：{task['manifest_sha256'] or '-'}\n"
                f"本地审计目录：{task['audit_path'] or '-'}"
            )
            summary.setWordWrap(True)
            summary.setStyleSheet("padding:10px;background:#F5F8FB;border:1px solid #DCE6EE;border-radius:8px;")
            lay.addWidget(summary)

        tabs = QTabWidget()

        mapping_rows = db.list_task_mappings(task_id)
        if mapping_rows:
            tabs.addTab(_table(
                mapping_rows,
                ["mapping_id","source_type","source_path","target_path","source_kind","folder_mode","file_count","total_bytes","manifest_sha256"],
                ["映射 ID","来源类型","源文件 / 目录","目标目录","源类型","目录方式","文件数","总字节数","映射清单 SHA256"],
                {"source_type": source_text, "source_kind": lambda v: "目录" if v == "DIR" else "文件",
                 "folder_mode": lambda v: "复制目录本身" if v == "SELF" else "复制目录内容"},
            ), "分发映射")

        host_rows = db.list_task_hosts(task_id)
        tabs.addTab(_table(
            host_rows,
            ["host","hostname","status","transferred_bytes","new_files","updated_files",
             "skipped_files","failed_files","error_message"],
            ["主机 / IP","主机名","状态","传输字节数","新增文件","更新文件","跳过文件","失败文件","错误信息"],
            {"status": status_text},
        ), "主机")

        file_rows = db.list_task_files(task_id)
        tabs.addTab(_table(
            file_rows,
            ["host","mapping_id","source_path","target_path","relative_path","action","size","source_sha256","target_sha256","status","error_message"],
            ["主机 / IP","映射 ID","实际源文件","实际目标文件","相对路径","操作","大小","源 SHA256","目标 SHA256","状态","错误信息"],
            {"action": action_text, "status": status_text},
        ), "文件分发")

        action_rows = db.list_task_actions(task_id)
        tabs.addTab(_table(
            action_rows,
            ["host","phase","action","command","status","exit_code","duration_ms","stdout","stderr","created_at"],
            ["主机 / IP","阶段","操作","命令","状态","退出码","耗时(ms)","标准输出","错误输出","时间"],
            {"phase": phase_text, "action": action_text, "status": status_text},
        ), "远程操作")

        backup_rows = db.list_task_backups(task_id)
        tabs.addTab(_table(
            backup_rows,
            ["host","relative_path","original_path","backup_path","size","sha256","status","error_message","created_at"],
            ["主机 / IP","相对路径","原文件","备份文件","大小","备份 SHA256","状态","错误信息","时间"],
            {"status": status_text},
        ), "备份记录")

        verify_rows = db.list_task_verifications(task_id)
        tabs.addTab(_table(
            verify_rows,
            ["host","relative_path","stage","algorithm","expected_value","actual_value","status","details","created_at"],
            ["主机 / IP","相对路径","校验阶段","算法","期望值","实际值","状态","说明","时间"],
            {"status": status_text, "stage": verify_stage_text},
        ), "校验记录")

        audit_rows = db.list_audit_events(limit=10000, task_id=task_id)
        audit_rows = list(reversed(audit_rows))
        tabs.addTab(_table(
            audit_rows,
            ["created_at","category","action","status","host","subject","message","details_json"],
            ["时间","分类","操作","状态","主机 / IP","对象","说明","详细数据"],
            {"status": status_text, "category": audit_category_text, "action": action_text},
        ), "完整审计")

        lay.addWidget(tabs)


class WinRMSetupDialog(QDialog):
    """面向非专业用户的目标机 WinRM 准备向导。"""

    ADMS_ONECLICK = "TARGET_PREP_ADMS_WINRM.cmd"
    ADMS_RESTORE = "TARGET_RESTORE_ADMS_WINRM.cmd"
    STANDARD = "TARGET_PREP_WINRM.cmd"
    LOCAL_ADMIN = "TARGET_PREP_WINRM_LOCAL_ADMIN.cmd"
    RESTORE = "TARGET_RESTORE_WINRM_LOCAL_ADMIN.cmd"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WinRM 配置向导")
        self.resize(760, 620)

        title = QLabel("目标 Windows 只需完成一次 WinRM 准备")
        title.setStyleSheet("font-size:14pt;font-weight:700;")
        intro = QLabel(
            "目标机首次使用时，可导出简化 WinRM 设置脚本并以管理员身份运行。脚本只启用 WinRM 并设置约定的 LocalAccountTokenFilterPolicy；不会读取或修改任何账号、用户组或 RDP 权限。"
        )
        intro.setWordWrap(True)

        self.mode = QComboBox()
        self.mode.addItem("简化 WinRM 设置（推荐：不修改任何账号）", self.ADMS_ONECLICK)
        self.mode.addItem("标准 WinRM 服务准备（不修改 UAC / 注册表）", self.STANDARD)
        self.mode.addItem("本地管理员 WinRM 模式（通用）", self.LOCAL_ADMIN)
        self.mode.currentIndexChanged.connect(self._refresh_text)

        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet("color:#667A8A;")

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QTextEdit.NoWrap)
        self.preview.setMinimumHeight(300)

        row = QHBoxLayout()
        save_btn = QPushButton("保存 CMD 到…")
        copy_btn = QPushButton("复制脚本内容")
        self.restore_btn = QPushButton("保存对应恢复脚本…")
        save_btn.clicked.connect(self._save_selected)
        copy_btn.clicked.connect(self._copy_selected)
        self.restore_btn.clicked.connect(self._save_restore)
        row.addWidget(save_btn); row.addWidget(copy_btn); row.addWidget(self.restore_btn); row.addStretch(1)

        help_text = QLabel(
            "使用方式：保存脚本 → 复制到目标 Windows → 右键“以管理员身份运行” → 回到本软件点击“测试 WinRM”。\n"
            "设置脚本只启用 WinRM 并写入 LocalAccountTokenFilterPolicy=1；还原脚本只删除该注册表值并停止 WinRM。账号权限检查/加入命令请到“使用帮助”中手工执行。"
        )
        help_text.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(intro)
        form = QFormLayout(); form.addRow("准备模式", self.mode); lay.addLayout(form)
        lay.addWidget(self.desc)
        lay.addWidget(self.preview, 1)
        lay.addLayout(row)
        lay.addWidget(help_text)
        lay.addWidget(buttons)
        self._refresh_text()

    def _selected_script(self):
        return str(self.mode.currentData())

    @staticmethod
    def _read_script(name: str) -> str:
        p = script_path(name)
        if not p.exists():
            return f"[脚本资源缺失] {name}"
        return p.read_text(encoding="utf-8-sig")

    def _refresh_text(self):
        name = self._selected_script()
        if name == self.ADMS_ONECLICK:
            self.desc.setText(
                "最简模式：只启用 WinRM，并设置 LocalAccountTokenFilterPolicy=1，使已属于本地 Administrators 的账号可获得完整远程管理员令牌。脚本不会检查或修改 ADMS、Administrators、Remote Desktop Users 或其他账号设置。"
            )
        elif name == self.LOCAL_ADMIN:
            self.desc.setText(
                "适用于任意已属于目标机本地 Administrators 的本地账号。除标准 WinRM 服务初始化外，还会设置 "
                "LocalAccountTokenFilterPolicy=1，使本地管理员通过 WinRM 获得完整管理员令牌。"
                "脚本执行前会保存原始状态，并提供恢复脚本。"
            )
        else:
            self.desc.setText(
                "只执行 winrm quickconfig、查询/启动 WinRM、设置自动启动、检查 Listener/5985 和 winrm id；"
                "不修改注册表、UAC、TrustedHosts 或账号权限。"
            )
        self.preview.setPlainText(self._read_script(name))
        self.restore_btn.setEnabled(name != self.STANDARD)
        self.restore_btn.setToolTip("标准 WinRM 模式不修改账号权限/Remote UAC，无需对应恢复脚本。" if name == self.STANDARD else "保存与当前准备模式对应的恢复脚本。")

    def _save_script(self, name: str, title: str):
        default_name = name
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, "Windows CMD (*.cmd);;所有文件 (*)")
        if not path:
            return
        if not path.lower().endswith(".cmd"):
            path += ".cmd"
        try:
            source = script_path(name)
            if not source.exists():
                raise FileNotFoundError(f"脚本资源缺失：{name}")
            # 直接复制资源字节，保留仓库中经过验证的 ASCII + CRLF + 无 BOM 格式，
            # 避免 Windows cmd.exe 因 BOM/行尾被破坏而把脚本片段误解析为命令。
            Path(path).write_bytes(source.read_bytes())
            QMessageBox.information(self, "WinRM 配置向导", f"脚本已保存：\n{path}\n\n请复制到目标 Windows 并以管理员身份运行。")
        except Exception as e:
            QMessageBox.critical(self, "WinRM 配置向导", f"保存脚本失败：\n{e}")

    def _save_selected(self):
        self._save_script(self._selected_script(), "保存目标机 WinRM 配置脚本")

    def _save_restore(self):
        selected = self._selected_script()
        if selected == self.ADMS_ONECLICK:
            self._save_script(self.ADMS_RESTORE, "保存 WinRM 还原脚本")
        elif selected == self.LOCAL_ADMIN:
            self._save_script(self.RESTORE, "保存 WinRM 本地管理员策略恢复脚本")
        else:
            QMessageBox.information(self, "WinRM 配置向导", "标准 WinRM 模式不修改账号权限或 Remote UAC，因此无需对应恢复脚本。")

    def _copy_selected(self):
        QApplication.clipboard().setText(self._read_script(self._selected_script()))
        QMessageBox.information(self, "WinRM 配置向导", "脚本内容已复制到剪贴板。")

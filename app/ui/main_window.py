from __future__ import annotations
from pathlib import Path
from datetime import datetime
import csv
import os
import logging

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QSpinBox, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QPlainTextEdit, QGroupBox,
    QAbstractItemView, QProgressBar, QFrame, QStackedWidget, QScrollArea, QApplication, QDialog, QDialogButtonBox
)

from ..version import APP_NAME, APP_VERSION
from ..config import AppSettings
from ..models import HostRecord, DistributionMapping
from ..resources import asset_path, script_path
from ..services.discovery import local_ipv4_networks, normalize_networks
from ..services.sftp_source import SftpSource
from ..services.remote_exec import RemoteActionPlan, WinRMExecutor, split_items, split_commands
from ..services import audit, credential_store
from ..utils import human_bytes, validate_windows_target_path
from ..workers import DiscoveryThread, DistributionThread, HostnameVerificationThread, WinRMTargetTestThread, HostStatusTestThread
from .. import db
from .dialogs import (HostEditDialog, TaskDetailDialog, HostnameCredentialDialog, MappingTargetDialog,
                      SftpMappingDialog, WinRMSetupDialog, WinRMHostCredentialDialog, RemoteProcessBrowserDialog)
from .theme import app_icon
from .widgets import PasswordLineEdit
from .locale_zh import (status_text, source_text, mode_text, group_text, audit_category_text, action_text,
                         hostname_source_text)
from ..services.host_status import status_label as online_status_text, smb_status_label, winrm_status_label

ROLE_HOST_OBJECT = Qt.UserRole + 2

WINRM_SERVICE_HELP = r""":: 1. 初始化
winrm quickconfig

:: 2. 服务状态
sc query WinRM

:: 3. 启动
sc start WinRM

:: 4. 停止
sc stop WinRM

:: 5. Listener
winrm enumerate winrm/config/listener

:: 6. 5985
netstat -ano | findstr :5985

:: 7. 本机 WinRM
winrm id

:: 启动类型
sc config WinRM start= disabled
sc config WinRM start= auto
sc start WinRM"""

ADMS_ACCOUNT_HELP = r""":: 1. 查看 ADMS 账号
net user ADMS

:: 2. 查看本地 Administrators 成员（只读）
:: SID S-1-5-32-544 = Windows 内置 Administrators
Get-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-544').Name

:: 3. 仅在确认需要时，把 ADMS 加入 Administrators
:: 请在“管理员 PowerShell”中执行
Add-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-544').Name -Member 'ADMS'
Get-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-544').Name"""

WINRM_LOCAL_ADMIN_HELP = r""":: ======================================
:: CMD：查看当前状态
:: ======================================
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy

:: ======================================
:: CMD：开启
:: LocalAccountTokenFilterPolicy = 1
:: 允许本地管理员通过网络获得完整管理员令牌
:: ======================================
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System ^
 /v LocalAccountTokenFilterPolicy ^
 /t REG_DWORD ^
 /d 1 ^
 /f

:: ======================================
:: CMD：设置为 0
:: 恢复 Windows Remote UAC 默认过滤行为
:: 注意：如果原来这个值“不存在”，严格意义上这不叫恢复原状
:: ======================================
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System ^
 /v LocalAccountTokenFilterPolicy ^
 /t REG_DWORD ^
 /d 0 ^
 /f

:: ======================================
:: CMD：删除该值
:: 如果你机器原来就是“不存在”，这个才是最准确的恢复原状
:: ======================================
reg delete HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System ^
 /v LocalAccountTokenFilterPolicy ^
 /f

:: ======================================
:: CMD：再次确认
:: ======================================
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy"""

PAGE_META = [
    ("文件分发", "选择本地或远程源，将文件安全分发到多台 Windows 主机。", "send"),
    ("主机管理", "管理目标主机身份、分组、在线状态和远程访问能力。", "hosts"),
    ("主机发现", "跨一个或多个 IPv4 网段发现可访问的 Windows 主机。", "radar"),
    ("分发历史", "查看任务、主机、文件、备份、校验以及远程操作详情。", "history"),
    ("审计日志", "查看软件操作、主机发现、分发、备份、校验和远程操作的本地审计记录。", "terminal"),
    ("使用帮助", "查看 WinRM 初始化、ADMS 账号权限检查和常用远程操作说明。", "terminal"),
    ("系统设置", "调整并发、扫描、备份、校验、本地缓存和审计目录。", "settings"),
]


def card(title: str, subtitle: str = ""):
    frame = QFrame(); frame.setObjectName("Card")
    lay = QVBoxLayout(frame); lay.setContentsMargins(16, 14, 16, 16); lay.setSpacing(10)
    t = QLabel(title); t.setObjectName("SectionTitle"); lay.addWidget(t)
    if subtitle:
        s = QLabel(subtitle); s.setObjectName("Muted"); s.setWordWrap(True); lay.addWidget(s)
    return frame, lay


def wrap_scroll(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(widget)
    # 主页面内容很长，Qt 默认滚轮步长偏小。提高页面滚动步长，只影响真正的
    # QScrollArea 滚动，不会重新让输入框/下拉框响应滚轮修改值。
    scroll.verticalScrollBar().setSingleStep(64)
    scroll.horizontalScrollBar().setSingleStep(48)
    return scroll


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = AppSettings.load()
        self.discovery_thread = None
        self.hostname_thread = None
        self.hostname_verify_context = ""
        self.distribution_thread = None
        self.winrm_test_thread = None
        self.host_status_thread = None
        self.host_status_context = ""
        self._nav_buttons = []
        self._page_title = None
        self._page_subtitle = None
        self._status_badge = None
        # 自定义主机密码只保存在当前进程内；若用户选择“安全记住”，同时写入 Windows 凭据管理器。
        self._session_host_credentials: dict[str, tuple[str, str]] = {}
        self._suppress_target_selection_persist = False

        self.setWindowTitle(f"{APP_NAME}  ·  v{APP_VERSION}")
        self.resize(1420, 900)
        self.setMinimumSize(1120, 720)
        self._build_shell()
        self.refresh_hosts(); self.refresh_networks(); self.refresh_history(); self.refresh_audit()
        audit.operation(self.settings.audit_path, "APP", "START", "SUCCESS", "文件分发工作台已启动。",
                        details={"version": APP_VERSION})
        self.set_page(0)

    def _build_shell(self):
        root = QWidget(); self.setCentralWidget(root)
        shell = QHBoxLayout(root); shell.setContentsMargins(0,0,0,0); shell.setSpacing(0)

        side = QFrame(); side.setObjectName("Sidebar"); side.setFixedWidth(238)
        sl = QVBoxLayout(side); sl.setContentsMargins(14,18,14,16); sl.setSpacing(8)
        brand = QHBoxLayout(); brand.setSpacing(10)
        logo = QLabel(); logo.setFixedSize(46,46)
        p = asset_path("logo.png")
        if p.exists(): logo.setPixmap(QIcon(str(p)).pixmap(QSize(44,44)))
        bt = QVBoxLayout(); bt.setSpacing(0)
        a = QLabel("文件分发工作台"); a.setObjectName("BrandTitle")
        b = QLabel("Windows 多主机分发与部署"); b.setObjectName("BrandAccent")
        c = QLabel("桌面版  ·  v" + APP_VERSION); c.setObjectName("BrandSub")
        bt.addWidget(a); bt.addWidget(b); bt.addWidget(c)
        brand.addWidget(logo); brand.addLayout(bt); sl.addLayout(brand)

        corp = QFrame(); corp.setObjectName("CorpBrand")
        corp_l = QHBoxLayout(corp); corp_l.setContentsMargins(10,7,10,7); corp_l.setSpacing(9)
        corp_mark = QLabel("NARI"); corp_mark.setObjectName("CorpMark")
        corp_text = QVBoxLayout(); corp_text.setSpacing(0)
        dept = QLabel("国际业务部"); dept.setObjectName("CorpDept")
        dept_en = QLabel("International Business Division"); dept_en.setObjectName("CorpDeptEn")
        corp_text.addWidget(dept); corp_text.addWidget(dept_en)
        corp_l.addWidget(corp_mark); corp_l.addLayout(corp_text,1)
        sl.addWidget(corp); sl.addSpacing(12)

        for i, (title, _, icon_name) in enumerate(PAGE_META):
            btn = QPushButton(title); btn.setObjectName("NavButton"); btn.setCheckable(True)
            btn.setIcon(app_icon(icon_name)); btn.setIconSize(QSize(18,18)); btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, index=i: self.set_page(index))
            sl.addWidget(btn); self._nav_buttons.append(btn)
        sl.addStretch(1)
        cap = QLabel("本地应用数据"); cap.setStyleSheet("color:#7895AE;font-size:8pt;font-weight:700;")
        data = QLabel("%LOCALAPPDATA%\\FileDistributionStudio"); data.setWordWrap(True)
        data.setStyleSheet("color:#A9BECE;font-size:8pt;")
        sl.addWidget(cap); sl.addWidget(data)

        content = QWidget(); cl = QVBoxLayout(content); cl.setContentsMargins(0,0,0,0); cl.setSpacing(0)
        top = QFrame(); top.setObjectName("Topbar"); top.setFixedHeight(82)
        tl = QHBoxLayout(top); tl.setContentsMargins(24,10,24,10)
        left = QVBoxLayout(); left.setSpacing(1)
        self._page_title = QLabel(); self._page_title.setObjectName("PageTitle")
        self._page_subtitle = QLabel(); self._page_subtitle.setObjectName("PageSub")
        left.addWidget(self._page_title); left.addWidget(self._page_subtitle)
        self._status_badge = QLabel("就绪"); self._status_badge.setObjectName("StatusReady")
        top_brand = QFrame(); top_brand.setObjectName("TopCorpBrand")
        top_brand_l = QHBoxLayout(top_brand); top_brand_l.setContentsMargins(10,4,10,4); top_brand_l.setSpacing(8)
        top_nari = QLabel("NARI"); top_nari.setObjectName("TopCorpMark")
        top_dept = QLabel("国际业务部"); top_dept.setObjectName("TopCorpDept")
        top_brand_l.addWidget(top_nari); top_brand_l.addWidget(top_dept)
        tl.addLayout(left); tl.addStretch(1); tl.addWidget(top_brand, alignment=Qt.AlignVCenter); tl.addSpacing(10); tl.addWidget(self._status_badge, alignment=Qt.AlignVCenter)

        self.stack = QStackedWidget()
        self.pages = [
            self._build_distribution_page(), self._build_inventory_page(), self._build_discovery_page(),
            self._build_history_page(), self._build_audit_page(), self._build_help_page(), self._build_settings_page()
        ]
        for p in self.pages: self.stack.addWidget(p)
        cl.addWidget(top); cl.addWidget(self.stack,1)
        shell.addWidget(side); shell.addWidget(content,1)
        self.statusBar().showMessage("就绪")

    def set_page(self, index: int):
        self.stack.setCurrentIndex(index)
        for i,b in enumerate(self._nav_buttons): b.setChecked(i == index)
        title, sub, _ = PAGE_META[index]
        self._page_title.setText(title); self._page_subtitle.setText(sub)

    def _page_canvas(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(24,22,24,22); l.setSpacing(14)
        return w,l

    # ---------- Distribution ----------
    def _build_distribution_page(self):
        canvas, root = self._page_canvas()

        src, src_l = card(
            "分发映射",
            "一次任务可以配置多条“源 → 目标目录”映射。每条映射的目标目录都可以不同；所有勾选主机会执行完全相同的映射计划。",
        )
        self.mapping_scope_label = QLabel("当前分发范围：尚未选择目标主机。")
        self.mapping_scope_label.setWordWrap(True)
        self.mapping_scope_label.setStyleSheet("color:#476574; font-weight:600;")
        src_l.addWidget(self.mapping_scope_label)
        self.mapping_table = QTableWidget(0, 8)
        self.mapping_table.setHorizontalHeaderLabels([
            "启用", "来源", "类型", "源文件 / 目录", "目标目录", "当前目标主机", "目录方式", "状态"
        ])
        self.mapping_table.setAlternatingRowColors(True)
        self.mapping_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mapping_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.mapping_table.verticalHeader().setVisible(False)
        mh = self.mapping_table.horizontalHeader()
        mh.setSectionResizeMode(QHeaderView.ResizeToContents)
        mh.setSectionResizeMode(3, QHeaderView.Stretch)
        mh.setSectionResizeMode(4, QHeaderView.Stretch)
        mh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        src_l.addWidget(self.mapping_table)

        mr = QHBoxLayout()
        for text, fn, icon in [
            ("添加文件", self._add_mapping_files, "file"),
            ("添加目录", self._add_mapping_folder, "folder"),
            ("添加 SFTP", self._add_mapping_sftp, "server"),
            ("编辑映射", self._edit_mapping, "edit"),
            ("删除", self._delete_mapping, "delete"),
            ("清空", self._clear_mappings, "clear"),
        ]:
            b = QPushButton(text); b.setIcon(app_icon(icon)); b.clicked.connect(fn); mr.addWidget(b)
        mr.addStretch(1)
        src_l.addLayout(mr)
        map_hint = QLabel(
            "示例：core.dll → D:\\ADMS\\dll；config.xml → D:\\ADMS\\conf；整个 translations 目录 → E:\\ADMS\\translations。"
            "目录可选择“仅复制目录内容”或“复制目录本身”。每条映射都会应用到当前全部已勾选目标主机；开始分发前会逐台预检查。"
        )
        map_hint.setObjectName("Muted"); map_hint.setWordWrap(True); src_l.addWidget(map_hint)

        target, target_l = card(
            "Windows 目标主机",
            "选择本次要分发的 Windows 主机，并设置默认账号密码。",
        )
        cred = QHBoxLayout()
        self.win_user = QLineEdit(self.settings.winrm_default_username); self.win_password = PasswordLineEdit()
        self.win_user.setPlaceholderText(r"本地账号直接填 ADMS；域账号填 DOMAIN\user")
        self.remember_default_cred = QCheckBox("记住默认凭据")
        self.remember_default_cred.setChecked(bool(self.settings.remember_winrm_default_credential))
        self.remember_default_cred.setToolTip("密码仅保存到当前 Windows 用户的 Windows 凭据管理器，不写入配置、SQLite 或审计日志。")
        cred.addWidget(QLabel("默认 Windows 用户")); cred.addWidget(self.win_user, 1)
        cred.addWidget(QLabel("密码")); cred.addWidget(self.win_password, 1)
        cred.addWidget(self.remember_default_cred)
        target_l.addLayout(cred)
        self._load_default_winrm_credential()
        self.win_user.editingFinished.connect(lambda: self._save_default_winrm_credential(show_error=False))
        self.win_password.editingFinished.connect(lambda: self._save_default_winrm_credential(show_error=False))
        self.remember_default_cred.toggled.connect(lambda _checked: self._save_default_winrm_credential(show_error=False))
        cred_hint = QLabel(
            "默认使用 WinRM HTTP 5985。首次使用目标机可下载 WinRM 设置脚本；脚本不会修改任何 Windows 账号、用户组或 RDP 权限。"
            "大多数主机直接使用默认凭据，只有账号或密码不同的主机才需要单独设置。"
        )
        cred_hint.setObjectName("Muted"); cred_hint.setWordWrap(True); target_l.addWidget(cred_hint)
        self.target_table = QTableWidget(0, 8)
        self.target_table.setHorizontalHeaderLabels(["选择", "名称", "主机 / IP", "分组", "凭据", "在线状态", "WinRM 状态", "任务状态"])
        self.target_table.setAlternatingRowColors(True)
        self.target_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.target_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.target_table.verticalHeader().setVisible(False)
        hdr = self.target_table.horizontalHeader(); hdr.setSectionResizeMode(QHeaderView.Stretch); hdr.setSectionResizeMode(0,QHeaderView.ResizeToContents)
        target_l.addWidget(self.target_table)
        tr = QHBoxLayout()
        for text, fn, icon in [
            ("全选", lambda:self._set_all_targets(True), "check"),
            ("取消全选", lambda:self._set_all_targets(False), "clear"),
            ("刷新主机", self.refresh_hosts, "refresh"),
            ("测试在线状态", self._test_distribution_hosts_online, "radar"),
        ]:
            b = QPushButton(text); b.setIcon(app_icon(icon)); b.clicked.connect(fn); tr.addWidget(b)
        self.btn_winrm_test = QPushButton("测试 WinRM")
        self.btn_winrm_test.setIcon(app_icon("terminal"))
        self.btn_winrm_test.setToolTip("对所有已勾选主机执行各自主机凭据的 WinRM 身份验证，并逐一测试当前目标目录的创建、写入、读取和删除。")
        self.btn_winrm_test.clicked.connect(self._test_winrm_targets)
        tr.addWidget(self.btn_winrm_test)
        self.btn_download_adms_setup = QPushButton("下载 ADMS WinRM 设置脚本")
        self.btn_download_adms_setup.setIcon(app_icon("file"))
        self.btn_download_adms_setup.setToolTip("保存目标机首次使用的简化 WinRM 设置脚本；脚本不修改任何账号、用户组或 RDP 权限。")
        self.btn_download_adms_setup.clicked.connect(lambda: self._save_bundled_cmd("TARGET_PREP_ADMS_WINRM.cmd", "保存 ADMS WinRM 设置脚本"))
        tr.addWidget(self.btn_download_adms_setup)
        self.btn_download_adms_restore = QPushButton("下载 ADMS 还原脚本")
        self.btn_download_adms_restore.setIcon(app_icon("refresh"))
        self.btn_download_adms_restore.setToolTip("保存 WinRM 还原脚本；仅删除 LocalAccountTokenFilterPolicy 并停止 WinRM，不修改任何账号、用户组或 RDP 权限。")
        self.btn_download_adms_restore.clicked.connect(lambda: self._save_bundled_cmd("TARGET_RESTORE_ADMS_WINRM.cmd", "保存 ADMS 还原脚本"))
        tr.addWidget(self.btn_download_adms_restore)
        self.btn_winrm_setup = QPushButton("WinRM 配置向导")
        self.btn_winrm_setup.setIcon(app_icon("settings"))
        self.btn_winrm_setup.setToolTip("查看其他 WinRM 准备方式和脚本内容。")
        self.btn_winrm_setup.clicked.connect(self._show_winrm_setup_guide)
        tr.addWidget(self.btn_winrm_setup); tr.addStretch(1); target_l.addLayout(tr)

        cred_actions = QHBoxLayout()
        cred_actions.addWidget(QLabel("凭据管理"))
        self.btn_set_host_cred = QPushButton("设置选中凭据")
        self.btn_set_host_cred.setIcon(app_icon("edit"))
        self.btn_set_host_cred.setToolTip("为表格中高亮选择的一台或多台主机设置自定义 WinRM 凭据。按 Ctrl / Shift 可选择多行。")
        self.btn_set_host_cred.clicked.connect(self._set_selected_host_credentials)
        cred_actions.addWidget(self.btn_set_host_cred)
        self.btn_batch_host_cred = QPushButton("批量设置凭据")
        self.btn_batch_host_cred.setIcon(app_icon("hosts"))
        self.btn_batch_host_cred.setToolTip("为当前勾选的全部分发目标主机设置同一套自定义 WinRM 凭据。")
        self.btn_batch_host_cred.clicked.connect(self._set_checked_host_credentials)
        cred_actions.addWidget(self.btn_batch_host_cred)
        self.btn_clear_host_cred = QPushButton("恢复默认凭据")
        self.btn_clear_host_cred.setIcon(app_icon("clear"))
        self.btn_clear_host_cred.setToolTip("清除表格中高亮选择主机的自定义凭据，恢复使用顶部默认凭据。")
        self.btn_clear_host_cred.clicked.connect(self._restore_selected_default_credentials)
        cred_actions.addWidget(self.btn_clear_host_cred)
        cred_note = QLabel("大多数主机使用默认凭据；只有账号/密码不同的主机才需要自定义。")
        cred_note.setObjectName("Muted")
        cred_actions.addWidget(cred_note); cred_actions.addStretch(1)
        target_l.addLayout(cred_actions)


        opts, opts_l = card("分发策略", "默认执行文件大小校验；启用 SHA256 后会对源文件、临时文件、备份文件和正式文件进行多阶段强校验。")
        ol=QHBoxLayout(); self.chk_verify=QCheckBox("SHA256 强校验"); self.chk_verify.setChecked(self.settings.verify_sha256); self.chk_backup=QCheckBox("覆盖前备份"); self.chk_backup.setChecked(self.settings.backup_existing); self.chk_preflight=QCheckBox("分发前预检查"); self.chk_preflight.setChecked(self.settings.preflight_check)
        self.retry_spin=QSpinBox(); self.retry_spin.setRange(0,10); self.retry_spin.setValue(self.settings.retry_count); self.concurrent_spin=QSpinBox(); self.concurrent_spin.setRange(1,32); self.concurrent_spin.setValue(self.settings.max_concurrency)
        ol.addWidget(self.chk_verify); ol.addWidget(self.chk_backup); ol.addWidget(self.chk_preflight); ol.addWidget(QLabel("失败重试")); ol.addWidget(self.retry_spin); ol.addWidget(QLabel("主机并发数")); ol.addWidget(self.concurrent_spin); ol.addStretch(1); opts_l.addLayout(ol)
        bf=QFormLayout(); self.backup_root=QLineEdit(self.settings.default_backup_root); self.backup_root.setPlaceholderText(r"留空：各目标目录\.fds_backup；可填目标机本地路径，例如 E:\FDS_Backup")
        bf.addRow("备份根目录", self.backup_root); opts_l.addLayout(bf)
        hint=QLabel(r"自定义备份根目录时，会按任务 ID、原盘符和原目录结构保存，例如 D:\ADMS\dll\a.dll 会备份到 <Backup>\<Task>\D\ADMS\dll\a.dll，避免多目标目录同名文件冲突。")
        hint.setObjectName("Muted"); hint.setWordWrap(True); opts_l.addWidget(hint)

        remote, remote_l = card(
            "WinRM 连接与远程操作",
            "执行顺序：结束目标进程 → 分发前命令 → 文件分发 → 分发后命令。",
        )
        rt=QHBoxLayout(); self.remote_enabled=QCheckBox("启用分发前 / 后操作"); self.remote_enabled.setChecked(bool(self.settings.winrm_remote_actions_enabled)); self.remote_https=QCheckBox("HTTPS"); self.remote_port=QSpinBox(); self.remote_port.setRange(1,65535); self.remote_port.setValue(int(self.settings.winrm_port or 5985)); self.remote_https.toggled.connect(lambda c:self.remote_port.setValue(5986 if c else 5985)); self.remote_https.setChecked(bool(self.settings.winrm_use_https)); self.remote_port.setValue(int(self.settings.winrm_port or (5986 if self.remote_https.isChecked() else 5985)))
        self.remote_https.setVisible(False); self.remote_port.setVisible(False)
        self.winrm_conn_summary = QLabel(); self.winrm_conn_summary.setObjectName("Muted")
        self._refresh_winrm_connection_summary()
        advanced_btn = QPushButton("高级连接…"); advanced_btn.clicked.connect(self._show_winrm_advanced_connection)
        rt.addWidget(self.remote_enabled); rt.addWidget(self.winrm_conn_summary); rt.addWidget(advanced_btn); rt.addStretch(1); remote_l.addLayout(rt)

        pipeline = QFrame(); pipeline.setObjectName("SoftCard")
        pl = QVBoxLayout(pipeline); pl.setContentsMargins(14,12,14,12); pl.setSpacing(9)
        order = QLabel("0  WinRM 认证与目标目录预检查（自动，任何停服/杀进程之前完成）")
        order.setObjectName("PipelineAuto"); pl.addWidget(order)

        cwd_row=QHBoxLayout(); cwd_label=QLabel("命令工作目录"); cwd_label.setObjectName("PipelineLabel")
        self.command_workdir=QLineEdit(self.settings.winrm_command_workdir or ""); self.command_workdir.setPlaceholderText(r"例如：D:\ADMS\bin；用于 sys_ctl 等依赖当前目录的命令")
        cwd_row.addWidget(cwd_label); cwd_row.addWidget(self.command_workdir,1); pl.addLayout(cwd_row)

        exec_row=QHBoxLayout(); exec_label=QLabel("命令执行方式"); exec_label.setObjectName("PipelineLabel")
        self.command_execution_mode=QComboBox()
        self.command_execution_mode.addItem("登录桌面（推荐，最接近本机执行）", "INTERACTIVE")
        self.command_execution_mode.addItem("WinRM 后台（无界面）", "WINRM_BACKGROUND")
        idx=self.command_execution_mode.findData(self.settings.winrm_command_execution_mode or "INTERACTIVE")
        self.command_execution_mode.setCurrentIndex(max(0,idx))
        self.command_execution_mode.setToolTip("登录桌面模式仍由 WinRM 控制，但使用 Windows 任务计划程序在目标机当前已登录用户的交互桌面会话中运行命令；适合 sys_ctl 和会启动 GUI 的程序。")
        exec_row.addWidget(exec_label); exec_row.addWidget(self.command_execution_mode,1); pl.addLayout(exec_row)
        exec_hint=QLabel("推荐“登录桌面”：WinRM 本身是非交互式会话，GUI/厂商启动器即使工作目录正确也可能与本机执行不同。登录桌面模式无需额外 Agent，但目标机必须已有用户登录。")
        exec_hint.setObjectName("Muted"); exec_hint.setWordWrap(True); pl.addWidget(exec_hint)

        sf=QFormLayout()
        self.kill_processes=QLineEdit("; ".join(self.settings.winrm_kill_processes or [])); self.kill_processes.setPlaceholderText("例如：adms.exe; helper.exe")
        self.btn_select_remote_processes=QPushButton("选择远程进程")
        self.btn_select_remote_processes.setIcon(app_icon("terminal"))
        self.btn_select_remote_processes.setToolTip("通过 WinRM 只读读取参考目标主机当前进程，按实际 exe 镜像名勾选要结束的进程。")
        self.btn_select_remote_processes.clicked.connect(self._select_remote_processes)
        kill_row=QHBoxLayout(); kill_row.setContentsMargins(0,0,0,0); kill_row.addWidget(self.kill_processes,1); kill_row.addWidget(self.btn_select_remote_processes)
        sf.addRow("① 结束目标进程", kill_row); pl.addLayout(sf)

        kill_hint=QLabel("先结束这里选择/填写的 EXE（例如 GUI/客户端），释放可能占用的程序文件；随后再执行下面的停止/准备命令。")
        kill_hint.setObjectName("Muted"); kill_hint.setWordWrap(True); pl.addWidget(kill_hint)

        pre_label=QLabel("② 分发前 CMD（停止/准备）"); pre_label.setObjectName("PipelineStep")
        self.pre_commands=QPlainTextEdit(); self.pre_commands.setMaximumHeight(82); self.pre_commands.setPlaceholderText("每行一条命令，例如：\nsys_ctl stop\necho 开始分发")
        self.pre_commands.setPlainText(self.settings.winrm_pre_commands_text or "")
        pl.addWidget(pre_label); pl.addWidget(self.pre_commands)

        transfer = QLabel("③ 文件分发（自动：备份旧文件 → WinRM 流式上传 → 临时文件校验 → 正式替换 → 最终校验）")
        transfer.setObjectName("PipelineAuto"); transfer.setWordWrap(True); pl.addWidget(transfer)

        post_label=QLabel("④ 分发后 CMD"); post_label.setObjectName("PipelineStep")
        self.post_commands=QPlainTextEdit(); self.post_commands.setMaximumHeight(82); self.post_commands.setPlaceholderText("每行一条命令，例如：\nsys_ctl start fast\necho 分发完成")
        self.post_commands.setPlainText(self.settings.winrm_post_commands_text or "")
        pl.addWidget(post_label); pl.addWidget(self.post_commands)

        self.post_on_failure=QCheckBox("失败恢复：即使文件分发失败，也尝试执行分发后 CMD")
        self.post_on_failure.setChecked(bool(self.settings.winrm_post_on_failure)); pl.addWidget(self.post_on_failure)
        remote_l.addWidget(pipeline)

        cwd_hint=QLabel(r"提示：当前顺序为先结束选中的 GUI/客户端进程，再执行 sys_ctl stop 等停止/准备命令，完成文件分发后再执行 sys_ctl start fast 等启动/恢复命令。命令工作目录建议设置为实际 bin 目录。上次使用的命令、进程和执行方式会自动记住。")
        cwd_hint.setObjectName("Muted"); cwd_hint.setWordWrap(True); remote_l.addWidget(cwd_hint)

        run_card, run_l = card("执行与日志", "所有分发映射、WinRM 连接、真实目标路径、备份、校验、进程和命令操作都会进入本地审计链。")
        ar=QHBoxLayout(); self.btn_start=QPushButton("开始分发"); self.btn_start.setObjectName("Primary"); self.btn_start.setIcon(app_icon("play")); self.btn_cancel=QPushButton("取消"); self.btn_cancel.setObjectName("Danger"); self.btn_cancel.setEnabled(False); self.btn_start.clicked.connect(self._start_distribution); self.btn_cancel.clicked.connect(self._cancel_distribution); ar.addStretch(1); ar.addWidget(self.btn_start); ar.addWidget(self.btn_cancel); run_l.addLayout(ar)
        self.overall=QProgressBar(); self.overall.setValue(0); run_l.addWidget(self.overall); self.dist_log=QPlainTextEdit(); self.dist_log.setReadOnly(True); self.dist_log.setMaximumBlockCount(5000); self.dist_log.setMinimumHeight(150); run_l.addWidget(self.dist_log)

        root.addWidget(src); root.addWidget(target); root.addWidget(opts); root.addWidget(remote); root.addWidget(run_card); root.addStretch(1)
        return wrap_scroll(canvas)

    def _mapping_rows(self, enabled_only=True):
        out=[]
        for r in range(self.mapping_table.rowCount()):
            chk=self.mapping_table.cellWidget(r,0)
            if enabled_only and chk and not chk.isChecked():
                continue
            item=self.mapping_table.item(r,3)
            if item:
                obj=item.data(ROLE_HOST_OBJECT)
                if obj: out.append(obj)
        return out

    def _target_scope_display(self, hosts=None):
        hosts = list(hosts if hosts is not None else self._selected_hosts())
        if not hosts:
            return "0 台", "当前没有勾选目标主机。"
        labels=[]
        details=[]
        for h in hosts:
            name=(h.name or h.host or "").strip()
            shown=name if name and name != h.host else h.host
            labels.append(shown)
            details.append(f"{shown} ({h.host})" if shown != h.host else h.host)
        preview="、".join(labels[:3])
        if len(labels)>3:
            preview += f" 等 {len(labels)} 台"
        else:
            preview = f"{len(labels)} 台：{preview}"
        tooltip="当前勾选目标主机：\n" + "\n".join(f"• {x}" for x in details) + "\n\n同一条分发映射会应用到以上全部主机。"
        return preview, tooltip

    def _refresh_mapping_scope(self):
        if not hasattr(self, "mapping_table"):
            return
        hosts = self._selected_hosts() if hasattr(self, "target_table") else []
        enabled_mappings = self._mapping_rows(True)
        host_count=len(hosts)
        mapping_count=len(enabled_mappings)
        execution_count=host_count * mapping_count
        host_text, host_tip = self._target_scope_display(hosts)
        if host_count:
            summary=f"当前分发范围：{host_count} 台目标主机 × {mapping_count} 条启用映射 = {execution_count} 个主机映射执行项；{host_text}。"
        else:
            summary=f"当前分发范围：0 台目标主机；当前有 {mapping_count} 条启用映射。请先在下方勾选目标主机。"
        if hasattr(self, "mapping_scope_label"):
            self.mapping_scope_label.setText(summary)
            self.mapping_scope_label.setToolTip(host_tip)
        for r in range(self.mapping_table.rowCount()):
            cell=QTableWidgetItem(host_text if host_count else "0 台")
            cell.setToolTip(host_tip)
            self.mapping_table.setItem(r,5,cell)

    def _on_mapping_enabled_changed(self, *_):
        self._refresh_mapping_scope()

    def _append_mapping(self, mapping: DistributionMapping):
        r=self.mapping_table.rowCount(); self.mapping_table.insertRow(r)
        chk=QCheckBox(); chk.setChecked(True); chk.stateChanged.connect(self._on_mapping_enabled_changed); self.mapping_table.setCellWidget(r,0,chk)
        scope_text, scope_tip = self._target_scope_display()
        vals=["SFTP" if mapping.source_type=="SFTP" else "本地", "目录" if mapping.source_kind=="DIR" else "文件", mapping.display_source(), mapping.target_path,
              scope_text, ("复制目录本身" if mapping.folder_mode=="SELF" else "复制目录内容") if mapping.source_kind=="DIR" else "文件", "待分发"]
        for c,v in enumerate(vals,1): self.mapping_table.setItem(r,c,QTableWidgetItem(v))
        self.mapping_table.item(r,3).setData(ROLE_HOST_OBJECT,mapping)
        self.mapping_table.item(r,5).setToolTip(scope_tip)
        self._refresh_mapping_scope()
        audit.operation(self.settings.audit_path,"MAPPING","ADD","SUCCESS","已添加分发映射。",subject=mapping.mapping_id,details=mapping.safe_dict())

    def _remote_drive_context_for_mapping(self):
        """为分发映射对话框准备按主机解析后的 WinRM 凭据。

        只用于读取远程盘符，不记录密码。没有勾选主机或任一主机缺少凭据时，
        映射仍可手工输入目标路径，但自动盘符读取会禁用。
        """
        hosts = self._selected_hosts()
        if not hosts:
            return [], "请先在下方勾选至少一台目标主机，即可通过 WinRM 自动读取远程盘符；也可以继续手动输入。"
        try:
            credentials = self._resolve_credentials(hosts)
        except Exception as exc:
            return [], f"当前勾选主机的 WinRM 凭据不完整，暂时不能自动读取盘符：{exc}"
        contexts = []
        for h in hosts:
            username, password, source = credentials[h.host]
            contexts.append({
                "host": h.host,
                "name": h.name,
                "username": username,
                "password": password,
                "source": source,
            })
        return contexts, ""

    def _mapping_dialog_kwargs(self):
        contexts, hint = self._remote_drive_context_for_mapping()
        return {
            "remote_drive_contexts": contexts,
            "remote_drive_hint": hint,
            "use_https": self.remote_https.isChecked(),
            "winrm_port": self.remote_port.value(),
        }

    def _add_mapping_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"选择一个或多个分发文件")
        if not paths:return
        d=MappingTargetDialog(self,title=f"配置 {len(paths)} 个文件的目标目录",target_path="",is_directory=False, **self._mapping_dialog_kwargs())
        if not d.exec():return
        target=d.value()["target_path"]
        for path in paths:self._append_mapping(DistributionMapping("LOCAL",path,target,"FILE","CONTENTS"))
        self.refresh_audit()

    def _add_mapping_folder(self):
        path=QFileDialog.getExistingDirectory(self,"选择分发目录")
        if not path:return
        d=MappingTargetDialog(self,title="配置目录分发映射",is_directory=True, **self._mapping_dialog_kwargs())
        if not d.exec():return
        v=d.value(); self._append_mapping(DistributionMapping("LOCAL",path,v["target_path"],"DIR",v["folder_mode"])); self.refresh_audit()

    def _add_mapping_sftp(self):
        d=SftpMappingDialog(self, **self._mapping_dialog_kwargs())
        if not d.exec():return
        v=d.value(); m=DistributionMapping("SFTP",v["source_path"],v["target_path"],v["source_kind"],v["folder_mode"],sftp_host=v["host"],sftp_port=v["port"],sftp_username=v["username"],sftp_password=v["password"]); self._append_mapping(m); self.refresh_audit()

    def _selected_mapping_row(self):
        rows=self.mapping_table.selectionModel().selectedRows(); return rows[0].row() if rows else -1

    def _edit_mapping(self):
        r=self._selected_mapping_row()
        if r<0:return
        m=self.mapping_table.item(r,3).data(ROLE_HOST_OBJECT)
        if not m:return
        if m.source_type=="SFTP":
            d=SftpMappingDialog(self,initial={"host":m.sftp_host,"port":m.sftp_port,"username":m.sftp_username,"password":m.sftp_password,"source_path":m.source_path,"source_kind":m.source_kind,"target_path":m.target_path,"folder_mode":m.folder_mode}, **self._mapping_dialog_kwargs())
            if not d.exec():return
            v=d.value(); m.sftp_host=v["host"];m.sftp_port=v["port"];m.sftp_username=v["username"];m.sftp_password=v["password"];m.source_path=v["source_path"];m.source_kind=v["source_kind"];m.target_path=v["target_path"];m.folder_mode=v["folder_mode"]
        else:
            d=MappingTargetDialog(self,target_path=m.target_path,is_directory=m.source_kind=="DIR",folder_mode=m.folder_mode, **self._mapping_dialog_kwargs())
            if not d.exec():return
            v=d.value();m.target_path=v["target_path"];m.folder_mode=v["folder_mode"]
        scope_text,scope_tip=self._target_scope_display(); self.mapping_table.setItem(r,1,QTableWidgetItem("SFTP" if m.source_type=="SFTP" else "本地"));self.mapping_table.setItem(r,2,QTableWidgetItem("目录" if m.source_kind=="DIR" else "文件"));src=QTableWidgetItem(m.display_source());src.setData(ROLE_HOST_OBJECT,m);self.mapping_table.setItem(r,3,src);self.mapping_table.setItem(r,4,QTableWidgetItem(m.target_path));scope_item=QTableWidgetItem(scope_text);scope_item.setToolTip(scope_tip);self.mapping_table.setItem(r,5,scope_item);self.mapping_table.setItem(r,6,QTableWidgetItem(("复制目录本身" if m.folder_mode=="SELF" else "复制目录内容") if m.source_kind=="DIR" else "文件"));self.mapping_table.setItem(r,7,QTableWidgetItem("待分发"))
        self._refresh_mapping_scope(); audit.operation(self.settings.audit_path,"MAPPING","EDIT","SUCCESS","已修改分发映射。",subject=m.mapping_id,details=m.safe_dict());self.refresh_audit()

    def _delete_mapping(self):
        r=self._selected_mapping_row()
        if r>=0:
            m=self.mapping_table.item(r,3).data(ROLE_HOST_OBJECT); self.mapping_table.removeRow(r); self._refresh_mapping_scope()
            if m:audit.operation(self.settings.audit_path,"MAPPING","DELETE","SUCCESS","已删除分发映射。",subject=m.mapping_id,details=m.safe_dict());self.refresh_audit()

    def _clear_mappings(self):
        if self.mapping_table.rowCount() and QMessageBox.question(self,"清空映射","确定清空当前所有分发映射吗？")!=QMessageBox.Yes:return
        self.mapping_table.setRowCount(0); self._refresh_mapping_scope(); audit.operation(self.settings.audit_path,"MAPPING","CLEAR","SUCCESS","已清空当前分发映射。");self.refresh_audit()

    def _selected_hosts(self):
        out=[]
        for r in range(self.target_table.rowCount()):
            chk=self.target_table.cellWidget(r,0)
            if chk and chk.isChecked():
                obj=self.target_table.item(r,1).data(ROLE_HOST_OBJECT)
                if obj:out.append(obj)
        return out

    def _set_all_targets(self,checked):
        # 批量切换时只落盘一次，避免每个复选框都重复写 settings.json。
        self._suppress_target_selection_persist = True
        try:
            for r in range(self.target_table.rowCount()):
                w=self.target_table.cellWidget(r,0)
                if w:w.setChecked(checked)
        finally:
            self._suppress_target_selection_persist = False
        self._save_all_target_selection_preferences()
        self._refresh_mapping_scope()

    def _on_target_check_changed(self, host: str, checked: bool):
        """Persist one target-host checkbox without ever storing credentials/secrets."""
        if self._suppress_target_selection_persist:
            return
        checks = dict(getattr(self.settings, "distribution_target_checks", {}) or {})
        checks[str(host).strip()] = bool(checked)
        self.settings.distribution_target_checks = checks
        # A real user selection means the one-time first-launch default has been consumed.
        self.settings.distribution_target_selection_initialized = True
        try:
            self.settings.save()
        except Exception as exc:
            logging.getLogger("fds.settings").warning("保存目标主机选择状态失败：%s", exc)
        self._refresh_mapping_scope()

    def _save_all_target_selection_preferences(self):
        """Persist the current distribution-target checkbox map.

        First-ever initialization is intentionally different: when no saved selection exists yet,
        every existing host is checked. From then on we restore exactly the user's last choices;
        newly discovered hosts default to unchecked until the user explicitly selects them.
        """
        if not hasattr(self, "target_table"):
            return
        current = dict(getattr(self.settings, "distribution_target_checks", {}) or {})
        rows = 0
        for r in range(self.target_table.rowCount()):
            host_item = self.target_table.item(r, 2)
            chk = self.target_table.cellWidget(r, 0)
            if host_item and chk:
                host = host_item.text().strip()
                if host:
                    current[host] = bool(chk.isChecked())
                    rows += 1
        self.settings.distribution_target_checks = current
        if rows:
            self.settings.distribution_target_selection_initialized = True
        try:
            self.settings.save()
        except Exception as exc:
            logging.getLogger("fds.settings").warning("保存目标主机选择状态失败：%s", exc)

    def _load_default_winrm_credential(self):
        """Load the remembered default credential from Windows Credential Manager."""
        if not getattr(self, "remember_default_cred", None) or not self.remember_default_cred.isChecked():
            return
        if not credential_store.is_available():
            self.remember_default_cred.setToolTip("当前系统不是 Windows；运行到 Windows 后将使用 Windows 凭据管理器。")
            return
        try:
            saved = credential_store.read(credential_store.DEFAULT_TARGET)
            if saved:
                if saved.username:
                    self.win_user.setText(saved.username)
                self.win_password.setText(saved.password)
        except Exception as exc:
            logging.getLogger("fds.credentials").warning("读取默认 WinRM 凭据失败：%s", exc)

    def _save_default_winrm_credential(self, *, show_error=False):
        """Persist only non-secret settings plus the password in Windows Credential Manager."""
        username = self.win_user.text().strip() if hasattr(self, "win_user") else ""
        password = self.win_password.text() if hasattr(self, "win_password") else ""
        remember = bool(self.remember_default_cred.isChecked()) if hasattr(self, "remember_default_cred") else False
        self.settings.winrm_default_username = username
        self.settings.remember_winrm_default_credential = remember
        self.settings.save()
        if not credential_store.is_available():
            return
        try:
            if remember and username and password:
                credential_store.write(credential_store.DEFAULT_TARGET, username, password)
            else:
                credential_store.delete(credential_store.DEFAULT_TARGET)
        except Exception as exc:
            logging.getLogger("fds.credentials").warning("保存默认 WinRM 凭据失败：%s", exc)
            if show_error:
                QMessageBox.warning(self, "WinRM 凭据", f"默认用户名已保存，但密码写入 Windows 凭据管理器失败：\n{exc}")

    def _load_host_credential(self, host: str):
        cached = self._session_host_credentials.get(host)
        if cached:
            return cached
        if not credential_store.is_available():
            return None
        try:
            saved = credential_store.read(credential_store.host_target(host))
            if saved:
                value = (saved.username, saved.password)
                self._session_host_credentials[host] = value
                return value
        except Exception as exc:
            logging.getLogger("fds.credentials").warning("读取主机 %s 的 WinRM 凭据失败：%s", host, exc)
        return None

    def _credential_status_for_host(self, host: str) -> tuple[str, str]:
        custom_user = (self.settings.winrm_host_usernames or {}).get(host, "").strip()
        if custom_user:
            saved = self._load_host_credential(host)
            if saved and saved[1]:
                return "自定义凭据", f"此主机使用自定义 WinRM 用户：{custom_user}。密码受 Windows 凭据管理器/当前会话保护，不在表格中显示。"
            return "自定义（缺少密码）", f"此主机已指定自定义用户：{custom_user}，但当前未找到可用密码。请重新设置凭据。"
        default_user = self.win_user.text().strip() if hasattr(self, "win_user") else self.settings.winrm_default_username
        default_password = self.win_password.text() if hasattr(self, "win_password") else ""
        if default_user and default_password:
            return "默认凭据", f"使用顶部默认 WinRM 用户：{default_user}。"
        if default_user:
            return "默认（缺少密码）", f"使用默认用户：{default_user}，但密码尚未设置。"
        return "未设置", "尚未配置默认或自定义 WinRM 凭据。"

    def _resolve_credentials(self, hosts: list[HostRecord]) -> dict[str, tuple[str, str, str]]:
        """Resolve the effective credential for every host without logging secrets."""
        default_user = self.win_user.text().strip()
        default_password = self.win_password.text()
        result: dict[str, tuple[str, str, str]] = {}
        missing = []
        overrides = self.settings.winrm_host_usernames or {}
        for h in hosts:
            custom_user = (overrides.get(h.host) or "").strip()
            if custom_user:
                saved = self._load_host_credential(h.host)
                username = saved[0].strip() if saved and saved[0].strip() else custom_user
                password = saved[1] if saved else ""
                source = "自定义凭据"
            else:
                username = default_user
                password = default_password
                source = "默认凭据"
            if not username or not password:
                missing.append(f"{h.host}（{source}）")
            result[h.host] = (username, password, source)
        if missing:
            preview = "\n".join(missing[:8])
            if len(missing) > 8:
                preview += f"\n……另有 {len(missing)-8} 台"
            raise ValueError("以下目标主机缺少可用的 WinRM 用户名或密码：\n" + preview)
        return result

    def _highlighted_target_hosts(self) -> list[HostRecord]:
        rows = self.target_table.selectionModel().selectedRows()
        out = []
        seen = set()
        for idx in rows:
            item = self.target_table.item(idx.row(), 1)
            obj = item.data(ROLE_HOST_OBJECT) if item else None
            if obj and obj.host not in seen:
                seen.add(obj.host); out.append(obj)
        return out

    def _configure_host_credentials(self, hosts: list[HostRecord]):
        if not hosts:
            QMessageBox.warning(self, "WinRM 凭据", "请先选择目标主机。")
            return
        initial_user = self.win_user.text().strip()
        initial_password = self.win_password.text()
        if len(hosts) == 1:
            custom_user = (self.settings.winrm_host_usernames or {}).get(hosts[0].host, "").strip()
            if custom_user:
                initial_user = custom_user
                saved = self._load_host_credential(hosts[0].host)
                initial_password = saved[1] if saved else ""
        d = WinRMHostCredentialDialog(
            self, host_count=len(hosts), username=initial_user,
            password=initial_password, remember=True,
        )
        if not d.exec():
            return
        value = d.value()
        username, password, remember = value["username"], value["password"], value["remember"]
        self.settings.winrm_host_usernames = dict(self.settings.winrm_host_usernames or {})
        failures = []
        for h in hosts:
            self.settings.winrm_host_usernames[h.host] = username
            self._session_host_credentials[h.host] = (username, password)
            if credential_store.is_available():
                try:
                    if remember:
                        credential_store.write(credential_store.host_target(h.host), username, password)
                    else:
                        credential_store.delete(credential_store.host_target(h.host))
                except Exception as exc:
                    failures.append(f"{h.host}: {exc}")
        self.settings.save()
        audit.operation(
            self.settings.audit_path, "WINRM", "CREDENTIAL_OVERRIDE", "SUCCESS" if not failures else "PARTIAL_FAILED",
            f"已为 {len(hosts)} 台主机设置自定义 WinRM 凭据。", details={
                "hosts": [h.host for h in hosts], "username": username,
                "remembered_in_windows_credential_manager": bool(remember and credential_store.is_available()),
            },
        )
        self.refresh_hosts(); self.refresh_audit()
        if failures:
            QMessageBox.warning(self, "WinRM 凭据", "自定义用户名已设置，但部分密码未能写入 Windows 凭据管理器：\n" + "\n".join(failures[:5]))

    def _set_selected_host_credentials(self):
        hosts = self._highlighted_target_hosts()
        if not hosts:
            QMessageBox.information(self, "WinRM 凭据", "请先在主机表中高亮选择一台或多台主机。\n可按 Ctrl / Shift 选择多行。")
            return
        self._configure_host_credentials(hosts)

    def _set_checked_host_credentials(self):
        hosts = self._selected_hosts()
        if not hosts:
            QMessageBox.warning(self, "WinRM 凭据", "请先勾选至少一台目标主机。")
            return
        if len(hosts) > 1 and QMessageBox.question(
            self, "批量设置凭据", f"将为当前勾选的 {len(hosts)} 台主机设置同一套自定义 WinRM 凭据。\n确定继续吗？"
        ) != QMessageBox.Yes:
            return
        self._configure_host_credentials(hosts)

    def _restore_selected_default_credentials(self):
        hosts = self._highlighted_target_hosts()
        if not hosts:
            QMessageBox.information(self, "WinRM 凭据", "请先在主机表中高亮选择需要恢复默认凭据的主机。")
            return
        if QMessageBox.question(self, "恢复默认凭据", f"清除选中 {len(hosts)} 台主机的自定义凭据并恢复使用顶部默认凭据？") != QMessageBox.Yes:
            return
        self.settings.winrm_host_usernames = dict(self.settings.winrm_host_usernames or {})
        for h in hosts:
            self.settings.winrm_host_usernames.pop(h.host, None)
            self._session_host_credentials.pop(h.host, None)
            if credential_store.is_available():
                try:
                    credential_store.delete(credential_store.host_target(h.host))
                except Exception as exc:
                    logging.getLogger("fds.credentials").warning("删除主机 %s 的保存凭据失败：%s", h.host, exc)
        self.settings.save()
        audit.operation(self.settings.audit_path, "WINRM", "CREDENTIAL_RESTORE_DEFAULT", "SUCCESS",
                        f"已将 {len(hosts)} 台主机恢复为默认 WinRM 凭据。", details={"hosts":[h.host for h in hosts]})
        self.refresh_hosts(); self.refresh_audit()

    def _test_distribution_hosts_online(self):
        hosts=self._selected_hosts()
        if not hosts:QMessageBox.warning(self,"在线测试","请先至少勾选一台目标主机。");return
        self._start_host_status_test([h.host for h in hosts],"distribution")

    def _save_bundled_cmd(self, name: str, title: str):
        source = script_path(name)
        if not source.exists():
            QMessageBox.critical(self, title, f"脚本资源缺失：\n{name}")
            return
        path, _ = QFileDialog.getSaveFileName(self, title, name, "Windows CMD (*.cmd);;所有文件 (*)")
        if not path:
            return
        if not path.lower().endswith(".cmd"):
            path += ".cmd"
        try:
            Path(path).write_bytes(source.read_bytes())
            QMessageBox.information(self, title, f"脚本已保存：\n{path}\n\n请复制到目标 Windows，并以管理员身份运行。")
        except Exception as e:
            QMessageBox.critical(self, title, f"保存脚本失败：\n{e}")

    def _refresh_winrm_connection_summary(self):
        if not hasattr(self, "winrm_conn_summary"):
            return
        scheme = "HTTPS" if self.remote_https.isChecked() else "HTTP"
        self.winrm_conn_summary.setText(f"WinRM：{scheme} {self.remote_port.value()}")

    def _show_winrm_advanced_connection(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("WinRM 高级连接")
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        note = QLabel("一般情况下保持 HTTP 5985 即可。只有现场已经配置 WinRM HTTPS Listener 时才需要修改。")
        note.setWordWrap(True); note.setObjectName("Muted"); lay.addWidget(note)
        form = QFormLayout()
        https = QCheckBox("使用 HTTPS")
        https.setChecked(self.remote_https.isChecked())
        port = QSpinBox(); port.setRange(1,65535); port.setValue(self.remote_port.value())
        https.toggled.connect(lambda checked: port.setValue(5986 if checked else 5985))
        form.addRow("连接方式", https); form.addRow("端口", port); lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定"); buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); lay.addWidget(buttons)
        if dlg.exec() == QDialog.Accepted:
            self.remote_https.setChecked(https.isChecked())
            self.remote_port.setValue(port.value())
            self._refresh_winrm_connection_summary()

    def _show_winrm_setup_guide(self):
        WinRMSetupDialog(self).exec()

    def _test_winrm_targets(self):
        hosts=self._selected_hosts(); mappings=self._mapping_rows(True)
        if not hosts:
            QMessageBox.warning(self,"WinRM 测试","请先至少勾选一台目标主机。")
            return
        self._save_default_winrm_credential(show_error=True)
        try:
            credentials=self._resolve_credentials(hosts)
        except Exception as e:
            QMessageBox.warning(self,"WinRM 测试",str(e))
            return
        targets=list(dict.fromkeys(m.target_path for m in mappings))
        for target in targets:
            try:validate_windows_target_path(target, allow_unc=False)
            except Exception as e:QMessageBox.warning(self,"WinRM 测试",f"目标目录格式不正确：\n{target}\n\n{e}");return
        self.btn_winrm_test.setEnabled(False);self.btn_winrm_test.setText("正在测试 WinRM…")
        host_set={h.host for h in hosts}
        for r in range(self.target_table.rowCount()):
            if self.target_table.item(r,2).text() in host_set:self.target_table.setItem(r,6,QTableWidgetItem("正在测试…"))
        self.winrm_test_thread=WinRMTargetTestThread(
            [h.host for h in hosts], targets, credentials,
            use_https=self.remote_https.isChecked(), port=self.remote_port.value(),
            max_workers=min(self.concurrent_spin.value(),8),
        )
        self.winrm_test_thread.result.connect(self._winrm_target_test_result)
        self.winrm_test_thread.completed.connect(self._winrm_target_test_completed)
        self.winrm_test_thread.start()

    def _winrm_target_test_result(self,host,success,message):
        has_targets = bool(self._mapping_rows(True))
        short=("可分发" if has_targets else "WinRM 可用") if success else "测试失败"
        for r in range(self.target_table.rowCount()):
            if self.target_table.item(r,2).text()==host:
                cell=QTableWidgetItem(short);cell.setToolTip(message);self.target_table.setItem(r,6,cell);break
        db.update_host_winrm_status(host,("WRITABLE" if has_targets else "AUTH_OK") if success else "FAILED",datetime.now().isoformat(timespec="seconds"))
        self._append_log(f"[{host}] {'WinRM 测试通过' if success else 'WinRM 测试失败'}：{message}")
        audit.operation(self.settings.audit_path,"WINRM","UI_TEST","SUCCESS" if success else "FAILED",message,host=host,details={"targets":[m.target_path for m in self._mapping_rows(True)],"port":self.remote_port.value(),"https":self.remote_https.isChecked()})

    def _winrm_target_test_completed(self,success,failed):
        self.btn_winrm_test.setEnabled(True);self.btn_winrm_test.setText("测试 WinRM");self.refresh_audit();self.refresh_hosts()
        message=f"WinRM 测试完成。\n通过：{success} 台\n失败：{failed} 台"
        if failed==0:
            QMessageBox.information(self,"WinRM 测试",message)
            return
        message += "\n\n如果 5985 已开放但 ADMS 等本地管理员仍被拒绝，可以打开 WinRM 配置向导，导出目标机准备脚本。"
        box=QMessageBox(self); box.setIcon(QMessageBox.Warning); box.setWindowTitle("WinRM 测试"); box.setText(message)
        guide_btn=box.addButton("打开 WinRM 配置向导", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is guide_btn:
            self._show_winrm_setup_guide()

    def _test_winrm(self):
        hosts=self._selected_hosts()
        if not hosts:
            QMessageBox.warning(self,"WinRM","请先至少选择一台目标主机。")
            return
        self._save_default_winrm_credential(show_error=True)
        try:
            credentials=self._resolve_credentials(hosts[:1])
            username,password,source=credentials[hosts[0].host]
            plan=RemoteActionPlan(enabled=True,use_https=self.remote_https.isChecked(),port=self.remote_port.value(),username=username,password=password)
            result=WinRMExecutor(hosts[0].host,plan).test()
            detail=result["stdout"] or result["stderr"] or f"退出码={result['exit_code']}"
            text=f"{hosts[0].host}\n凭据来源：{source}\n用户：{username}\n\n{detail}"
            (QMessageBox.information if result["exit_code"]==0 else QMessageBox.critical)(self,"WinRM",text)
        except Exception as e:
            QMessageBox.critical(self,"WinRM",str(e))

    def _select_remote_processes(self):
        """通过 WinRM 读取参考主机进程，让用户选择正式 taskkill 使用的镜像名。"""
        hosts=self._selected_hosts()
        if not hosts:
            QMessageBox.warning(self,"远程进程","请先在“Windows 目标主机”中勾选至少一台目标主机。")
            return
        self._save_default_winrm_credential(show_error=True)
        contexts,hint=self._remote_drive_context_for_mapping()
        if not contexts:
            QMessageBox.warning(self,"远程进程",hint or "当前目标主机没有可用 WinRM 凭据。")
            return
        d=RemoteProcessBrowserDialog(
            self, contexts=contexts, preselected=split_items(self.kill_processes.text()),
            use_https=self.remote_https.isChecked(), port=self.remote_port.value(),
        )
        if d.exec():
            names=d.selected_image_names()
            self.kill_processes.setText("; ".join(names))
            self._save_remote_action_preferences()
            if names:
                self.statusBar().showMessage(f"已选择 {len(names)} 个远程进程镜像名。",3000)
            else:
                self.statusBar().showMessage("已清空结束进程列表。",3000)

    def _validate_mappings(self,mappings,host_for_path):
        if not mappings:raise ValueError("请至少添加一条分发映射。")
        for m in mappings:
            validate_windows_target_path(m.target_path, allow_unc=False)
            if m.source_type=="LOCAL" and not Path(m.source_path).exists():raise FileNotFoundError(f"本地分发源不存在：{m.source_path}")
            if m.source_type=="SFTP" and not all([m.sftp_host,m.sftp_username,m.source_path]):raise ValueError(f"SFTP 映射配置不完整：{m.display_source()}")

    def _start_distribution(self):
        hosts=self._selected_hosts();mappings=self._mapping_rows(True)
        if not hosts:
            QMessageBox.warning(self,"文件分发","请至少选择一台目标主机。")
            return
        try:
            self._validate_mappings(mappings,hosts[0].host)
            if self.chk_backup.isChecked() and self.backup_root.text().strip():
                validate_windows_target_path(self.backup_root.text().strip(), allow_unc=False)
        except Exception as e:
            QMessageBox.warning(self,"分发计划检查",str(e));return

        self.settings.verify_sha256=self.chk_verify.isChecked();self.settings.backup_existing=self.chk_backup.isChecked();self.settings.preflight_check=self.chk_preflight.isChecked();self.settings.retry_count=self.retry_spin.value();self.settings.max_concurrency=self.concurrent_spin.value();self.settings.default_backup_root=self.backup_root.text().strip();self.settings.winrm_use_https=self.remote_https.isChecked();self.settings.winrm_port=self.remote_port.value();self.settings.winrm_command_workdir=self.command_workdir.text().strip()
        self._save_remote_action_preferences()
        self._save_default_winrm_credential(show_error=True)
        try:
            credentials=self._resolve_credentials(hosts)
        except Exception as e:
            QMessageBox.warning(self,"WinRM 凭据",str(e));return

        # 远程动作模板不携带统一账号密码；真正执行时每台主机注入自己的有效凭据。
        remote_plan=RemoteActionPlan(
            enabled=self.remote_enabled.isChecked(),use_https=self.remote_https.isChecked(),port=self.remote_port.value(),
            username="",password="",pre_commands=split_commands(self.pre_commands.toPlainText()),
            kill_processes=split_items(self.kill_processes.text()),
            post_commands=split_commands(self.post_commands.toPlainText()),
            post_on_failure=self.post_on_failure.isChecked(),command_workdir=self.command_workdir.text().strip(),
            command_execution_mode=self.command_execution_mode.currentData() or "INTERACTIVE",
        )
        targets=len(set(m.target_path for m in mappings))
        default_count=sum(1 for _,_,src in credentials.values() if src=="默认凭据")
        custom_count=len(credentials)-default_count
        host_lines="\n".join(f"  • {(h.name or h.host)} ({h.host})" if (h.name or h.host) != h.host else f"  • {h.host}" for h in hosts)
        msg=(f"分发映射：{len(mappings)} 条\n目标目录：{targets} 个\n目标主机：{len(hosts)} 台\n{host_lines}\n"
             f"凭据：默认 {default_count} 台 / 自定义 {custom_count} 台\n"
             f"备份目录：{self.backup_root.text().strip() or '各目标目录\\.fds_backup'}\n"
             f"校验策略：{'文件大小 + SHA256' if self.chk_verify.isChecked() else '文件大小'}\n"
             f"分发前预检查：{'启用' if self.chk_preflight.isChecked() else '关闭'}\n传输方式：WinRM\n"
             f"附加远程操作：{'已启用' if remote_plan.enabled else '未启用'}\n"
             f"CMD 工作目录：{remote_plan.command_workdir or 'WinRM 默认目录'}\n"
             f"CMD 执行方式：{'目标机登录桌面（交互式）' if remote_plan.command_execution_mode == 'INTERACTIVE' else 'WinRM 后台'}\n"
             "执行顺序：预检查 → 结束目标进程 → 分发前 CMD → 文件分发 → 分发后 CMD\n\n"
             "所有主机会执行同一套映射，但每台主机会使用自己的有效 WinRM 凭据。"
             "本操作可能结束远程进程并覆盖目标文件。\n确定继续吗？")
        if QMessageBox.question(self,"确认分发",msg)!=QMessageBox.Yes:return
        self._append_log("开始执行多源多目标分发任务……")
        self.btn_start.setEnabled(False); self.btn_cancel.setEnabled(True)
        self.overall.setFormat("%p%")
        self.overall.setValue(0)
        # 进度条表示完整主机工作流，而不是仅表示文件数量。文件全部上传完成时最多到 90%，
        # 每台主机的分发后 CMD / 收尾完成后逐步到 99%，只有 completed 信号到达才显示 100%。
        self._dist_progress_hosts = {h.host: 0.0 for h in hosts}
        self._dist_progress_finished = set()
        self._dist_progress_host_count = max(1, len(hosts))
        self._set_busy(True,"正在分发")
        audit.operation(
            self.settings.audit_path,"TASK","USER_START","SUCCESS","用户确认开始多源多目标分发。",
            details={"mappings":[m.safe_dict() for m in mappings],"hosts":[h.host for h in hosts],
                     "auth_sources":{h.host:credentials[h.host][2] for h in hosts},
                     "backup_root":self.backup_root.text().strip(),"verify_sha256":self.chk_verify.isChecked(),
                     "preflight":self.chk_preflight.isChecked(),"remote_actions":remote_plan.enabled,
                     "transport":"WINRM","winrm_port":remote_plan.port,"winrm_https":remote_plan.use_https,
                     "command_execution_mode":remote_plan.command_execution_mode},
        )
        for r in range(self.target_table.rowCount()):self.target_table.setItem(r,7,QTableWidgetItem(""))
        self.distribution_thread=DistributionThread(
            mappings,hosts,credentials,self.settings,remote_plan,self.backup_root.text().strip()
        )
        self.distribution_thread.log.connect(self._append_log);self.distribution_thread.prepared.connect(self._dist_prepared);self.distribution_thread.host_status.connect(self._host_status);self.distribution_thread.mapping_status.connect(self._mapping_status);self.distribution_thread.file_progress.connect(self._file_progress);self.distribution_thread.completed.connect(self._dist_completed);self.distribution_thread.start()

    def _cancel_distribution(self):
        if self.distribution_thread:self.distribution_thread.cancel();self._append_log("已请求取消当前任务……")
    def _append_log(self,text):self.dist_log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {text}");logging.getLogger("fds.ui").info(text)
    def _dist_prepared(self,task_id,files,total_bytes):
        self._append_log(f"{task_id}：{files} 个文件，共 {human_bytes(total_bytes)}")
        if self.overall.value() < 5:
            self.overall.setValue(5)

    def _refresh_distribution_progress(self):
        hosts = getattr(self, "_dist_progress_hosts", {})
        count = max(1, int(getattr(self, "_dist_progress_host_count", len(hosts) or 1)))
        file_ratio = sum(max(0.0, min(1.0, float(v))) for v in hosts.values()) / count
        finished_ratio = len(getattr(self, "_dist_progress_finished", set())) / count
        # 文件处理占 5~90%，完整主机收尾（包含分发后 CMD）占 90~99%。
        value = int(5 + file_ratio * 85 + finished_ratio * 9)
        self.overall.setValue(max(self.overall.value(), min(99, value)))

    def _host_status(self,host,status,detail):
        for r in range(self.target_table.rowCount()):
            if self.target_table.item(r,2).text()==host:
                self.target_table.setItem(r,7,QTableWidgetItem(f"{status_text(status)} {detail}".strip()));break
        if status in {"SUCCESS", "FAILED", "CANCELLED"}:
            if hasattr(self, "_dist_progress_finished"):
                self._dist_progress_finished.add(host)
                self._refresh_distribution_progress()
    def _mapping_status(self,mapping_id,status,detail):
        for r in range(self.mapping_table.rowCount()):
            item=self.mapping_table.item(r,3)
            m=item.data(ROLE_HOST_OBJECT) if item else None
            if m and m.mapping_id==mapping_id:
                label={"PREPARING":"准备中","READY":"已准备","SUCCESS":"成功","FAILED":"失败","PARTIAL_FAILED":"部分失败","CANCELLED":"已取消"}.get(status,status)
                cell=QTableWidgetItem(f"{label} {detail}".strip());self.mapping_table.setItem(r,7,cell);break
    def _file_progress(self,host,i,total,rel,status):
        if total and hasattr(self, "_dist_progress_hosts"):
            self._dist_progress_hosts[host] = max(0.0, min(1.0, float(i) / float(total)))
            self._refresh_distribution_progress()
        if status=="FAILED":
            self._append_log(f"[{host}] 分发失败：{rel}")

    def _dist_completed(self,status,success,failed):
        self.btn_start.setEnabled(True); self.btn_cancel.setEnabled(False); self._set_busy(False)
        # 100% 只在整个任务真正结束时出现：此时所有主机的文件、分发后 CMD 和审计收尾均已完成。
        self.overall.setValue(100)
        if status == "SUCCESS":
            self.overall.setFormat("100% · 已完成")
        elif status == "CANCELLED":
            self.overall.setFormat("100% · 已结束（已取消）")
        else:
            self.overall.setFormat("100% · 已结束（有失败）")
        self._append_log(f"任务完成：{status_text(status)}，成功主机={success}，失败主机={failed}")
        self.refresh_history(); self.refresh_audit()
        QApplication.processEvents()  # 先让用户真正看到 100%，再弹出最终结果。
        QMessageBox.information(self,"文件分发",f"状态：{status_text(status)}\n成功主机：{success}\n失败主机：{failed}\n\n详细记录请查看“分发历史”和“审计日志”。")

    # ---------- Inventory ----------
    def _build_inventory_page(self):
        canvas,root=self._page_canvas(); c,l=card(
            "已保存主机",
            "主机管理只维护机器身份、分组和连接能力；“WinRM 凭据”在此处仅显示状态，不直接编辑，实际凭据统一在“文件分发 → Windows 目标主机”管理。所有实际分发和远程操作只使用 WinRM；Ping、SMB 445、RDP 3389、DNS/NetBIOS/SMB 身份仅用于发现与辅助诊断。",
        )
        self.host_table=QTableWidget(0,18)
        self.host_table.setHorizontalHeaderLabels([
            "选择","ID","名称","主机 / IP","名称来源","名称状态","分组","WinRM 凭据",
            "在线状态","Ping","445 SMB（识别）","3389 RDP（识别）","WinRM 端口","WinRM 状态","SMB 辅助状态","最后测试","最后发现","备注"
        ])
        self.host_table.setAlternatingRowColors(True); self.host_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.host_table.setSelectionMode(QAbstractItemView.ExtendedSelection); self.host_table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.host_table.verticalHeader().setVisible(False)
        self.host_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents); self.host_table.horizontalHeader().setStretchLastSection(True)
        l.addWidget(self.host_table)
        row=QHBoxLayout()
        for text,fn,icon in [
            ("添加",self._add_host,"add"),("编辑",self._edit_host,"edit"),("删除",self._delete_host,"delete"),
            ("全选",lambda:self._set_inventory_checks(True),"check"),("取消全选",lambda:self._set_inventory_checks(False),"clear"),
            ("测试在线状态",self._test_inventory_online,"radar"),
            ("验证主机名",self._verify_inventory_names,"terminal"),("刷新",self.refresh_hosts,"refresh")
        ]:
            b=QPushButton(text); b.setIcon(app_icon(icon)); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(1); l.addLayout(row); root.addWidget(c); return canvas

    def refresh_hosts(self):
        hosts=db.list_hosts()

        # 刷新主机状态时绝不能改变用户已经勾选的分发目标。
        # v0.5.0 的问题就在这里：每次 refresh_hosts() 都重新创建 QCheckBox 并 setChecked(True)，
        # 因此任何主机状态测试/验证结束后的刷新都不能把全部主机重新勾选。
        target_check_state = {}
        target_had_rows = False
        target_scroll = 0
        persisted_target_checks = dict(getattr(self.settings, "distribution_target_checks", {}) or {})
        target_selection_initialized = bool(getattr(self.settings, "distribution_target_selection_initialized", False))
        if hasattr(self,"target_table"):
            target_had_rows = self.target_table.rowCount() > 0
            target_scroll = self.target_table.verticalScrollBar().value()
            for r in range(self.target_table.rowCount()):
                host_item = self.target_table.item(r,2)
                chk = self.target_table.cellWidget(r,0)
                if host_item and chk:
                    target_check_state[host_item.text().strip()] = bool(chk.isChecked())

        inventory_scroll = 0
        inventory_check_state = {}
        if hasattr(self,"host_table"):
            inventory_scroll = self.host_table.verticalScrollBar().value()
            # 主机管理中的复选框用于批量操作；刷新状态时保留用户已勾选的主机。
            for r in range(self.host_table.rowCount()):
                host_item = self.host_table.item(r,3)
                chk = self.host_table.cellWidget(r,0)
                if host_item and chk:
                    inventory_check_state[host_item.text().strip()] = bool(chk.isChecked())
            self.host_table.setRowCount(len(hosts))
            for r,h in enumerate(hosts):
                chk=QCheckBox(); chk.setChecked(inventory_check_state.get(h.host, False)); self.host_table.setCellWidget(r,0,chk)
                verify_text="已验证" if h.hostname_verified else ("未验证" if h.name and h.name != h.host else "未识别")
                cred_status,_cred_tip=self._credential_status_for_host(h.host)
                vals=[h.id,h.name,h.host,hostname_source_text(h.hostname_source),verify_text,group_text(h.group_name),cred_status,
                      online_status_text(h.online_status),"是" if h.ping_ok else "","是" if h.smb_port_ok else "","是" if h.rdp_port_ok else "","是" if h.winrm_port_ok else "",winrm_status_label(h.winrm_status),smb_status_label(h.smb_status),h.last_test_at,h.last_seen,h.notes]
                for c,v in enumerate(vals, start=1):self.host_table.setItem(r,c,QTableWidgetItem(str(v if v is not None else "")))
            self.host_table.verticalScrollBar().setValue(inventory_scroll)

        if hasattr(self,"target_table"):
            self.target_table.setRowCount(len(hosts))
            for r,h in enumerate(hosts):
                chk=QCheckBox()
                # v0.6.20：目标主机选择跨启动持久化。
                # 1) 当前会话已有表格时，刷新只恢复当前会话状态；
                # 2) 新启动且已有历史记录时，恢复上次每台主机的勾选状态；
                # 3) 真正第一次初始化时，所有已有目标主机默认全部勾选；
                # 4) 初始化之后新发现的主机默认不勾选，防止意外加入正式分发。
                if h.host in target_check_state:
                    checked = target_check_state[h.host]
                elif target_selection_initialized:
                    checked = bool(persisted_target_checks.get(h.host, False))
                else:
                    checked = True
                chk.setChecked(checked)
                chk.stateChanged.connect(lambda state, host=h.host: self._on_target_check_changed(host, state == Qt.Checked.value))
                self.target_table.setCellWidget(r,0,chk)
                item=QTableWidgetItem(h.name); item.setData(ROLE_HOST_OBJECT,h); self.target_table.setItem(r,1,item)
                self.target_table.setItem(r,2,QTableWidgetItem(h.host)); self.target_table.setItem(r,3,QTableWidgetItem(group_text(h.group_name)))
                cred_text,cred_tip=self._credential_status_for_host(h.host); cred_item=QTableWidgetItem(cred_text); cred_item.setToolTip(cred_tip); self.target_table.setItem(r,4,cred_item)
                online=QTableWidgetItem(online_status_text(h.online_status)); online.setToolTip(f"Ping={'是' if h.ping_ok else '否'}，445={'是' if h.smb_port_ok else '否'}，3389={'是' if h.rdp_port_ok else '否'}，WinRM={'是' if h.winrm_port_ok else '否'}\n最后测试：{h.last_test_at or '未测试'}"); self.target_table.setItem(r,5,online)
                self.target_table.setItem(r,6,QTableWidgetItem(winrm_status_label(h.winrm_status))); self.target_table.setItem(r,7,QTableWidgetItem(""))
            self.target_table.verticalScrollBar().setValue(target_scroll)
            # 首次初始化：只有实际存在主机时才消费“一次性默认全选”规则。
            # 这样全新安装若尚未发现任何主机，首次发现主机后仍会默认全部勾选。
            if hosts and not target_selection_initialized:
                self._save_all_target_selection_preferences()
            self._refresh_mapping_scope()

    def _add_host(self):
        d=HostEditDialog(self)
        if d.exec():
            h=d.value()
            if h.host:
                db.upsert_host(h); self.refresh_hosts();audit.operation(self.settings.audit_path,"HOST","ADD","SUCCESS","已添加或更新主机。",host=h.host,details={"name":h.name,"group":h.group_name,"hostname_source":h.hostname_source,"hostname_verified":bool(h.hostname_verified)});self.refresh_audit()

    def _set_inventory_checks(self, checked: bool):
        for r in range(self.host_table.rowCount()):
            chk=self.host_table.cellWidget(r,0)
            if chk: chk.setChecked(checked)

    def _checked_inventory_hosts(self):
        ids=set()
        for r in range(self.host_table.rowCount()):
            chk=self.host_table.cellWidget(r,0)
            item=self.host_table.item(r,1)
            if chk and chk.isChecked() and item:
                ids.add(int(item.text()))
        return [h for h in db.list_hosts() if h.id in ids]

    def _highlighted_inventory_hosts(self):
        rows=self.host_table.selectionModel().selectedRows()
        if not rows:return []
        ids={int(self.host_table.item(x.row(),1).text()) for x in rows if self.host_table.item(x.row(),1)}
        return [h for h in db.list_hosts() if h.id in ids]

    def _selected_inventory_hosts(self):
        # 批量按钮优先使用复选框；若用户仍按旧习惯高亮行，也继续兼容。
        checked=self._checked_inventory_hosts()
        return checked if checked else self._highlighted_inventory_hosts()

    def _selected_inventory_host(self):
        # “编辑”优先编辑当前高亮行，避免旁边存在批量勾选时误编辑其他主机。
        highlighted=self._highlighted_inventory_hosts()
        if highlighted:return highlighted[0]
        checked=self._checked_inventory_hosts()
        return checked[0] if checked else None
    def _edit_host(self):
        h=self._selected_inventory_host()
        if not h:return
        d=HostEditDialog(self,h)
        if d.exec():
            new_h=d.value();db.upsert_host(new_h);self.refresh_hosts();audit.operation(self.settings.audit_path,"HOST","EDIT","SUCCESS","已修改主机配置。",host=new_h.host,details={"name":new_h.name,"group":new_h.group_name,"hostname_source":new_h.hostname_source,"hostname_verified":bool(new_h.hostname_verified)});self.refresh_audit()
    def _delete_host(self):
        hosts=self._selected_inventory_hosts()
        if not hosts:return
        if QMessageBox.question(self,"删除主机",f"确定删除选中的 {len(hosts)} 台主机吗？")==QMessageBox.Yes:
            self.settings.winrm_host_usernames = dict(self.settings.winrm_host_usernames or {})
            for h in hosts:
                db.delete_host(h.id)
                self.settings.winrm_host_usernames.pop(h.host, None)
                self._session_host_credentials.pop(h.host, None)
                if credential_store.is_available():
                    try: credential_store.delete(credential_store.host_target(h.host))
                    except Exception: pass
                audit.operation(self.settings.audit_path,"HOST","DELETE","SUCCESS","已删除主机。",host=h.host,details={"name":h.name,"group":h.group_name})
            self.settings.save(); self.refresh_hosts();self.refresh_audit()

    def _test_inventory_online(self):
        hosts=self._selected_inventory_hosts()
        if not hosts:QMessageBox.warning(self,"在线测试","请先选择一台或多台主机。");return
        self._start_host_status_test([h.host for h in hosts],"inventory")

    def _start_host_status_test(self,hosts,context):
        if self.host_status_thread and self.host_status_thread.isRunning():QMessageBox.information(self,"在线测试","已有在线测试正在执行，请稍候。");return
        self.host_status_context=context;self._set_busy(True,"正在测试主机")
        audit.operation(self.settings.audit_path,"HOST","CONNECTIVITY_TEST_START","SUCCESS","开始批量主机在线状态测试。",details={"hosts":hosts,"context":context})
        self.host_status_thread=HostStatusTestThread(hosts,self.settings.socket_timeout,max_workers=min(max(8,self.settings.discovery_workers),32));self.host_status_thread.result.connect(self._host_status_test_result);self.host_status_thread.completed.connect(self._host_status_test_completed);self.host_status_thread.start()

    def _host_status_test_result(self,host,result):
        now=datetime.now().isoformat(timespec="seconds")
        db.update_host_connectivity(host,online_status=result.online_status,ping_ok=result.ping_ok,smb_port_ok=result.smb_port_ok,rdp_port_ok=result.rdp_port_ok,winrm_port_ok=result.winrm_port_ok,last_test_at=now)
        audit.operation(self.settings.audit_path,"HOST","CONNECTIVITY_TEST","SUCCESS" if result.online_status!="OFFLINE" else "FAILED",result.message,host=host,details={"ping":result.ping_ok,"smb_445":result.smb_port_ok,"rdp_3389":result.rdp_port_ok,"winrm_5985":result.winrm_5985_ok,"winrm_5986":result.winrm_5986_ok,"online_status":result.online_status})
        if hasattr(self,"dist_log"):self._append_log(f"[{host}] 在线测试：{result.message.replace(chr(10),'；')}")

    def _host_status_test_completed(self,online,offline):
        self._set_busy(False);self.refresh_hosts();self.refresh_audit();QMessageBox.information(self,"在线测试",f"测试完成。\n在线 / 可达：{online} 台\n离线 / 不可达：{offline} 台\n\n注意：Ping/445/3389 仅用于发现和识别；正式分发要求 WinRM 5985/5986 可达并且账号认证通过。")

    def _verify_inventory_names(self):
        hosts=self._selected_inventory_hosts()
        if not hosts:QMessageBox.warning(self,"主机名验证","请先在主机管理中选择一台或多台主机。");return
        self._request_hostname_verification([h.host for h in hosts],context="inventory")

    # ---------- Discovery ----------
    def _build_discovery_page(self):
        canvas,root=self._page_canvas(); c,l=card("扫描范围","不限制为当前网卡所在网段。可以一次添加多个可路由的 IPv4 CIDR，每行一个网段并统一扫描。主机发现允许使用 Ping、445、3389、5985/5986、DNS、NetBIOS 和 SMB 身份等多种只读信号；这些方式只负责识别主机，不参与文件分发或远程修改。")
        ad=QHBoxLayout(); self.network_combo=QComboBox(); self.btn_refresh_networks=QPushButton("刷新网卡"); self.btn_add_adapter_range=QPushButton("添加网卡网段"); self.btn_refresh_networks.setIcon(app_icon("refresh")); self.btn_add_adapter_range.setIcon(app_icon("add")); self.btn_refresh_networks.clicked.connect(self.refresh_networks); self.btn_add_adapter_range.clicked.connect(self._add_adapter_cidr); ad.addWidget(QLabel("本机网卡")); ad.addWidget(self.network_combo,1); ad.addWidget(self.btn_refresh_networks); ad.addWidget(self.btn_add_adapter_range); l.addLayout(ad)
        ranges=QHBoxLayout(); self.cidr_edit=QPlainTextEdit(); self.cidr_edit.setMaximumHeight(90); self.cidr_edit.setPlaceholderText("172.16.22.0/24\n172.16.21.0/24\n10.20.30.0/24")
        quick=QVBoxLayout(); self.quick_cidr=QLineEdit(); self.quick_cidr.setPlaceholderText("添加网段，例如 172.16.21.0/24"); qb=QPushButton("添加网段"); cb=QPushButton("清空网段"); qb.clicked.connect(self._add_quick_cidr); cb.clicked.connect(lambda:self.cidr_edit.clear()); quick.addWidget(self.quick_cidr); quick.addWidget(qb); quick.addWidget(cb); quick.addStretch(1); ranges.addWidget(self.cidr_edit,2); ranges.addLayout(quick,1); l.addLayout(ranges)
        action=QHBoxLayout(); self.btn_scan=QPushButton("扫描全部网段"); self.btn_scan.setObjectName("Primary"); self.btn_scan.setIcon(app_icon("radar")); self.btn_stop_scan=QPushButton("停止"); self.btn_stop_scan.setObjectName("Danger"); self.btn_stop_scan.setEnabled(False); self.btn_scan.clicked.connect(self._start_scan); self.btn_stop_scan.clicked.connect(self._stop_scan); self.discovery_progress=QProgressBar(); self.discovery_progress.setValue(0); action.addWidget(self.discovery_progress,1); action.addWidget(self.btn_scan); action.addWidget(self.btn_stop_scan); l.addLayout(action); root.addWidget(c)

        rcard,rl=card("发现的主机","445/3389/5985/5986 仅用于轻量存活与 Windows 服务特征探测；主机名可通过 Windows 工作站 API、SMB 身份、DNS PTR、NetBIOS 或 WinRM hostname 识别。正式文件上传、目录操作、备份、校验、服务和进程控制始终只走 WinRM。")
        self.discovery_table=QTableWidget(0,12)
        self.discovery_table.setHorizontalHeaderLabels(["添加","来源网段","IP","识别名称","名称来源","名称状态","445 SMB","3389 RDP","5985 WinRM","5986 WinRM","主机状态","名称说明"])
        self.discovery_table.setAlternatingRowColors(True); self.discovery_table.verticalHeader().setVisible(False)
        h=self.discovery_table.horizontalHeader(); h.setSectionResizeMode(QHeaderView.ResizeToContents); h.setStretchLastSection(True); h.setSectionResizeMode(0,QHeaderView.ResizeToContents)
        rl.addWidget(self.discovery_table)
        bottom=QHBoxLayout(); self.discovery_status=QLabel("就绪"); self.discovery_status.setObjectName("Muted")
        b_verify=QPushButton("深度验证主机名"); b_verify.setIcon(app_icon("terminal")); b_verify.clicked.connect(self._verify_discovered_names)
        b_add=QPushButton("添加选中主机"); b_add.setIcon(app_icon("hosts")); b_add.clicked.connect(self._add_discovered)
        bottom.addWidget(self.discovery_status); bottom.addStretch(1); bottom.addWidget(b_verify); bottom.addWidget(b_add); rl.addLayout(bottom); root.addWidget(rcard); return canvas

    def refresh_networks(self):
        if not hasattr(self,"network_combo"):return
        self.network_combo.clear(); nets=local_ipv4_networks()
        for ifname,ip,cidr in nets:self.network_combo.addItem(f"{ifname}  ·  {ip}  ·  {cidr}",cidr)
        if hasattr(self,"cidr_edit") and not self.cidr_edit.toPlainText().strip():
            initial=self.settings.discovery_ranges or ([nets[0][2]] if nets else [])
            self.cidr_edit.setPlainText("\n".join(initial))

    def _append_cidr(self,cidr):
        try: canonical=normalize_networks([cidr])[0]
        except Exception as e: QMessageBox.warning(self,"CIDR",str(e)); return
        current=normalize_networks(self.cidr_edit.toPlainText()) if self.cidr_edit.toPlainText().strip() else []
        if canonical not in current: current.append(canonical)
        self.cidr_edit.setPlainText("\n".join(current))

    def _add_adapter_cidr(self):
        cidr=self.network_combo.currentData()
        if cidr:self._append_cidr(cidr)

    def _add_quick_cidr(self):
        if self.quick_cidr.text().strip():self._append_cidr(self.quick_cidr.text().strip());self.quick_cidr.clear()

    def _start_scan(self):
        try: cidrs=normalize_networks(self.cidr_edit.toPlainText())
        except Exception as e: QMessageBox.warning(self,"主机发现",str(e)); return
        if not cidrs: QMessageBox.warning(self,"主机发现","请至少添加一个 CIDR 网段。"); return
        self.settings.discovery_ranges=cidrs; self.settings.save(); self.discovery_table.setRowCount(0); self.btn_scan.setEnabled(False); self.btn_stop_scan.setEnabled(True); self.discovery_progress.setValue(0); self.discovery_status.setText("正在扫描：" + ", ".join(cidrs)); self._set_busy(True,"正在扫描")
        audit.operation(self.settings.audit_path,"DISCOVERY","START","SUCCESS","开始扫描 Windows 主机。",details={"ranges":cidrs})
        self.discovery_thread=DiscoveryThread(cidrs,self.settings.socket_timeout,self.settings.discovery_workers); self.discovery_thread.result_found.connect(self._discovery_result); self.discovery_thread.log.connect(self.discovery_status.setText); self.discovery_thread.progress.connect(self._scan_progress); self.discovery_thread.completed.connect(self._scan_completed); self.discovery_thread.start()

    def _stop_scan(self):
        if self.discovery_thread:
            self.discovery_thread.cancel(); audit.operation(self.settings.audit_path,"DISCOVERY","CANCEL","CANCELLED","用户请求停止主机扫描。")

    def _scan_progress(self,done,total):
        self.discovery_progress.setValue(int(done*100/total) if total else 0)

    def _discovery_result(self,x):
        for r in range(self.discovery_table.rowCount()):
            if self.discovery_table.item(r,2).text()==x.host:return
        r=self.discovery_table.rowCount(); self.discovery_table.insertRow(r)
        chk=QCheckBox(); chk.setChecked(True); self.discovery_table.setCellWidget(r,0,chk)
        verify="已验证" if x.hostname_verified else ("未验证" if x.hostname else "未识别")
        vals=[x.network,x.host,x.hostname,hostname_source_text(x.hostname_source),verify,
              "是" if x.port_445 else "","是" if x.port_3389 else "","是" if x.port_5985 else "","是" if x.port_5986 else "",status_text(x.status),x.hostname_note]
        for c,v in enumerate(vals,1):self.discovery_table.setItem(r,c,QTableWidgetItem(v))
        # Store stable source/verification values separately from presentation text.
        self.discovery_table.item(r,3).setData(Qt.UserRole,x.hostname_source)
        self.discovery_table.item(r,5).setData(Qt.UserRole,bool(x.hostname_verified))
        self.discovery_table.item(r,5).setData(Qt.UserRole + 1, "")
        self._mark_duplicate_discovery_names()
        audit.operation(self.settings.audit_path,"DISCOVERY","HOST_FOUND","SUCCESS","扫描发现主机。",host=x.host,details={"network":x.network,"hostname":x.hostname,"hostname_source":x.hostname_source,"hostname_verified":x.hostname_verified,"hostname_note":x.hostname_note,"smb_445":x.port_445,"rdp_3389":x.port_3389,"winrm_5985":x.port_5985,"winrm_5986":x.port_5986,"status":x.status})

    def _mark_duplicate_discovery_names(self):
        names={}
        for r in range(self.discovery_table.rowCount()):
            item=self.discovery_table.item(r,3)
            name=(item.text().strip() if item else "")
            if name:
                names.setdefault(name.casefold(),[]).append(r)
        duplicate_rows={r for rows in names.values() if len(rows)>1 for r in rows}
        for r in range(self.discovery_table.rowCount()):
            name_item=self.discovery_table.item(r,3); state_item=self.discovery_table.item(r,5); note_item=self.discovery_table.item(r,11)
            verified=bool(state_item.data(Qt.UserRole)) if state_item else False
            verify_error=(state_item.data(Qt.UserRole + 1) if state_item else "") or ""
            name=name_item.text().strip() if name_item else ""
            if verified:
                state_item.setText("已验证")
            elif verify_error == "FAILED":
                state_item.setText("验证失败")
            elif r in duplicate_rows:
                state_item.setText("名称重复，建议验证")
                note=(note_item.text().strip() if note_item else "")
                if "重复" not in note:
                    note_item.setText((note+" ").strip()+"同一次扫描中有多个 IP 返回相同名称，建议执行 SMB/WKSSVC 或 WinRM 深度验证实际计算机名。")
            elif name:
                state_item.setText("未验证")
            else:
                state_item.setText("未识别")

    def _scan_completed(self,status):
        self.btn_scan.setEnabled(True); self.btn_stop_scan.setEnabled(False); self._set_busy(False)
        if status=="Completed":self.discovery_progress.setValue(100)
        self.discovery_status.setText(f"{status_text(status)}：发现 {self.discovery_table.rowCount()} 台主机")
        audit_status={"Completed":"SUCCESS","Cancelled":"CANCELLED","Failed":"FAILED"}.get(status,str(status).upper()); audit.operation(self.settings.audit_path,"DISCOVERY","FINISH",audit_status,"主机扫描结束。",details={"found":self.discovery_table.rowCount(),"ranges":self.settings.discovery_ranges})
        self.refresh_audit()

    def _verify_discovered_names(self):
        hosts=[]
        for r in range(self.discovery_table.rowCount()):
            chk=self.discovery_table.cellWidget(r,0)
            if chk and chk.isChecked():hosts.append(self.discovery_table.item(r,2).text())
        if not hosts:
            QMessageBox.warning(self,"主机名验证","请先勾选需要验证的已发现主机。")
            return
        self._request_hostname_verification(hosts,context="discovery")

    def _request_hostname_verification(self, hosts, context):
        if self.hostname_thread and self.hostname_thread.isRunning():
            QMessageBox.information(self,"主机名验证","已有主机名验证任务正在执行，请等待完成。")
            return
        d=HostnameCredentialDialog(self)
        if not d.exec():return
        cfg=d.value(); self.hostname_verify_context=context
        self.hostname_thread=HostnameVerificationThread(hosts,cfg["username"],cfg["password"],cfg["use_https"],cfg["port"],max_workers=min(8,self.settings.max_concurrency*2),method=cfg["method"])
        self.hostname_thread.result.connect(self._hostname_verify_result)
        self.hostname_thread.progress.connect(self._hostname_verify_progress)
        self.hostname_thread.log.connect(self._hostname_verify_log)
        self.hostname_thread.completed.connect(self._hostname_verify_completed)
        audit.operation(self.settings.audit_path,"DISCOVERY" if context=="discovery" else "HOST","VERIFY_HOSTNAME_START","SUCCESS","开始主机名深度验证。",details={"host_count":len(hosts),"username":cfg["username"],"method":cfg["method"],"use_https":cfg["use_https"],"port":cfg["port"]})
        self._set_busy(True,"验证名称"); self.hostname_thread.start()

    def _hostname_verify_log(self,text):
        if hasattr(self,"discovery_status") and self.hostname_verify_context=="discovery":self.discovery_status.setText(text)

    def _hostname_verify_progress(self,done,total):
        if self.hostname_verify_context=="discovery" and hasattr(self,"discovery_progress"):
            self.discovery_progress.setValue(int(done*100/total) if total else 0)

    def _hostname_verify_result(self,host,hostname,source,verified,message):
        audit.operation(self.settings.audit_path,"DISCOVERY" if self.hostname_verify_context=="discovery" else "HOST","VERIFY_HOSTNAME","SUCCESS" if verified else "FAILED",message,host=host,subject=hostname,details={"source":source,"verified":verified})
        if self.hostname_verify_context=="discovery":
            for r in range(self.discovery_table.rowCount()):
                if self.discovery_table.item(r,2).text()!=host:continue
                if verified and hostname:
                    self.discovery_table.item(r,3).setText(hostname)
                    self.discovery_table.item(r,3).setData(Qt.UserRole,source)
                    self.discovery_table.item(r,4).setText(hostname_source_text(source))
                    self.discovery_table.item(r,5).setText("已验证")
                    self.discovery_table.item(r,5).setData(Qt.UserRole,True)
                    self.discovery_table.item(r,5).setData(Qt.UserRole + 1, "")
                    self.discovery_table.item(r,11).setText(message)
                else:
                    self.discovery_table.item(r,5).setText("验证失败")
                    self.discovery_table.item(r,5).setData(Qt.UserRole,False)
                    self.discovery_table.item(r,5).setData(Qt.UserRole + 1,"FAILED")
                    self.discovery_table.item(r,11).setText(message)
                break
            self._mark_duplicate_discovery_names()
        else:
            if verified and hostname:
                db.update_host_hostname(host,hostname,source,True,message)
            else:
                db.mark_host_hostname_verification_failed(host,message)

    def _hostname_verify_completed(self,success,failed):
        context=self.hostname_verify_context
        self._set_busy(False)
        if context=="inventory":self.refresh_hosts()
        if context=="discovery":self.discovery_status.setText(f"主机名验证完成：成功 {success}，失败 {failed}")
        audit.operation(self.settings.audit_path,"DISCOVERY" if context=="discovery" else "HOST","VERIFY_HOSTNAME_FINISH","SUCCESS" if failed==0 else "PARTIAL_FAILED","主机名深度验证结束。",details={"success":success,"failed":failed})
        self.refresh_audit(); QMessageBox.information(self,"主机名验证",f"验证完成。\n成功：{success}\n失败：{failed}")

    def _add_discovered(self):
        added=0; now=datetime.now().isoformat(timespec="seconds")
        for r in range(self.discovery_table.rowCount()):
            chk=self.discovery_table.cellWidget(r,0)
            if chk and chk.isChecked():
                ip=self.discovery_table.item(r,2).text(); hostname=self.discovery_table.item(r,3).text().strip(); source=self.discovery_table.item(r,4).text(); source_code=self.discovery_table.item(r,3).data(Qt.UserRole)
                source_map={"DNS PTR":"DNS_PTR","NetBIOS":"NETBIOS","Windows 名称解析":"WINDOWS_RESOLVER","Windows 工作站 API":"NETAPI_WKSTA","SMB/NTLM 指纹":"SMB_NTLM","SMB/WKSSVC 验证":"SMB_RPC","WinRM hostname":"WINRM","手工维护":"MANUAL"}
                stable_source=source_code or source_map.get(source, "")
                verified=bool(self.discovery_table.item(r,5).data(Qt.UserRole))
                note=self.discovery_table.item(r,11).text(); status=self.discovery_table.item(r,10).text()
                smb_open=self.discovery_table.item(r,6).text()=="是"; rdp_open=self.discovery_table.item(r,7).text()=="是"; w1=self.discovery_table.item(r,8).text()=="是"; w2=self.discovery_table.item(r,9).text()=="是"
                online_status="ONLINE_WINRM" if (w1 or w2) else ("ONLINE_SMB" if smb_open else ("ONLINE" if rdp_open else "UNTESTED"))
                db.upsert_host(HostRecord(id=None,name=hostname or ip,host=ip,group_name="自动发现",target_mode="WINRM",default_target="",os_hint=status,last_seen=now,notes="",hostname_source=stable_source,hostname_verified=int(verified),hostname_note=note,online_status=online_status,ping_ok=0,smb_port_ok=int(smb_open),rdp_port_ok=int(rdp_open),winrm_port_ok=int(w1 or w2),smb_status="UNTESTED",last_test_at=now)); added+=1
        self.refresh_hosts(); audit.operation(self.settings.audit_path,"DISCOVERY","ADD_HOSTS","SUCCESS","已将扫描结果加入主机库。",details={"count":added}); self.refresh_audit(); QMessageBox.information(self,"主机发现",f"已添加 / 更新 {added} 台主机。")

    # ---------- History ----------
    def _build_history_page(self):
        canvas,root=self._page_canvas(); c,l=card("分发历史","双击任务可查看主机、文件分发、远程操作、备份、校验和完整审计详情。")
        self.history_cols=["task_id","created_at","operator","workstation","source_type","source_path","target_path","backup_root","verification_mode","host_count","file_count","total_bytes","status","success_hosts","failed_hosts","finished_at"]
        self.history_headers=["任务 ID","创建时间","操作用户","操作计算机","来源类型","来源路径","目标目录","备份根目录","校验策略","主机数","文件数","总大小","状态","成功主机","失败主机","结束时间"]
        self.history_table=QTableWidget(0,len(self.history_cols)); self.history_table.setHorizontalHeaderLabels(self.history_headers); self.history_table.setAlternatingRowColors(True); self.history_table.verticalHeader().setVisible(False); self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents); self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.history_table.doubleClicked.connect(self._open_history_detail); l.addWidget(self.history_table)
        row=QHBoxLayout()
        for text,fn,icon in [("刷新",self.refresh_history,"refresh"),("查看详情",self._open_history_detail,"history"),("导出 CSV",self._export_history,"export")]:b=QPushButton(text);b.setIcon(app_icon(icon));b.clicked.connect(fn);row.addWidget(b)
        row.addStretch(1);l.addLayout(row);root.addWidget(c);return canvas
    def refresh_history(self):
        if not hasattr(self,"history_table"):return
        rows=db.list_tasks();self.history_table.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,key in enumerate(self.history_cols):
                value=row[key]; value=human_bytes(int(value)) if key=="total_bytes" else value; value=source_text(value) if key=="source_type" else value; value=status_text(value) if key=="status" else value; self.history_table.setItem(r,c,QTableWidgetItem(str(value)))
    def _selected_task_id(self):
        rows=self.history_table.selectionModel().selectedRows();return self.history_table.item(rows[0].row(),0).text() if rows else None
    def _open_history_detail(self,*_):
        tid=self._selected_task_id()
        if tid:TaskDetailDialog(tid,self).exec()
    def _export_history(self):
        p,_=QFileDialog.getSaveFileName(self,"导出分发历史","分发历史.csv","CSV (*.csv)")
        if not p:return
        rows=db.list_tasks(limit=100000)
        with open(p,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f);w.writerow(self.history_headers)
            for row in rows:w.writerow([row[k] for k in self.history_cols])
        audit.operation(self.settings.audit_path,"EXPORT","HISTORY_CSV","SUCCESS","已导出分发历史 CSV。",subject=p,details={"rows":len(rows)})
        self.refresh_audit(); QMessageBox.information(self,"导出","已保存：\n" + p)

    # ---------- Audit ----------
    def _build_audit_page(self):
        canvas,root=self._page_canvas(); c,l=card("本地审计日志","所有关键用户操作、主机发现、SFTP 来源拉取、WinRM 文件分发、远程操作、备份和校验事件都会同时写入 SQLite 与本地 JSONL。")
        flt=QHBoxLayout(); self.audit_category=QComboBox(); self.audit_category.addItem("全部", "");
        for code in ["APP","TASK","SOURCE","MAPPING","DISCOVERY","HOST","SFTP","SMB","WINRM","PREFLIGHT","DISTRIBUTION","BACKUP","VERIFY","SETTINGS","EXPORT"]: self.audit_category.addItem(audit_category_text(code), code)
        self.audit_status=QComboBox(); self.audit_status.addItem("全部", ""); self.audit_status.addItem("成功", "SUCCESS"); self.audit_status.addItem("失败", "FAILED"); self.audit_status.addItem("已跳过", "SKIPPED"); self.audit_status.addItem("已取消", "CANCELLED"); self.audit_keyword=QLineEdit(); self.audit_keyword.setPlaceholderText("搜索任务 ID、主机、文件、操作或说明")
        btn=QPushButton("查询"); btn.setIcon(app_icon("refresh")); btn.clicked.connect(self.refresh_audit); flt.addWidget(QLabel("分类")); flt.addWidget(self.audit_category); flt.addWidget(QLabel("状态")); flt.addWidget(self.audit_status); flt.addWidget(self.audit_keyword,1); flt.addWidget(btn); l.addLayout(flt)
        self.audit_cols=["created_at","operator","workstation","category","action","status","task_id","host","subject","message","details_json"]
        self.audit_headers=["时间","操作人","操作终端","分类","操作","状态","任务 ID","主机 / IP","对象","说明","详细数据"]
        self.audit_table=QTableWidget(0,len(self.audit_cols)); self.audit_table.setHorizontalHeaderLabels(self.audit_headers); self.audit_table.setAlternatingRowColors(True); self.audit_table.verticalHeader().setVisible(False); self.audit_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.audit_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents); l.addWidget(self.audit_table,1)
        row=QHBoxLayout(); b_refresh=QPushButton("刷新"); b_export=QPushButton("导出 CSV"); b_open=QPushButton("打开本地审计目录"); b_refresh.setIcon(app_icon("refresh")); b_export.setIcon(app_icon("export")); b_open.setIcon(app_icon("folder")); b_refresh.clicked.connect(self.refresh_audit); b_export.clicked.connect(self._export_audit); b_open.clicked.connect(self._open_audit_dir); row.addWidget(b_refresh); row.addWidget(b_export); row.addWidget(b_open); row.addStretch(1); l.addLayout(row); root.addWidget(c); return canvas

    def refresh_audit(self):
        if not hasattr(self,"audit_table"):return
        category=self.audit_category.currentData() or ""; status=self.audit_status.currentData() or ""; keyword=self.audit_keyword.text().strip()
        rows=db.list_audit_events(limit=3000,category=category,status=status,keyword=keyword); self.audit_table.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,key in enumerate(self.audit_cols):
                value=row[key]; value=status_text(value) if key=="status" else (audit_category_text(value) if key=="category" else (action_text(value) if key=="action" else value)); self.audit_table.setItem(r,c,QTableWidgetItem(str(value if value is not None else "")))

    def _export_audit(self):
        p,_=QFileDialog.getSaveFileName(self,"导出审计日志","审计日志.csv","CSV (*.csv)")
        if not p:return
        category=self.audit_category.currentData() or ""; status=self.audit_status.currentData() or ""; keyword=self.audit_keyword.text().strip(); rows=db.list_audit_events(limit=100000,category=category,status=status,keyword=keyword)
        with open(p,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f); w.writerow(self.audit_headers)
            for row in rows:w.writerow([row[k] for k in self.audit_cols])
        audit.operation(self.settings.audit_path,"EXPORT","AUDIT_CSV","SUCCESS","已导出审计日志 CSV。",subject=p,details={"rows":len(rows)}); QMessageBox.information(self,"导出","已保存：\n"+p)

    def _open_audit_dir(self):
        p=Path(self.settings.audit_path); p.mkdir(parents=True,exist_ok=True)
        try:
            if os.name=="nt": os.startfile(str(p))
            else: QMessageBox.information(self,"审计目录",str(p))
            audit.operation(self.settings.audit_path,"APP","OPEN_AUDIT_DIR","SUCCESS","已打开本地审计目录。",subject=str(p))
        except Exception as e: QMessageBox.critical(self,"审计目录",str(e))

    # ---------- Help ----------
    def _build_help_page(self):
        canvas,root=self._page_canvas()

        c,l=card(
            "WinRM 常用命令",
            "目标机首次使用时先启用 WinRM。软件提供的设置/还原脚本只处理 WinRM 和 LocalAccountTokenFilterPolicy，不会修改 ADMS 或任何 Windows 账号、用户组、RDP 权限。",
        )
        service_box=QPlainTextEdit(); service_box.setReadOnly(True); service_box.setPlainText(WINRM_SERVICE_HELP); service_box.setMinimumHeight(300)
        l.addWidget(service_box)
        row=QHBoxLayout(); copy_service=QPushButton("复制 WinRM 命令"); copy_service.clicked.connect(lambda: self._copy_help_text(WINRM_SERVICE_HELP)); setup=QPushButton("打开 WinRM 配置向导"); setup.clicked.connect(self._show_winrm_setup_guide); row.addWidget(copy_service); row.addWidget(setup); row.addStretch(1); l.addLayout(row)
        root.addWidget(c)

        a,la=card(
            "ADMS 账号与管理员组（手工）",
            "这里只保留 WinRM 真正需要关注的账号检查：确认 ADMS 存在，并确认它是否属于本地 Administrators。若 ADMS 已在 Administrators 中，通常无需再加入 Remote Desktop Users；软件不会自动修改任何账号或用户组。",
        )
        account_box=QPlainTextEdit(); account_box.setReadOnly(True); account_box.setPlainText(ADMS_ACCOUNT_HELP); account_box.setMinimumHeight(300); la.addWidget(account_box)
        account_row=QHBoxLayout(); copy_account=QPushButton("复制 ADMS 检查命令"); copy_account.clicked.connect(lambda: self._copy_help_text(ADMS_ACCOUNT_HELP)); account_row.addWidget(copy_account); account_row.addStretch(1); la.addLayout(account_row)
        root.addWidget(a)

        u,l2=card(
            "本地管理员 / Remote UAC",
            "仅当 ADMS 等本地 Administrators 成员出现 WinRM AccessDenied 时才需要关注。修改注册表会改变远程管理员令牌策略；请先查询当前状态，并按现场安全要求决定是否开启。若原值不存在，删除该值才是最准确的恢复原状。",
        )
        uac_box=QPlainTextEdit(); uac_box.setReadOnly(True); uac_box.setPlainText(WINRM_LOCAL_ADMIN_HELP); uac_box.setMinimumHeight(520); l2.addWidget(uac_box)
        row2=QHBoxLayout(); copy_uac=QPushButton("复制 Remote UAC 命令"); copy_uac.clicked.connect(lambda: self._copy_help_text(WINRM_LOCAL_ADMIN_HELP)); row2.addWidget(copy_uac); row2.addStretch(1); l2.addLayout(row2)
        root.addWidget(u)

        d,l4=card(
            "远程盘符自动读取",
            "添加或编辑分发映射时，只要先勾选目标主机并配置好 WinRM 凭据，程序会通过 WinRM 自动读取目标 Windows 的 C:\\、D:\\、E:\\ 等可用盘符、卷标和剩余空间。单主机直接选择；多主机会显示每个盘符在多少台主机上存在。选择盘符后仍可继续输入子目录，例如 E:\\ADMS\\bin。盘符读取不使用 SMB/C$/D$/E$。",
        )
        drive_help=QLabel("建议：先勾选本次真正要分发的目标主机，再添加映射。多主机时优先选择带 ✓ 的共同盘符；正式分发前程序仍会逐台执行目录写入和空间预检查。")
        drive_help.setWordWrap(True); drive_help.setObjectName("Muted"); l4.addWidget(drive_help); root.addWidget(d)

        b,lb=card(
            "远程目录浏览器",
            "配置目标目录时可以点击“浏览远程目录”。目录树仍只读取一台参考主机，但现在会额外显示当前全部已勾选目标主机，并可一键只读检查所选路径在每台主机上的存在情况。不会弹出目标机桌面窗口，也不使用 SMB/C$/D$/E$。",
        )
        browser_help=QLabel("多主机：路径覆盖表会显示“已存在 / 目录未创建 / 盘符不存在 / 检查失败”。同一映射始终应用到当前全部勾选主机；目录未创建时正式预检查会按现有逻辑尝试创建，盘符不存在则该主机会失败。分发映射表还会直接显示当前目标主机数量和名称。")
        browser_help.setWordWrap(True); browser_help.setObjectName("Muted"); lb.addWidget(browser_help); root.addWidget(b)

        p,lp=card(
            "远程进程选择器",
            "在“文件分发 → WinRM 连接与远程操作 → ① 结束目标进程”点击“选择远程进程”，程序会通过 WinRM 只读读取参考主机当前进程，并同时显示任务管理器友好名称、实际 exe 镜像名、实例数、PID 和可执行路径。窗口不会结束任何进程，只有正式分发进入第①步时才执行。",
        )
        process_help=QLabel("选择结果按 exe 镜像名保存，例如 graphic.exe。正式执行使用 taskkill /F /T /IM，因此同名 exe 的多个实例会一起结束；多主机任务会在所有勾选目标主机上执行同一组镜像名。建议先通过搜索定位 ADMS/厂商应用，不要随意选择 Windows 核心系统进程。")
        process_help.setWordWrap(True); process_help.setObjectName("Muted"); lp.addWidget(process_help); root.addWidget(p)

        w,lw=card(
            "命令工作目录与登录桌面执行",
            r"WinRM 本身是非交互式远程管理会话，所以即使在 D:\ADMS\bin 下执行 sys_ctl start fast，也可能和人在目标机桌面 CMD 中执行不同。v0.6.14 增加“命令执行方式”：推荐选择“登录桌面（最接近本机执行）”。程序仍只通过 WinRM 控制目标机，但会临时使用 Windows 任务计划程序，把命令放到目标机当前已登录用户的交互桌面会话中执行，并使用配置的命令工作目录。",
        )
        gui_note=QLabel("登录桌面模式不安装 Agent、不保存额外密码；要求目标机已有用户登录。适合 sys_ctl、会启动 GUI 的厂商程序以及依赖当前用户 HKCU/桌面环境的命令。若目标机无人登录，请改用 WinRM 后台模式，GUI 程序不会显示到桌面。上次使用的分发前 CMD、分发后 CMD、进程列表、工作目录和执行方式都会自动保存。")
        gui_note.setWordWrap(True); gui_note.setObjectName("Muted"); lw.addWidget(gui_note); root.addWidget(w)

        t,l3=card(
            "关于 .fds_tmp 临时目录",
            "分发时程序会在每条目标映射目录下临时创建 .fds_tmp，用于“先上传到临时文件 → 大小/SHA256 校验 → 再提交为正式文件”。这样上传中断时不会直接破坏原文件。v0.6.6 起任务结束后会自动清理映射临时目录、任务目录，并在 .fds_tmp 为空时删除它；失败时也会尽力清理。若因断网/强制退出仍残留，确认没有任务运行后可手动删除该 .fds_tmp。",
        )
        flow=QLabel("安全流程：WinRM 上传临时文件 → 校验临时文件 → 备份旧文件 → 正式替换 → 最终校验 → 自动清理临时目录")
        flow.setWordWrap(True); flow.setObjectName("Muted"); l3.addWidget(flow); root.addWidget(t)
        root.addStretch(1)
        return wrap_scroll(canvas)

    def _copy_help_text(self, text: str):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("帮助命令已复制到剪贴板。", 3000)

    # ---------- Settings ----------
    def _build_settings_page(self):
        canvas,root=self._page_canvas(); c,l=card("系统设置",r"配置文件保存在 %LOCALAPPDATA%\FileDistributionStudio；WinRM 密码只可安全保存到 Windows 凭据管理器，不会写入配置、SQLite 或审计日志。")
        form=QFormLayout(); self.set_concurrency=QSpinBox(); self.set_concurrency.setRange(1,32); self.set_concurrency.setValue(self.settings.max_concurrency); self.set_timeout=QLineEdit(str(self.settings.socket_timeout)); self.set_workers=QSpinBox(); self.set_workers.setRange(1,256); self.set_workers.setValue(self.settings.discovery_workers); self.set_retry=QSpinBox(); self.set_retry.setRange(0,10); self.set_retry.setValue(self.settings.retry_count); self.set_margin=QSpinBox(); self.set_margin.setRange(0,102400); self.set_margin.setValue(self.settings.min_free_space_margin_mb)
        self.set_cache=QLineEdit(self.settings.cache_path); br=QPushButton("浏览"); br.clicked.connect(self._pick_cache); rw=QHBoxLayout(); rw.addWidget(self.set_cache); rw.addWidget(br); cw=QWidget(); cw.setLayout(rw)
        self.set_audit=QLineEdit(self.settings.audit_path); ba=QPushButton("浏览"); ba.clicked.connect(self._pick_audit); ar=QHBoxLayout(); ar.addWidget(self.set_audit); ar.addWidget(ba); aw=QWidget(); aw.setLayout(ar)
        self.set_backup_root=QLineEdit(self.settings.default_backup_root); self.set_backup_root.setPlaceholderText(r"留空使用目标目录\.fds_backup；也可填目标机本地路径，例如 E:\FDS_Backup")
        save=QPushButton("保存设置"); save.setObjectName("Primary"); save.clicked.connect(self._save_settings)
        form.addRow("最大并发主机数",self.set_concurrency);form.addRow("主机发现连接超时（秒）",self.set_timeout);form.addRow("主机发现并发线程数",self.set_workers);form.addRow("默认失败重试次数",self.set_retry);form.addRow("预检查磁盘空间安全余量（MB）",self.set_margin);form.addRow("本地缓存目录",cw);form.addRow("本地审计目录",aw);form.addRow("默认远程备份根目录",self.set_backup_root);form.addRow("",save);l.addLayout(form);root.addWidget(c);root.addStretch(1);return canvas
    def _pick_cache(self):
        p=QFileDialog.getExistingDirectory(self,"选择缓存目录",self.set_cache.text());
        if p:self.set_cache.setText(p)
    def _pick_audit(self):
        p=QFileDialog.getExistingDirectory(self,"选择本地审计目录",self.set_audit.text());
        if p:self.set_audit.setText(p)
    def _save_settings(self):
        try:
            timeout=float(self.set_timeout.text().strip()); assert timeout>0
        except Exception: QMessageBox.warning(self,"系统设置","连接超时必须为大于 0 的数字。");return
        try:
            cache_path=Path(self.set_cache.text().strip()); cache_path.mkdir(parents=True,exist_ok=True)
            cache_probe=cache_path/".fds_cache_write_probe"; cache_probe.write_text("ok",encoding="utf-8"); cache_probe.unlink(missing_ok=True)
            audit_path=Path(self.set_audit.text().strip()); audit_path.mkdir(parents=True,exist_ok=True)
            probe=audit_path/".fds_write_probe"; probe.write_text("ok",encoding="utf-8"); probe.unlink(missing_ok=True)
        except Exception as e:
            QMessageBox.warning(self,"系统设置",f"缓存目录或审计目录不可写：\n{e}"); return
        backup_root_value=self.set_backup_root.text().strip()
        if backup_root_value:
            try:
                validate_windows_target_path(backup_root_value, allow_unc=False)
            except Exception as e:
                QMessageBox.warning(self,"系统设置",f"默认远程备份根目录必须是目标机本地绝对 Windows 路径：\n{e}"); return
        old_audit=self.settings.audit_path
        self.settings.max_concurrency=self.set_concurrency.value();self.settings.socket_timeout=timeout;self.settings.discovery_workers=self.set_workers.value();self.settings.retry_count=self.set_retry.value();self.settings.min_free_space_margin_mb=self.set_margin.value();self.settings.cache_path=str(cache_path);self.settings.audit_path=str(audit_path);self.settings.default_backup_root=backup_root_value;self.settings.save();self.concurrent_spin.setValue(self.settings.max_concurrency);self.retry_spin.setValue(self.settings.retry_count);self.backup_root.setText(self.settings.default_backup_root)
        audit.operation(self.settings.audit_path,"SETTINGS","SAVE","SUCCESS","系统设置已保存。",details={"max_concurrency":self.settings.max_concurrency,"socket_timeout":self.settings.socket_timeout,"discovery_workers":self.settings.discovery_workers,"retry_count":self.settings.retry_count,"cache_path":self.settings.cache_path,"audit_path":self.settings.audit_path,"default_backup_root":self.settings.default_backup_root,"min_free_space_margin_mb":self.settings.min_free_space_margin_mb,"previous_audit_path":old_audit})
        self.refresh_audit(); QMessageBox.information(self,"系统设置","设置已保存。")

    def _save_remote_action_preferences(self):
        """Persist non-secret remote-operation choices for the next launch."""
        self.settings.winrm_remote_actions_enabled=bool(self.remote_enabled.isChecked())
        self.settings.winrm_command_workdir=self.command_workdir.text().strip()
        self.settings.winrm_pre_commands_text=self.pre_commands.toPlainText()
        self.settings.winrm_kill_processes=split_items(self.kill_processes.text())
        self.settings.winrm_post_commands_text=self.post_commands.toPlainText()
        self.settings.winrm_post_on_failure=bool(self.post_on_failure.isChecked())
        self.settings.winrm_command_execution_mode=self.command_execution_mode.currentData() or "INTERACTIVE"
        self.settings.winrm_use_https=bool(self.remote_https.isChecked())
        self.settings.winrm_port=int(self.remote_port.value())
        self.settings.save()

    def closeEvent(self,event):
        try: self._save_all_target_selection_preferences()
        except Exception: pass
        try: self._save_remote_action_preferences()
        except Exception: pass
        try: self._save_default_winrm_credential(show_error=False)
        except Exception: pass
        try: audit.operation(self.settings.audit_path,"APP","STOP","SUCCESS","文件分发工作台已退出。",details={"version":APP_VERSION})
        except Exception: pass
        super().closeEvent(event)

    def _set_busy(self,busy:bool,label="处理中"):
        if not self._status_badge:return
        self._status_badge.setText(label if busy else "就绪");self._status_badge.setObjectName("StatusBusy" if busy else "StatusReady");self._status_badge.style().unpolish(self._status_badge);self._status_badge.style().polish(self._status_badge)

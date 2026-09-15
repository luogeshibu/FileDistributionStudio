from __future__ import annotations
from pathlib import Path
from datetime import datetime
import csv
import os
import logging
import ipaddress
import subprocess

from PySide6.QtCore import Qt, QSize, QTimer, QItemSelectionModel, QStandardPaths
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QSpinBox, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QPlainTextEdit, QGroupBox,
    QAbstractItemView, QProgressBar, QFrame, QStackedWidget, QScrollArea, QApplication, QDialog, QDialogButtonBox, QSizePolicy, QInputDialog, QTextEdit, QMenu
)

from ..version import APP_NAME, APP_VERSION
from ..config import AppSettings
from ..models import HostRecord, DistributionMapping
from ..resources import asset_path, script_path
from ..services.discovery import local_ipv4_networks, normalize_networks
from ..services.sftp_source import SftpSource
from ..services.remote_exec import RemoteActionPlan, WinRMExecutor, qualify_windows_username, split_items, split_commands
from ..services import audit, credential_store
from ..services.xlsx_export import export_xlsx
from ..utils import human_bytes, validate_windows_target_path
from ..workers import DiscoveryThread, DistributionThread, DryRunThread, BackupThread, VersionCheckThread, HostnameVerificationThread, WinRMTargetTestThread, HostStatusTestThread, DiscoveryReconcileThread, HostEnvironmentCheckThread, RemoteFileOperationThread
from .. import db
from .dialogs import (HostEditDialog, TaskDetailDialog, HostnameCredentialDialog, MappingTargetDialog,
                      SftpMappingDialog, WinRMSetupDialog, WinRMHostCredentialDialog, RemoteProcessBrowserDialog,
                      RemoteBackupBrowserDialog)
from .theme import app_icon
from .widgets import PasswordLineEdit, LocalFileTable, RemoteFileDropTable, configure_full_content_table, fit_full_content_table
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
    ("远程文件", "像文件管理器一样浏览单台 Windows 主机，并通过 WinRM 手工上传或下载文件。", "file"),
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
    # 主页面只允许纵向滚动。页面内容随可用宽度收缩，避免底部出现整页横向滚动条。
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(widget)
    # 主页面内容很长，Qt 默认滚轮步长偏小。提高页面滚动步长，只影响真正的
    # QScrollArea 滚动，不会重新让输入框/下拉框响应滚轮修改值。
    scroll.verticalScrollBar().setSingleStep(64)
    return scroll


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = AppSettings.load()
        self.discovery_thread = None
        self.discovery_reconcile_thread = None
        self._scan_cidrs = []
        self._scan_existing_hosts = {}
        self._scan_discovered_ips = set()
        self.hostname_thread = None
        self.hostname_verify_context = ""
        self.distribution_thread = None
        self.dry_run_thread = None
        self.backup_thread = None
        self.version_check_thread = None
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
        # 分发映射与“文件分发”模块的智能联动状态。
        # 用户明确手工关闭文件分发后，不因后续编辑/新增映射而强制重新打开；
        # 只有从 0 条启用映射变成至少 1 条启用映射时才自动开启。
        self._mapping_auto_toggle_guard = False
        self._distribution_manual_off = False
        self._last_enabled_mapping_count = 0

        self.setWindowTitle(f"{APP_NAME}  ·  v{APP_VERSION}")
        self.resize(1420, 900)
        self.setMinimumSize(1120, 720)
        self._build_shell()
        # 保持正常窗口启动，不再强制最大化；页面本身按窗口宽度自适应，仅保留纵向滚动。
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
            self._build_distribution_page(), self._build_remote_files_page(), self._build_inventory_page(), self._build_discovery_page(),
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
        configure_full_content_table(self.mapping_table, fixed_columns={0: 44})
        # 默认完整显示 10 条分发映射；更多映射继续使用表格内部滚动。
        self.mapping_table.verticalHeader().setDefaultSectionSize(32)
        self.mapping_table.setMinimumHeight(32 * 10 + 36)
        self.mapping_table.setMaximumHeight(32 * 10 + 52)
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
            "目录统一采用“合并覆盖（安全）”：同名文件强制覆盖、缺少文件新增，目标目录中源目录没有的额外文件保留且绝不删除；可选择“仅复制目录内容”或“复制目录本身”。启用备份时先备份旧文件再覆盖。每条映射都会应用到当前全部已勾选目标主机；开始分发前会逐台预检查。"
        )
        map_hint.setObjectName("Muted"); map_hint.setWordWrap(True); src_l.addWidget(map_hint)

        target, target_l = card(
            "Windows 目标主机",
            "选择本次要分发的 Windows 主机，并设置默认账号密码。",
        )
        cred = QHBoxLayout()
        self.win_user = QLineEdit(self.settings.winrm_default_username); self.win_password = PasswordLineEdit()
        self.win_user.setPlaceholderText(r"本地账号填 COMPUTER\user；域账号填 DOMAIN\user")
        self.remember_default_cred = QCheckBox("记住默认凭据")
        self.remember_default_cred.setChecked(bool(self.settings.remember_winrm_default_credential))
        self.remember_default_cred.setToolTip("密码仅保存到当前 Windows 用户的 Windows 凭据管理器，不写入配置、SQLite 或审计日志。")
        # 默认凭据恢复为原有的横向自适应布局：用户名与密码输入框共同占满可用宽度。
        cred.addWidget(QLabel("默认 Windows 用户")); cred.addWidget(self.win_user, 1)
        cred.addSpacing(10); cred.addWidget(QLabel("密码")); cred.addWidget(self.win_password, 1)
        cred.addWidget(self.remember_default_cred)
        target_l.addLayout(cred)
        self._load_default_winrm_credential()
        self.win_user.editingFinished.connect(lambda: self._save_default_winrm_credential(show_error=False))
        self.win_password.editingFinished.connect(lambda: self._save_default_winrm_credential(show_error=False))
        self.remember_default_cred.toggled.connect(lambda _checked: self._save_default_winrm_credential(show_error=False))
        cred_hint = QLabel(
            "默认使用 WinRM HTTP 5985。首次使用目标机可下载 WinRM 设置脚本；脚本不会修改任何 Windows 账号、用户组或 RDP 权限。"
            "记住凭据时密码来自 Windows 凭据管理器；取消勾选并重新输入可清除旧密码。本地账号会按目标主机名发送。"
        )
        cred_hint.setObjectName("Muted"); cred_hint.setWordWrap(True); target_l.addWidget(cred_hint)

        target_filter_row = QHBoxLayout(); target_filter_row.setSpacing(8)
        target_filter_row.addWidget(QLabel("快速筛选"))
        self.target_filter_edit = QLineEdit()
        self.target_filter_edit.setClearButtonEnabled(True)
        self.target_filter_edit.setPlaceholderText("输入 IP、主机名、分组、凭据或状态，例如：172.16.21 / Dispatcher / AUTH_OK")
        self.target_filter_edit.textChanged.connect(self._apply_target_filter)
        target_filter_row.addWidget(self.target_filter_edit, 1)
        self.target_filter_count = QLabel("显示 0 / 0 台 · 已选 0 台")
        self.target_filter_count.setObjectName("Muted")
        target_filter_row.addWidget(self.target_filter_count)
        target_l.addLayout(target_filter_row)

        self.target_table = QTableWidget(0, 8)
        self.target_table.setHorizontalHeaderLabels(["选择", "名称", "主机 / IP", "分组", "凭据", "在线状态", "WinRM 状态", "任务状态"])
        self.target_table.setAlternatingRowColors(True)
        self.target_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.target_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.target_table.verticalHeader().setVisible(False)
        configure_full_content_table(self.target_table, fixed_columns={0: 44})
        # 默认至少完整显示 10 台目标主机；主机更多时由表格自身滚动，不把整页无限撑高。
        self.target_table.verticalHeader().setDefaultSectionSize(32)
        self.target_table.setMinimumHeight(32 * 10 + 36)
        self.target_table.setMaximumHeight(32 * 10 + 52)
        target_l.addWidget(self.target_table)
        # 目标主机操作区：常用动作尽量铺满整行。远程桌面属于独立人工操作，不参与任何分发任务流程。
        tr = QGridLayout(); tr.setHorizontalSpacing(8); tr.setVerticalSpacing(8)
        target_action_buttons = []
        for text, fn, icon in [
            ("全选", lambda:self._set_all_targets(True), "check"),
            ("取消全选", lambda:self._set_all_targets(False), "clear"),
            ("刷新主机", self.refresh_hosts, "refresh"),
            ("测试在线状态", self._test_distribution_hosts_online, "radar"),
        ]:
            b = QPushButton(text); b.setIcon(app_icon(icon)); b.clicked.connect(fn); target_action_buttons.append(b)
        self.btn_winrm_test = QPushButton("测试 WinRM")
        self.btn_winrm_test.setIcon(app_icon("terminal"))
        self.btn_winrm_test.setToolTip("对所有已勾选主机执行各自主机凭据的 WinRM 身份验证，并逐一测试当前目标目录的创建、写入、读取和删除。")
        self.btn_winrm_test.clicked.connect(self._test_winrm_targets)
        target_action_buttons.append(self.btn_winrm_test)
        self.btn_target_env_check = QPushButton("ADMS 部署前检查")
        self.btn_target_env_check.setIcon(app_icon("search"))
        self.btn_target_env_check.setToolTip(
            "只读检查当前勾选目标机是否满足 ADMS 客户端部署前置条件：WinRM 可连接、"
            "与本机时间偏差不超过 2 分钟、Private/Public 防火墙均关闭；"
            "同时显示 Domain 防火墙、时区和 Windows Time 状态供诊断，不修改目标机。"
        )
        self.btn_target_env_check.clicked.connect(
            lambda: self._check_host_environment(self._selected_hosts(), "Windows 目标主机")
        )
        target_action_buttons.append(self.btn_target_env_check)
        self.btn_open_rdp = QPushButton("打开远程桌面")
        self.btn_open_rdp.setIcon(app_icon("terminal"))
        self.btn_open_rdp.setToolTip(
            "使用当前目标主机的有效凭据启动本机 Windows 远程桌面（mstsc）。\n"
            "仅支持一次打开 1 台主机；会把该 RDP 凭据写入当前 Windows 用户的凭据管理器（TERMSRV/目标主机），密码不会写入日志。"
        )
        self.btn_open_rdp.clicked.connect(self._open_selected_rdp)
        target_action_buttons.append(self.btn_open_rdp)
        self.btn_download_adms_setup = QPushButton("下载 ADMS WinRM 设置脚本")
        self.btn_download_adms_setup.setIcon(app_icon("file"))
        self.btn_download_adms_setup.setToolTip(
            "下载后请将该脚本放到目标主机，并在目标主机上以管理员身份运行。\n"
            "用于首次准备 ADMS WinRM；脚本不修改任何账号、用户组或 RDP 权限。"
        )
        self.btn_download_adms_setup.clicked.connect(lambda: self._save_bundled_cmd("TARGET_PREP_ADMS_WINRM.cmd", "保存 ADMS WinRM 设置脚本"))
        target_action_buttons.append(self.btn_download_adms_setup)
        self.btn_download_adms_restore = QPushButton("下载 ADMS 还原脚本")
        self.btn_download_adms_restore.setIcon(app_icon("refresh"))
        self.btn_download_adms_restore.setToolTip(
            "下载后请将该脚本放到目标主机，并在目标主机上以管理员身份运行。\n"
            "用于还原 ADMS WinRM 准备项；仅删除 LocalAccountTokenFilterPolicy 并停止 WinRM，"
            "不修改任何账号、用户组或 RDP 权限。"
        )
        self.btn_download_adms_restore.clicked.connect(lambda: self._save_bundled_cmd("TARGET_RESTORE_ADMS_WINRM.cmd", "保存 ADMS 还原脚本"))
        target_action_buttons.append(self.btn_download_adms_restore)
        self.btn_winrm_setup = QPushButton("WinRM 配置向导")
        self.btn_winrm_setup.setIcon(app_icon("settings"))
        self.btn_winrm_setup.setToolTip("查看其他 WinRM 准备方式和脚本内容。")
        self.btn_winrm_setup.clicked.connect(self._show_winrm_setup_guide)
        target_action_buttons.append(self.btn_winrm_setup)
        def _compact_action_button(button: QPushButton):
            # 横向允许拉伸，让同一行按钮均匀填满；高度仍保持紧凑。
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setMinimumWidth(0)
            button.setMinimumHeight(34)

        # 固定呈现顺序：第二行放诊断/连接类动作，第三行放两个脚本下载。
        target_action_buttons = target_action_buttons[:7] + [self.btn_winrm_setup, self.btn_download_adms_setup, self.btn_download_adms_restore]
        for b in target_action_buttons:
            _compact_action_button(b)
        # 4 + 4 + 2（最后两个脚本按钮各跨两列），避免最后一行只剩一个按钮。
        positions = [
            (0, 0, 1, 1), (0, 1, 1, 1), (0, 2, 1, 1), (0, 3, 1, 1),
            (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1), (1, 3, 1, 1),
            (2, 0, 1, 2), (2, 2, 1, 2),
        ]
        for b, pos in zip(target_action_buttons, positions):
            tr.addWidget(b, *pos)
        for col in range(4):
            tr.setColumnStretch(col, 1)
        target_l.addLayout(tr)
        adms_precheck_hint = QLabel(
            "ADMS 部署前检查：只读检查 WinRM、与本机时间偏差（≤2 分钟）、Private/Public 防火墙（必须关闭）；"
            "Domain 防火墙、时区和 Windows Time 仅作为诊断信息显示。"
        )
        adms_precheck_hint.setObjectName("Muted")
        adms_precheck_hint.setWordWrap(True)
        target_l.addWidget(adms_precheck_hint)

        # 凭据管理同样采用内容自适应宽度，说明文字使用剩余空间。
        cred_actions = QHBoxLayout(); cred_actions.setSpacing(8)
        cred_title = QLabel("凭据管理")
        self.btn_set_host_cred = QPushButton("设置选中凭据")
        self.btn_set_host_cred.setIcon(app_icon("edit"))
        self.btn_set_host_cred.setToolTip("为表格中高亮选择的一台或多台主机设置自定义 WinRM 凭据。按 Ctrl / Shift 可选择多行。")
        self.btn_set_host_cred.clicked.connect(self._set_selected_host_credentials)
        self.btn_batch_host_cred = QPushButton("批量设置凭据")
        self.btn_batch_host_cred.setIcon(app_icon("hosts"))
        self.btn_batch_host_cred.setToolTip("为当前勾选的全部分发目标主机设置同一套自定义 WinRM 凭据。")
        self.btn_batch_host_cred.clicked.connect(self._set_checked_host_credentials)
        self.btn_clear_host_cred = QPushButton("恢复默认凭据")
        self.btn_clear_host_cred.setIcon(app_icon("clear"))
        self.btn_clear_host_cred.setToolTip("清除表格中高亮选择主机的自定义凭据，恢复使用顶部默认凭据。")
        self.btn_clear_host_cred.clicked.connect(self._restore_selected_default_credentials)
        for b in (self.btn_set_host_cred, self.btn_batch_host_cred, self.btn_clear_host_cred):
            _compact_action_button(b)
        cred_actions.addWidget(cred_title)
        cred_actions.addWidget(self.btn_set_host_cred)
        cred_actions.addWidget(self.btn_batch_host_cred)
        cred_actions.addWidget(self.btn_clear_host_cred)
        cred_note = QLabel("大多数主机使用默认凭据；只有账号/密码不同的主机才需要自定义。")
        cred_note.setObjectName("Muted"); cred_note.setWordWrap(True)
        cred_actions.addWidget(cred_note, 1)
        target_l.addLayout(cred_actions)


        def task_toggle(title: str, checked: bool) -> QPushButton:
            """模块级任务开关：与普通参数 QCheckBox 做明显视觉区分。"""
            btn = QPushButton()
            btn.setObjectName("TaskToggle")
            btn.setCheckable(True)
            btn.setMinimumWidth(190)
            btn.setToolTip("模块级任务开关：决定该步骤是否加入本次组合任务；模块内部普通复选框仅控制该步骤的参数。")
            def refresh_text(on: bool):
                btn.setText(("● 已加入本次任务  |  " if on else "○ 未加入本次任务  |  ") + title)
            btn.toggled.connect(refresh_text)
            btn.setChecked(bool(checked))
            refresh_text(btn.isChecked())
            return btn

        def bind_task_card(toggle: QPushButton, frame: QFrame):
            """模块开关开启时高亮整个卡片，让“加入本次任务”状态一眼可见。"""
            def refresh_card(on: bool):
                frame.setProperty("taskActive", "true" if on else "false")
                frame.style().unpolish(frame)
                frame.style().polish(frame)
                frame.update()
            toggle.toggled.connect(refresh_card)
            refresh_card(toggle.isChecked())

        opts, opts_l = card("文件分发", "文件分发本身作为独立任务；模块级任务开关决定是否执行，SHA256/预检查等普通复选框仅是内部参数。")
        ol=QHBoxLayout(); self.chk_distribution=task_toggle("文件分发", bool(self.settings.distribution_enabled)); self.chk_distribution.toggled.connect(self._on_distribution_task_toggled); self.chk_verify=QCheckBox("SHA256 强校验"); self.chk_verify.setChecked(self.settings.verify_sha256); self.chk_preflight=QCheckBox("分发前预检查"); self.chk_preflight.setChecked(self.settings.preflight_check)
        self.retry_spin=QSpinBox(); self.retry_spin.setRange(0,10); self.retry_spin.setValue(self.settings.retry_count); self.concurrent_spin=QSpinBox(); self.concurrent_spin.setRange(1,32); self.concurrent_spin.setValue(self.settings.max_concurrency)
        ol.addWidget(self.chk_distribution); ol.addWidget(self.chk_verify); ol.addWidget(self.chk_preflight); ol.addWidget(QLabel("失败重试")); ol.addWidget(self.retry_spin); ol.addWidget(QLabel("主机并发数")); ol.addWidget(self.concurrent_spin); ol.addStretch(1); opts_l.addLayout(ol)
        bind_task_card(self.chk_distribution, opts)

        backup_card, backup_l = card("备份（独立可选）", "备份与文件分发完全解耦：备份文件保存在各目标 Windows 主机本地；加入组合任务时备份即将被覆盖的文件，单独执行时通过 WinRM 浏览并勾选需要备份的目标。")
        br0=QHBoxLayout(); self.chk_backup=task_toggle("分发前备份", bool(self.settings.backup_task_enabled or self.settings.backup_existing)); self.btn_run_backup=QPushButton("单独执行备份"); self.btn_run_backup.setIcon(app_icon("play")); self.btn_run_backup.clicked.connect(self._run_backup_standalone); br0.addWidget(self.chk_backup); br0.addStretch(1); br0.addWidget(self.btn_run_backup); backup_l.addLayout(br0)
        bf=QFormLayout(); self.backup_root=QLineEdit(self.settings.default_backup_root); self.backup_root.setPlaceholderText(r"留空：在目标主机对应目录使用 .fds_backup；可填目标主机本地路径，例如 E:\Backup")
        bf.addRow("目标主机备份根目录", self.backup_root); backup_l.addLayout(bf)
        backup_location=QLabel("备份位置：各目标 Windows 主机本地，不会把备份文件回收到当前电脑。")
        backup_location.setObjectName("Muted"); backup_location.setWordWrap(True); backup_l.addWidget(backup_location)
        hint=QLabel(r"组合执行时，只备份本次将被覆盖的目标文件；单独执行备份时会打开远程文件浏览器，从盘符开始直接浏览文件夹和文件并勾选目标，文件夹会递归备份，不依赖分发映射。目标主机备份根目录不存在时自动创建，已存在时直接使用；自定义根目录按任务 ID、原盘符和原目录结构保存。")
        hint.setObjectName("Muted"); hint.setWordWrap(True); backup_l.addWidget(hint)
        bind_task_card(self.chk_backup, backup_card)

        remote, remote_l = card(
            "WinRM 连接与远程操作",
            "执行顺序：结束目标进程 → 分发前命令 → 文件分发 → 分发后命令。",
        )
        rt=QHBoxLayout(); self.remote_enabled=task_toggle("程序 / 服务前后操作", bool(self.settings.winrm_remote_actions_enabled)); self.remote_https=QCheckBox("HTTPS"); self.remote_port=QSpinBox(); self.remote_port.setRange(1,65535); self.remote_port.setValue(int(self.settings.winrm_port or 5985)); self.remote_https.toggled.connect(lambda c:self.remote_port.setValue(5986 if c else 5985)); self.remote_https.setChecked(bool(self.settings.winrm_use_https)); self.remote_port.setValue(int(self.settings.winrm_port or (5986 if self.remote_https.isChecked() else 5985)))
        self.remote_https.setVisible(False); self.remote_port.setVisible(False)
        self.winrm_conn_summary = QLabel(); self.winrm_conn_summary.setObjectName("Muted")
        self._refresh_winrm_connection_summary()
        advanced_btn = QPushButton("高级连接…"); advanced_btn.clicked.connect(self._show_winrm_advanced_connection)
        rt.addWidget(self.remote_enabled); rt.addWidget(self.winrm_conn_summary); rt.addWidget(advanced_btn); rt.addStretch(1); remote_l.addLayout(rt)
        bind_task_card(self.remote_enabled, remote)

        pipeline = QFrame(); pipeline.setObjectName("SoftCard"); self.remote_pipeline = pipeline
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
        cmd_hint=QLabel("命令输入规则：每行一条命令，按从上到下顺序执行。示例：第一行 sys_ctl start fast，第二行 echo 分发完成。空行会忽略；以 # 或 REM 开头的行作为注释忽略。")
        cmd_hint.setObjectName("Muted"); cmd_hint.setWordWrap(True); pl.addWidget(cmd_hint)

        self.post_on_failure=QCheckBox("失败恢复：即使文件分发失败，也尝试执行分发后 CMD")
        self.post_on_failure.setChecked(bool(self.settings.winrm_post_on_failure)); pl.addWidget(self.post_on_failure)
        remote_l.addWidget(pipeline)

        cwd_hint=QLabel(r"提示：当前顺序为先结束选中的 GUI/客户端进程，再执行 sys_ctl stop 等停止/准备命令，完成文件分发后再执行 sys_ctl start fast 等启动/恢复命令。命令工作目录建议设置为实际 bin 目录。上次使用的命令、进程和执行方式会自动记住。")
        cwd_hint.setObjectName("Muted"); cwd_hint.setWordWrap(True); remote_l.addWidget(cwd_hint)

        version_card, version_l = card(
            "版本检查（独立可选）",
            "完全独立：模块级任务开关决定是否加入本次组合任务；关闭时不会因分发成功自动执行，也可随时单独执行。",
        )
        vr0=QHBoxLayout()
        self.version_after_distribution=task_toggle("Version Checker", bool(self.settings.version_checker_enabled or self.settings.version_checker_after_distribution))
        self.btn_run_version_check=QPushButton("单独执行版本检查")
        self.btn_run_version_check.setIcon(app_icon("play")); self.btn_run_version_check.clicked.connect(self._run_version_check_standalone)
        vr0.addWidget(self.version_after_distribution); vr0.addStretch(1); vr0.addWidget(self.btn_run_version_check); version_l.addLayout(vr0)
        bind_task_card(self.version_after_distribution, version_card)
        vf=QFormLayout()
        self.version_exe=QLineEdit(self.settings.version_checker_exe_path); self.version_exe.setPlaceholderText(r"例如 D:\ADMS\bin\version_checker.exe")
        self.version_workdir=QLineEdit(self.settings.version_checker_workdir); self.version_workdir.setPlaceholderText(r"例如 D:\ADMS\bin")
        self.version_output_dir=QLineEdit(self.settings.version_checker_output_dir); self.version_output_dir.setPlaceholderText("Save 后 CSV 生成目录（version_checker_result.csv）")
        self.version_save_button=QLineEdit(self.settings.version_checker_save_button or "Save")
        self.version_timeout=QSpinBox(); self.version_timeout.setRange(15,1800); self.version_timeout.setValue(int(self.settings.version_checker_timeout_seconds or 120)); self.version_timeout.setSuffix(" 秒")
        self.version_local_root=QLineEdit(self.settings.version_checker_local_result_root)
        self.btn_version_local_root=QPushButton("选择…"); self.btn_version_local_root.clicked.connect(self._choose_version_result_root)
        local_row=QHBoxLayout(); local_row.setContentsMargins(0,0,0,0); local_row.addWidget(self.version_local_root,1); local_row.addWidget(self.btn_version_local_root)
        vf.addRow("程序路径",self.version_exe); vf.addRow("工作目录",self.version_workdir); vf.addRow("CSV 生成目录",self.version_output_dir); vf.addRow("Save 按钮名称",self.version_save_button); vf.addRow("等待超时",self.version_timeout); vf.addRow("本机结果目录",local_row)
        version_l.addLayout(vf)
        vr1=QHBoxLayout(); self.version_collect=QCheckBox("回收 CSV 到本机"); self.version_collect.setChecked(bool(self.settings.version_checker_collect_excel)); self.version_close=QCheckBox("完成后关闭 Version Checker"); self.version_close.setChecked(bool(self.settings.version_checker_close_after)); vr1.addWidget(self.version_collect); vr1.addWidget(self.version_close); vr1.addStretch(1); version_l.addLayout(vr1)
        vh=QLabel("执行方式：WinRM 负责调度，在目标机当前已登录用户的交互桌面启动 version_checker；自动执行 Save → 等待 Information 提示 → OK → 确认 version_checker_result.csv 写入完成 → 直接回收到所选本机结果目录（文件名前加主机名）→ 可选关闭程序。Save 优先使用 UI Automation/Win32 控件，必要时使用窗口相对位置点击。目标机必须已有用户登录。")
        vh.setObjectName("Muted"); vh.setWordWrap(True); version_l.addWidget(vh)

        run_card, run_l = card("执行与日志", "本次任务按已启用组件执行：备份 → 程序/服务前置操作 → 文件分发 → 后置操作 → Version Checker。未启用的步骤自动跳过。")
        self.task_plan_label=QLabel(); self.task_plan_label.setObjectName("PipelineAuto"); self.task_plan_label.setWordWrap(True); run_l.addWidget(self.task_plan_label)
        ar=QHBoxLayout()
        self.btn_clear_log=QPushButton("清空日志"); self.btn_clear_log.setIcon(app_icon("clear")); self.btn_clear_log.setToolTip("仅清空当前页面的执行日志显示，不删除审计日志和分发历史"); self.btn_clear_log.clicked.connect(self._clear_distribution_log)
        self.btn_dry_run=QPushButton("Dry Run 预演"); self.btn_dry_run.setIcon(app_icon("search")); self.btn_dry_run.setToolTip("只检查源文件、WinRM、远端路径、磁盘空间、CMD 工作目录和 Version Checker 路径；不会上传、覆盖、备份、结束进程或执行命令。")
        self.btn_retry_failed=QPushButton("重试失败主机"); self.btn_retry_failed.setIcon(app_icon("refresh")); self.btn_retry_failed.setEnabled(False); self.btn_retry_failed.setToolTip("仅重新执行上一轮失败的主机，不重复执行已经成功的主机。")
        self.btn_export_result=QPushButton("导出任务结果"); self.btn_export_result.setIcon(app_icon("file")); self.btn_export_result.setEnabled(False); self.btn_export_result.setToolTip("把最近一次分发任务的主机级结果导出为 Excel。")
        self.btn_start=QPushButton("开始执行所选任务"); self.btn_start.setObjectName("Primary"); self.btn_start.setIcon(app_icon("play"))
        self.btn_cancel=QPushButton("取消"); self.btn_cancel.setObjectName("Danger"); self.btn_cancel.setEnabled(False)
        self.btn_dry_run.clicked.connect(self._run_dry_run); self.btn_retry_failed.clicked.connect(self._retry_failed_distribution); self.btn_export_result.clicked.connect(self._export_last_task_result)
        self.btn_start.clicked.connect(self._start_distribution); self.btn_cancel.clicked.connect(self._cancel_distribution)
        ar.addStretch(1); ar.addWidget(self.btn_clear_log); ar.addWidget(self.btn_dry_run); ar.addWidget(self.btn_retry_failed); ar.addWidget(self.btn_export_result); ar.addWidget(self.btn_start); ar.addWidget(self.btn_cancel); run_l.addLayout(ar)
        self.overall=QProgressBar(); self.overall.setValue(0); run_l.addWidget(self.overall); self.dist_log=QPlainTextEdit(); self.dist_log.setReadOnly(True); self.dist_log.setMaximumBlockCount(5000); self.dist_log.setMinimumHeight(150); run_l.addWidget(self.dist_log)

        root.addWidget(src); root.addWidget(target); root.addWidget(opts); root.addWidget(backup_card); root.addWidget(remote); root.addWidget(version_card); root.addWidget(run_card); root.addStretch(1)
        for w in (self.chk_distribution,self.chk_backup,self.remote_enabled,self.version_after_distribution):
            w.toggled.connect(self._refresh_task_plan)
        self.remote_enabled.toggled.connect(lambda enabled:self.remote_pipeline.setEnabled(bool(enabled)))
        self.remote_pipeline.setEnabled(bool(self.remote_enabled.isChecked()))
        self._refresh_task_plan()
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

    def _on_distribution_task_toggled(self, checked: bool):
        """记录用户对“文件分发”模块的明确手工选择。

        自动联动修改开关时使用 guard，避免把程序自动关闭误记成用户手工关闭。
        """
        if self._mapping_auto_toggle_guard:
            return
        self._distribution_manual_off = not bool(checked)

    def _sync_distribution_with_mappings(self):
        """按启用映射数量智能联动文件分发模块。

        - 0 -> >=1：若用户没有明确手工关闭，则自动加入“文件分发”；
        - >=1 -> 0：自动移出“文件分发”；
        - 用户明确手工关闭后，编辑/新增映射不会抢夺控制权；用户再次手工开启后解除该状态。
        """
        if not hasattr(self, "mapping_table") or not hasattr(self, "chk_distribution"):
            return
        current = len(self._mapping_rows(True))
        previous = int(getattr(self, "_last_enabled_mapping_count", 0) or 0)
        should_enable = previous == 0 and current > 0 and not self._distribution_manual_off
        should_disable = previous > 0 and current == 0
        if should_enable and not self.chk_distribution.isChecked():
            self._mapping_auto_toggle_guard = True
            try:
                self.chk_distribution.setChecked(True)
            finally:
                self._mapping_auto_toggle_guard = False
        elif should_disable and self.chk_distribution.isChecked():
            self._mapping_auto_toggle_guard = True
            try:
                self.chk_distribution.setChecked(False)
            finally:
                self._mapping_auto_toggle_guard = False
        self._last_enabled_mapping_count = current

    def _on_mapping_enabled_changed(self, *_):
        self._refresh_mapping_scope()
        self._sync_distribution_with_mappings()

    def _append_mapping(self, mapping: DistributionMapping):
        r=self.mapping_table.rowCount(); self.mapping_table.insertRow(r)
        chk=QCheckBox(); chk.setChecked(True); chk.setStyleSheet("QCheckBox { margin-left: 12px; margin-right: 12px; }"); chk.stateChanged.connect(self._on_mapping_enabled_changed); self.mapping_table.setCellWidget(r,0,chk)
        scope_text, scope_tip = self._target_scope_display()
        vals=["SFTP" if mapping.source_type=="SFTP" else "本地", "目录" if mapping.source_kind=="DIR" else "文件", mapping.display_source(), mapping.target_path,
              scope_text, ("合并覆盖（安全）·复制目录本身" if mapping.folder_mode=="SELF" else "合并覆盖（安全）·复制目录内容") if mapping.source_kind=="DIR" else "文件", "待分发"]
        for c,v in enumerate(vals,1): self.mapping_table.setItem(r,c,QTableWidgetItem(v))
        self.mapping_table.item(r,3).setData(ROLE_HOST_OBJECT,mapping)
        self.mapping_table.item(r,5).setToolTip(scope_tip)
        self._refresh_mapping_scope()
        self._sync_distribution_with_mappings()
        fit_full_content_table(self.mapping_table)
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
        scope_text,scope_tip=self._target_scope_display(); self.mapping_table.setItem(r,1,QTableWidgetItem("SFTP" if m.source_type=="SFTP" else "本地"));self.mapping_table.setItem(r,2,QTableWidgetItem("目录" if m.source_kind=="DIR" else "文件"));src=QTableWidgetItem(m.display_source());src.setData(ROLE_HOST_OBJECT,m);self.mapping_table.setItem(r,3,src);self.mapping_table.setItem(r,4,QTableWidgetItem(m.target_path));scope_item=QTableWidgetItem(scope_text);scope_item.setToolTip(scope_tip);self.mapping_table.setItem(r,5,scope_item);self.mapping_table.setItem(r,6,QTableWidgetItem(("合并覆盖（安全）·复制目录本身" if m.folder_mode=="SELF" else "合并覆盖（安全）·复制目录内容") if m.source_kind=="DIR" else "文件"));self.mapping_table.setItem(r,7,QTableWidgetItem("待分发"))
        self._refresh_mapping_scope(); fit_full_content_table(self.mapping_table); audit.operation(self.settings.audit_path,"MAPPING","EDIT","SUCCESS","已修改分发映射。",subject=m.mapping_id,details=m.safe_dict());self.refresh_audit()

    def _delete_mapping(self):
        r=self._selected_mapping_row()
        if r>=0:
            m=self.mapping_table.item(r,3).data(ROLE_HOST_OBJECT); self.mapping_table.removeRow(r); self._refresh_mapping_scope(); self._sync_distribution_with_mappings()
            if m:audit.operation(self.settings.audit_path,"MAPPING","DELETE","SUCCESS","已删除分发映射。",subject=m.mapping_id,details=m.safe_dict());self.refresh_audit()

    def _clear_mappings(self):
        if self.mapping_table.rowCount() and QMessageBox.question(self,"清空映射","确定清空当前所有分发映射吗？")!=QMessageBox.Yes:return
        self.mapping_table.setRowCount(0); self._refresh_mapping_scope(); self._sync_distribution_with_mappings(); audit.operation(self.settings.audit_path,"MAPPING","CLEAR","SUCCESS","已清空当前分发映射。");self.refresh_audit()

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
                if self.target_table.isRowHidden(r):
                    continue
                w=self.target_table.cellWidget(r,0)
                if w:w.setChecked(checked)
        finally:
            self._suppress_target_selection_persist = False
        self._save_all_target_selection_preferences()
        self._refresh_mapping_scope()
        self._apply_target_filter()

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
        self._apply_target_filter()

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

    def _open_selected_rdp(self):
        """Launch local Windows Remote Desktop for exactly one target host.

        This is an independent convenience action only. It does not change task selection,
        distribution, backup, Dry Run, WinRM pre/post actions, or Version Checker.
        """
        hosts = self._selected_hosts()
        if len(hosts) != 1:
            highlighted = self._highlighted_target_hosts()
            if len(hosts) == 0 and len(highlighted) == 1:
                hosts = highlighted
            else:
                QMessageBox.information(self, "打开远程桌面", "请只勾选 1 台目标主机后再打开远程桌面。")
                return
        host_rec = hosts[0]
        self._save_default_winrm_credential(show_error=True)
        try:
            credentials = self._resolve_credentials([host_rec])
            username, password, source = credentials[host_rec.host]
        except Exception as exc:
            QMessageBox.warning(self, "打开远程桌面", str(exc))
            return

        # 对本地账号优先使用“目标主机名\用户”，避免 mstsc 把裸用户名解释成本机账号。
        rdp_user = username.strip()
        if "\\" not in rdp_user and "@" not in rdp_user:
            remote_name = (host_rec.name or "").strip()
            if remote_name and remote_name != host_rec.host and not remote_name.replace('.', '').isdigit():
                rdp_user = f"{remote_name}\\{rdp_user}"

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        target = f"TERMSRV/{host_rec.host}"
        try:
            saved = subprocess.run(
                ["cmdkey.exe", f"/generic:{target}", f"/user:{rdp_user}", f"/pass:{password}"],
                capture_output=True, text=True, timeout=10, creationflags=creationflags, check=False,
            )
            if saved.returncode != 0:
                detail = (saved.stderr or saved.stdout or "cmdkey 执行失败").strip()
                raise RuntimeError(detail)
            subprocess.Popen(["mstsc.exe", f"/v:{host_rec.host}"], creationflags=0)
            self._append_log(f"[{host_rec.host}] 已启动 Windows 远程桌面；账号={rdp_user}；凭据来源={source}。")
            audit.operation(
                self.settings.audit_path, "HOST", "OPEN_RDP", "SUCCESS",
                "已启动本机 Windows 远程桌面。", host=host_rec.host,
                details={"name": host_rec.name, "username": rdp_user, "credential_source": source},
            )
            self.refresh_audit()
        except FileNotFoundError:
            QMessageBox.warning(self, "打开远程桌面", "当前系统未找到 Windows 远程桌面客户端 mstsc.exe / cmdkey.exe。")
        except Exception as exc:
            audit.operation(self.settings.audit_path, "HOST", "OPEN_RDP", "FAILED", str(exc), host=host_rec.host)
            self.refresh_audit()
            QMessageBox.warning(self, "打开远程桌面", f"启动远程桌面失败：\n{exc}")

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
            host_names={h.host: h.name for h in hosts},
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
            username=qualify_windows_username(username, hosts[0].name)
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

    def _refresh_task_plan(self, *_):
        steps=[]
        if hasattr(self,"chk_backup") and self.chk_backup.isChecked(): steps.append("备份")
        if hasattr(self,"remote_enabled") and self.remote_enabled.isChecked(): steps.append("前置操作")
        if hasattr(self,"chk_distribution") and self.chk_distribution.isChecked(): steps.append("文件分发")
        if hasattr(self,"remote_enabled") and self.remote_enabled.isChecked(): steps.append("后置操作")
        if hasattr(self,"version_after_distribution") and self.version_after_distribution.isChecked(): steps.append("Version Checker")
        text=" → ".join(steps) if steps else "未选择任务"
        if hasattr(self,"task_plan_label"):
            self.task_plan_label.setText("本次执行流程：" + text)
        if hasattr(self,"btn_start"):
            self.btn_start.setEnabled(bool(steps))

    def _run_backup_standalone(self):
        hosts=self._selected_hosts()
        if not hosts:
            QMessageBox.warning(self,"备份","请先勾选至少一台目标主机。")
            return
        if self.backup_thread and self.backup_thread.isRunning():
            QMessageBox.warning(self,"备份","备份任务正在执行。")
            return

        self._save_default_winrm_credential(show_error=True)
        contexts,hint=self._remote_drive_context_for_mapping()
        if not contexts:
            QMessageBox.warning(self,"备份",hint or "当前目标主机没有可用 WinRM 凭据，无法读取远程文件。")
            return

        d=RemoteBackupBrowserDialog(
            self, contexts=contexts, preselected=getattr(self,"_standalone_backup_paths",[]),
            use_https=self.remote_https.isChecked(), port=self.remote_port.value(),
        )
        if not d.exec():
            return
        remote_paths=d.selected_paths()
        if not remote_paths:
            return
        self._standalone_backup_paths=remote_paths

        root=self.backup_root.text().strip()
        if root:
            try: validate_windows_target_path(root, allow_unc=False)
            except Exception as e:
                QMessageBox.warning(self,"备份",str(e)); return
        try: credentials=self._resolve_credentials(hosts)
        except Exception as e:
            QMessageBox.warning(self,"WinRM 凭据",str(e)); return

        names="\n".join(f"• {(h.name or h.host)} ({h.host})" for h in hosts)
        backup_display=root or r"各源文件所在目录\.fds_backup"
        path_lines="\n".join(f"• {x}" for x in remote_paths)
        msg=(f"将在 {len(hosts)} 台主机执行单独备份：\n{names}\n\n"
             f"备份文件 / 文件夹：\n{path_lines}\n\n目标主机本地备份根目录：{backup_display}\n确定继续吗？")
        if QMessageBox.question(self,"确认单独备份",msg)!=QMessageBox.Yes:
            return

        self.settings.max_concurrency=self.concurrent_spin.value(); self.settings.default_backup_root=root
        self.settings.backup_task_enabled=bool(self.chk_backup.isChecked()); self.settings.save()
        plan=RemoteActionPlan(enabled=False,use_https=self.remote_https.isChecked(),port=self.remote_port.value(),username="",password="")
        self.btn_run_backup.setEnabled(False); self.btn_start.setEnabled(False); self._set_busy(True,"备份中")
        self._append_log(f"开始单独备份任务：{len(hosts)} 台主机，目标={len(remote_paths)} 项，并发数={self.concurrent_spin.value()}。")
        self.backup_thread=BackupThread([],hosts,credentials,self.settings,plan,root,remote_paths=remote_paths)
        self.backup_thread.log.connect(self._append_log); self.backup_thread.host_status.connect(self._host_status); self.backup_thread.completed.connect(self._backup_completed); self.backup_thread.start()

    def _backup_completed(self,status,success,failed):
        self.btn_run_backup.setEnabled(True); self.btn_start.setEnabled(True); self._set_busy(False)
        self._append_log(f"备份任务完成：{status_text(status)}，成功主机={success}，失败主机={failed}。")
        self.refresh_audit()
        QMessageBox.information(self,"备份",f"状态：{status_text(status)}\n成功主机：{success}\n失败主机：{failed}")

    def _choose_version_result_root(self):
        start=self.version_local_root.text().strip() or str(Path.home())
        path=QFileDialog.getExistingDirectory(self,"选择 Version Checker 本机结果目录",start)
        if path:self.version_local_root.setText(path)

    def _save_version_checker_preferences(self):
        self.settings.version_checker_after_distribution=bool(self.version_after_distribution.isChecked())
        self.settings.version_checker_enabled=bool(self.version_after_distribution.isChecked())
        self.settings.version_checker_exe_path=self.version_exe.text().strip()
        self.settings.version_checker_workdir=self.version_workdir.text().strip()
        self.settings.version_checker_output_dir=self.version_output_dir.text().strip()
        self.settings.version_checker_save_button=self.version_save_button.text().strip() or "Save"
        self.settings.version_checker_timeout_seconds=int(self.version_timeout.value())
        self.settings.version_checker_collect_excel=bool(self.version_collect.isChecked())
        self.settings.version_checker_close_after=bool(self.version_close.isChecked())
        self.settings.version_checker_local_result_root=self.version_local_root.text().strip()
        self.settings.save()

    def _run_version_check_standalone(self):
        hosts=self._selected_hosts()
        if not hosts:
            QMessageBox.warning(self,"版本检查","请先勾选至少一台目标主机。")
            return
        self._start_version_check_for_hosts(hosts,False)

    def _start_version_check_for_hosts(self,hosts,chained=False):
        if self.version_check_thread and self.version_check_thread.isRunning():
            QMessageBox.warning(self,"版本检查","Version Checker 任务正在执行。")
            return
        exe=self.version_exe.text().strip(); workdir=self.version_workdir.text().strip(); outdir=self.version_output_dir.text().strip()
        if not exe or not workdir or not outdir:
            QMessageBox.warning(self,"版本检查","请填写 Version Checker 程序路径、工作目录和 CSV 生成目录。")
            return
        if self.version_collect.isChecked() and not self.version_local_root.text().strip():
            QMessageBox.warning(self,"版本检查","已启用 CSV 回收，请填写本机结果目录。")
            return
        self._save_version_checker_preferences(); self._save_default_winrm_credential(show_error=True)
        try: credentials=self._resolve_credentials(hosts)
        except Exception as e:
            QMessageBox.warning(self,"WinRM 凭据",str(e)); return
        if not chained:
            names="\n".join(f"• {(h.name or h.host)} ({h.host})" for h in hosts)
            msg=(f"将在 {len(hosts)} 台主机并发执行 Version Checker：\n{names}\n\n"
                 f"程序：{exe}\nCSV 目录：{outdir}\nSave 按钮：{self.version_save_button.text().strip() or 'Save'}\n"
                 f"执行步骤：Save → OK → version_checker_result.csv → {'回收到本机' if self.version_collect.isChecked() else '仅远端保留'} → {'关闭程序' if self.version_close.isChecked() else '保留程序'}\n确定继续吗？")
            if QMessageBox.question(self,"确认版本检查",msg)!=QMessageBox.Yes:return
        plan=RemoteActionPlan(enabled=False,use_https=self.remote_https.isChecked(),port=self.remote_port.value(),username="",password="",command_timeout=max(90,self.version_timeout.value()+30))
        self.btn_run_version_check.setEnabled(False); self.btn_start.setEnabled(False); self._set_busy(True,"版本检查中")
        self._append_log(f"开始 Version Checker 任务：{len(hosts)} 台主机，并发数={self.concurrent_spin.value()}。")
        self.settings.max_concurrency=self.concurrent_spin.value()
        self.version_check_thread=VersionCheckThread(hosts,credentials,self.settings,plan,exe,workdir,outdir,
            self.version_save_button.text().strip() or "Save",self.version_timeout.value(),self.version_collect.isChecked(),
            self.version_close.isChecked(),self.version_local_root.text().strip())
        # Version Checker 进度独立计算。组合任务时文件分发只占前 90%，
        # Version Checker 使用最后 10%；单独执行时则从 0% 开始。
        self._version_progress_chained=bool(chained)
        self._version_progress_total=max(1,len(hosts))
        self._version_progress_done=set()
        if chained:
            self.overall.setValue(90)
            self.overall.setFormat("90% · 文件分发完成，正在执行 Version Checker")
        else:
            self.overall.setValue(0)
            self.overall.setFormat("Version Checker %p%")
        self.version_check_thread.log.connect(self._append_log)
        self.version_check_thread.host_status.connect(self._version_check_host_status)
        self.version_check_thread.completed.connect(self._version_check_completed)
        self.version_check_thread.start()

    def _version_check_host_status(self,host,status,detail):
        # 保留原有目标主机状态更新，同时按“已完成主机数”推进版本检查进度。
        self._host_status(host,status,detail)
        if status not in {"SUCCESS","FAILED","CANCELLED"}:
            return
        done=getattr(self,"_version_progress_done",None)
        if done is None:
            return
        done.add(host)
        total=max(1,int(getattr(self,"_version_progress_total",1)))
        ratio=min(1.0,len(done)/total)
        chained=bool(getattr(self,"_version_progress_chained",False))
        base=90 if chained else 0
        # completed 信号到达前最多显示 99%，避免任务仍在收尾时提前显示 100%。
        value=min(99,base+int(ratio*(99-base)))
        self.overall.setValue(value)
        if chained:
            self.overall.setFormat(f"{value}% · Version Checker {len(done)}/{total} 台完成")
        else:
            self.overall.setFormat(f"{value}% · Version Checker {len(done)}/{total} 台完成")

    def _version_check_completed(self,status,success,failed,result_dir):
        self.btn_run_version_check.setEnabled(True); self._refresh_task_plan(); self.btn_dry_run.setEnabled(True)
        self.btn_retry_failed.setEnabled(bool(getattr(self,"_last_failed_hosts",set())))
        self.overall.setValue(100)
        self.overall.setFormat("100% · 全部完成" if status=="SUCCESS" else ("100% · 已结束（已取消）" if status=="CANCELLED" else "100% · 已结束（版本检查有失败）"))
        self._set_busy(False)
        self._append_log(f"Version Checker 完成：{status_text(status)}，成功={success}，失败={failed}。")
        self.refresh_audit()
        extra=f"\n结果目录：{result_dir}" if self.version_collect.isChecked() else ""
        QMessageBox.information(self,"版本检查",f"状态：{status_text(status)}\n成功主机：{success}\n失败主机：{failed}{extra}")

    def _validate_mappings(self,mappings,host_for_path):
        if not mappings:raise ValueError("请至少添加一条分发映射。")
        for m in mappings:
            validate_windows_target_path(m.target_path, allow_unc=False)
            if m.source_type=="LOCAL" and not Path(m.source_path).exists():raise FileNotFoundError(f"本地分发源不存在：{m.source_path}")
            if m.source_type=="SFTP" and not all([m.sftp_host,m.sftp_username,m.source_path]):raise ValueError(f"SFTP 映射配置不完整：{m.display_source()}")

    def _run_dry_run(self):
        hosts=self._selected_hosts(); mappings=self._mapping_rows(True)
        if not hosts:
            QMessageBox.warning(self,"Dry Run","请至少选择一台目标主机。")
            return
        if not self.chk_distribution.isChecked():
            QMessageBox.information(self,"Dry Run","Dry Run 当前用于验证文件分发组合任务，请先启用“文件分发”。")
            return
        if not mappings:
            QMessageBox.warning(self,"Dry Run","当前没有启用的分发映射。")
            return
        if self.dry_run_thread and self.dry_run_thread.isRunning():
            QMessageBox.information(self,"Dry Run","当前预演正在执行。")
            return
        try:
            self._validate_mappings(mappings,hosts[0].host)
            if self.chk_backup.isChecked() and self.backup_root.text().strip():
                validate_windows_target_path(self.backup_root.text().strip(), allow_unc=False)
        except Exception as e:
            QMessageBox.warning(self,"Dry Run",str(e)); return
        self._save_default_winrm_credential(show_error=True)
        try:
            credentials=self._resolve_credentials(hosts)
        except Exception as e:
            QMessageBox.warning(self,"WinRM 凭据",str(e)); return

        # Dry Run 使用当前界面的即时配置，但不会触发任何实际写入/停服/命令执行。
        self.settings.verify_sha256=self.chk_verify.isChecked()
        self.settings.backup_existing=self.chk_backup.isChecked()
        self.settings.preflight_check=self.chk_preflight.isChecked()
        self.settings.max_concurrency=self.concurrent_spin.value()
        self.settings.min_free_space_margin_mb=max(0,int(self.settings.min_free_space_margin_mb))
        remote_plan=RemoteActionPlan(
            enabled=self.remote_enabled.isChecked(), use_https=self.remote_https.isChecked(), port=self.remote_port.value(),
            username="", password="", pre_commands=split_commands(self.pre_commands.toPlainText()),
            kill_processes=split_items(self.kill_processes.text()), post_commands=split_commands(self.post_commands.toPlainText()),
            post_on_failure=self.post_on_failure.isChecked(), command_workdir=self.command_workdir.text().strip(),
            command_execution_mode=self.command_execution_mode.currentData() or "INTERACTIVE",
        )
        version_config={
            "enabled": bool(self.version_after_distribution.isChecked()),
            "exe_path": self.version_exe.text().strip(),
            "workdir": self.version_workdir.text().strip(),
            "output_dir": self.version_output_dir.text().strip(),
        }
        self._append_log(f"开始 Dry Run：{len(hosts)} 台主机，{len(mappings)} 条启用映射。")
        self._dry_run_total_hosts=max(1,len(hosts)); self._dry_run_done_hosts=0
        self.overall.setFormat("Dry Run %p%"); self.overall.setValue(0)
        self.btn_dry_run.setEnabled(False); self.btn_start.setEnabled(False); self.btn_cancel.setEnabled(True)
        self._set_busy(True,"Dry Run 预演中")
        self.dry_run_thread=DryRunThread(
            mappings,hosts,credentials,self.settings,remote_plan,
            self.backup_root.text().strip(),version_config,
        )
        self.dry_run_thread.log.connect(self._append_log)
        self.dry_run_thread.prepared.connect(self._dry_run_prepared)
        self.dry_run_thread.host_status.connect(self._dry_run_host_status)
        self.dry_run_thread.mapping_status.connect(self._mapping_status)
        self.dry_run_thread.dry_completed.connect(self._dry_run_completed)
        self.dry_run_thread.start()

    def _dry_run_prepared(self,task_id,files,total_bytes):
        self._last_dry_run_id=task_id
        self._append_log(f"{task_id}：Dry Run 清单完成，{files} 个文件，共 {human_bytes(total_bytes)}。")
        self.overall.setValue(25)

    def _dry_run_host_status(self,host,status,detail):
        self._dry_run_done_hosts=int(getattr(self,"_dry_run_done_hosts",0))+1
        total=max(1,int(getattr(self,"_dry_run_total_hosts",1)))
        self.overall.setValue(min(95,25+int(self._dry_run_done_hosts/total*70)))
        label={"SUCCESS":"预演通过","WARNING":"预演警告","FAILED":"预演失败"}.get(status,status)
        for r in range(self.target_table.rowCount()):
            if self.target_table.item(r,2).text()==host:
                cell=QTableWidgetItem(f"{label} {detail}".strip())
                cell.setToolTip(detail)
                self.target_table.setItem(r,7,cell)
                break

    def _dry_run_completed(self,summary):
        self.btn_cancel.setEnabled(False); self.btn_dry_run.setEnabled(True)
        self._refresh_task_plan(); self._set_busy(False)
        status=str(summary.get("status") or "FAILED")
        if status=="CANCELLED":
            self.overall.setFormat("Dry Run 已取消")
        elif status=="SUCCESS":
            self.overall.setValue(100); self.overall.setFormat("Dry Run 100% · 全部通过")
        elif status=="WARNING":
            self.overall.setValue(100); self.overall.setFormat("Dry Run 100% · 有警告")
        else:
            self.overall.setValue(100); self.overall.setFormat("Dry Run 100% · 有失败")
        ok=int(summary.get("success",0)); warn=int(summary.get("warning",0)); fail=int(summary.get("failed",0))
        self._append_log(f"Dry Run 完成：通过={ok}，警告={warn}，失败={fail}。")
        text=(f"Dry Run 结果\n\n通过：{ok} 台\n警告：{warn} 台\n失败：{fail} 台\n"
              f"映射：{int(summary.get('mapping_count',0))} 条\n文件：{int(summary.get('file_count',0))} 个\n"
              f"总大小：{human_bytes(int(summary.get('total_bytes',0)))}")
        if summary.get("error"):
            text += "\n\n" + str(summary.get("error"))
        if fail:
            text += "\n\n存在失败项，不建议直接开始正式分发；请先查看执行日志修复。"
            QMessageBox.warning(self,"Dry Run",text)
        elif warn:
            text += "\n\n警告项通常表示目标/备份目录尚不存在，正式任务会尝试创建；建议确认后再执行。"
            QMessageBox.information(self,"Dry Run",text)
        else:
            QMessageBox.information(self,"Dry Run",text+"\n\n当前执行计划可以进入正式分发。")

    def _retry_failed_distribution(self):
        failed_hosts=set(getattr(self,"_last_failed_hosts",set()) or set())
        if not failed_hosts:
            QMessageBox.information(self,"重试失败主机","上一轮没有可重试的失败主机。")
            return
        records={h.host:h for h in db.list_hosts()}
        hosts=[records[h] for h in sorted(failed_hosts) if h in records]
        if not hosts:
            QMessageBox.warning(self,"重试失败主机","失败主机已不在主机管理中，无法重试。")
            return
        self._start_distribution_for_hosts(hosts,retry_mode=True)

    def _export_last_task_result(self):
        task_id=str(getattr(self,"_last_task_id","") or "")
        if not task_id:
            QMessageBox.information(self,"导出任务结果","当前还没有可导出的分发任务结果。")
            return
        task=db.get_task(task_id)
        host_rows=list(db.list_task_hosts(task_id))
        file_rows=list(db.list_task_files(task_id))
        if not task:
            QMessageBox.warning(self,"导出任务结果",f"找不到任务记录：{task_id}")
            return
        host_names={h.host:(h.name or h.host) for h in db.list_hosts()}
        host_summary={r["host"]:r for r in host_rows}
        headers=[
            "Task ID","Task Status","Host Name","Host / IP","Host Status",
            "New Files","Updated Files","Skipped Files","Failed Files","Transferred Bytes",
            "Mapping ID","Source Path","Target Path","Relative Path","Action","File Size",
            "Source SHA256","Target SHA256","File Status","Error",
        ]
        rows=[]
        if file_rows:
            for f in file_rows:
                h=host_summary.get(f["host"])
                rows.append([
                    task_id,status_text(task["status"]),host_names.get(f["host"],f["host"]),f["host"],
                    status_text(h["status"]) if h else "",
                    h["new_files"] if h else 0,h["updated_files"] if h else 0,h["skipped_files"] if h else 0,
                    h["failed_files"] if h else 0,h["transferred_bytes"] if h else 0,
                    f["mapping_id"],f["source_path"],f["target_path"],f["relative_path"],f["action"],f["size"],
                    f["source_sha256"],f["target_sha256"],status_text(f["status"]),f["error_message"],
                ])
        else:
            # 认证/预检查阶段就失败时可能还没有文件明细；仍导出主机级失败原因。
            for h in host_rows:
                rows.append([
                    task_id,status_text(task["status"]),host_names.get(h["host"],h["hostname"] or h["host"]),h["host"],
                    status_text(h["status"]),h["new_files"],h["updated_files"],h["skipped_files"],h["failed_files"],h["transferred_bytes"],
                    "","","","","","","","","",h["error_message"],
                ])
        default=Path.home()/"Downloads"/f"FileDistributionStudio_Result_{task_id}.xlsx"
        path,_=QFileDialog.getSaveFileName(self,"导出任务结果",str(default),"Excel 工作簿 (*.xlsx)")
        if not path:return
        if not path.lower().endswith(".xlsx"):path += ".xlsx"
        try:
            saved=export_xlsx(path,headers,rows,sheet_name="任务结果")
        except Exception as e:
            logging.exception("Export task result failed"); QMessageBox.critical(self,"导出任务结果",str(e)); return
        audit.operation(self.settings.audit_path,"EXPORT","TASK_RESULT_XLSX","SUCCESS","已导出分发任务结果 Excel。",task_id=task_id,subject=str(saved),details={"rows":len(rows),"file_detail":bool(file_rows)})
        QMessageBox.information(self,"导出任务结果",f"已保存：\n{saved}\n\n共导出 {len(rows)} 行；包含主机结果、文件动作及 SHA256 对比。")

    def _start_distribution(self):
        return self._start_distribution_for_hosts()

    def _start_distribution_for_hosts(self, hosts_override=None, retry_mode=False):
        hosts=list(hosts_override) if hosts_override is not None else self._selected_hosts();mappings=self._mapping_rows(True)
        if not hosts:
            QMessageBox.warning(self,"执行任务","请至少选择一台目标主机。")
            return
        distribution_enabled=bool(self.chk_distribution.isChecked())
        backup_enabled=bool(self.chk_backup.isChecked())
        remote_enabled=bool(self.remote_enabled.isChecked())
        version_enabled=bool(self.version_after_distribution.isChecked())
        if not any((distribution_enabled,backup_enabled,remote_enabled,version_enabled)):
            QMessageBox.warning(self,"执行任务","请至少启用一个任务组件。")
            return
        if not distribution_enabled:
            # 无文件分发时，独立任务使用各自按钮执行，避免把“前/后”语义误套到不存在的分发步骤上。
            if backup_enabled and not remote_enabled and not version_enabled:
                self._run_backup_standalone(); return
            if version_enabled and not backup_enabled and not remote_enabled:
                self._run_version_check_standalone(); return
            QMessageBox.information(self,"执行任务","当前未启用“文件分发”。\n\n备份、程序/服务操作、Version Checker 均支持独立执行；请使用对应模块右侧的“单独执行”按钮。\n需要组合执行时，请启用“文件分发”，任务链会按：备份 → 前置操作 → 文件分发 → 后置操作 → Version Checker 执行。")
            return
        try:
            self._validate_mappings(mappings,hosts[0].host)
            if backup_enabled and self.backup_root.text().strip():
                validate_windows_target_path(self.backup_root.text().strip(), allow_unc=False)
        except Exception as e:
            QMessageBox.warning(self,"执行计划检查",str(e));return

        self.settings.distribution_enabled=distribution_enabled; self.settings.backup_task_enabled=backup_enabled; self.settings.version_checker_enabled=version_enabled
        self.settings.verify_sha256=self.chk_verify.isChecked();self.settings.backup_existing=backup_enabled;self.settings.preflight_check=self.chk_preflight.isChecked();self.settings.retry_count=self.retry_spin.value();self.settings.max_concurrency=self.concurrent_spin.value();self.settings.default_backup_root=self.backup_root.text().strip();self.settings.winrm_use_https=self.remote_https.isChecked();self.settings.winrm_port=self.remote_port.value();self.settings.winrm_command_workdir=self.command_workdir.text().strip()
        self._save_remote_action_preferences()
        self._save_version_checker_preferences()
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
             + (f"目标主机本地备份目录：{self.backup_root.text().strip() or '各目标目录\\.fds_backup'}\n" if backup_enabled else "")
             + f"校验策略：{'文件大小 + SHA256' if self.chk_verify.isChecked() else '文件大小'}\n"
             f"分发前预检查：{'启用' if self.chk_preflight.isChecked() else '关闭'}\n传输方式：WinRM\n"
             f"附加远程操作：{'已启用' if remote_plan.enabled else '未启用'}\n"
             f"分发后版本检查：{'已启用' if self.version_after_distribution.isChecked() else '未启用'}\n"
             f"CMD 工作目录：{remote_plan.command_workdir or 'WinRM 默认目录'}\n"
             f"CMD 执行方式：{'目标机登录桌面（交互式）' if remote_plan.command_execution_mode == 'INTERACTIVE' else 'WinRM 后台'}\n"
             "执行顺序：预检查 → 结束目标进程 → 分发前 CMD → 文件分发 → 分发后 CMD\n\n"
             "所有主机会执行同一套映射，但每台主机会使用自己的有效 WinRM 凭据。"
             "本操作可能结束远程进程并覆盖目标文件。\n确定继续吗？")
        if retry_mode:
            msg = "【仅重试上一轮失败主机】\n\n" + msg
        if QMessageBox.question(self,"确认执行任务",msg)!=QMessageBox.Yes:return
        self._append_log("开始重试失败主机……" if retry_mode else "开始执行所选任务链……")
        self.btn_start.setEnabled(False); self.btn_dry_run.setEnabled(False); self.btn_retry_failed.setEnabled(False); self.btn_cancel.setEnabled(True)
        self.overall.setFormat("%p%")
        self.overall.setValue(0)
        # 进度条表示完整主机工作流，而不是仅表示文件数量。文件全部上传完成时最多到 90%，
        # 每台主机的分发后 CMD / 收尾完成后逐步到 99%，只有 completed 信号到达才显示 100%。
        self._dist_progress_hosts = {h.host: 0.0 for h in hosts}
        self._dist_progress_bytes = {h.host: 0.0 for h in hosts}
        self._dist_success_hosts = set()
        self._dist_progress_finished = set()
        self._dist_progress_host_count = max(1, len(hosts))
        self._last_distribution_hosts = {h.host: h for h in hosts}
        self._dist_plan_prepared = False
        self._last_failed_hosts = set()
        self._set_busy(True,"任务执行中")
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
        self._last_task_id=self.distribution_thread.task_id
        self.btn_export_result.setEnabled(False)
        self.distribution_thread.log.connect(self._append_log);self.distribution_thread.prepared.connect(self._dist_prepared);self.distribution_thread.host_status.connect(self._host_status);self.distribution_thread.mapping_status.connect(self._mapping_status);self.distribution_thread.file_progress.connect(self._file_progress);self.distribution_thread.byte_progress.connect(self._dist_byte_progress);self.distribution_thread.completed.connect(self._dist_completed);self.distribution_thread.start()

    def _cancel_distribution(self):
        if self.dry_run_thread and self.dry_run_thread.isRunning():
            self.dry_run_thread.cancel(); self._append_log("已请求取消 Dry Run……"); return
        if self.distribution_thread and self.distribution_thread.isRunning():
            self.distribution_thread.cancel(); self._append_log("已请求取消当前任务……")
    def _clear_distribution_log(self):
        if hasattr(self, "dist_log"):
            self.dist_log.clear()

    def _append_log(self,text):self.dist_log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {text}");logging.getLogger("fds.ui").info(text)
    def _dist_prepared(self,task_id,files,total_bytes):
        self._last_task_id=task_id
        self._dist_plan_prepared=True
        self._append_log(f"{task_id}：{files} 个文件，共 {human_bytes(total_bytes)}")
        if self.overall.value() < 5:
            self.overall.setValue(5)

    def _refresh_distribution_progress(self):
        hosts = getattr(self, "_dist_progress_hosts", {})
        count = max(1, int(getattr(self, "_dist_progress_host_count", len(hosts) or 1)))
        file_ratio = sum(max(0.0, min(1.0, float(v))) for v in hosts.values()) / count
        byte_hosts = getattr(self, "_dist_progress_bytes", {})
        byte_ratio = sum(max(0.0, min(1.0, float(v))) for v in byte_hosts.values()) / count if byte_hosts else 0.0
        file_ratio = max(file_ratio, byte_ratio)
        finished_ratio = len(getattr(self, "_dist_progress_finished", set())) / count
        # 如果本次还包含 Version Checker，则为版本检查保留最后 10%：
        # 文件分发完整结束最多到 90%；否则普通分发最多到 99%，completed 后才到 100%。
        cap = 90 if bool(self.version_after_distribution.isChecked()) else 99
        file_span = max(1, cap - 14)
        value = int(5 + file_ratio * file_span + finished_ratio * 9)
        self.overall.setValue(max(self.overall.value(), min(cap, value)))

    def _host_status(self,host,status,detail):
        if self.distribution_thread and self.distribution_thread.isRunning():
            if status == "SUCCESS" and hasattr(self,"_dist_success_hosts"):
                self._dist_success_hosts.add(host)
                if hasattr(self,"_last_failed_hosts"):
                    self._last_failed_hosts.discard(host)
            elif status == "FAILED" and hasattr(self,"_last_failed_hosts"):
                self._last_failed_hosts.add(host)
        for r in range(self.target_table.rowCount()):
            if self.target_table.item(r,2).text()==host:
                cell=QTableWidgetItem(f"{status_text(status)} {detail}".strip()); cell.setToolTip(detail)
                self.target_table.setItem(r,7,cell);break
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

    def _dist_byte_progress(self, host, done_bytes, total_bytes, rel):
        if total_bytes and hasattr(self, "_dist_progress_bytes"):
            self._dist_progress_bytes[host] = max(0.0, min(1.0, float(done_bytes) / float(total_bytes)))
            self._refresh_distribution_progress()

    def _dist_completed(self,status,success,failed):
        self.btn_cancel.setEnabled(False)
        # 若成功主机还需要继续执行 Version Checker，此时整个任务尚未完成，
        # 进度保持在 90%，不能提前显示 100%。
        will_chain_version=bool(self.version_after_distribution.isChecked() and success>0 and status!="CANCELLED")
        if will_chain_version:
            self.overall.setValue(90)
            self.overall.setFormat("90% · 文件分发完成，等待 Version Checker")
        else:
            self.overall.setValue(100)
            self.overall.setFormat("100% · 已完成" if status=="SUCCESS" else ("100% · 已结束（已取消）" if status=="CANCELLED" else "100% · 已结束（有失败）"))
        # 如果任务在生成正式分发计划前就整体失败（例如 WinRM 前置认证未全部通过），
        # 则这一轮所有目标主机都没有真正完成分发，重试时应包含整批主机。
        all_hosts=set(getattr(self,"_last_distribution_hosts",{}).keys())
        succeeded=set(getattr(self,"_dist_success_hosts",set()))
        if failed:
            if not getattr(self,"_dist_plan_prepared",False):
                self._last_failed_hosts=set(all_hosts)
            else:
                self._last_failed_hosts=set(all_hosts)-succeeded
        else:
            self._last_failed_hosts=set()
        self.btn_retry_failed.setEnabled(bool(self._last_failed_hosts) and status!="CANCELLED")
        task_id=str(getattr(self,"_last_task_id","") or "")
        self.btn_export_result.setEnabled(bool(task_id and db.get_task(task_id)))
        self._append_log(f"任务完成：{status_text(status)}，成功主机={success}，失败主机={failed}")
        if self._last_failed_hosts:
            self._append_log("可直接点击“重试失败主机”，仅重新执行：" + ", ".join(sorted(self._last_failed_hosts)))
        self.refresh_history(); self.refresh_audit(); QApplication.processEvents()
        if self.version_after_distribution.isChecked() and success>0 and status!="CANCELLED":
            records=getattr(self,"_last_distribution_hosts",{})
            hosts=[records[h] for h in sorted(succeeded) if h in records]
            if hosts:
                self._append_log(f"文件分发结束，按当前设置继续执行 Version Checker：{len(hosts)} 台成功主机。")
                self._start_version_check_for_hosts(hosts,True)
                return
        self._refresh_task_plan(); self.btn_dry_run.setEnabled(True); self._set_busy(False)
        QMessageBox.information(self,"文件分发",f"状态：{status_text(status)}\n成功主机：{success}\n失败主机：{failed}\n\n详细记录请查看“分发历史”和“审计日志”。")

    # ---------- Inventory ----------
    def _apply_table_filter(self, table: QTableWidget, query: str, count_label: QLabel | None = None):
        """Case-insensitive contains filter across all textual cells; preserves checkbox state."""
        q=(query or "").strip().casefold()
        visible=0
        for r in range(table.rowCount()):
            values=[]
            for c in range(table.columnCount()):
                item=table.item(r,c)
                if item:
                    values.append(item.text())
            matched=(not q) or (q in " ".join(values).casefold())
            table.setRowHidden(r, not matched)
            if matched:
                visible += 1
        if count_label is not None:
            if table is getattr(self, "target_table", None):
                selected = 0
                for r in range(table.rowCount()):
                    chk = table.cellWidget(r, 0)
                    if chk and chk.isChecked():
                        selected += 1
                count_label.setText(f"显示 {visible} / {table.rowCount()} 台 · 已选 {selected} 台")
            else:
                count_label.setText(f"显示 {visible} / {table.rowCount()} 台")
        return visible

    def _apply_target_filter(self, _text=None):
        if not hasattr(self, "target_table"):
            return
        query=self.target_filter_edit.text() if hasattr(self, "target_filter_edit") else ""
        self._apply_table_filter(self.target_table, query, getattr(self, "target_filter_count", None))

    def _apply_inventory_filter(self, _text=None):
        if not hasattr(self, "host_table"):
            return
        query=self.inventory_filter_edit.text() if hasattr(self, "inventory_filter_edit") else ""
        self._apply_table_filter(self.host_table, query, getattr(self, "inventory_filter_count", None))

    def _check_host_environment(self, hosts: list[HostRecord], source_title: str = "主机"):
        """Read-only system-time/firewall inspection over WinRM."""
        hosts=list(hosts or [])
        if not hosts:
            QMessageBox.information(self, "ADMS 部署前检查", f"请先在“{source_title}”中勾选或选择至少一台主机。")
            return
        try:
            credentials=self._resolve_credentials(hosts)
        except Exception as exc:
            QMessageBox.warning(self, "ADMS 部署前检查", str(exc))
            return
        if getattr(self, "host_env_thread", None) and self.host_env_thread.isRunning():
            QMessageBox.information(self, "ADMS 部署前检查", "已有环境检查正在执行，请等待完成。")
            return
        self._append_log(f"开始 ADMS 部署前检查：{len(hosts)} 台；检查 WinRM、时间偏差、Private/Public 防火墙，并读取诊断信息。")
        self._env_check_results=[]
        self.host_env_thread=HostEnvironmentCheckThread(
            hosts, credentials, use_https=self.remote_https.isChecked(), port=self.remote_port.value(),
            max_workers=min(8, max(1, len(hosts))))
        self.host_env_thread.result.connect(self._host_environment_result)
        self.host_env_thread.completed.connect(self._host_environment_completed)
        self.host_env_thread.start()

    @staticmethod
    def _environment_firewall_states(data: dict) -> dict[str, str]:
        """Return readable Domain/Private/Public firewall states."""
        states={"Domain":"未知", "Private":"未知", "Public":"未知"}
        profiles=data.get("firewall_profiles") or []
        if isinstance(profiles, dict):
            profiles=[profiles]
        for item in profiles:
            name=str(item.get("Name", "") or "").strip()
            if name in states:
                states[name]="开启" if bool(item.get("Enabled")) else "关闭"
        return states

    @classmethod
    def _environment_alarm_state(cls, data: dict) -> tuple[bool, bool, bool, dict[str, str]]:
        """Alarm when either |clock drift| > 2 min OR Private/Public are not both disabled."""
        drift=abs(float(data.get("time_drift_seconds", 0.0) or 0.0))
        states=cls._environment_firewall_states(data)
        time_bad=drift > 120.0
        firewall_bad=not (states.get("Private") == "关闭" and states.get("Public") == "关闭")
        return bool(time_bad or firewall_bad), time_bad, firewall_bad, states

    def _host_environment_result(self, host: str, ok: bool, data):
        data=dict(data or {}) if isinstance(data, dict) else {"error": str(data or "")}
        data["host"]=host; data["ok"]=bool(ok)
        self._env_check_results.append(data)
        if ok:
            drift=float(data.get("time_drift_seconds",0.0) or 0.0)
            tz=data.get("time_zone", "未知")
            svc=data.get("time_service", "未知")
            alarm,time_bad,firewall_bad,states=self._environment_alarm_state(data)
            level="告警" if alarm else "正常"
            self._append_log(
                f"[{host}] ADMS 部署前检查：{level}；时间偏差={drift:+.1f}s "
                f"({'超过2分钟' if time_bad else '未超过2分钟'})；时区={tz}；W32Time={svc}；"
                f"防火墙 Domain={states['Domain']}，Private={states['Private']}，Public={states['Public']}；"
                f"防火墙条件={'异常' if firewall_bad else '满足要求'}。"
            )
        else:
            self._append_log(f"[{host}] ADMS 部署前检查失败：{data.get('error','未知错误')}")

    def _host_environment_completed(self, success: int, failed: int):
        rows=list(getattr(self,"_env_check_results",[]) or [])
        rows.sort(key=lambda x: x.get("host", ""))
        lines=[]; alarms=[]
        for x in rows:
            host=x.get("host","")
            if not x.get("ok"):
                lines.append(f"✗ {host}\n  检查失败：{x.get('error','未知错误')}")
                continue
            drift=float(x.get("time_drift_seconds",0.0) or 0.0)
            tz=x.get("time_zone","未知")
            svc=x.get("time_service","未知")
            alarm,time_bad,firewall_bad,states=self._environment_alarm_state(x)
            mark="⚠" if alarm else "✓"
            if alarm:
                alarms.append(host)
            lines.append(
                f"{mark} {host}  {'告警' if alarm else '正常'}\n"
                f"  时间偏差：{drift:+.1f}s（{'超过2分钟' if time_bad else '未超过2分钟'}）\n"
                f"  时区：{tz}\n"
                f"  Windows Time：{svc}\n"
                f"  防火墙：Domain={states['Domain']}，Private={states['Private']}，Public={states['Public']}\n"
                f"  ADMS 前置结论：{'不满足（告警）' if alarm else '满足'}；要求：时间偏差≤2分钟，Private=关闭，Public=关闭"
            )
        summary=f"ADMS 部署前检查完成：成功 {success} 台，失败 {failed} 台。"
        summary += f"\n满足前置条件 {max(0, success-len(alarms))} 台，告警 {len(alarms)} 台。规则：时间偏差必须 ≤ 2 分钟，且 Private/Public 防火墙必须全部关闭；任一条件不满足即告警。"
        # 使用固定的中等尺寸结果窗口，避免 QMessageBox 的 DetailedText
        # 因长行 sizeHint 把窗口横向撑到接近全屏。结果直接展示，无需再点“Show Details”。
        dlg=QDialog(self)
        dlg.setWindowTitle("ADMS 部署前检查")
        dlg.setModal(True)
        dlg.resize(760, 560)
        dlg.setMinimumSize(680, 480)
        dlg.setMaximumWidth(860)

        layout=QVBoxLayout(dlg)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        summary_label=QLabel(summary)
        summary_label.setWordWrap(True)
        summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        summary_label.setStyleSheet("font-size:13px; font-weight:600; padding:4px 2px 8px 2px;")
        layout.addWidget(summary_label)

        detail=QPlainTextEdit()
        detail.setReadOnly(True)
        detail.setPlainText("\n\n".join(lines) if lines else "没有结果。")
        detail.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        detail.setStyleSheet("font-family:Consolas, 'Microsoft YaHei UI'; font-size:12px;")
        layout.addWidget(detail, 1)

        buttons=QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()


    # ---------- Remote files (independent manual tool) ----------
    def _build_remote_files_page(self):
        canvas, root = self._page_canvas()
        c, l = card(
            "远程文件",
            "独立的单机人工文件管理工具：通过 WinRM 浏览目标 Windows 文件系统，并在本机与远程之间上传/下载。"
            "它不会加入文件分发、备份、Dry Run、Version Checker 或任何组合任务。",
        )

        top = QHBoxLayout(); top.setSpacing(8)
        top.addWidget(QLabel("目标主机"))
        self.remote_file_host_combo = QComboBox()
        self.remote_file_host_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.remote_file_host_combo.currentIndexChanged.connect(self._remote_file_host_changed)
        top.addWidget(self.remote_file_host_combo, 1)
        self.remote_file_connect_btn = QPushButton("连接 / 刷新")
        self.remote_file_connect_btn.setIcon(app_icon("refresh"))
        self.remote_file_connect_btn.clicked.connect(self._remote_file_connect)
        top.addWidget(self.remote_file_connect_btn)
        self.remote_file_status = QLabel("未连接")
        self.remote_file_status.setObjectName("Muted")
        top.addWidget(self.remote_file_status)
        l.addLayout(top)

        help_label = QLabel(
            "一次只连接 1 台主机，使用“文件分发 → Windows 目标主机”中相同的有效 WinRM 凭据。"
            "左侧是本机，右侧是远程主机；可把 Windows 资源管理器中的文件/文件夹直接拖到右侧上传。"
        )
        help_label.setWordWrap(True); help_label.setObjectName("Muted"); l.addWidget(help_label)

        panes = QHBoxLayout(); panes.setSpacing(12)

        # Local pane
        local_box, local_l = card("本机", "文件和文件夹都可以选择；Ctrl/Shift 可多选，双击文件夹进入，也可以直接拖到右侧上传。")
        local_nav = QHBoxLayout(); local_nav.setSpacing(6)
        self.remote_local_pc_btn = QPushButton("此电脑"); self.remote_local_pc_btn.setToolTip("显示本机所有可用磁盘。")
        self.remote_local_pc_btn.clicked.connect(self._remote_local_show_drives)
        self.remote_local_up_btn = QPushButton("上一级"); self.remote_local_up_btn.clicked.connect(self._remote_local_up)
        self.remote_local_path = QLineEdit()
        self.remote_local_path.setClearButtonEnabled(False)
        self.remote_local_path.returnPressed.connect(self._remote_local_go)
        self.remote_local_browse_btn = QPushButton("选择…")
        self.remote_local_browse_btn.setToolTip("选择本机文件或文件夹。文件可多选；选中后会在左侧列表中定位并选中。")
        choose_menu = QMenu(self.remote_local_browse_btn)
        choose_files_action = choose_menu.addAction("选择文件…")
        choose_files_action.triggered.connect(self._remote_local_choose_files)
        choose_dir_action = choose_menu.addAction("选择文件夹…")
        choose_dir_action.triggered.connect(self._remote_local_choose_directory)
        self.remote_local_browse_btn.setMenu(choose_menu)
        self.remote_local_refresh_btn = QPushButton("刷新"); self.remote_local_refresh_btn.clicked.connect(self._refresh_remote_local_table)
        local_nav.addWidget(self.remote_local_pc_btn); local_nav.addWidget(self.remote_local_up_btn); local_nav.addWidget(self.remote_local_path, 1)
        local_nav.addWidget(self.remote_local_browse_btn); local_nav.addWidget(self.remote_local_refresh_btn)
        local_l.addLayout(local_nav)
        self.remote_local_table = LocalFileTable(0, 4)
        self.remote_local_table.setHorizontalHeaderLabels(["名称", "类型", "大小", "修改时间"])
        self.remote_local_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.remote_local_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.remote_local_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.remote_local_table.setDragEnabled(True)
        self.remote_local_table.verticalHeader().setVisible(False)
        configure_full_content_table(self.remote_local_table)
        self.remote_local_table.cellDoubleClicked.connect(self._remote_local_double_clicked)
        local_l.addWidget(self.remote_local_table, 1)
        panes.addWidget(local_box, 1)

        # Transfer controls
        controls = QVBoxLayout(); controls.setSpacing(8); controls.addStretch(1)
        self.remote_upload_btn = QPushButton("上传  →")
        self.remote_upload_btn.setToolTip("把左侧选中的本地文件/目录上传到右侧当前远程目录。也可以直接把资源管理器文件拖到右侧。")
        self.remote_upload_btn.clicked.connect(self._remote_upload_selected)
        self.remote_download_btn = QPushButton("←  下载")
        self.remote_download_btn.setToolTip("把右侧选中的远程文件/目录下载到左侧当前本机目录。")
        self.remote_download_btn.clicked.connect(self._remote_download_selected)
        controls.addWidget(self.remote_upload_btn); controls.addWidget(self.remote_download_btn); controls.addStretch(1)
        panes.addLayout(controls)

        # Remote pane
        remote_box, remote_l = card("远程主机", "双击文件夹进入；支持上传、下载、新建目录、重命名和删除。")
        remote_nav = QHBoxLayout(); remote_nav.setSpacing(6)
        self.remote_file_pc_btn = QPushButton("远程电脑")
        self.remote_file_pc_btn.setToolTip("返回远程电脑首页，显示常用位置和所有可用磁盘。")
        self.remote_file_pc_btn.clicked.connect(self._remote_file_connect)
        self.remote_file_up_btn = QPushButton("上一级"); self.remote_file_up_btn.clicked.connect(self._remote_file_up)
        self.remote_file_path = QLineEdit()
        self.remote_file_path.setPlaceholderText(r"连接后显示远程路径，例如 D:\ADMS\bin")
        self.remote_file_path.returnPressed.connect(self._remote_file_go)
        self.remote_file_refresh_btn = QPushButton("刷新"); self.remote_file_refresh_btn.clicked.connect(self._remote_file_refresh)
        remote_nav.addWidget(self.remote_file_pc_btn); remote_nav.addWidget(self.remote_file_up_btn); remote_nav.addWidget(self.remote_file_path, 1); remote_nav.addWidget(self.remote_file_refresh_btn)
        remote_l.addLayout(remote_nav)
        self.remote_file_table = RemoteFileDropTable(0, 4)
        self.remote_file_table.setHorizontalHeaderLabels(["名称", "类型", "大小 / 可用", "修改时间"])
        self.remote_file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.remote_file_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.remote_file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.remote_file_table.verticalHeader().setVisible(False)
        configure_full_content_table(self.remote_file_table)
        self.remote_file_table.cellDoubleClicked.connect(self._remote_file_double_clicked)
        self.remote_file_table.localPathsDropped.connect(self._remote_file_drop_upload)
        remote_l.addWidget(self.remote_file_table, 1)
        remote_actions = QHBoxLayout(); remote_actions.setSpacing(6)
        self.remote_mkdir_btn = QPushButton("新建目录"); self.remote_mkdir_btn.clicked.connect(self._remote_file_mkdir)
        self.remote_rename_btn = QPushButton("重命名"); self.remote_rename_btn.clicked.connect(self._remote_file_rename)
        self.remote_delete_btn = QPushButton("删除"); self.remote_delete_btn.clicked.connect(self._remote_file_delete)
        remote_actions.addWidget(self.remote_mkdir_btn); remote_actions.addWidget(self.remote_rename_btn); remote_actions.addWidget(self.remote_delete_btn); remote_actions.addStretch(1)
        remote_l.addLayout(remote_actions)
        panes.addWidget(remote_box, 1)
        l.addLayout(panes, 1)

        transfer = QHBoxLayout(); transfer.setSpacing(8)
        self.remote_file_progress = QProgressBar(); self.remote_file_progress.setRange(0,100); self.remote_file_progress.setValue(0)
        self.remote_file_progress.setFormat("就绪")
        self.remote_file_clear_log_btn = QPushButton("清空日志"); self.remote_file_clear_log_btn.clicked.connect(lambda: self.remote_file_log.clear())
        transfer.addWidget(self.remote_file_progress, 1); transfer.addWidget(self.remote_file_clear_log_btn)
        l.addLayout(transfer)
        self.remote_file_log = QPlainTextEdit(); self.remote_file_log.setReadOnly(True); self.remote_file_log.setMaximumHeight(130)
        l.addWidget(self.remote_file_log)

        root.addWidget(c, 1)
        self._remote_file_thread = None
        self._remote_file_connected_host = ""
        self._refresh_remote_file_hosts()
        initial_local = (getattr(self.settings, "remote_file_local_path", "") or "").strip()
        if initial_local and Path(initial_local).is_dir():
            self.remote_local_path.setText(initial_local)
            self._refresh_remote_local_table()
        else:
            self._remote_local_show_drives()
        return canvas

    def _remote_file_append_log(self, text: str):
        if hasattr(self, "remote_file_log"):
            self.remote_file_log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {text}")

    def _refresh_remote_file_hosts(self):
        if not hasattr(self, "remote_file_host_combo"):
            return
        hosts = db.list_hosts()
        current_host = ""
        data = self.remote_file_host_combo.currentData()
        if isinstance(data, HostRecord): current_host = data.host
        desired = current_host or getattr(self.settings, "remote_file_last_host", "") or ""
        self.remote_file_host_combo.blockSignals(True)
        self.remote_file_host_combo.clear()
        chosen = -1
        for i, h in enumerate(hosts):
            label = f"{h.name}  ({h.host})" if h.name and h.name != h.host else h.host
            self.remote_file_host_combo.addItem(label, h)
            if h.host == desired: chosen = i
        if chosen >= 0: self.remote_file_host_combo.setCurrentIndex(chosen)
        self.remote_file_host_combo.blockSignals(False)
        self.remote_file_status.setText(f"{len(hosts)} 台可选 · 未连接" if hosts else "暂无已保存主机")

    def _remote_file_current_host(self):
        if not hasattr(self, "remote_file_host_combo"):
            return None
        obj = self.remote_file_host_combo.currentData()
        return obj if isinstance(obj, HostRecord) else None

    def _remote_file_context(self):
        host = self._remote_file_current_host()
        if not host:
            raise ValueError("请先选择一台目标主机。")
        self._save_default_winrm_credential(show_error=False)
        creds = self._resolve_credentials([host])
        username, password, source = creds[host.host]
        return host, {"host": host.host, "name": host.name, "username": username, "password": password, "credential_source": source}

    def _remote_file_host_changed(self, *_):
        host = self._remote_file_current_host()
        self._remote_file_connected_host = ""
        self.remote_file_table.setRowCount(0)
        self.remote_file_path.clear()
        if host:
            self.settings.remote_file_last_host = host.host; self.settings.save()
            self.remote_file_status.setText(f"{host.host} · 未连接")

    def _remote_file_set_busy(self, busy: bool, text=""):
        widgets = [self.remote_file_host_combo, self.remote_file_connect_btn, self.remote_file_pc_btn, self.remote_file_up_btn,
                   self.remote_file_path, self.remote_file_refresh_btn, self.remote_upload_btn,
                   self.remote_download_btn, self.remote_mkdir_btn, self.remote_rename_btn, self.remote_delete_btn]
        for w in widgets:
            w.setEnabled(not busy)
        if text: self.remote_file_status.setText(text)

    def _start_remote_file_op(self, operation: str, **kwargs):
        if self._remote_file_thread and self._remote_file_thread.isRunning():
            QMessageBox.information(self, "远程文件", "已有远程文件操作正在执行，请等待完成。")
            return False
        try:
            host, ctx = self._remote_file_context()
        except Exception as exc:
            QMessageBox.warning(self, "远程文件", str(exc)); return False
        self._remote_file_set_busy(True, f"{host.host} · 正在执行 {operation} …")
        self.remote_file_progress.setValue(0); self.remote_file_progress.setFormat("处理中…")
        self._remote_file_thread = RemoteFileOperationThread(
            ctx, operation, use_https=self.remote_https.isChecked(), port=self.remote_port.value(), **kwargs)
        self._remote_file_thread.progress.connect(self._remote_file_progress_changed)
        self._remote_file_thread.byte_progress.connect(self._remote_file_byte_progress_changed)
        self._remote_file_thread.log.connect(self._remote_file_append_log)
        self._remote_file_thread.completed.connect(self._remote_file_op_done)
        self._remote_file_thread.start()
        return True

    def _remote_file_progress_changed(self, done: int, total: int, name: str):
        total=max(1,int(total)); done=max(0,int(done)); pct=min(99,int(done*100/total))
        self.remote_file_progress.setValue(pct); self.remote_file_progress.setFormat(f"{pct}% · {done}/{total} · {name}")

    def _remote_file_byte_progress_changed(self, done: int, total: int, name: str):
        total=max(1,int(total)); done=max(0,min(int(done),total)); pct=min(99,int(done*100/total))
        self.remote_file_progress.setValue(pct); self.remote_file_progress.setFormat(f"{pct}% · {human_bytes(done)}/{human_bytes(total)} · {name}")

    def _remote_file_connect(self):
        self._start_remote_file_op("LIST", remote_path="")

    def _remote_file_refresh(self):
        path=self.remote_file_path.text().strip().replace("/", "\\")
        self._start_remote_file_op("LIST", remote_path=path)

    def _remote_file_go(self):
        path=self.remote_file_path.text().strip().replace("/", "\\")
        if path and len(path)==2 and path[1]==':': path += "\\"
        self._start_remote_file_op("LIST", remote_path=path)

    def _remote_file_up(self):
        import ntpath
        path=self.remote_file_path.text().strip().replace("/", "\\")
        if not path:
            self._remote_file_connect(); return
        drive, _ = ntpath.splitdrive(path)
        root=drive+"\\" if drive else ""
        if path.rstrip("\\").lower()==drive.lower():
            self._remote_file_connect(); return
        parent=ntpath.dirname(path.rstrip("\\")) or root
        self._start_remote_file_op("LIST", remote_path=parent)

    def _remote_file_double_clicked(self, row: int, _column: int):
        item=self.remote_file_table.item(row,0)
        if not item: return
        path=str(item.data(Qt.UserRole+401) or "")
        is_dir=bool(item.data(Qt.UserRole+402))
        if is_dir and path:
            self._start_remote_file_op("LIST", remote_path=path)

    def _remote_file_populate(self, payload: dict):
        self.remote_file_table.setRowCount(0)
        kind=payload.get("kind")
        if kind=="drives":
            self.remote_file_path.clear()
            for folder in payload.get("known_folders") or []:
                r=self.remote_file_table.rowCount(); self.remote_file_table.insertRow(r)
                name=str(folder.get("name", "") or "")
                path=str(folder.get("path", "") or "")
                item=QTableWidgetItem(name)
                item.setData(Qt.UserRole+401,path); item.setData(Qt.UserRole+402,True); self.remote_file_table.setItem(r,0,item)
                self.remote_file_table.setItem(r,1,QTableWidgetItem("常用位置"))
                self.remote_file_table.setItem(r,2,QTableWidgetItem("—"))
                self.remote_file_table.setItem(r,3,QTableWidgetItem(path))
            for d in payload.get("data") or []:
                r=self.remote_file_table.rowCount(); self.remote_file_table.insertRow(r)
                name=d.get("name","")
                item=QTableWidgetItem(f"{name}  {d.get('volume_label','')}".strip())
                item.setData(Qt.UserRole+401,name); item.setData(Qt.UserRole+402,True); self.remote_file_table.setItem(r,0,item)
                self.remote_file_table.setItem(r,1,QTableWidgetItem("磁盘"))
                self.remote_file_table.setItem(r,2,QTableWidgetItem(f"可用 {human_bytes(int(d.get('free_size',0) or 0))} / {human_bytes(int(d.get('total_size',0) or 0))}"))
                self.remote_file_table.setItem(r,3,QTableWidgetItem(""))
        else:
            path=str(payload.get("path") or "")
            self.remote_file_path.setText(path)
            self.settings.remote_file_remote_path=path; self.settings.save()
            for e in payload.get("data") or []:
                r=self.remote_file_table.rowCount(); self.remote_file_table.insertRow(r)
                item=QTableWidgetItem(str(e.get("name", "")))
                item.setData(Qt.UserRole+401,e.get("path", "")); item.setData(Qt.UserRole+402,bool(e.get("is_dir"))); self.remote_file_table.setItem(r,0,item)
                self.remote_file_table.setItem(r,1,QTableWidgetItem("文件夹" if e.get("is_dir") else "文件"))
                self.remote_file_table.setItem(r,2,QTableWidgetItem("—" if e.get("is_dir") else human_bytes(int(e.get("size",0) or 0))))
                self.remote_file_table.setItem(r,3,QTableWidgetItem(str(e.get("modified", ""))))
        fit_full_content_table(self.remote_file_table)

    def _remote_file_op_done(self, payload):
        payload=dict(payload or {})
        self._remote_file_set_busy(False)
        if not payload.get("ok"):
            self.remote_file_progress.setValue(0); self.remote_file_progress.setFormat("失败")
            self.remote_file_status.setText("操作失败")
            error=str(payload.get("error") or "未知错误")
            self._remote_file_append_log("失败："+error)
            QMessageBox.warning(self, "远程文件", error)
            return
        kind=payload.get("kind")
        host=self._remote_file_current_host()
        if kind in ("drives","entries"):
            self._remote_file_connected_host = host.host if host else ""
            self._remote_file_populate(payload)
            self.remote_file_status.setText(f"{self._remote_file_connected_host} · 已连接")
            self.remote_file_progress.setValue(100); self.remote_file_progress.setFormat("已连接")
            self._remote_file_append_log(f"[{self._remote_file_connected_host}] 目录读取完成。")
            return
        self.remote_file_progress.setValue(100); self.remote_file_progress.setFormat("100% · 完成")
        self.remote_file_status.setText(f"{host.host if host else ''} · 已完成")
        self._remote_file_append_log(f"{kind} 完成。")
        if kind in ("upload","mkdir","delete","rename"):
            QTimer.singleShot(80, self._remote_file_refresh)
        if kind=="download":
            self._refresh_remote_local_table()

    def _remote_local_go(self):
        raw=self.remote_local_path.text().strip()
        if raw in ("", "此电脑"):
            self._remote_local_show_drives(); return
        path=Path(raw).expanduser()
        if not path.is_dir():
            QMessageBox.warning(self, "本机目录", f"目录不存在：\n{path}"); return
        self.remote_local_path.setText(str(path)); self._refresh_remote_local_table()

    def _remote_local_show_drives(self):
        self.remote_local_path.setText("此电脑")
        self.remote_local_table.setRowCount(0)

        # Windows 常用目录使用 Qt/系统解析后的真实路径，不硬编码 C:\Users\<name>。
        # 因此桌面/下载等被 OneDrive 或组策略重定向后仍可正确进入。
        common_locations = [
            ("桌面", QStandardPaths.DesktopLocation),
            ("下载", QStandardPaths.DownloadLocation),
            ("文档", QStandardPaths.DocumentsLocation),
            ("图片", QStandardPaths.PicturesLocation),
            ("音乐", QStandardPaths.MusicLocation),
            ("视频", QStandardPaths.MoviesLocation),
        ]
        seen=set()
        for label, location in common_locations:
            path=QStandardPaths.writableLocation(location)
            if not path or not os.path.isdir(path):
                continue
            norm=os.path.normcase(os.path.normpath(path))
            if norm in seen:
                continue
            seen.add(norm)
            r=self.remote_local_table.rowCount(); self.remote_local_table.insertRow(r)
            item=QTableWidgetItem(label)
            item.setData(LocalFileTable.ROLE_PATH, path); item.setData(Qt.UserRole+302, True); item.setData(Qt.UserRole+303, False)
            item.setToolTip(path)
            self.remote_local_table.setItem(r,0,item)
            self.remote_local_table.setItem(r,1,QTableWidgetItem("常用位置"))
            self.remote_local_table.setItem(r,2,QTableWidgetItem("—"))
            self.remote_local_table.setItem(r,3,QTableWidgetItem(path))

        drives=[]
        if os.name == "nt":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                root=f"{letter}:\\"
                try:
                    if os.path.exists(root):
                        drives.append(root)
                except Exception:
                    pass
        else:
            drives=[os.path.abspath(os.sep)]
        for drive in drives:
            try:
                import shutil
                usage=shutil.disk_usage(drive)
                size_text=f"可用 {human_bytes(usage.free)} / {human_bytes(usage.total)}"
            except Exception:
                size_text="—"
            r=self.remote_local_table.rowCount(); self.remote_local_table.insertRow(r)
            item=QTableWidgetItem(drive)
            item.setData(LocalFileTable.ROLE_PATH, drive); item.setData(Qt.UserRole+302, True); item.setData(Qt.UserRole+303, True)
            self.remote_local_table.setItem(r,0,item)
            self.remote_local_table.setItem(r,1,QTableWidgetItem("磁盘"))
            self.remote_local_table.setItem(r,2,QTableWidgetItem(size_text))
            self.remote_local_table.setItem(r,3,QTableWidgetItem(""))
        fit_full_content_table(self.remote_local_table)

    def _remote_local_choose_files(self):
        start=self.remote_local_path.text().strip()
        if start == "此电脑" or not Path(start).is_dir(): start=str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传的本机文件（可多选）", start, "所有文件 (*.*)")
        if paths:
            self._remote_local_reveal_and_select(paths)

    def _remote_local_choose_directory(self):
        start=self.remote_local_path.text().strip()
        if start == "此电脑" or not Path(start).is_dir(): start=str(Path.home())
        path=QFileDialog.getExistingDirectory(self, "选择要上传的本机文件夹", start)
        if path:
            self._remote_local_reveal_and_select([path])

    def _remote_local_reveal_and_select(self, paths):
        paths=[str(Path(p)) for p in paths if p]
        if not paths: return
        parents={str(Path(p).parent) for p in paths}
        parent=next(iter(parents)) if len(parents)==1 else str(Path(paths[0]).parent)
        self.remote_local_path.setText(parent)
        self._refresh_remote_local_table()
        wanted={os.path.normcase(os.path.normpath(p)) for p in paths}
        self.remote_local_table.clearSelection()
        first_row=None
        for r in range(self.remote_local_table.rowCount()):
            item=self.remote_local_table.item(r,0)
            item_path=str(item.data(LocalFileTable.ROLE_PATH) or "") if item else ""
            if item_path and os.path.normcase(os.path.normpath(item_path)) in wanted:
                idx=self.remote_local_table.model().index(r, 0)
                self.remote_local_table.selectionModel().select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                if first_row is None: first_row=r
        if first_row is not None:
            self.remote_local_table.scrollToItem(self.remote_local_table.item(first_row,0), QAbstractItemView.PositionAtCenter)

    def _remote_local_browse(self):
        # 兼容旧调用：默认进入“选择文件夹”。
        self._remote_local_choose_directory()

    def _remote_local_up(self):
        raw=self.remote_local_path.text().strip()
        if raw in ("", "此电脑"):
            self._remote_local_show_drives(); return
        p=Path(raw)
        if os.name == "nt" and p.parent == p:
            self._remote_local_show_drives(); return
        # Windows 盘符根目录（C:\）的上一级是“此电脑”。
        if os.name == "nt" and len(str(p)) <= 3 and str(p)[1:2] == ":":
            self._remote_local_show_drives(); return
        parent=p.parent
        if parent==p:
            self._remote_local_show_drives(); return
        self.remote_local_path.setText(str(parent)); self._refresh_remote_local_table()

    def _refresh_remote_local_table(self):
        raw=self.remote_local_path.text().strip()
        if raw == "此电脑":
            self._remote_local_show_drives(); return
        p=Path(raw or Path.home()).expanduser()
        if not p.is_dir(): return
        self.settings.remote_file_local_path=str(p); self.settings.save()
        self.remote_local_table.setRowCount(0)
        try:
            entries=sorted(p.iterdir(), key=lambda x:(not x.is_dir(), x.name.lower()))
        except Exception as exc:
            self._remote_file_append_log(f"读取本机目录失败：{exc}"); return
        for entry in entries:
            try:
                stat=entry.stat(); is_dir=entry.is_dir()
                r=self.remote_local_table.rowCount(); self.remote_local_table.insertRow(r)
                item=QTableWidgetItem(entry.name); item.setData(LocalFileTable.ROLE_PATH,str(entry)); item.setData(Qt.UserRole+302,is_dir); item.setData(Qt.UserRole+303,False); self.remote_local_table.setItem(r,0,item)
                self.remote_local_table.setItem(r,1,QTableWidgetItem("文件夹" if is_dir else "文件"))
                self.remote_local_table.setItem(r,2,QTableWidgetItem("—" if is_dir else human_bytes(stat.st_size)))
                self.remote_local_table.setItem(r,3,QTableWidgetItem(datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")))
            except Exception:
                continue
        fit_full_content_table(self.remote_local_table)

    def _remote_local_double_clicked(self, row: int, _column: int):
        item=self.remote_local_table.item(row,0)
        if item and bool(item.data(Qt.UserRole+302)):
            path=str(item.data(LocalFileTable.ROLE_PATH) or "")
            if path:
                self.remote_local_path.setText(path); self._refresh_remote_local_table()

    def _selected_local_paths(self):
        rows=sorted({idx.row() for idx in self.remote_local_table.selectionModel().selectedRows()})
        out=[]
        for r in rows:
            item=self.remote_local_table.item(r,0); path=str(item.data(LocalFileTable.ROLE_PATH) or "") if item else ""
            if path: out.append(path)
        return out

    def _selected_remote_paths(self):
        rows=sorted({idx.row() for idx in self.remote_file_table.selectionModel().selectedRows()})
        out=[]
        for r in rows:
            item=self.remote_file_table.item(r,0); path=str(item.data(Qt.UserRole+401) or "") if item else ""
            if path: out.append(path)
        return out

    def _remote_upload_selected(self):
        paths=self._selected_local_paths()
        if not paths:
            QMessageBox.information(self,"远程文件","请先在左侧选择要上传的文件或文件夹。") ; return
        self._remote_file_drop_upload(paths)

    def _remote_file_drop_upload(self, paths):
        remote_dir=self.remote_file_path.text().strip().replace("/", "\\")
        if not remote_dir:
            QMessageBox.information(self,"远程文件","请先进入右侧某个远程磁盘/目录，再上传文件。") ; return
        names="\n".join(Path(p).name for p in list(paths)[:8])
        if len(paths)>8: names += f"\n……另有 {len(paths)-8} 项"
        if QMessageBox.question(self,"确认上传",f"上传到：{remote_dir}\n\n{names}\n\n确定继续？", QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:
            return
        self._start_remote_file_op("UPLOAD", remote_path=remote_dir, paths=list(paths))

    def _remote_download_selected(self):
        paths=self._selected_remote_paths()
        if not paths:
            QMessageBox.information(self,"远程文件","请先在右侧选择要下载的文件或文件夹。") ; return
        local_dir=self.remote_local_path.text().strip()
        if QMessageBox.question(self,"确认下载",f"下载 {len(paths)} 项到本机：\n{local_dir}\n\n确定继续？", QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:
            return
        self._start_remote_file_op("DOWNLOAD", local_path=local_dir, paths=paths)

    def _remote_file_mkdir(self):
        import ntpath
        parent=self.remote_file_path.text().strip().replace("/", "\\")
        if not parent:
            QMessageBox.information(self,"远程文件","请先进入一个远程磁盘/目录。") ; return
        name, ok=QInputDialog.getText(self,"新建远程目录","目录名称：")
        if not ok or not name.strip(): return
        self._start_remote_file_op("MKDIR", remote_path=ntpath.join(parent,name.strip()))

    def _remote_file_rename(self):
        paths=self._selected_remote_paths()
        if len(paths)!=1:
            QMessageBox.information(self,"远程文件","重命名时请只选择 1 个远程文件或文件夹。") ; return
        import ntpath
        old_name=ntpath.basename(paths[0].rstrip("\\"))
        name, ok=QInputDialog.getText(self,"重命名远程项目","新名称：", text=old_name)
        if not ok or not name.strip() or name.strip()==old_name: return
        self._start_remote_file_op("RENAME", remote_path=paths[0], new_name=name.strip())

    def _remote_file_delete(self):
        paths=self._selected_remote_paths()
        if not paths:
            QMessageBox.information(self,"远程文件","请先选择要删除的远程文件或文件夹。") ; return
        preview="\n".join(paths[:6])
        if len(paths)>6: preview += f"\n……另有 {len(paths)-6} 项"
        if QMessageBox.warning(self,"确认删除",f"以下远程项目将被直接删除，不进入回收站：\n\n{preview}\n\n确定删除？", QMessageBox.Yes|QMessageBox.No, QMessageBox.No)!=QMessageBox.Yes:
            return
        self._start_remote_file_op("DELETE", paths=paths)

    def _build_inventory_page(self):
        canvas,root=self._page_canvas(); c,l=card(
            "已保存主机",
            "主机管理只维护机器身份、分组和连接能力；“WinRM 凭据”在此处仅显示状态，不直接编辑，实际凭据统一在“文件分发 → Windows 目标主机”管理。所有实际分发和远程操作只使用 WinRM；Ping、SMB 445、RDP 3389、DNS/NetBIOS/SMB 身份仅用于发现与辅助诊断。",
        )
        # 主机数量直接展示给用户，不再需要通过行数或数据库 ID 猜测。
        self.inventory_count_label = QLabel("当前共 0 台主机")
        self.inventory_count_label.setObjectName("Muted")
        self.inventory_count_label.setStyleSheet("font-weight: 700; padding: 2px 0 0 0;")
        l.addWidget(self.inventory_count_label)

        inventory_filter_row = QHBoxLayout(); inventory_filter_row.setSpacing(8)
        inventory_filter_row.addWidget(QLabel("快速筛选"))
        self.inventory_filter_edit = QLineEdit()
        self.inventory_filter_edit.setClearButtonEnabled(True)
        self.inventory_filter_edit.setPlaceholderText("输入 IP、主机名、分组、在线状态、WinRM 状态或备注")
        self.inventory_filter_edit.textChanged.connect(self._apply_inventory_filter)
        inventory_filter_row.addWidget(self.inventory_filter_edit, 1)
        self.inventory_filter_count = QLabel("显示 0 / 0 台")
        self.inventory_filter_count.setObjectName("Muted")
        inventory_filter_row.addWidget(self.inventory_filter_count)
        l.addLayout(inventory_filter_row)

        self.host_table=QTableWidget(0,18)
        self.host_table.setHorizontalHeaderLabels([
            "选择","ID","名称","主机 / IP","名称来源","名称状态","分组","WinRM 凭据",
            "在线状态","Ping","445 SMB（识别）","3389 RDP（识别）","WinRM 端口","WinRM 状态","SMB 辅助状态","最后测试","最后发现","备注"
        ])
        self.host_table.setAlternatingRowColors(True); self.host_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.host_table.setSelectionMode(QAbstractItemView.ExtendedSelection); self.host_table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.host_table.verticalHeader().setVisible(False); configure_full_content_table(self.host_table, fixed_columns={0: 44})
        # SQLite 自增 ID 仅用于程序内部关联。删除/重新发现主机后数字不会连续，
        # 对用户没有业务意义，因此主机管理界面隐藏，避免误解为“机器数量”。
        self.host_table.setColumnHidden(1, True)
        l.addWidget(self.host_table)
        row=QHBoxLayout()
        for text,fn,icon in [
            ("添加",self._add_host,"add"),("编辑",self._edit_host,"edit"),("删除",self._delete_host,"delete"),
            ("全选",lambda:self._set_inventory_checks(True),"check"),("取消全选",lambda:self._set_inventory_checks(False),"clear"),
            ("测试在线状态",self._test_inventory_online,"radar"),
            ("ADMS 部署前检查",lambda:self._check_host_environment(self._selected_inventory_hosts(), "主机管理"),"search"),
            ("验证主机名",self._verify_inventory_names,"terminal"),("导出 Excel",self._export_inventory_excel,"file"),("刷新",self.refresh_hosts,"refresh")
        ]:
            b=QPushButton(text); b.setIcon(app_icon(icon)); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(1); l.addLayout(row); root.addWidget(c); return canvas

    def refresh_hosts(self):
        hosts=db.list_hosts()
        if hasattr(self, "inventory_count_label"):
            self.inventory_count_label.setText(f"当前共 {len(hosts)} 台主机")

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
                chk=QCheckBox(); chk.setChecked(inventory_check_state.get(h.host, False)); chk.setStyleSheet("QCheckBox { margin-left: 12px; margin-right: 12px; }"); self.host_table.setCellWidget(r,0,chk)
                verify_text="已验证" if h.hostname_verified else ("未验证" if h.name and h.name != h.host else "未识别")
                cred_status,_cred_tip=self._credential_status_for_host(h.host)
                vals=[h.id,h.name,h.host,hostname_source_text(h.hostname_source),verify_text,group_text(h.group_name),cred_status,
                      online_status_text(h.online_status),"是" if h.ping_ok else "","是" if h.smb_port_ok else "","是" if h.rdp_port_ok else "","是" if h.winrm_port_ok else "",winrm_status_label(h.winrm_status),smb_status_label(h.smb_status),h.last_test_at,h.last_seen,h.notes]
                for c,v in enumerate(vals, start=1):self.host_table.setItem(r,c,QTableWidgetItem(str(v if v is not None else "")))
            fit_full_content_table(self.host_table)
            self.host_table.verticalScrollBar().setValue(inventory_scroll)
            self._apply_inventory_filter()

        if hasattr(self,"target_table"):
            self.target_table.setRowCount(len(hosts))
            for r,h in enumerate(hosts):
                chk=QCheckBox()
                chk.setStyleSheet("QCheckBox { margin-left: 12px; margin-right: 12px; }")
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
            fit_full_content_table(self.target_table)
            self.target_table.verticalScrollBar().setValue(target_scroll)
            self._apply_target_filter()
            # 首次初始化：只有实际存在主机时才消费“一次性默认全选”规则。
            # 这样全新安装若尚未发现任何主机，首次发现主机后仍会默认全部勾选。
            if hosts and not target_selection_initialized:
                self._save_all_target_selection_preferences()
            self._refresh_mapping_scope()

        if hasattr(self, "remote_file_host_combo"):
            self._refresh_remote_file_hosts()

    def _export_inventory_excel(self):
        hosts = db.list_hosts()
        if not hosts:
            QMessageBox.information(self, "导出主机信息", "当前没有可导出的主机信息。")
            return
        default_name = f"FileDistributionStudio_Hosts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        default_path = str(Path.home() / "Downloads" / default_name)
        file_path, _ = QFileDialog.getSaveFileName(self, "导出主机信息", default_path, "Excel 工作簿 (*.xlsx)")
        if not file_path:
            return
        if not file_path.lower().endswith(".xlsx"):
            file_path += ".xlsx"
        headers = [
            "名称", "主机 / IP", "名称来源", "名称状态", "分组", "WinRM 凭据",
            "在线状态", "Ping", "445 SMB（识别）", "3389 RDP（识别）", "WinRM 端口",
            "WinRM 状态", "SMB 辅助状态", "最后测试", "最后发现", "备注"
        ]
        rows = []
        for h in hosts:
            verify_text = "已验证" if h.hostname_verified else ("未验证" if h.name and h.name != h.host else "未识别")
            cred_status, _ = self._credential_status_for_host(h.host)
            rows.append([
                h.name, h.host, hostname_source_text(h.hostname_source), verify_text,
                group_text(h.group_name), cred_status, online_status_text(h.online_status),
                "是" if h.ping_ok else "否", "是" if h.smb_port_ok else "否",
                "是" if h.rdp_port_ok else "否", "是" if h.winrm_port_ok else "否",
                winrm_status_label(h.winrm_status), smb_status_label(h.smb_status),
                h.last_test_at or "", h.last_seen or "", h.notes or ""
            ])
        try:
            saved = export_xlsx(file_path, headers, rows, sheet_name="主机信息")
        except Exception as exc:
            logging.exception("Export host inventory failed")
            QMessageBox.critical(self, "导出主机信息", f"Excel 导出失败：\n{exc}")
            return
        audit.operation(self.settings.audit_path, "HOST", "EXPORT", "SUCCESS",
                        "已导出主机信息 Excel。", details={"count": len(rows), "path": str(saved)})
        self.refresh_audit()
        QMessageBox.information(self, "导出主机信息", f"导出成功。\n\n主机数量：{len(rows)}\n文件：{saved}")

    def _add_host(self):
        d=HostEditDialog(self)
        if d.exec():
            h=d.value()
            if h.host:
                db.upsert_host(h); self.refresh_hosts();audit.operation(self.settings.audit_path,"HOST","ADD","SUCCESS","已添加或更新主机。",host=h.host,details={"name":h.name,"group":h.group_name,"hostname_source":h.hostname_source,"hostname_verified":bool(h.hostname_verified)});self.refresh_audit()

    def _set_inventory_checks(self, checked: bool):
        for r in range(self.host_table.rowCount()):
            if self.host_table.isRowHidden(r):
                continue
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
        canvas,root=self._page_canvas()
        c,l=card(
            "主机发现",
            "跨一个或多个 IPv4 网段发现可访问的 Windows 主机。扫描范围由用户直接设置；扫描完成后仍会与主机管理自动比对并提示新增、疑似变化和离线主机。",
        )
        top=QHBoxLayout(); top.setSpacing(10)
        self.discovery_ranges_edit=QLineEdit()
        self.discovery_ranges_edit.setPlaceholderText("例如：172.16.21.0/24, 172.16.22.0/24")
        self.discovery_ranges_edit.returnPressed.connect(self._edit_discovery_ranges)
        self.btn_edit_ranges=QPushButton("保存范围")
        self.btn_edit_ranges.setIcon(app_icon("edit")); self.btn_edit_ranges.clicked.connect(self._edit_discovery_ranges)
        top.addWidget(QLabel("扫描范围")); top.addWidget(self.discovery_ranges_edit,1); top.addWidget(self.btn_edit_ranges); l.addLayout(top)
        action=QHBoxLayout(); action.setSpacing(10)
        self.discovery_progress=QProgressBar(); self.discovery_progress.setValue(0)
        self.btn_scan=QPushButton("扫描内网"); self.btn_scan.setObjectName("Primary"); self.btn_scan.setIcon(app_icon("radar")); self.btn_scan.clicked.connect(self._start_scan)
        self.btn_stop_scan=QPushButton("停止"); self.btn_stop_scan.setObjectName("Danger"); self.btn_stop_scan.setEnabled(False); self.btn_stop_scan.clicked.connect(self._stop_scan)
        action.addWidget(self.discovery_progress,1); action.addWidget(self.btn_scan); action.addWidget(self.btn_stop_scan); l.addLayout(action)
        root.addWidget(c)

        rcard,rl=card("发现的 Windows 主机","发现结果仅使用 Ping/445/3389/5985/5986、DNS、NetBIOS、SMB/WKSSVC 等只读信号进行识别。正式文件分发、备份、服务与进程控制仍只使用 WinRM。")
        self.discovery_table=QTableWidget(0,12)
        self.discovery_table.setHorizontalHeaderLabels(["添加","来源网段","IP","识别名称","名称来源","名称状态","445 SMB","3389 RDP","5985 WinRM","5986 WinRM","主机状态","名称说明"])
        self.discovery_table.setAlternatingRowColors(True); self.discovery_table.verticalHeader().setVisible(False); configure_full_content_table(self.discovery_table, fixed_columns={0: 44})
        rl.addWidget(self.discovery_table)
        bottom=QHBoxLayout(); self.discovery_status=QLabel("就绪"); self.discovery_status.setObjectName("Muted")
        b_verify=QPushButton("深度验证主机名"); b_verify.setIcon(app_icon("terminal")); b_verify.clicked.connect(self._verify_discovered_names)
        b_add=QPushButton("手动同步选中主机"); b_add.setIcon(app_icon("hosts")); b_add.clicked.connect(self._add_discovered)
        bottom.addWidget(self.discovery_status); bottom.addStretch(1); bottom.addWidget(b_verify); bottom.addWidget(b_add); rl.addLayout(bottom); root.addWidget(rcard); return canvas

    def _default_discovery_ranges(self):
        """Return conservative local defaults only when the user has no saved range.

        Link-local/APIPA networks (169.254/16) are deliberately ignored.  They can
        make a scan enormous and are not useful as a routed Windows discovery range.
        """
        ranges=[]
        for _ifname,ip,cidr in local_ipv4_networks():
            try:
                addr=ipaddress.ip_address(ip)
                net=ipaddress.ip_network(cidr,strict=False)
                if not addr.is_private or addr.is_link_local or net.network_address.is_link_local:
                    continue
                # Keep automatic defaults practical. Larger routed ranges can still
                # be entered explicitly by the user.
                if net.num_addresses > 4096:
                    continue
                n=str(net)
                if n not in ranges:ranges.append(n)
            except Exception:
                continue
        return ranges

    def _configured_discovery_ranges(self):
        ranges=[]
        for x in (self.settings.discovery_ranges or []):
            try:
                n=ipaddress.ip_network(x,strict=False)
                # Remove the APIPA range that older automatic-discovery versions may
                # have persisted into settings.
                if n.network_address.is_link_local:
                    continue
                c=str(n)
                if c not in ranges:ranges.append(c)
            except Exception:
                continue
        return ranges

    def refresh_networks(self):
        if not hasattr(self,"discovery_ranges_edit"):return
        ranges=self._configured_discovery_ranges()
        if not ranges:
            ranges=self._default_discovery_ranges()
            if ranges:
                self.settings.discovery_ranges=list(ranges); self.settings.save()
        self.discovery_ranges_edit.setText(", ".join(ranges))

    def _edit_discovery_ranges(self):
        text=self.discovery_ranges_edit.text().strip()
        try:
            ranges=normalize_networks(text)
            if not ranges:
                raise ValueError("请至少填写一个 IPv4 CIDR 网段。")
            total=sum(max(0,ipaddress.ip_network(x,strict=False).num_addresses-2) for x in ranges)
            if total>4096:
                raise ValueError(f"当前扫描范围约包含 {total} 个可用地址，超过单次扫描上限 4096。请缩小或拆分扫描范围。")
        except Exception as e:
            QMessageBox.warning(self,"扫描范围",str(e)); return False
        self.settings.discovery_ranges=ranges; self.settings.save()
        self.discovery_ranges_edit.setText(", ".join(ranges))
        self.discovery_status.setText("扫描范围已保存："+"、".join(ranges))
        return True

    def _host_in_scan_ranges(self,host,cidrs):
        try:addr=ipaddress.ip_address(host)
        except Exception:return False
        for cidr in cidrs:
            try:
                if addr in ipaddress.ip_network(cidr,strict=False):return True
            except Exception:
                pass
        return False

    def _start_scan(self):
        # Always scan exactly the range currently shown to the user.  Do not append
        # networks from adapters behind the scenes.
        if not self._edit_discovery_ranges():
            return
        cidrs=list(self.settings.discovery_ranges or [])
        try: cidrs=normalize_networks(cidrs)
        except Exception as e: QMessageBox.warning(self,"主机发现",str(e)); return
        self._scan_cidrs=list(cidrs)
        self._scan_existing_hosts={h.host:h for h in db.list_hosts() if self._host_in_scan_ranges(h.host,cidrs)}
        self._scan_discovered_ips=set()
        self.discovery_table.setRowCount(0); self.btn_scan.setEnabled(False); self.btn_stop_scan.setEnabled(True); self.btn_edit_ranges.setEnabled(False); self.discovery_progress.setValue(0)
        self.discovery_status.setText("正在扫描：" + "、".join(cidrs)); self._set_busy(True,"正在扫描")
        audit.operation(self.settings.audit_path,"DISCOVERY","START","SUCCESS","开始智能扫描 Windows 主机。",details={"ranges":cidrs,"known_hosts_in_scope":len(self._scan_existing_hosts)})
        self.discovery_thread=DiscoveryThread(cidrs,self.settings.socket_timeout,self.settings.discovery_workers); self.discovery_thread.result_found.connect(self._discovery_result); self.discovery_thread.log.connect(self.discovery_status.setText); self.discovery_thread.progress.connect(self._scan_progress); self.discovery_thread.completed.connect(self._scan_completed); self.discovery_thread.start()

    def _stop_scan(self):
        if self.discovery_thread:
            self.discovery_thread.cancel(); audit.operation(self.settings.audit_path,"DISCOVERY","CANCEL","CANCELLED","用户请求停止主机扫描。")

    def _scan_progress(self,done,total):
        self.discovery_progress.setValue(int(done*100/total) if total else 0)

    def _discovery_result(self,x):
        self._scan_discovered_ips.add(x.host)
        for r in range(self.discovery_table.rowCount()):
            if self.discovery_table.item(r,2).text()==x.host:return
        r=self.discovery_table.rowCount(); self.discovery_table.insertRow(r)
        chk=QCheckBox(); chk.setChecked(True); holder=QWidget(); hl=QHBoxLayout(holder); hl.setContentsMargins(0,0,0,0); hl.setAlignment(Qt.AlignCenter); hl.addWidget(chk); self.discovery_table.setCellWidget(r,0,holder)
        verify="已验证" if x.hostname_verified else ("未验证" if x.hostname else "未识别")
        vals=[x.network,x.host,x.hostname,hostname_source_text(x.hostname_source),verify,
              "是" if x.port_445 else "","是" if x.port_3389 else "","是" if x.port_5985 else "","是" if x.port_5986 else "",status_text(x.status),x.hostname_note]
        for c,v in enumerate(vals,1):self.discovery_table.setItem(r,c,QTableWidgetItem(v))
        self.discovery_table.item(r,3).setData(Qt.UserRole,x.hostname_source)
        self.discovery_table.item(r,5).setData(Qt.UserRole,bool(x.hostname_verified))
        self.discovery_table.item(r,5).setData(Qt.UserRole + 1, "")
        fit_full_content_table(self.discovery_table)
        self._mark_duplicate_discovery_names()
        audit.operation(self.settings.audit_path,"DISCOVERY","HOST_FOUND","SUCCESS","扫描发现 Windows 主机。",host=x.host,details={"network":x.network,"hostname":x.hostname,"hostname_source":x.hostname_source,"hostname_verified":x.hostname_verified,"hostname_note":x.hostname_note,"smb_445":x.port_445,"rdp_3389":x.port_3389,"winrm_5985":x.port_5985,"winrm_5986":x.port_5986,"status":x.status})

    def _mark_duplicate_discovery_names(self):
        names={}
        for r in range(self.discovery_table.rowCount()):
            item=self.discovery_table.item(r,3); name=(item.text().strip() if item else "")
            if name:names.setdefault(name.casefold(),[]).append(r)
        duplicate_rows={r for rows in names.values() if len(rows)>1 for r in rows}
        for r in range(self.discovery_table.rowCount()):
            name_item=self.discovery_table.item(r,3); state_item=self.discovery_table.item(r,5); note_item=self.discovery_table.item(r,11)
            verified=bool(state_item.data(Qt.UserRole)) if state_item else False; verify_error=(state_item.data(Qt.UserRole + 1) if state_item else "") or ""; name=name_item.text().strip() if name_item else ""
            if verified:state_item.setText("已验证")
            elif verify_error == "FAILED":state_item.setText("验证失败")
            elif r in duplicate_rows:
                state_item.setText("名称重复，建议验证"); note=(note_item.text().strip() if note_item else "")
                if "重复" not in note:note_item.setText((note+" ").strip()+"同一次扫描中有多个 IP 返回相同名称，建议执行 SMB/WKSSVC 或 WinRM 深度验证实际计算机名。")
            elif name:state_item.setText("未验证")
            else:state_item.setText("未识别")

    def _scan_completed(self,status):
        self.btn_scan.setEnabled(True); self.btn_stop_scan.setEnabled(False); self.btn_edit_ranges.setEnabled(True); self._set_busy(False)
        if status=="Completed":self.discovery_progress.setValue(100)
        self.discovery_status.setText(f"{status_text(status)}：发现 {self.discovery_table.rowCount()} 台 Windows 主机")
        audit_status={"Completed":"SUCCESS","Cancelled":"CANCELLED","Failed":"FAILED"}.get(status,str(status).upper()); audit.operation(self.settings.audit_path,"DISCOVERY","FINISH",audit_status,"主机扫描结束。",details={"found":self.discovery_table.rowCount(),"ranges":self._scan_cidrs})
        self.refresh_audit()
        if status=="Completed":self._start_discovery_reconcile()

    def _start_discovery_reconcile(self):
        missing=[ip for ip in self._scan_existing_hosts if ip not in self._scan_discovered_ips]
        if not missing:
            self._show_discovery_sync_dialog([]); return
        self.discovery_status.setText(f"扫描完成：发现 {self.discovery_table.rowCount()} 台；正在复查主机库中 {len(missing)} 个未发现 IP…")
        self.discovery_reconcile_thread=DiscoveryReconcileThread(missing,self.settings.socket_timeout,max_workers=min(24,max(8,self.settings.discovery_workers)))
        self.discovery_reconcile_thread.completed.connect(self._discovery_reconcile_completed); self.discovery_reconcile_thread.start()

    def _discovery_reconcile_completed(self,results):
        self._show_discovery_sync_dialog(results)

    def _discovery_row_data(self,r):
        return {
            "row":r,"ip":self.discovery_table.item(r,2).text(),"hostname":self.discovery_table.item(r,3).text().strip(),
            "source_text":self.discovery_table.item(r,4).text(),"source_code":self.discovery_table.item(r,3).data(Qt.UserRole),
            "verified":bool(self.discovery_table.item(r,5).data(Qt.UserRole)),"note":self.discovery_table.item(r,11).text(),
            "status":self.discovery_table.item(r,10).text(),"smb":self.discovery_table.item(r,6).text()=="是",
            "rdp":self.discovery_table.item(r,7).text()=="是","w1":self.discovery_table.item(r,8).text()=="是","w2":self.discovery_table.item(r,9).text()=="是",
        }

    def _upsert_discovery_data(self,d):
        now=datetime.now().isoformat(timespec="seconds")
        source_map={"DNS PTR":"DNS_PTR","NetBIOS":"NETBIOS","Windows 名称解析":"WINDOWS_RESOLVER","Windows 工作站 API":"NETAPI_WKSTA","SMB/NTLM 指纹":"SMB_NTLM","SMB/WKSSVC 验证":"SMB_RPC","WinRM hostname":"WINRM","手工维护":"MANUAL"}
        stable_source=d["source_code"] or source_map.get(d["source_text"],"")
        online_status="ONLINE_WINRM" if (d["w1"] or d["w2"]) else ("ONLINE_SMB" if d["smb"] else ("ONLINE" if d["rdp"] else "UNTESTED"))
        db.upsert_host(HostRecord(id=None,name=d["hostname"] or d["ip"],host=d["ip"],group_name="自动发现",target_mode="WINRM",default_target="",os_hint=d["status"],last_seen=now,notes="",hostname_source=stable_source,hostname_verified=int(d["verified"]),hostname_note=d["note"],online_status=online_status,ping_ok=0,smb_port_ok=int(d["smb"]),rdp_port_ok=int(d["rdp"]),winrm_port_ok=int(d["w1"] or d["w2"]),smb_status="UNTESTED",last_test_at=now))

    def _show_discovery_sync_dialog(self,recheck_results):
        known=set(self._scan_existing_hosts)
        new_rows=[self._discovery_row_data(r) for r in range(self.discovery_table.rowCount()) if self.discovery_table.item(r,2).text() not in known]
        non_windows=[]; offline=[]
        for ip,res in recheck_results:
            h=self._scan_existing_hosts.get(ip)
            if res is None:
                offline.append((h,"复查失败")); continue
            windows_signal=bool(res.smb_port_ok or res.rdp_port_ok or res.winrm_port_ok)
            if windows_signal:
                db.update_host_connectivity(ip,online_status=res.online_status,ping_ok=res.ping_ok,smb_port_ok=res.smb_port_ok,rdp_port_ok=res.rdp_port_ok,winrm_port_ok=res.winrm_port_ok,last_test_at=datetime.now().isoformat(timespec="seconds"))
            elif res.ping_ok:
                non_windows.append((h,"IP 可达，但 445/3389/5985/5986 均未检测到 Windows 服务特征"))
            else:
                offline.append((h,"本次扫描和复查均不可达，可能关机、断网或被防火墙阻断"))
        if not new_rows and not non_windows and not offline:
            self.discovery_status.setText(f"扫描完成：发现 {self.discovery_table.rowCount()} 台 Windows 主机；主机库无需变更。")
            return
        d=QDialog(self); d.setWindowTitle("智能同步主机库 - File Distribution Studio"); d.resize(900,560)
        vl=QVBoxLayout(d); title=QLabel("扫描完成，发现主机库变化"); title.setStyleSheet("font-size:18px;font-weight:700;"); vl.addWidget(title)
        desc=QLabel(f"新发现 Windows 主机 {len(new_rows)} 台；疑似已不是 Windows 的已保存 IP {len(non_windows)} 个；暂时不可达 {len(offline)} 个。\n新增项默认勾选；移除属于删除操作，默认不勾选，由你确认。离线主机只提醒并保留。")
        desc.setWordWrap(True); desc.setObjectName("Muted"); vl.addWidget(desc)
        table=QTableWidget(0,6); table.setHorizontalHeaderLabels(["处理","变化类型","名称","IP","检测结果","建议"]); table.verticalHeader().setVisible(False); table.setAlternatingRowColors(True); configure_full_content_table(table); table.setSelectionMode(QAbstractItemView.NoSelection)
        actions=[]
        def add_row(kind,hname,ip,result,suggestion,checked,action,payload):
            r=table.rowCount(); table.insertRow(r); holder=QWidget(); lay=QHBoxLayout(holder); lay.setContentsMargins(0,0,0,0); lay.setAlignment(Qt.AlignCenter); chk=QCheckBox(); chk.setChecked(checked); chk.setEnabled(action in ("ADD","REMOVE")); lay.addWidget(chk); table.setCellWidget(r,0,holder)
            for c,val in enumerate([kind,hname,ip,result,suggestion],1):table.setItem(r,c,QTableWidgetItem(val))
            actions.append((chk,action,payload))
        for x in new_rows:add_row("新发现 Windows",x["hostname"] or x["ip"],x["ip"],x["status"],"添加到主机管理",True,"ADD",x)
        for h,msg in non_windows:add_row("Windows 特征消失",(h.name if h else ""),(h.host if h else ""),msg,"确认环境变化后可移除",False,"REMOVE",h)
        for h,msg in offline:add_row("暂时不可达",(h.name if h else ""),(h.host if h else ""),msg,"保留；稍后重新扫描",False,"KEEP",h)
        fit_full_content_table(table); vl.addWidget(table,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Ok).setText("应用所选变更"); buttons.button(QDialogButtonBox.Cancel).setText("暂不处理"); buttons.accepted.connect(d.accept); buttons.rejected.connect(d.reject); vl.addWidget(buttons)
        if not d.exec():
            self.discovery_status.setText(f"扫描完成：发现 {self.discovery_table.rowCount()} 台 Windows 主机；主机库变化暂未处理。")
            return
        added=removed=0
        for chk,action,payload in actions:
            if not chk.isEnabled() or not chk.isChecked():continue
            if action=="ADD":self._upsert_discovery_data(payload); added+=1
            elif action=="REMOVE" and payload and payload.id is not None:db.delete_host(payload.id); removed+=1
        self.refresh_hosts(); self.refresh_audit(); self.discovery_status.setText(f"智能同步完成：新增 {added} 台，移除 {removed} 台；发现 {self.discovery_table.rowCount()} 台 Windows 主机。")
        audit.operation(self.settings.audit_path,"DISCOVERY","SMART_SYNC","SUCCESS","已应用主机发现智能同步。",details={"added":added,"removed":removed,"new_found":len(new_rows),"non_windows_candidates":len(non_windows),"offline_kept":len(offline)})

    def _verify_discovered_names(self):
        hosts=[]
        for r in range(self.discovery_table.rowCount()):
            w=self.discovery_table.cellWidget(r,0); chk=w.findChild(QCheckBox) if w else None
            if chk and chk.isChecked():hosts.append(self.discovery_table.item(r,2).text())
        if not hosts:
            QMessageBox.warning(self,"主机名验证","请先勾选需要验证的已发现主机。")
            return
        self._request_hostname_verification(hosts,context="discovery")

    def _request_hostname_verification(self, hosts, context):
        if self.hostname_thread and self.hostname_thread.isRunning():QMessageBox.information(self,"主机名验证","已有主机名验证任务正在执行，请等待完成。"); return
        d=HostnameCredentialDialog(self)
        if not d.exec():return
        cfg=d.value(); self.hostname_verify_context=context
        self.hostname_thread=HostnameVerificationThread(hosts,cfg["username"],cfg["password"],cfg["use_https"],cfg["port"],max_workers=min(8,self.settings.max_concurrency*2),method=cfg["method"])
        self.hostname_thread.result.connect(self._hostname_verify_result); self.hostname_thread.progress.connect(self._hostname_verify_progress); self.hostname_thread.log.connect(self._hostname_verify_log); self.hostname_thread.completed.connect(self._hostname_verify_completed)
        audit.operation(self.settings.audit_path,"DISCOVERY" if context=="discovery" else "HOST","VERIFY_HOSTNAME_START","SUCCESS","开始主机名深度验证。",details={"host_count":len(hosts),"username":cfg["username"],"method":cfg["method"],"use_https":cfg["use_https"],"port":cfg["port"]})
        self._set_busy(True,"验证名称"); self.hostname_thread.start()

    def _hostname_verify_log(self,text):
        if hasattr(self,"discovery_status") and self.hostname_verify_context=="discovery":self.discovery_status.setText(text)

    def _hostname_verify_progress(self,done,total):
        if self.hostname_verify_context=="discovery" and hasattr(self,"discovery_progress"):self.discovery_progress.setValue(int(done*100/total) if total else 0)

    def _hostname_verify_result(self,host,hostname,source,verified,message):
        audit.operation(self.settings.audit_path,"DISCOVERY" if self.hostname_verify_context=="discovery" else "HOST","VERIFY_HOSTNAME","SUCCESS" if verified else "FAILED",message,host=host,subject=hostname,details={"source":source,"verified":verified})
        if self.hostname_verify_context=="discovery":
            for r in range(self.discovery_table.rowCount()):
                if self.discovery_table.item(r,2).text()!=host:continue
                if verified and hostname:
                    self.discovery_table.item(r,3).setText(hostname); self.discovery_table.item(r,3).setData(Qt.UserRole,source); self.discovery_table.item(r,4).setText(hostname_source_text(source)); self.discovery_table.item(r,5).setText("已验证"); self.discovery_table.item(r,5).setData(Qt.UserRole,True); self.discovery_table.item(r,5).setData(Qt.UserRole + 1, ""); self.discovery_table.item(r,11).setText(message)
                else:
                    self.discovery_table.item(r,5).setText("验证失败"); self.discovery_table.item(r,5).setData(Qt.UserRole,False); self.discovery_table.item(r,5).setData(Qt.UserRole + 1,"FAILED"); self.discovery_table.item(r,11).setText(message)
                break
            self._mark_duplicate_discovery_names()
        else:
            if verified and hostname:db.update_host_hostname(host,hostname,source,True,message)
            else:db.mark_host_hostname_verification_failed(host,message)

    def _hostname_verify_completed(self,success,failed):
        context=self.hostname_verify_context; self._set_busy(False)
        if context=="inventory":self.refresh_hosts()
        if context=="discovery":self.discovery_status.setText(f"主机名验证完成：成功 {success}，失败 {failed}")
        audit.operation(self.settings.audit_path,"DISCOVERY" if context=="discovery" else "HOST","VERIFY_HOSTNAME_FINISH","SUCCESS" if failed==0 else "PARTIAL_FAILED","主机名深度验证结束。",details={"success":success,"failed":failed})
        self.refresh_audit(); QMessageBox.information(self,"主机名验证",f"验证完成。\n成功：{success}\n失败：{failed}")

    def _add_discovered(self):
        added=0
        existing={h.host for h in db.list_hosts()}
        for r in range(self.discovery_table.rowCount()):
            w=self.discovery_table.cellWidget(r,0); chk=w.findChild(QCheckBox) if w else None
            if chk and chk.isChecked():
                d=self._discovery_row_data(r); self._upsert_discovery_data(d); added+=1
        self.refresh_hosts(); audit.operation(self.settings.audit_path,"DISCOVERY","ADD_HOSTS","SUCCESS","已手动将扫描结果同步到主机库。",details={"count":added}); self.refresh_audit(); QMessageBox.information(self,"主机发现",f"已添加 / 更新 {added} 台主机。")

    # ---------- History ----------
    def _build_history_page(self):
        canvas,root=self._page_canvas(); c,l=card("分发历史","双击任务可查看主机、文件分发、远程操作、备份、校验和完整审计详情。")
        self.history_cols=["task_id","created_at","operator","workstation","source_type","source_path","target_path","backup_root","verification_mode","host_count","file_count","total_bytes","status","success_hosts","failed_hosts","finished_at"]
        self.history_headers=["任务 ID","创建时间","操作用户","操作计算机","来源类型","来源路径","目标目录","备份根目录","校验策略","主机数","文件数","总大小","状态","成功主机","失败主机","结束时间"]
        self.history_table=QTableWidget(0,len(self.history_cols)); self.history_table.setHorizontalHeaderLabels(self.history_headers); self.history_table.setAlternatingRowColors(True); self.history_table.verticalHeader().setVisible(False); configure_full_content_table(self.history_table); self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.history_table.doubleClicked.connect(self._open_history_detail); l.addWidget(self.history_table)
        row=QHBoxLayout()
        for text,fn,icon in [("刷新",self.refresh_history,"refresh"),("查看详情",self._open_history_detail,"history"),("导出 CSV",self._export_history,"export")]:b=QPushButton(text);b.setIcon(app_icon(icon));b.clicked.connect(fn);row.addWidget(b)
        row.addStretch(1);l.addLayout(row);root.addWidget(c);return canvas
    def refresh_history(self):
        if not hasattr(self,"history_table"):return
        rows=db.list_tasks();self.history_table.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,key in enumerate(self.history_cols):
                value=row[key]; value=human_bytes(int(value)) if key=="total_bytes" else value; value=source_text(value) if key=="source_type" else value; value=status_text(value) if key=="status" else value; self.history_table.setItem(r,c,QTableWidgetItem(str(value)))
        fit_full_content_table(self.history_table)
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
        self.audit_table=QTableWidget(0,len(self.audit_cols)); self.audit_table.setHorizontalHeaderLabels(self.audit_headers); self.audit_table.setAlternatingRowColors(True); self.audit_table.verticalHeader().setVisible(False); configure_full_content_table(self.audit_table); self.audit_table.setSelectionBehavior(QAbstractItemView.SelectRows); l.addWidget(self.audit_table,1)
        row=QHBoxLayout(); b_refresh=QPushButton("刷新"); b_export=QPushButton("导出 CSV"); b_open=QPushButton("打开本地审计目录"); b_refresh.setIcon(app_icon("refresh")); b_export.setIcon(app_icon("export")); b_open.setIcon(app_icon("folder")); b_refresh.clicked.connect(self.refresh_audit); b_export.clicked.connect(self._export_audit); b_open.clicked.connect(self._open_audit_dir); row.addWidget(b_refresh); row.addWidget(b_export); row.addWidget(b_open); row.addStretch(1); l.addLayout(row); root.addWidget(c); return canvas

    def refresh_audit(self):
        if not hasattr(self,"audit_table"):return
        category=self.audit_category.currentData() or ""; status=self.audit_status.currentData() or ""; keyword=self.audit_keyword.text().strip()
        rows=db.list_audit_events(limit=3000,category=category,status=status,keyword=keyword); self.audit_table.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,key in enumerate(self.audit_cols):
                value=row[key]; value=status_text(value) if key=="status" else (audit_category_text(value) if key=="category" else (action_text(value) if key=="action" else value)); self.audit_table.setItem(r,c,QTableWidgetItem(str(value if value is not None else "")))
        fit_full_content_table(self.audit_table)

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
            "远程目录浏览",
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
        if hasattr(self,"chk_distribution"): self.settings.distribution_enabled=bool(self.chk_distribution.isChecked())
        if hasattr(self,"chk_backup"):
            self.settings.backup_task_enabled=bool(self.chk_backup.isChecked())
            self.settings.backup_existing=bool(self.chk_backup.isChecked())
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
        try: self._save_version_checker_preferences()
        except Exception: pass
        try: self._save_default_winrm_credential(show_error=False)
        except Exception: pass
        try: audit.operation(self.settings.audit_path,"APP","STOP","SUCCESS","文件分发工作台已退出。",details={"version":APP_VERSION})
        except Exception: pass
        super().closeEvent(event)

    def _set_busy(self,busy:bool,label="处理中"):
        if not self._status_badge:return
        self._status_badge.setText(label if busy else "就绪");self._status_badge.setObjectName("StatusBusy" if busy else "StatusReady");self._status_badge.style().unpolish(self._status_badge);self._status_badge.style().polish(self._status_badge)

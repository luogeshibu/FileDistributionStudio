from __future__ import annotations
import ctypes
import os
import sys
import logging
import traceback
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap, QPainter, QPen
from PySide6.QtWidgets import QApplication, QSplashScreen
from .db import init_db
from .logging_setup import setup_logging
from .resources import asset_path
from .ui.main_window import MainWindow
from .ui.input_wheel_guard import InputWheelGuard
from .ui.theme import APP_QSS
from .version import APP_NAME, APP_VERSION



def install_exception_logger():
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            logging.getLogger("fds.crash").critical(
                "未捕获异常\n%s",
                "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
            )
        finally:
            previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

def configure_windows_identity():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "FileDistributionStudio.Desktop"
        )
    except Exception:
        pass


def runtime_icon() -> QIcon:
    for name in ("logo.png", "logo.ico"):
        p = asset_path(name)
        if p.exists():
            icon = QIcon(str(p))
            if not icon.isNull():
                return icon
    return QIcon()


def apply_native_windows_icon(window):
    if os.name != "nt":
        return
    p = asset_path("logo.ico")
    if not p.exists():
        return
    try:
        user32 = ctypes.windll.user32
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x0010, 0x0080
        ICON_SMALL, ICON_BIG = 0, 1
        user32.LoadImageW.restype = ctypes.c_void_p
        hwnd = int(window.winId())
        small = user32.LoadImageW(None, str(p), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        big = user32.LoadImageW(None, str(p), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if small: user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
        if big: user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
    except Exception:
        pass


def run():
    if "--self-test" in sys.argv:
        required = [asset_path("logo.png"), asset_path("logo.ico"), asset_path("splash.png")]
        return 0 if all(x.exists() for x in required) else 2

    setup_logging(); install_exception_logger(); init_db(); configure_windows_identity()
    app = QApplication(sys.argv)

    # 全局输入控件滚轮保护：只拦截输入控件，页面/表格/滚动条仍可正常使用滚轮。
    # 由 QApplication 统一拦截，因此主窗口、弹窗以及后续动态创建的输入控件都生效。
    input_wheel_guard = InputWheelGuard(app)
    app.installEventFilter(input_wheel_guard)
    app._fds_input_wheel_guard = input_wheel_guard
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("NARI 国际业务部")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    app.setFont(QFont("Microsoft YaHei UI" if os.name == "nt" else "Noto Sans CJK SC", 10))

    icon = runtime_icon()
    if not icon.isNull(): app.setWindowIcon(icon)

    splash = None
    splash_path = asset_path("splash.png")
    if splash_path.exists():
        pix = QPixmap(str(splash_path))
        if not pix.isNull():
            # Splash background is stable branding; render the version dynamically
            # so a new release never shows an old hard-coded version string.
            painter = QPainter(pix)
            try:
                scale_x = pix.width() / 900.0
                scale_y = pix.height() / 470.0
                # The static splash already carries the NARI / 国际业务部 / 作者 branding.
                # Only the release version is rendered dynamically to prevent stale version text.
                painter.fillRect(530 * scale_x, 342 * scale_y, 290 * scale_x, 46 * scale_y, QColor("#102A44"))
                font = QFont("Microsoft YaHei UI" if os.name == "nt" else "Noto Sans CJK SC", max(10, int(14 * scale_y)), QFont.Bold)
                painter.setFont(font)
                painter.setPen(QPen(QColor("#D4E4EF")))
                painter.drawText(int(545 * scale_x), int(371 * scale_y), f"v{APP_VERSION}  ·  简体中文")
            finally:
                painter.end()
            splash = QSplashScreen(pix)
            splash.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            splash.showMessage(
                "正在加载文件分发工作台……",
                Qt.AlignLeft | Qt.AlignBottom,
                QColor("#CFE8EE"),
            )
            splash.show(); app.processEvents()

    w = MainWindow()
    if not icon.isNull(): w.setWindowIcon(icon)
    w.show()
    QTimer.singleShot(0, lambda: apply_native_windows_icon(w))
    if splash: QTimer.singleShot(120, lambda: splash.finish(w))
    return app.exec()

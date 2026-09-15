from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QObject, QTimer
from PySide6.QtWidgets import QLineEdit, QTableWidget, QHeaderView

from .theme import app_icon


class _FullContentTableFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, lambda: fit_full_content_table(watched))
        return False


def fit_full_content_table(table: QTableWidget) -> QTableWidget:
    """按内容计算最小宽度，再把剩余宽度均匀分配给可见列。"""
    table.setWordWrap(False)
    table.setTextElideMode(Qt.ElideNone)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setTextElideMode(Qt.ElideNone)
    table.resizeColumnsToContents()

    fixed = dict(getattr(table, "_fds_fixed_columns", {}) or {})
    visible = [c for c in range(table.columnCount()) if not table.isColumnHidden(c)]
    flexible = [c for c in visible if c not in fixed]
    for column, width in fixed.items():
        if 0 <= int(column) < table.columnCount():
            header.setSectionResizeMode(int(column), QHeaderView.Fixed)
            header.resizeSection(int(column), max(24, int(width)))
    for column in flexible:
        header.setSectionResizeMode(column, QHeaderView.Interactive)

    minimum_total = sum(header.sectionSize(c) for c in visible)
    available = max(0, table.viewport().width())
    extra = available - minimum_total
    if extra > 0 and flexible:
        each, remainder = divmod(extra, len(flexible))
        for index, column in enumerate(flexible):
            header.resizeSection(column, header.sectionSize(column) + each + (1 if index < remainder else 0))
    return table


def configure_full_content_table(table: QTableWidget, fixed_columns: dict[int, int] | None = None) -> QTableWidget:
    """配置完整显示表格，并在窗口缩放或内容刷新时自动重新均分列宽。"""
    table._fds_fixed_columns = dict(fixed_columns or {})
    if not hasattr(table, "_fds_full_content_filter"):
        table._fds_full_content_filter = _FullContentTableFilter(table)
        table.installEventFilter(table._fds_full_content_filter)
    fit_full_content_table(table)
    QTimer.singleShot(0, lambda: fit_full_content_table(table))
    return table


class PasswordLineEdit(QLineEdit):
    """带“小眼睛”显示/隐藏按钮的密码输入框。

    仅改变界面显示方式，不复制、不记录密码，也不改变现有审计策略。
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        if text:
            self.setText(text)
        self.setEchoMode(QLineEdit.Password)
        self._password_visible = False
        self._toggle_action = self.addAction(app_icon("eye"), QLineEdit.TrailingPosition)
        self._toggle_action.setToolTip("显示密码")
        self._toggle_action.triggered.connect(self.toggle_password_visibility)

    def toggle_password_visibility(self):
        self._password_visible = not self._password_visible
        self.setEchoMode(QLineEdit.Normal if self._password_visible else QLineEdit.Password)
        self._toggle_action.setIcon(app_icon("eye-off" if self._password_visible else "eye"))
        self._toggle_action.setToolTip("隐藏密码" if self._password_visible else "显示密码")

from PySide6.QtCore import Signal, QMimeData, QUrl
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView


class LocalFileTable(QTableWidget):
    """本地文件列表：允许把当前选中的本地文件/目录拖到远程文件表。"""
    ROLE_PATH = Qt.UserRole + 301

    def startDrag(self, supportedActions):
        paths = []
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        for row in rows:
            item = self.item(row, 0)
            path = str(item.data(self.ROLE_PATH) or "") if item else ""
            if path:
                paths.append(path)
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class RemoteFileDropTable(QTableWidget):
    """远程文件列表：接收来自资源管理器或左侧本地列表的本地文件拖放。"""
    localPathsDropped = Signal(list)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        paths = [p for p in paths if p]
        if paths:
            self.localPathsDropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit

from .theme import app_icon


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

from PySide6.QtCore import Qt, Signal, QMimeData, QUrl
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QTableWidget, QAbstractItemView


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

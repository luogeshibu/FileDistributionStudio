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

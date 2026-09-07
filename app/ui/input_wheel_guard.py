from __future__ import annotations

from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QScrollArea,
)


class InputWheelGuard(QObject):
    """仅禁止“输入控件”响应鼠标滚轮，其他滚动行为保持正常。

    目的：
    - 鼠标停留在端口、并发数、失败重试、下拉选择等输入控件上时，
      防止滚轮误改参数；
    - 页面右侧滚动条、表格滚动条、列表、日志查看器等仍可正常使用滚轮。

    规则：
    - QAbstractSpinBox：拦截滚轮（QSpinBox/QDoubleSpinBox/QDateTimeEdit 等）；
    - QComboBox：折叠状态下拦截滚轮，防止误切换选项；弹出列表仍可正常滚动；
    - QLineEdit：拦截滚轮；
    - QTextEdit/QPlainTextEdit：仅可编辑输入框拦截；只读日志仍可滚轮浏览；
    - 不再拦截 QAbstractSlider/QScrollBar，因此主页面、表格和右侧滚动条均正常。

    QApplication 级事件过滤器会自动覆盖主窗口、弹窗和后续动态创建的输入控件。
    """

    @staticmethod
    def _scroll_parent_page(watched, event) -> bool:
        """If an input consumes the wheel, forward the intent to the containing page.

        This preserves the safety rule "wheel must not change input values" without
        making a long form feel stuck whenever the pointer happens to be over a line
        edit / combo / command text box.
        """
        parent = watched.parentWidget() if hasattr(watched, "parentWidget") else None
        while parent is not None:
            if isinstance(parent, QScrollArea):
                bar = parent.verticalScrollBar()
                pixel_y = event.pixelDelta().y() if hasattr(event, "pixelDelta") else 0
                angle_y = event.angleDelta().y() if hasattr(event, "angleDelta") else 0
                if pixel_y:
                    delta = int(pixel_y)
                elif angle_y:
                    # One classic wheel notch is 120 angle units.  QScrollArea's
                    # singleStep is already tuned for our long pages.
                    delta = int((angle_y / 120.0) * max(1, bar.singleStep()) * 3)
                else:
                    return False
                bar.setValue(bar.value() - delta)
                return True
            parent = parent.parentWidget() if hasattr(parent, "parentWidget") else None
        return False

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel:
            return super().eventFilter(watched, event)

        # 输入控件本身不允许被滚轮改值；若位于主页面滚动区，则把滚动意图
        # 转交给页面，这样鼠标停在输入框上仍可顺畅上下滚页面。
        if isinstance(watched, (QAbstractSpinBox, QComboBox, QLineEdit)):
            self._scroll_parent_page(watched, event)
            event.accept()
            return True

        # 多行可编辑命令框同理：不滚动其内部文本，而滚动外层页面；只读日志
        # 仍保留自己的滚轮浏览行为。
        if isinstance(watched, (QTextEdit, QPlainTextEdit)) and not watched.isReadOnly():
            self._scroll_parent_page(watched, event)
            event.accept()
            return True

        return super().eventFilter(watched, event)

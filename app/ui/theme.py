from __future__ import annotations
from PySide6.QtGui import QIcon
from ..resources import icon_path

COLORS = {
    "nav": "#10233D",
    "nav_hover": "#193554",
    "nav_selected": "#245B86",
    "accent": "#1399A8",
    "accent_dark": "#0D7D89",
    "blue": "#2C6FA3",
    "canvas": "#F3F6F9",
    "surface": "#FFFFFF",
    "soft": "#F8FAFC",
    "border": "#D9E2EA",
    "text": "#17263A",
    "muted": "#697A8D",
    "success": "#13875E",
    "warning": "#B7791F",
    "danger": "#B42318",
}

APP_QSS = f"""
QMainWindow {{ background: {COLORS['canvas']}; }}
QWidget {{ font-family:'Microsoft YaHei UI','Segoe UI'; font-size:10pt; color:{COLORS['text']}; }}
QFrame#Sidebar {{ background:{COLORS['nav']}; border:none; }}
QFrame#Topbar {{ background:#FFFFFF; border-bottom:1px solid {COLORS['border']}; }}
QFrame#Card {{ background:#FFFFFF; border:1px solid {COLORS['border']}; border-radius:12px; }}
QFrame#SoftCard {{ background:{COLORS['soft']}; border:1px solid #E1E8EF; border-radius:10px; }}
QLabel#BrandTitle {{ color:white; font-size:14pt; font-weight:750; }}
QLabel#BrandSub {{ color:#9EB6CB; font-size:8.5pt; font-weight:650; }}
QLabel#BrandAccent {{ color:#69D1D5; font-size:8pt; font-weight:700; }}
QFrame#CorpBrand {{ background:#173552; border:1px solid #2A4B68; border-radius:8px; }}
QLabel#CorpMark {{ color:#FFFFFF; font-size:13pt; font-weight:900; letter-spacing:1px; }}
QLabel#CorpDept {{ color:#E7F4F6; font-size:9pt; font-weight:750; }}
QLabel#CorpDeptEn {{ color:#8FB1C8; font-size:6.8pt; }}
QFrame#TopCorpBrand {{ background:#F6FAFC; border:1px solid #D9E6EE; border-radius:8px; }}
QLabel#TopCorpMark {{ color:#0B6B87; font-size:11pt; font-weight:900; letter-spacing:1px; }}
QLabel#TopCorpDept {{ color:#34536C; font-size:9pt; font-weight:750; }}
QLabel#PipelineStep {{ color:#173D5A; font-size:10pt; font-weight:750; }}
QLabel#PipelineAuto {{ color:#486277; background:#F3F7FA; border:1px solid #E0E8EF; border-radius:6px; padding:6px 8px; font-weight:650; }}
QLabel#PipelineLabel {{ color:#314A5F; font-weight:700; }}
QLabel#PageTitle {{ color:{COLORS['text']}; font-size:18pt; font-weight:750; }}
QLabel#PageSub {{ color:{COLORS['muted']}; font-size:9.5pt; }}
QLabel#SectionTitle {{ color:{COLORS['text']}; font-size:11.5pt; font-weight:700; }}
QLabel#Muted {{ color:{COLORS['muted']}; }}
QLabel#StatusReady {{ color:#0A6B58; background:#E8F7F2; border:1px solid #BFE7DB; border-radius:10px; padding:4px 9px; font-weight:700; }}
QLabel#StatusBusy {{ color:#805B12; background:#FFF8E6; border:1px solid #F1D493; border-radius:10px; padding:4px 9px; font-weight:700; }}
QPushButton {{ background:#FFFFFF; border:1px solid #C7D2DD; border-radius:7px; padding:7px 12px; font-weight:600; }}
QPushButton:hover {{ background:#F5F8FA; border-color:#9FAFBE; }}
QPushButton:pressed {{ background:#EAF0F5; }}
QPushButton:disabled {{ color:#9DA9B4; background:#F3F5F7; border-color:#E1E6EA; }}
QPushButton#Primary {{ background:{COLORS['accent']}; color:white; border-color:{COLORS['accent']}; }}
QPushButton#Primary:hover {{ background:{COLORS['accent_dark']}; border-color:{COLORS['accent_dark']}; }}
QPushButton#Danger {{ background:#FFF4F2; color:{COLORS['danger']}; border-color:#F2C6C1; }}
QPushButton#NavButton {{ background:transparent; color:#C6D6E5; border:none; border-radius:8px; padding:10px 12px; text-align:left; font-weight:600; }}
QPushButton#NavButton:hover {{ background:{COLORS['nav_hover']}; color:white; }}
QPushButton#NavButton:checked {{ background:{COLORS['nav_selected']}; color:white; }}
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QSpinBox {{ background:#FFFFFF; border:1px solid #C8D3DE; border-radius:7px; padding:7px 8px; selection-background-color:{COLORS['accent']}; }}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border:1px solid {COLORS['accent']}; }}
QComboBox::drop-down {{ border:none; width:26px; }}
QGroupBox {{ background:#FFFFFF; border:1px solid {COLORS['border']}; border-radius:10px; margin-top:12px; padding-top:10px; font-weight:700; }}
QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 5px; color:#2D4054; }}
QTableWidget {{ background:#FFFFFF; border:1px solid {COLORS['border']}; border-radius:9px; gridline-color:#E8EDF2; alternate-background-color:#FAFBFC; selection-background-color:#E7F4F6; selection-color:#17263A; }}
QHeaderView::section {{ background:#F1F5F8; color:#2B3D50; border:none; border-right:1px solid #D8E0E8; border-bottom:1px solid #D8E0E8; padding:8px 7px; font-weight:700; }}
QTableCornerButton::section {{ background:#EAF0F5; border:1px solid #D4DDE5; }}
QProgressBar {{ background:#EDF2F6; border:1px solid #D9E2EA; border-radius:6px; text-align:center; color:#395064; min-height:17px; }}
QProgressBar::chunk {{ background:{COLORS['accent']}; border-radius:5px; }}
QScrollBar:vertical {{ background:#EEF3F6; width:12px; margin:0; border:none; }}
QScrollBar::handle:vertical {{ background:#B5C2CE; border-radius:6px; min-height:28px; }}
QScrollBar:horizontal {{ background:#EEF3F6; height:12px; margin:0; border:none; }}
QScrollBar::handle:horizontal {{ background:#B5C2CE; border-radius:6px; min-width:28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QStatusBar {{ background:#FFFFFF; border-top:1px solid {COLORS['border']}; color:{COLORS['muted']}; }}
"""


def app_icon(name: str) -> QIcon:
    p = icon_path(name)
    return QIcon(str(p)) if p.exists() else QIcon()

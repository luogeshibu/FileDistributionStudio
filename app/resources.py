from __future__ import annotations
from pathlib import Path
import sys


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "resources"
    return project_root() / "resources"


def asset_path(name: str) -> Path:
    return resource_root() / "assets" / name


def icon_path(name: str) -> Path:
    return resource_root() / "icons" / f"{name}.svg"


def script_path(name: str) -> Path:
    return resource_root() / "scripts" / name

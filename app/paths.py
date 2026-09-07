from pathlib import Path
import os


def app_data_dir() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if base:
        path = Path(base) / "FileDistributionStudio"
    else:
        path = Path.home() / ".file_distribution_studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    p = app_data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = app_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def audit_dir() -> Path:
    p = app_data_dir() / "audit"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_data_dir() / "fds.db"


def settings_path() -> Path:
    return app_data_dir() / "settings.json"

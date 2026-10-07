"""Retired WinRM distribution compatibility surface.

Formal file distribution is implemented by :mod:`agent_distribution` and
uses the FDS Agent HTTPS API.  These names remain import-compatible for old
task objects, but fail before any network operation so WinRM cannot be used
accidentally by this branch.
"""

from __future__ import annotations


def _removed(*args, **kwargs):
    raise RuntimeError(
        "当前 Windows Agent 分支已移除 WinRM 分发。"
        "请安装 FDS Agent，并使用 Agent HTTPS 配对。"
    )


def distribute_plan_to_host_winrm(*args, **kwargs):
    return _removed(*args, **kwargs)


def test_winrm_targets(*args, **kwargs):
    return _removed(*args, **kwargs)

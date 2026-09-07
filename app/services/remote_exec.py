from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
import base64
import json
import ntpath
import re

import winrm

from .. import db


@dataclass
class RemoteActionPlan:
    # enabled 只控制“分发前/后附加操作”；WinRM 文件分发本身始终需要本计划中的连接参数。
    enabled: bool = False
    use_https: bool = False
    port: int = 5985
    username: str = ""
    password: str = ""
    pre_commands: list[str] | None = None
    kill_processes: list[str] | None = None
    post_commands: list[str] | None = None
    post_on_failure: bool = True
    command_timeout: int = 90
    ignore_tls_errors: bool = True
    upload_chunk_kb: int = 64
    command_workdir: str = ""
    command_execution_mode: str = "INTERACTIVE"

    def __post_init__(self):
        self.pre_commands = list(self.pre_commands or [])
        self.kill_processes = list(self.kill_processes or [])
        self.post_commands = list(self.post_commands or [])


class WinRMExecutor:
    """WinRM/PowerShell 统一远程执行器。

    v0.6.0 起文件上传、目录创建、备份、SHA256、服务和进程操作都通过同一条
    WinRM 会话完成，不再依赖 C$/D$/E$ 管理共享。
    """

    def __init__(self, host: str, plan: RemoteActionPlan):
        if not plan.username:
            raise ValueError("使用 WinRM 时必须填写 Windows 用户名。")
        if not plan.password:
            raise ValueError("使用 WinRM 时必须填写 Windows 密码。")

        scheme = "https" if plan.use_https else "http"
        endpoint = f"{scheme}://{host}:{plan.port}/wsman"
        kwargs = {
            "auth": (plan.username, plan.password),
            "transport": "ntlm",
            "read_timeout_sec": max(10, plan.command_timeout + 10),
            "operation_timeout_sec": max(5, plan.command_timeout),
        }
        if plan.use_https:
            kwargs["server_cert_validation"] = "ignore" if plan.ignore_tls_errors else "validate"
        self.session = winrm.Session(endpoint, **kwargs)
        self.host = host
        self.plan = plan

    @staticmethod
    def _decode(value: bytes | str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        for enc in ("utf-8", "gbk", "cp1252"):
            try:
                return value.decode(enc)
            except Exception:
                pass
        return value.decode("utf-8", errors="replace")

    @staticmethod
    def ps_quote(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def run_cmd(self, command: str, workdir: str = ""):
        """Run a CMD command through WinRM.

        ``workdir`` deliberately emulates running the same command from a local console
        opened in an application directory.  WinRM shells otherwise start in their own
        default directory, which can break vendor launchers that resolve config/plugins
        relative to the current working directory.
        """
        effective_workdir = (workdir or "").strip()
        wrapped = command
        if effective_workdir:
            safe_dir = effective_workdir.replace('"', '""')
            wrapped = f'cd /d "{safe_dir}" && {command}'
        started = monotonic()
        result = self.session.run_cmd("cmd.exe", ["/d", "/s", "/c", wrapped])
        duration_ms = int((monotonic() - started) * 1000)
        return {
            "exit_code": int(result.status_code),
            "stdout": self._decode(result.std_out).strip(),
            "stderr": self._decode(result.std_err).strip(),
            "duration_ms": duration_ms,
        }

    def run_ps(self, script: str):
        started = monotonic()
        result = self.session.run_ps(script)
        duration_ms = int((monotonic() - started) * 1000)
        return {
            "exit_code": int(result.status_code),
            "stdout": self._decode(result.std_out).strip(),
            "stderr": self._decode(result.std_err).strip(),
            "duration_ms": duration_ms,
        }

    def run_ps_streamed(self, script: str, chunk_size: int = 64 * 1024):
        """Run a potentially large PowerShell script without putting it on the command line.

        ``pywinrm.Session.run_ps`` encodes the *entire* script into PowerShell
        ``-EncodedCommand``.  Large orchestration scripts can therefore hit the Windows
        command-line limit before PowerShell even starts.  This helper launches only a
        tiny fixed bootstrap and sends the real UTF-8 script through WinRS/WSMan stdin.
        """
        script_bytes = (script or "").encode("utf-8")
        chunk_size = max(16 * 1024, min(int(chunk_size or 64 * 1024), 64 * 1024))
        bootstrap = r"""
$ErrorActionPreference='Stop'
$stdin=[Console]::OpenStandardInput()
$reader=[IO.StreamReader]::new($stdin,[Text.Encoding]::UTF8)
try { $scriptText=$reader.ReadToEnd() } finally { $reader.Dispose() }
& ([ScriptBlock]::Create($scriptText))
"""
        encoded = self._encode_powershell_script(bootstrap)
        protocol = self.session.protocol
        shell_id = None
        command_id = None
        started = monotonic()
        try:
            shell_id = protocol.open_shell()
            command_id = protocol.run_command(
                shell_id,
                "powershell.exe",
                ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-OutputFormat", "Text", "-EncodedCommand", encoded],
                console_mode_stdin=False,
            )
            if not script_bytes:
                protocol.send_command_input(shell_id, command_id, b"", end=True)
            else:
                for offset in range(0, len(script_bytes), chunk_size):
                    chunk = script_bytes[offset:offset + chunk_size]
                    end = offset + len(chunk) >= len(script_bytes)
                    protocol.send_command_input(shell_id, command_id, chunk, end=end)
            stdout, stderr, status_code = protocol.get_command_output(shell_id, command_id)
            return {
                "exit_code": int(status_code),
                "stdout": self._decode(stdout).strip(),
                "stderr": self._decode(stderr).strip(),
                "duration_ms": int((monotonic() - started) * 1000),
            }
        finally:
            if shell_id and command_id:
                try:
                    protocol.cleanup_command(shell_id, command_id)
                except Exception:
                    pass
            if shell_id:
                try:
                    protocol.close_shell(shell_id)
                except Exception:
                    pass


    def run_interactive_cmd(self, command: str, workdir: str = "", timeout_seconds: int | None = None):
        """Run a CMD command in a real logged-on Windows desktop session.

        WinRM itself is non-interactive.  For vendor launchers/GUI programs we keep
        WinRM as the only control channel, but create a short-lived Scheduled Task
        using the *already logged-on* desktop user's Interactive token.

        Important v0.6.16 detail: ``Win32_ComputerSystem.UserName`` can be empty for
        RDP/disconnected/console combinations even while an interactive desktop is
        visibly logged on.  We therefore discover desktop users primarily from the
        owner of ``explorer.exe`` processes (and their SessionId), then fall back to
        Win32_ComputerSystem.UserName.  If several desktop sessions exist, prefer the
        account whose leaf username matches the WinRM credential (for example ADMS).
        No password is written to the task or command line.
        """
        timeout = max(10, int(timeout_seconds or self.plan.command_timeout or 90))
        cmd_b64 = base64.b64encode((command or "").encode("utf-8")).decode("ascii")
        wd_b64 = base64.b64encode((workdir or "").encode("utf-8")).decode("ascii")
        token = f"FDS_{int(monotonic()*1000)}_{abs(hash((self.host, command))) & 0xFFFF:04X}"
        token_q = self.ps_quote(token)
        cmd_q = self.ps_quote(cmd_b64)
        wd_q = self.ps_quote(wd_b64)
        preferred_user_q = self.ps_quote((self.plan.username or "").strip())
        script = rf"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
$token = {token_q}
$cmdText = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({cmd_q}))
$workDir = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({wd_q}))
$preferredUser = {preferred_user_q}
$desktopUser = ''
$desktopSessionId = -1
$candidates = @()
$runner = $null
$outFile = $null
$errFile = $null
$exitFile = $null
$taskName = $null
$payload = $null

try {{
    # Win32_ComputerSystem.UserName is not reliable for RDP sessions.  explorer.exe
    # is a better signal for a real interactive Windows desktop.
    try {{
        $candidates = @(
            Get-Process -Name explorer -IncludeUserName -ErrorAction SilentlyContinue |
            Where-Object {{ $_.SessionId -gt 0 -and -not [string]::IsNullOrWhiteSpace($_.UserName) }} |
            ForEach-Object {{
                [pscustomobject]@{{ User = [string]$_.UserName; SessionId = [int]$_.SessionId }}
            }}
        )
    }} catch {{ $candidates = @() }}

    # Compatibility fallback for systems where Get-Process -IncludeUserName cannot
    # expose the owner from a remote management session.
    if ($candidates.Count -eq 0) {{
        try {{
            $tmp = @()
            foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -ErrorAction SilentlyContinue)) {{
                try {{
                    $owner = Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction Stop
                    if ($owner.ReturnValue -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$owner.User)) {{
                        $u = if ([string]::IsNullOrWhiteSpace([string]$owner.Domain)) {{ [string]$owner.User }} else {{ ([string]$owner.Domain + '\' + [string]$owner.User) }}
                        $tmp += [pscustomobject]@{{ User = $u; SessionId = [int]$p.SessionId }}
                    }}
                }} catch {{}}
            }}
            $candidates = @($tmp)
        }} catch {{ $candidates = @() }}
    }}

    $preferredLeaf = [string]$preferredUser
    if ($preferredLeaf.Contains('\')) {{ $preferredLeaf = ($preferredLeaf -split '\\')[-1] }}
    if ($preferredLeaf.Contains('@')) {{ $preferredLeaf = ($preferredLeaf -split '@')[0] }}
    $chosen = $null
    if (-not [string]::IsNullOrWhiteSpace($preferredLeaf)) {{
        $chosen = $candidates | Where-Object {{
            $leaf = ([string]$_.User -split '\\')[-1]
            $leaf -ieq $preferredLeaf
        }} | Select-Object -First 1
    }}
    if ($null -eq $chosen) {{ $chosen = $candidates | Select-Object -First 1 }}
    if ($null -ne $chosen) {{
        $desktopUser = [string]$chosen.User
        $desktopSessionId = [int]$chosen.SessionId
    }}

    # Final fallback for classic console logon.
    if ([string]::IsNullOrWhiteSpace($desktopUser)) {{
        try {{ $desktopUser = [string](Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).UserName }} catch {{}}
    }}
    if ([string]::IsNullOrWhiteSpace($desktopUser)) {{
        $seen = @($candidates | ForEach-Object {{ ([string]$_.User + ' [Session ' + [string]$_.SessionId + ']') }}) -join ', '
        if ([string]::IsNullOrWhiteSpace($seen)) {{ $seen = '未发现 explorer.exe 桌面会话' }}
        throw ('目标机没有检测到可用的已登录桌面用户。WinRM 已连接，但交互桌面用户检测为空。检测结果：' + $seen)
    }}
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {{
        throw '目标系统缺少 ScheduledTasks PowerShell 模块，无法使用登录桌面执行模式。'
    }}

    $base = Join-Path $env:ProgramData 'FileDistributionStudio\\interactive'
    [IO.Directory]::CreateDirectory($base) | Out-Null
    $runner = Join-Path $base ($token + '.ps1')
    $outFile = Join-Path $base ($token + '.out.txt')
    $errFile = Join-Path $base ($token + '.err.txt')
    $exitFile = Join-Path $base ($token + '.exit.txt')
    $taskName = 'FileDistributionStudio_' + $token
    $cmd64ForRunner = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmdText))
    $wd64ForRunner = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($workDir))
    $runnerText = @"
`$ErrorActionPreference = 'Continue'
`$ProgressPreference = 'SilentlyContinue'
`$cmdText = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$cmd64ForRunner'))
`$workDir = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$wd64ForRunner'))
if (-not [string]::IsNullOrWhiteSpace(`$workDir)) {{ Set-Location -LiteralPath `$workDir }}
try {{
    `$all = (& `$env:ComSpec /d /s /c `$cmdText 2>&1 | Out-String)
    `$code = if (`$null -eq `$LASTEXITCODE) {{ 0 }} else {{ [int]`$LASTEXITCODE }}
    [IO.File]::WriteAllText('$outFile', [string]`$all, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText('$exitFile', [string]`$code, [Text.Encoding]::ASCII)
    exit `$code
}} catch {{
    [IO.File]::WriteAllText('$errFile', [string]`$_.Exception.Message, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText('$exitFile', '1', [Text.Encoding]::ASCII)
    exit 1
}}
"@
    [IO.File]::WriteAllText($runner, $runnerText, (New-Object Text.UTF8Encoding($false)))
    $psExe = "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    $action = New-ScheduledTaskAction -Execute $psExe -Argument ('-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $runner + '"')
    $principal = New-ScheduledTaskPrincipal -UserId $desktopUser -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $startedAt = Get-Date

    try {{
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        $deadline = (Get-Date).AddSeconds({timeout})
        while ((Get-Date) -lt $deadline) {{
            if ([IO.File]::Exists($exitFile)) {{ break }}
            Start-Sleep -Milliseconds 250
        }}
        if (-not [IO.File]::Exists($exitFile)) {{
            $state = (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue).State
            throw ('交互式命令等待超时；任务状态=' + [string]$state + '。如果命令本身长期不退出，请使用能快速返回的启动器。')
        }}
        $code = [int]([IO.File]::ReadAllText($exitFile).Trim())
        $stdout = if ([IO.File]::Exists($outFile)) {{ [IO.File]::ReadAllText($outFile) }} else {{ '' }}
        $stderr = if ([IO.File]::Exists($errFile)) {{ [IO.File]::ReadAllText($errFile) }} else {{ '' }}
        $elapsed = [int]((Get-Date) - $startedAt).TotalMilliseconds
        $payload = [pscustomobject]@{{
            ExitCode = $code
            Stdout = $stdout
            Stderr = $stderr
            DurationMs = $elapsed
            InteractiveUser = $desktopUser
            InteractiveSessionId = $desktopSessionId
            LaunchError = ''
        }}
    }} finally {{
        if (-not [string]::IsNullOrWhiteSpace([string]$taskName)) {{
            try {{ Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
        }}
        foreach ($f in @($runner,$outFile,$errFile,$exitFile)) {{
            try {{ if ($null -ne $f -and [IO.File]::Exists([string]$f)) {{ [IO.File]::Delete([string]$f) }} }} catch {{}}
        }}
    }}
}} catch {{
    $payload = [pscustomobject]@{{
        ExitCode = 1
        Stdout = ''
        Stderr = ''
        DurationMs = 0
        InteractiveUser = $desktopUser
        InteractiveSessionId = $desktopSessionId
        LaunchError = [string]$_.Exception.Message
    }}
}}
$payload | ConvertTo-Json -Compress -Depth 4
"""
        started = monotonic()
        # The orchestration script is streamed through WinRS stdin.  Only a short
        # bootstrap lives on the remote command line, so large scripts cannot hit the
        # Windows command-line length limit.
        outer = self.run_ps_streamed(script)
        if int(outer.get("exit_code", 1)) != 0:
            detail = outer.get("stderr") or outer.get("stdout") or f"退出码={outer.get('exit_code')}"
            raise RuntimeError(f"交互式桌面命令启动失败：{detail}")
        text = (outer.get("stdout") or "").strip()
        if not text:
            raise RuntimeError("交互式桌面命令未返回执行结果。")
        try:
            payload = json.loads(text.splitlines()[-1])
        except Exception as e:
            raise RuntimeError(f"解析交互式桌面命令结果失败：{text}") from e
        launch_error = str(payload.get("LaunchError", "") or "").strip()
        if launch_error:
            raise RuntimeError(f"交互式桌面命令启动失败：{launch_error}")
        return {
            "exit_code": int(payload.get("ExitCode", 1)),
            "stdout": str(payload.get("Stdout", "") or "").strip(),
            "stderr": str(payload.get("Stderr", "") or "").strip(),
            "duration_ms": int(payload.get("DurationMs", int((monotonic()-started)*1000)) or 0),
            "interactive_user": str(payload.get("InteractiveUser", "") or "").strip(),
            "interactive_session_id": int(payload.get("InteractiveSessionId", -1) or -1),
        }

    def run_interactive_audited(self, task_id: str, phase: str, action: str, command: str,
                                allow_nonzero: bool = False, workdir: str = ""):
        result = self.run_interactive_cmd(command, workdir=workdir)
        user_note = result.get("interactive_user") or "当前登录桌面用户"
        audit_command = f"[interactive={user_note}] " + (command if not workdir else f"[cwd={workdir}] {command}")
        status = self._audit(task_id, phase, action, audit_command, result)
        if status == "FAILED" and not allow_nonzero:
            detail = result["stderr"] or result["stdout"] or f"退出码={result['exit_code']}"
            raise RuntimeError(f"交互式桌面命令执行失败 [{self.host}] {command}: {detail}")
        return result

    @staticmethod
    def _require_success(result: dict, action: str):
        if int(result.get("exit_code", 1)) != 0:
            detail = result.get("stderr") or result.get("stdout") or f"退出码={result.get('exit_code')}"
            raise RuntimeError(f"{action}失败：{detail}")
        return result

    def test(self):
        # 与现场手工 Invoke-Command 的验证语义保持一致：同时确认目标主机和实际登录身份。
        return self.run_cmd("hostname && whoami && echo FDS_REMOTE_OK")


    def list_drives(self) -> list[dict]:
        """读取目标 Windows 当前可用盘符。

        仅通过 WinRM 在目标主机本机调用 .NET DriveInfo，不依赖 SMB/管理共享。
        返回盘符、卷标、类型、总容量和可用容量，供分发映射对话框选择。
        """
        script = r"""
$ErrorActionPreference = 'Stop'
$drives = @([IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and ($_.DriveType -eq [IO.DriveType]::Fixed -or $_.DriveType -eq [IO.DriveType]::Removable) -and $_.Name -match '^[A-Za-z]:\\$' } | ForEach-Object {
    [pscustomobject]@{
        Name = $_.Name
        VolumeLabel = $_.VolumeLabel
        DriveType = $_.DriveType.ToString()
        TotalSize = [Int64]$_.TotalSize
        AvailableFreeSpace = [Int64]$_.AvailableFreeSpace
    }
})
$drives | ConvertTo-Json -Compress
"""
        r = self.run_ps(script)
        self._require_success(r, "读取远程盘符")
        text = (r.get("stdout") or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text.splitlines()[-1])
        except Exception as e:
            raise RuntimeError(f"解析远程盘符失败；返回：{text}") from e
        if isinstance(data, dict):
            data = [data]
        out = []
        for item in data or []:
            name = str(item.get("Name", "") or "").replace("/", "\\").upper()
            if not re.match(r"^[A-Z]:\\$", name):
                continue
            out.append({
                "name": name,
                "volume_label": str(item.get("VolumeLabel", "") or ""),
                "drive_type": str(item.get("DriveType", "") or ""),
                "total_size": int(item.get("TotalSize", 0) or 0),
                "free_size": int(item.get("AvailableFreeSpace", 0) or 0),
            })
        return sorted(out, key=lambda x: x["name"])

    def list_directories(self, path: str) -> list[dict]:
        """只读列出目标 Windows 某个目录下的直接子目录。

        该接口专门供 GUI 的“远程目录浏览器”懒加载使用：只读取一层，
        不递归扫描、不创建目录，也不依赖 SMB/管理共享。
        """
        raw = (path or "").strip().replace("/", "\\")
        if not re.match(r"^[A-Za-z]:\\(?:.*)?$", raw):
            raise ValueError(f"远程目录必须是绝对 Windows 路径：{path}")
        p = self.ps_quote(raw)
        script = rf"""
$ErrorActionPreference = 'Stop'
$p = {p}
if (-not [IO.Directory]::Exists($p)) {{ throw ('目录不存在：' + $p) }}
$items = @([IO.Directory]::GetDirectories($p) | Sort-Object | ForEach-Object {{
    $full = [string]$_
    $name = [IO.Path]::GetFileName($full.TrimEnd([char]92))
    [pscustomobject]@{{
        Name = $name
        FullName = $full
    }}
}})
$items | ConvertTo-Json -Compress
"""
        r = self.run_ps(script)
        self._require_success(r, f"读取远程目录 {raw}")
        text = (r.get("stdout") or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text.splitlines()[-1])
        except Exception as e:
            raise RuntimeError(f"解析远程目录失败：{raw}；返回：{text}") from e
        if isinstance(data, dict):
            data = [data]
        out = []
        for item in data or []:
            name = str(item.get("Name", "") or "").strip()
            full = str(item.get("FullName", "") or "").strip().replace("/", "\\")
            if not name or not full:
                continue
            out.append({"name": name, "path": full})
        return out

    def list_processes(self) -> list[dict]:
        """只读列出目标 Windows 当前进程，供 GUI 远程进程选择器使用。

        正式结束进程仍由 ``kill_process`` 按镜像名执行；该方法只查询，不修改远端。
        返回任务管理器友好描述、镜像名、PID、可执行路径和命令行（能读取时）。
        """
        script = r"""
$ErrorActionPreference = 'Stop'
$items = @(Get-CimInstance Win32_Process | ForEach-Object {
    $name = [string]$_.Name
    $pidValue = [int]$_.ProcessId
    $path = [string]$_.ExecutablePath
    $cmd = [string]$_.CommandLine
    $desc = ''
    if ($path -and [IO.File]::Exists($path)) {
        try {
            $desc = [string]([Diagnostics.FileVersionInfo]::GetVersionInfo($path).FileDescription)
        } catch {
            $desc = ''
        }
    }
    [pscustomobject]@{
        Name = $name
        ProcessId = $pidValue
        ExecutablePath = $path
        Description = $desc
        CommandLine = $cmd
    }
})
$items | Sort-Object Name, ProcessId | ConvertTo-Json -Compress -Depth 3
"""
        r = self.run_ps(script)
        self._require_success(r, "读取远程进程列表")
        text = (r.get("stdout") or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text.splitlines()[-1])
        except Exception as e:
            raise RuntimeError(f"解析远程进程列表失败；返回：{text}") from e
        if isinstance(data, dict):
            data = [data]
        out = []
        for item in data or []:
            image_name = str(item.get("Name", "") or "").strip()
            if not image_name:
                continue
            try:
                pid = int(item.get("ProcessId", 0) or 0)
            except Exception:
                pid = 0
            out.append({
                "image_name": image_name,
                "pid": pid,
                "path": str(item.get("ExecutablePath", "") or "").strip(),
                "description": str(item.get("Description", "") or "").strip(),
                "command_line": str(item.get("CommandLine", "") or "").strip(),
            })
        return out

    def test_write_path(self, target_path: str) -> dict:
        """通过远程 PowerShell 在真实 Windows 路径中创建/读回/删除探针。"""
        p = self.ps_quote(target_path)
        script = rf"""
$ErrorActionPreference = 'Stop'
$root = {p}
[IO.Directory]::CreateDirectory($root) | Out-Null
$probe = Join-Path $root ('.fds_probe_' + [Guid]::NewGuid().ToString('N') + '.tmp')
[IO.File]::WriteAllText($probe, 'FDS_WINRM_WRITE_PROBE', [Text.Encoding]::UTF8)
$text = [IO.File]::ReadAllText($probe, [Text.Encoding]::UTF8)
if ($text -ne 'FDS_WINRM_WRITE_PROBE') {{ throw '写入探针内容不一致' }}
Remove-Item -LiteralPath $probe -Force
[pscustomobject]@{{Path=$root; Writable=$true}} | ConvertTo-Json -Compress
"""
        result = self.run_ps(script)
        self._require_success(result, f"目标目录写入测试 {target_path}")
        return result

    def ensure_directory(self, path: str):
        p = self.ps_quote(path)
        r = self.run_ps(f"$ErrorActionPreference='Stop'; [IO.Directory]::CreateDirectory({p}) | Out-Null")
        return self._require_success(r, f"创建目录 {path}")

    def remove_path(self, path: str):
        p = self.ps_quote(path)
        r = self.run_ps(
            f"$ErrorActionPreference='Stop'; if (Test-Path -LiteralPath {p}) {{ Remove-Item -LiteralPath {p} -Force -Recurse }}"
        )
        return self._require_success(r, f"删除远程路径 {path}")

    def remove_empty_directory(self, path: str):
        """仅在目录确实为空时删除；用于安全收尾 .fds_tmp 的父目录。"""
        p = self.ps_quote(path)
        script = rf"""
$ErrorActionPreference='Stop'
$p={p}
if ([IO.Directory]::Exists($p)) {{
  if ([IO.Directory]::GetFileSystemEntries($p).Length -eq 0) {{
    [IO.Directory]::Delete($p, $false)
  }}
}}
"""
        r = self.run_ps(script)
        return self._require_success(r, f"清理空目录 {path}")

    def cleanup_staging_path(self, temp_root: str):
        """清理映射级临时目录，并在父级为空时逐级收掉任务目录与 .fds_tmp。"""
        self.remove_path(temp_root)
        task_root = ntpath.dirname(temp_root)
        staging_root = ntpath.dirname(task_root)
        if task_root:
            self.remove_empty_directory(task_root)
        if staging_root and ntpath.basename(staging_root).lower() == ".fds_tmp":
            self.remove_empty_directory(staging_root)

    def file_info(self, path: str, include_sha256: bool = False) -> dict:
        p = self.ps_quote(path)
        hash_block = ""
        if include_sha256:
            hash_block = r"""
$sha = ''
try {
  $sha = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()
} catch {
  $stream = [IO.File]::OpenRead($p)
  try {
    $alg = [Security.Cryptography.SHA256]::Create()
    try { $sha = ([BitConverter]::ToString($alg.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $alg.Dispose() }
  } finally { $stream.Dispose() }
}
"""
        script = rf"""
$ErrorActionPreference='Stop'
$p={p}
if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {{
  [pscustomobject]@{{Exists=$false;Size=0;Sha256=''}} | ConvertTo-Json -Compress
  exit 0
}}
$item=Get-Item -LiteralPath $p -Force
{hash_block}
if (-not (Get-Variable -Name sha -ErrorAction SilentlyContinue)) {{ $sha='' }}
[pscustomobject]@{{Exists=$true;Size=[Int64]$item.Length;Sha256=$sha}} | ConvertTo-Json -Compress
"""
        r = self.run_ps(script)
        self._require_success(r, f"读取文件信息 {path}")
        try:
            data = json.loads((r["stdout"].splitlines() or ["{}"])[-1])
            return {
                "exists": bool(data.get("Exists", False)),
                "size": int(data.get("Size", 0) or 0),
                "sha256": str(data.get("Sha256", "") or "").lower(),
            }
        except Exception as e:
            raise RuntimeError(f"解析远程文件信息失败：{path}；返回：{r['stdout']}") from e

    def free_space(self, path: str) -> int | None:
        """返回目标盘可用字节。UNC/无法识别卷时返回 None，不阻断写权限预检查。"""
        m = re.match(r"^([A-Za-z]):[\\/]", (path or "").strip())
        if not m:
            return None
        drive = m.group(1).upper()
        script = rf"""
$ErrorActionPreference='Stop'
$d = Get-PSDrive -Name '{drive}' -PSProvider FileSystem
[Console]::Write([Int64]$d.Free)
"""
        r = self.run_ps(script)
        if r["exit_code"] != 0:
            return None
        try:
            return int(r["stdout"].strip())
        except Exception:
            return None

    def copy_remote_file(self, source: str, destination: str, overwrite: bool = True):
        src = self.ps_quote(source)
        dst = self.ps_quote(destination)
        parent = ntpath.dirname(destination)
        parent_q = self.ps_quote(parent)
        force = "-Force" if overwrite else ""
        script = rf"""
$ErrorActionPreference='Stop'
[IO.Directory]::CreateDirectory({parent_q}) | Out-Null
Copy-Item -LiteralPath {src} -Destination {dst} {force}
"""
        r = self.run_ps(script)
        return self._require_success(r, f"远程备份 {source} → {destination}")

    def promote_temp_file(self, temp_path: str, destination: str):
        temp = self.ps_quote(temp_path)
        dest = self.ps_quote(destination)
        parent = self.ps_quote(ntpath.dirname(destination))
        script = rf"""
$ErrorActionPreference='Stop'
[IO.Directory]::CreateDirectory({parent}) | Out-Null
$temp={temp}; $dest={dest}
if (Test-Path -LiteralPath $dest -PathType Leaf) {{
  try {{
    [IO.File]::Replace($temp, $dest, $null, $true)
  }} catch {{
    Copy-Item -LiteralPath $temp -Destination $dest -Force
    Remove-Item -LiteralPath $temp -Force
  }}
}} else {{
  Move-Item -LiteralPath $temp -Destination $dest -Force
}}
"""
        r = self.run_ps(script)
        return self._require_success(r, f"提交远程文件 {destination}")

    @staticmethod
    def _encode_powershell_script(script: str) -> str:
        """Encode a small PowerShell bootstrap script for -EncodedCommand.

        File bytes are deliberately *not* embedded in this command.  pywinrm's
        Session.run_ps() places the whole script on the remote PowerShell command
        line; embedding Base64 file chunks there can hit the Windows command-line
        limit even for a file of only a few KiB.
        """
        return base64.b64encode(script.encode("utf-16-le")).decode("ascii")

    def upload_file(self, local_path: Path, remote_path: str, progress_cb=None, chunk_size: int | None = None):
        """通过 WinRM 标准输入流上传文件。

        v0.6.4 起不再把文件块 Base64 文本拼进 ``run_ps`` 命令行，而是只启动
        一个很小的远程 PowerShell 接收器，再通过 WSMan/WinRS stdin 发送原始
        二进制数据。这样既不依赖 SMB，也不会触发 Windows
        ``The command line is too long``。
        """
        local_path = Path(local_path)
        if not local_path.is_file():
            raise FileNotFoundError(str(local_path))

        # WSMan 的单个 Envelope 有大小限制；64 KiB 原始块在 Base64 SOAP
        # 封装后仍保留充足余量。允许调用方覆盖，但限制在保守范围内。
        chunk_size = int(chunk_size or max(16, min(64, self.plan.upload_chunk_kb)) * 1024)
        parent = ntpath.dirname(remote_path)
        self.ensure_directory(parent)
        remote_q = self.ps_quote(remote_path)

        receiver_script = rf"""
$ErrorActionPreference='Stop'
$p={remote_q}
$parent=[IO.Path]::GetDirectoryName($p)
if ($parent) {{ [IO.Directory]::CreateDirectory($parent) | Out-Null }}
$inputStream=[Console]::OpenStandardInput()
$outputStream=[IO.File]::Open($p,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::Read)
try {{
  $buffer=New-Object byte[] 65536
  while (($count=$inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {{
    $outputStream.Write($buffer,0,$count)
  }}
  $outputStream.Flush()
}} finally {{
  $outputStream.Dispose()
  $inputStream.Dispose()
}}
[Console]::Out.Write('FDS_UPLOAD_OK')
"""
        encoded = self._encode_powershell_script(receiver_script)
        protocol = self.session.protocol
        shell_id = None
        command_id = None
        total = local_path.stat().st_size
        sent = 0

        try:
            shell_id = protocol.open_shell()
            command_id = protocol.run_command(
                shell_id,
                "powershell.exe",
                ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-OutputFormat", "Text", "-EncodedCommand", encoded],
                console_mode_stdin=False,
            )

            with local_path.open("rb") as f:
                chunk = f.read(chunk_size)
                if not chunk:
                    protocol.send_command_input(shell_id, command_id, b"", end=True)
                else:
                    while chunk:
                        next_chunk = f.read(chunk_size)
                        protocol.send_command_input(
                            shell_id, command_id, chunk, end=(not next_chunk)
                        )
                        sent += len(chunk)
                        if progress_cb:
                            progress_cb(sent, total)
                        chunk = next_chunk

            stdout, stderr, status_code = protocol.get_command_output(shell_id, command_id)
            result = {
                "exit_code": int(status_code),
                "stdout": self._decode(stdout).strip(),
                "stderr": self._decode(stderr).strip(),
                "duration_ms": 0,
            }
            self._require_success(result, f"WinRM 流式上传 {local_path.name}")
            if "FDS_UPLOAD_OK" not in result["stdout"]:
                raise RuntimeError(
                    f"WinRM 流式上传 {local_path.name}失败：远端接收器未返回完成标记。"
                )
            return sent
        except Exception:
            try:
                self.remove_path(remote_path)
            except Exception:
                pass
            raise
        finally:
            if shell_id and command_id:
                try:
                    protocol.cleanup_command(shell_id, command_id)
                except Exception:
                    pass
            if shell_id:
                try:
                    protocol.close_shell(shell_id)
                except Exception:
                    pass

    def _audit(self, task_id: str, phase: str, action: str, command: str, result: dict):
        status = "SUCCESS" if result["exit_code"] == 0 else "FAILED"
        db.record_action(
            task_id, self.host, phase, action, command,
            status, result["exit_code"], result["stdout"], result["stderr"], result["duration_ms"]
        )
        return status

    def run_audited(self, task_id: str, phase: str, action: str, command: str,
                    allow_nonzero: bool = False, workdir: str = ""):
        result = self.run_cmd(command, workdir=workdir)
        audit_command = command if not workdir else f'[cwd={workdir}] {command}'
        status = self._audit(task_id, phase, action, audit_command, result)
        if status == "FAILED" and not allow_nonzero:
            detail = result["stderr"] or result["stdout"] or f"退出码={result['exit_code']}"
            raise RuntimeError(f"远程命令执行失败 [{self.host}] {command}: {detail}")
        return result

    def query_service_state(self, service_name: str):
        r = self.run_cmd(f'sc query "{service_name}"')
        if r["exit_code"] != 0:
            return None, r
        m = re.search(r"STATE\s*:\s*(\d+)", r["stdout"], re.IGNORECASE)
        return (int(m.group(1)) if m else None), r

    def stop_service(self, task_id: str, service_name: str, wait_seconds: int = 30):
        state, query = self.query_service_state(service_name)
        if state == 1:
            db.record_action(task_id, self.host, "PRE", "STOP_SERVICE",
                             f'sc stop "{service_name}"', "SKIPPED", 0,
                             "服务已经处于停止状态。", "", 0)
            return
        if state is None and query["exit_code"] != 0:
            self._audit(task_id, "PRE", "QUERY_SERVICE", f'sc query "{service_name}"', query)
            raise RuntimeError(f"无法查询服务 {service_name}：{query['stderr'] or query['stdout']}")

        self.run_audited(task_id, "PRE", "STOP_SERVICE", f'sc stop "{service_name}"', allow_nonzero=True)
        deadline = monotonic() + wait_seconds
        while monotonic() < deadline:
            state, _ = self.query_service_state(service_name)
            if state == 1:
                db.record_action(task_id, self.host, "PRE", "WAIT_SERVICE_STOPPED",
                                 service_name, "SUCCESS", 0, "已停止", "", 0)
                return
            sleep(0.8)
        db.record_action(task_id, self.host, "PRE", "WAIT_SERVICE_STOPPED",
                         service_name, "FAILED", 1, "", "等待服务停止超时", 0)
        raise RuntimeError(f"服务在 {wait_seconds} 秒内未停止：{service_name}")

    def start_service(self, task_id: str, service_name: str, wait_seconds: int = 30):
        state, query = self.query_service_state(service_name)
        if state == 4:
            db.record_action(task_id, self.host, "POST", "START_SERVICE",
                             f'sc start "{service_name}"', "SKIPPED", 0,
                             "服务已经处于运行状态。", "", 0)
            return
        if state is None and query["exit_code"] != 0:
            self._audit(task_id, "POST", "QUERY_SERVICE", f'sc query "{service_name}"', query)
            raise RuntimeError(f"无法查询服务 {service_name}：{query['stderr'] or query['stdout']}")

        self.run_audited(task_id, "POST", "START_SERVICE", f'sc start "{service_name}"', allow_nonzero=True)
        deadline = monotonic() + wait_seconds
        while monotonic() < deadline:
            state, _ = self.query_service_state(service_name)
            if state == 4:
                db.record_action(task_id, self.host, "POST", "WAIT_SERVICE_RUNNING",
                                 service_name, "SUCCESS", 0, "正在运行", "", 0)
                return
            sleep(0.8)
        db.record_action(task_id, self.host, "POST", "WAIT_SERVICE_RUNNING",
                         service_name, "FAILED", 1, "", "等待服务启动超时", 0)
        raise RuntimeError(f"服务在 {wait_seconds} 秒内未启动：{service_name}")

    def kill_process(self, task_id: str, image_name: str):
        probe = self.run_cmd(f'tasklist /FI "IMAGENAME eq {image_name}" /NH')
        probe_text = (probe["stdout"] + " " + probe["stderr"]).lower()
        if image_name.lower() not in probe_text:
            db.record_action(task_id, self.host, "PRE", "KILL_PROCESS",
                             f'taskkill /F /IM "{image_name}"', "SKIPPED", 0,
                             "进程当前未运行。", "", probe["duration_ms"])
            return
        self.run_audited(task_id, "PRE", "KILL_PROCESS", f'taskkill /F /T /IM "{image_name}"')



def qualify_windows_username(username: str, computer_name: str = "") -> str:
    """Return the WinRM username exactly as the operator entered it.

    v0.6.4 continues to avoid auto-prefixing simple local accounts with a discovered
    Computer Name.  In real Windows estates the display hostname, DNS hostname and
    NetBIOS/local-account authority can differ (and NetBIOS authorities can be truncated).
    Auto-rewriting ``ADMS`` to ``SOME-LONG-COMPUTER-NAME\\ADMS`` can therefore turn a
    known-good credential into an NTLM authentication failure.

    The field now follows PowerShell/Get-Credential semantics:
      * ``ADMS`` is sent as ``ADMS``;
      * ``DOMAIN\\user`` / ``COMPUTER\\user`` is sent unchanged;
      * ``user@example.com`` is sent unchanged.

    ``computer_name`` remains in the signature for call-site compatibility and host
    discovery is still free to use other protocols, but discovered identity is never
    allowed to rewrite credentials.
    """
    return (username or "").strip()


def describe_winrm_failure(host: str, plan: RemoteActionPlan, error) -> str:
    """Return a concise Chinese diagnosis for common WinRM failures.

    Keep protocol details in the message so field users can distinguish an unreachable
    listener from bad credentials or a Windows authorization policy rejection.
    """
    detail = str(error or "").strip() or "未知 WinRM 错误"
    low = detail.lower()
    scheme = "HTTPS" if plan.use_https else "HTTP"
    endpoint = f"{host}:{plan.port}"

    unreachable_tokens = (
        "max retries exceeded", "failed to establish a new connection",
        "connection refused", "actively refused", "timed out", "timeout",
        "no connection could be made", "winerror 10061", "winerror 10060",
    )
    if any(t in low for t in unreachable_tokens):
        return (
            f"无法连接 WinRM {scheme}:{plan.port}（{endpoint}）。"
            "目标机可能尚未启用 WinRM、监听端口未开放，或防火墙/网络策略阻止访问。"
            f"原始信息：{detail}"
        )

    if "401" in low or "unauthorized" in low or "credentials were rejected" in low or "invalid credentials" in low:
        return (
            f"WinRM 服务已可达，但服务器拒绝了认证账号 {plan.username!r}。"
            "这不一定代表密码错误：如果使用的是 ADMS 这类目标机本地管理员，Windows 的 WinRM/UAC 远程令牌限制也可能拒绝该账号。"
            "v0.6.5 对简单用户名会按输入原样发送，不再自动拼接发现到的 Computer Name。"
            "可在软件的“WinRM 配置向导”中导出目标机准备脚本；涉及本地管理员完整令牌的脚本会明确提示并提供恢复脚本。"
            f"原始信息：{detail}"
        )

    if "access is denied" in low or "0x80070005" in low or "e_accessdenied" in low:
        return (
            "WinRM 已到达目标机，但 Windows 拒绝了当前账号的远程管理权限。"
            "这通常是账号权限、WinRM 安全策略或本地管理员远程令牌限制导致；"
            "文件路径本身不是根因。优先使用现场已授权的域/部署账号，或由管理员统一配置 WinRM 权限。"
            f"原始信息：{detail}"
        )

    if "ssl" in low or "certificate" in low or "wrong version number" in low:
        return (
            f"WinRM {scheme} 连接的 TLS/证书配置不匹配。请确认目标端使用的是 "
            f"{'5986/HTTPS' if plan.use_https else '5985/HTTP'}。原始信息：{detail}"
        )

    return f"WinRM 操作失败：{detail}"

def split_items(text: str) -> list[str]:
    values = []
    for chunk in re.split(r"[;,\r\n]+", text or ""):
        value = chunk.strip()
        if value and value not in values:
            values.append(value)
    return values


def split_commands(text: str) -> list[str]:
    commands = []
    for raw in (text or "").splitlines():
        value = raw.strip()
        if not value:
            continue
        if value.startswith("#") or value.lower().startswith("rem "):
            continue
        commands.append(value)
    return commands

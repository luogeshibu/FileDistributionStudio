# 文件分发工作台 v0.6.16

## v0.6.16 登录桌面识别、执行顺序与滚轮优化

- 修复目标机明明存在已登录桌面用户，但“登录桌面”命令仍提示无交互用户的问题：不再只依赖 `Win32_ComputerSystem.UserName`，优先通过 `explorer.exe` 的实际进程所有者和 SessionId 识别控制台/RDP 桌面，并优先匹配当前 WinRM 用户（例如 ADMS）。
- 交互式任务编排改为返回干净 JSON 错误，不再把 PowerShell `CLIXML / Preparing modules for first use` 大段内容直接展示给用户。
- ADMS 默认远程操作顺序调整为：**WinRM/目标预检查 → ① 结束所选目标进程 → ② 分发前 CMD（例如 `sys_ctl stop`）→ ③ 文件分发 → ④ 分发后 CMD（例如 `sys_ctl start fast`）**。
- 主页面滚轮步长提高，长页面上下滚动更快；输入框/端口/下拉框仍保持滚轮防误改。


## v0.6.15 登录桌面命令流修复

“登录桌面”模式的调度编排脚本现在通过 WinRM/WinRS 标准输入流发送，不再把完整 PowerShell 脚本塞进 `-EncodedCommand`。这修复了 `sys_ctl stop` / `sys_ctl start fast` 等交互式桌面命令在启动前报 `The command line is too long` 的问题。控制通道仍然只有 WinRM。


中文优先的 Windows 多主机文件分发、远程部署、备份、校验与审计工具，使用 Python + PySide6 开发。

## v0.6.15 远程操作流程简化

- 不再要求填写 Windows Service 名称，也不再执行独立的 `sc stop/sc start` 服务控制。
- 需要停止业务程序时，直接在“① 结束目标进程”中使用 WinRM 进程选择器选择实际 EXE 镜像名。
- 当前固定流程为：WinRM 认证/目标预检查 → 结束目标进程 → 分发前 CMD → 文件分发 → 分发后 CMD。
- 如果业务自身有优雅启停命令（如 `sys_ctl stop` / `sys_ctl start fast`），继续放在分发前/后 CMD，并配合“命令工作目录”。

## v0.6.12 远程进程选择器

- 在“文件分发 → WinRM 连接与远程操作 → ① 结束目标进程”提供“选择远程进程”。
- 通过 WinRM 只读读取参考目标主机当前进程，不使用 SMB，不修改远端。
- 同时显示任务管理器友好描述、实际 EXE 镜像名、实例数、PID 和可执行路径，并支持搜索。
- 选择结果按 EXE 镜像名保存；正式第①步由 `taskkill /F /T /IM <image.exe>` 执行，因此同名多个实例会一起结束。
- 多主机任务只浏览一个明确的参考主机，最终会在所有勾选目标主机上执行同一组镜像名。
- 已配置但当前未运行的镜像名会保留显示，避免刷新进程列表时意外丢失原有配置。

## v0.6.11 界面与品牌整理

- 主界面显示 `NARI` 与 `国际业务部 / International Business Division` 品牌标识。
- 主机管理页的 WinRM 凭据只显示“默认凭据 / 自定义凭据 / 未设置”等状态，实际账号密码只在文件分发页管理。
- 远程操作区域按当前真实顺序展示：WinRM 认证/预检查 → 结束目标进程 → 分发前 CMD → 文件分发 → 分发后 CMD。
- 正式业务通道仍固定为 WinRM；SMB/Ping/RDP/DNS/NetBIOS 只用于发现、识别和诊断。

## v0.6.9 远程目录浏览器

配置本地文件、目录或 SFTP 分发映射时，目标目录输入框旁新增 **“浏览远程目录”**。程序不会尝试把目标机桌面的 Windows 文件选择器弹到管理机，而是在 File Distribution Studio 内部通过 WinRM 只读展示远程目录树。

- 根节点显示目标 Windows 的可用本地盘符、卷标、总容量和剩余空间。
- 展开某个盘符或目录时才通过 WinRM 读取该节点的**下一层子目录**，不递归扫描整个磁盘。
- 点击目录会把真实 Windows 路径带回目标目录，例如 `D:\ADMS\bin`。
- 多台目标主机同时勾选时，可在浏览器顶部选择一台“参考主机”；选择结果仍会在正式分发前对所有目标机逐台执行写入和空间预检查。
- 浏览操作只读，不创建/修改远程目录；无权限目录只显示读取失败。
- 全过程只使用每台主机已经解析好的 WinRM 凭据，不使用 SMB、`C$ / D$ / E$`。

## v0.6.8 远程盘符自动选择

分发映射对话框现在可以通过 WinRM 自动读取已勾选目标主机的 Windows 盘符。程序读取盘符、卷标、总容量和剩余空间后提供下拉选择；选择后自动写入真实目标路径前缀。多主机场景会显示每个盘符的覆盖数量，并用 `✓/⚠` 区分“所有主机均存在”与“仅部分主机存在”。整个盘符发现过程只使用每台主机自己的有效 WinRM 凭据，不通过 SMB 管理共享。

## v0.6.6 凭据管理

大多数 Windows 主机可以共用一套默认 WinRM 凭据；少数特殊主机可以设置自定义凭据覆盖默认值。主机表会显示“默认凭据 / 自定义凭据 / 缺少密码 / 未设置”。

默认密码和可选的主机自定义密码可以安全记住到当前 Windows 用户的 **Windows 凭据管理器**。用户名/覆盖关系可以写入 `settings.json`，但密码不会进入 `settings.json`、SQLite、JSONL、审计日志或命令行。

执行时每台主机只解析一次“有效凭据”，并在 WinRM 测试、正式认证、目录预检查、文件上传、备份、SHA256、结束进程和前后 CMD/PowerShell 中始终使用同一套凭据。


## v0.6.4 WinRM 文件上传修复

旧版文件上传会把 Base64 文件块直接放进 PowerShell 命令字符串。由于 `run_ps` 自身还会把整段脚本编码成 PowerShell `-EncodedCommand`，最终命令长度可能远超 Windows 限制，所以即使 6 KiB 左右的小文件也可能报 `The command line is too long`。

v0.6.4 改为 WinRM/WinRS 标准输入流：目标机只执行一个固定的小型 PowerShell 接收器，文件原始二进制通过 WSMan stdin 分块发送。用户不需要修改 WinRM 服务配置，也不需要 SMB 共享。

## v0.6.4 兼容性修复

如果 WinRM 已认证成功，但测试目标目录时报 `New-Item ... -LiteralPath` 参数不存在，这是旧版的 PowerShell 兼容性缺陷，不是目录权限问题。v0.6.4 已将目录创建统一改为 .NET `Directory.CreateDirectory`，无需在目标机额外配置。


## v0.6.4 核心变化：WinRM 统一业务通道 + 本地管理员配置向导

v0.6.4 的正式目标端分发流程不再依赖 `C$ / D$ / E$` Windows 管理共享。

用户只填写目标 Windows 的真实路径：

```text
C:\Program Files\NARI\bin
D:\ADMS\conf
E:\temp
F:\Update\data
```

程序通过 WinRM / PowerShell 在目标机上直接完成：

```text
连接 / 身份验证
→ 创建目标目录
→ 写权限探针
→ 磁盘空间检查
→ 结束目标进程（可选）
→ 上传到临时文件
→ 大小 / SHA256 校验
→ 备份旧文件
→ 正式替换
→ 最终校验
→ 执行后置命令（可选）
→ 审计记录
```

因此文件分发不需要用户理解 SMB、`E$` 或 UNC 管理共享。

> 前提：目标 Windows 已经启用 WinRM，并且所使用的 Windows 账号对目标目录和需要执行的远程操作具有权限。软件不能绕过 Windows 本身的访问控制。

## 多源 → 多目标目录

一次任务可以配置任意数量的映射，例如：

```text
D:\Release\core.dll       → D:\ADMS\dll
D:\Release\config.xml     → E:\ADMS\conf
D:\Release\translations  → F:\ADMS\translations
SFTP:/release/tool.exe     → C:\NariTech\bin
```

所有勾选 Windows 主机执行同一套映射计划。

目录支持：

```text
仅复制目录内容
复制目录本身
```

开始分发前会检查多个源是否最终写到同一个目标文件；发生冲突时直接阻止任务。

## WinRM 连接

文件分发页填写：

```text
Windows 用户：ADMS
密码：********  👁
WinRM：HTTP 5985 / HTTPS 5986
```

如果填写的是简单本地用户名，例如：

```text
ADMS
```

v0.6.6 会**按输入原样发送 `ADMS`**，不会再根据发现到的 Computer Name 自动改写用户名。
这是为了与 PowerShell `Get-Credential -UserName "ADMS"` 的现场验证方式保持一致，也避免长主机名 / NetBIOS 本地账号域名不一致导致 NTLM 认证失败。

如果填写的是：

```text
DOMAIN\deployuser
user@domain.example
```

则保持原样。


### WinRM 服务在线 ≠ 账号一定能登录

`winrm quickconfig`、`sc start WinRM` 和 5985 监听正常，只能证明 WinRM 服务已经可达。Windows 还会独立判断账号是否允许远程管理。

对于 `ADMS` 这类“目标机本地管理员但不是内置 Administrator”的账号，Windows 默认的 WinRM/UAC 远程限制可能直接拒绝 NTLM 会话，即使账号密码正确。v0.6.4 会把这种情况显示为“服务可达、账号被 Windows 拒绝”，不再简单提示为密码错误。主界面新增“WinRM 配置向导”，可导出：

- 标准 WinRM 服务准备脚本（不改注册表/UAC）；
- ADMS 等本地管理员 WinRM 模式脚本（明确确认后设置 `LocalAccountTokenFilterPolicy=1`）；
- 对应恢复脚本。

应用本身不会静默修改目标机注册表，修改动作只发生在用户明确导出并以管理员身份运行的目标机脚本中。


## WinRM 配置向导

文件分发页提供：

```text
WinRM 配置向导
```

目标机普通准备推荐导出：

```text
TARGET_PREP_WINRM.cmd
```

它执行现场约定的 `winrm quickconfig`、`sc query/config/start WinRM`、Listener、5985 和 `winrm id` 检查，不修改注册表。

如果目标机使用 `ADMS` 等本地 Administrators 成员账号，5985 已通但 `Invoke-Command` / 软件仍返回 AccessDenied，可由有权限的管理员明确选择：

```text
TARGET_PREP_WINRM_LOCAL_ADMIN.cmd
```

脚本会先记录 `LocalAccountTokenFilterPolicy` 原始状态，再在用户确认后设置为 `1`，并提供：

```text
TARGET_RESTORE_WINRM_LOCAL_ADMIN.cmd
```

用于恢复到脚本执行前状态。

## “测试 WinRM”

正式分发前建议点击：

```text
测试 WinRM
```

程序会对所有勾选主机执行：

```text
WinRM 连接
→ Windows 身份认证
→ 当前每一个目标目录创建/访问
→ 写入探针
→ 读回探针
→ 删除探针
```

通过后目标主机显示：

```text
WinRM 状态：可分发
```

这比单纯检查 5985/5986 端口更可靠。

## 在线检测

“测试在线状态”仍组合检查：

```text
Ping
TCP 445  SMB（兼容诊断）
TCP 3389 RDP
TCP 5985 WinRM
TCP 5986 WinRM HTTPS
```

v0.6.4 优先显示：

```text
在线（WinRM 可达）
```

Ping 不再作为唯一在线判断条件。

## 文件上传实现

Python 端使用 `pywinrm` 建立 NTLM WinRM 会话。

文件通过受控 Base64 分块上传到目标目录下的：

```text
<目标目录>\.fds_tmp\<TaskID>\<MappingID>\...
```

每个 WSMan stdin 数据块默认 64 KiB，并限制在 16–64 KiB 的保守范围；上传完成后在目标 Windows 上执行大小和可选 SHA256 校验，再提交到正式文件。

对于非常大的安装包，WinRM 的吞吐通常低于专用 SMB/SFTP 文件传输协议，但优点是分发、服务控制、进程控制和命令执行统一为一个 Windows 远程管理通道。

## 安全部署顺序

正式任务对每台主机执行：

```text
1. 建立本地任务审计链
2. 对全部选中主机执行 WinRM 前置身份验证
3. 只有全部认证通过后，才准备本地 / SFTP 分发源
4. 生成 Manifest 和任务 SHA256
5. 检查目标文件冲突
6. 全部目标目录 / 备份目录写权限预检查
7. 目标磁盘空间检查
8. 分发前 CMD（可选）
9. 结束目标进程（可选）
10. 结束进程（可选）
11. WinRM 上传临时文件
12. 临时文件大小 / SHA256 校验
13. 旧文件备份与校验
14. 正式替换
15. 正式文件大小 / SHA256 校验
16. 分发后命令（可选）
17. 分发后 CMD（可选）
18. 保存任务结果和审计
```

目标目录预检查失败时，不会先停止业务服务。

## 任意盘符 / 任意目录

目标路径属于分发映射，而不是主机属性。

支持：

```text
C:\...
D:\...
E:\...
F:\...
G:\...
```

例如同一个任务：

```text
a.dll → C:\Program Files\NARI\bin
b.xml → D:\ADMS\conf
c.exe → E:\NariTech\bin
lang\ → F:\Update\translations
```

只要目标机存在对应盘符，并且 WinRM 账号有权限，程序即可操作。

## 备份策略

“备份根目录”可留空：

```text
<目标目录>\.fds_backup\<TaskID>
```

也可填写目标机上的统一绝对路径，例如：

```text
F:\FDS_Backup
```

则保留原盘符和目录层级：

```text
F:\FDS_Backup\DIST-xxxx\D\ADMS\dll\a.dll
F:\FDS_Backup\DIST-xxxx\E\NariTech\bin\b.exe
```

v0.6.4 的自定义备份根目录限定为目标机本地绝对盘符路径（例如 `F:\FDS_Backup`），不接受 UNC 备份根目录，以避免 NTLM WinRM 的 double-hop/二次网络认证问题。

## 校验与审计

建议生产环境保持：

```text
☑ SHA256 强校验
☑ 覆盖前备份
☑ 分发前预检查
```

本地默认目录：

```text
%LOCALAPPDATA%\FileDistributionStudio\
```

包括：

```text
fds.db
settings.json
logs\app.log
audit\operations\YYYY-MM-DD.jsonl
audit\tasks\YYYY-MM-DD\DIST-xxxx\
    task.json
    manifest.json
    mapping_plan.json
    events.jsonl
    distribution.log
    summary.json
```

WinRM 密码如选择“记住”只保存到 Windows 凭据管理器；不会写入 settings.json、SQLite、JSONL、任务审计或命令行参数。

## 启动

第一次运行或升级后：

```text
run.bat
```

环境修复：

```text
setup.bat --ensure
```

控制台模式：

```text
run_console.bat
```

## Windows 打包

```text
build_windows.bat
```

或：

```powershell
.\build.ps1
```

## 主机发现与兼容识别

正式业务写入、备份、服务/进程控制和远程命令只走 WinRM。旧的 SMB 写入/管理共享业务模块已从 v0.6.4 移除。

为了提高 Windows 主机识别率，主机发现仍可只读使用 Ping、445、3389、DNS、NetBIOS、SMB/NTLM 和 SMB/WKSSVC；这些能力只负责发现/识别，不参与文件分发或远程修改。

> v0.6.4：未添加分发映射时也可以点击“测试 WinRM”，此时只验证 WinRM 连接/身份；添加映射后会额外验证每个真实目标目录的创建、写入、读取和删除。


## v0.6.6 主机管理复选框

主机管理页第一列为“选择”复选框，批量在线测试、验证主机名和删除等操作优先使用已勾选主机；“全选 / 取消全选”可快速批量选择。刷新主机状态不会清空当前勾选。

## `.fds_tmp` 为什么会出现

`.fds_tmp` 是程序在目标映射目录内建立的安全暂存区。文件先通过 WinRM 上传到临时位置，完成大小/SHA256 校验后再提交为正式文件，从而避免网络中断时直接破坏旧文件。v0.6.6 起任务结束会自动清理本任务临时目录，并在 `.fds_tmp` 为空时删除它；失败时也会尽力清理。断网或强制结束程序造成的残留，在确认没有分发任务运行后可以手工删除。

应用侧栏“使用帮助”已内置 WinRM 服务命令和 LocalAccountTokenFilterPolicy 查询/设置/恢复命令。


## v0.6.8 WinRM 命令工作目录

对于 `sys_ctl start fast` 之类依赖当前目录的控制程序，请在“WinRM 连接与远程操作”设置 `CMD 工作目录`，例如 `D:\ADMS\bin`。程序会在目标机执行等价于 `cd /d "D:\ADMS\bin" && sys_ctl start fast` 的命令。WinRM 本身仍是非交互式远程管理会话；需要直接显示到登录桌面的 GUI 程序与后台服务启动需要区别处理。
## v0.6.15 命令执行与记忆

- 软件会记住上次的分发前 CMD、分发后 CMD、已选择进程、命令工作目录和命令执行方式。
- `登录桌面（推荐）`：仍由 WinRM 控制，但临时通过 Windows 任务计划程序在目标机当前已登录桌面用户会话中执行 CMD，更接近人在目标机本地执行 `sys_ctl start fast` 的效果。
- `WinRM 后台`：保持传统非交互式 WinRM 命令方式，适合无 GUI、与桌面环境无关的后台命令。
- 登录桌面模式要求目标机已有用户登录，不安装 Agent，不在任务或命令行中保存密码。


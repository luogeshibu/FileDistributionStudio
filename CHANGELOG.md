# v0.6.18-zh-CN

- 修复 Windows `cmd.exe` 执行 `setup.bat` 时出现大量“不是内部或外部命令”的问题：根因是仓库中的 `.bat/.cmd` 使用 LF 行尾并包含 UTF-8 中文文本，Windows 批处理解析在部分环境下会把字节/行错误拆成命令。
- 所有 `.bat/.cmd` 入口脚本改为纯 ASCII 控制文本并强制 CRLF、无 BOM，避免系统代码页影响批处理语法。应用 GUI 和文档仍保持简体中文。
- `setup.bat` 继续支持 `--ensure / --verify / --recreate`，并改进 Python Launcher 查找，优先明确尝试 3.14→3.10 的 64 位解释器。
- PowerShell `.ps1` 脚本统一为 CRLF + UTF-8 BOM，兼容 Windows PowerShell 5.1 的 Unicode 读取。
- 新增 `.gitattributes` 与 `.editorconfig`，确保 Git clone/checkout 后 Windows 脚本仍为 CRLF，不会因开发机或 Git `core.autocrlf` 再次破坏。
- 不修改 v0.6.17 的 WinRM 文件分发、远程命令、进程、凭据、审计等业务行为。

# CHANGELOG


## v0.6.17-zh-CN - 2026-09-07

- Git 仓库整理版本：新增根目录 `.gitignore`。
- 默认忽略 `.venv`、Python 缓存、IDE 本地配置、PyInstaller `build/dist/release` 输出、EXE/ZIP 等生成物。
- 默认忽略运行时数据库、`settings.json`、日志、审计 JSONL、缓存/暂存目录和 `.fds_tmp`。
- 默认忽略 `.env`、证书/私钥、凭据/secret 文件和真实主机清单；仓库继续保留 `sample_hosts.csv`。
- 不包含“远程 GUI 自动点击/结果文件回收”需求；该需求暂不进入当前代码。
- 不改变 v0.6.16 的 WinRM 分发、交互桌面命令、进程选择、凭据、备份、校验和审计业务逻辑。

## v0.6.16-zh-CN - 2026-09-07

- 修复“登录桌面”模式错误判断无人登录：新增基于 explorer.exe 所有者 + SessionId 的交互桌面识别，并优先匹配当前 WinRM 用户；保留 Win32_ComputerSystem.UserName 兼容回退。
- 交互式任务编排统一返回 JSON 错误，抑制 PowerShell Progress/CLIXML 噪音，错误提示更直接。
- 远程操作固定顺序改为：预检查 → 结束目标进程 → 分发前 CMD → 文件分发 → 分发后 CMD。
- 长页面 QScrollArea 垂直滚轮步长提升至 64，提高右侧页面滚动速度；输入控件滚轮保护规则不变。
- 当远程操作已经开始而后续失败时，若启用“失败恢复”，仍会尝试执行分发后 CMD。

# Changelog

## v0.6.15-zh-CN - 2026-09-06

- 修复“登录桌面”交互式 CMD 在任务开始阶段报 `The command line is too long`。根因是 v0.6.14 将完整的任务计划编排 PowerShell 脚本交给 `pywinrm.Session.run_ps()`，脚本会被整体编码到 PowerShell `-EncodedCommand`，可能超过 Windows 远程命令行长度限制。
- 新增 WinRM/WinRS stdin 脚本流执行器：远端命令行只包含固定的小型 PowerShell bootstrap，真正的编排脚本以 UTF-8 数据通过 WSMan stdin 分块发送，不再进入命令行。
- “登录桌面”仍使用 WinRM 作为唯一控制通道，并继续通过临时 Scheduled Task 使用当前已登录桌面用户的 Interactive token 执行 `sys_ctl stop` / `sys_ctl start fast` 等命令。
- 不改变文件 WinRM 二进制流上传、进程结束、目录/备份/SHA256、凭据和审计策略。

## v0.6.14-zh-CN - 2026-09-06

- 记住上次远程操作设置：分发前 CMD、分发后 CMD、结束进程镜像名、命令工作目录、失败恢复开关、启用状态与命令执行方式会保存到本地设置；密码仍只按原有 Windows 凭据管理器策略保存。
- 新增“命令执行方式”：`登录桌面（推荐，最接近本机执行）` / `WinRM 后台（无界面）`。
- 登录桌面模式仍以 WinRM 为唯一控制通道，但通过目标 Windows 内置任务计划程序，以当前已登录桌面用户的 `Interactive` 令牌临时执行命令；不需要安装 Agent，也不会把密码写入任务或命令行。
- 解决 `sys_ctl start fast`、GUI/厂商 launcher 在 WinRM 非交互会话中与本机执行结果不同的问题；同时保留 `命令工作目录`。
- 交互任务执行完后会自动删除临时任务和 `%ProgramData%\FileDistributionStudio\interactive` 下的临时脚本/输出文件；目标机无人登录时明确报错，不静默退回。

# 更新日志

## v0.6.13-zh-CN - 2026-09-06

- 远程操作流程进一步简化：移除“停止服务”输入和“分发完成后重新启动服务”选项，不再要求用户提供 Windows Service 名称。
- 正式执行顺序调整为：`WinRM 认证/目标预检查 → ① 分发前 CMD → ② 结束目标进程 → ③ 文件分发 → ④ 分发后 CMD`。
- “② 结束目标进程”继续支持 WinRM 远程进程选择器，最终只按用户选中的真实 EXE 镜像名执行 `taskkill /F /T /IM <image.exe>`。
- 失败恢复语义收敛为“文件分发失败时仍尝试执行分发后 CMD”，不再包含服务启动恢复。
- 后端正式分发工作流已移除停止/启动服务步骤与对应计划字段，避免 UI 与真实行为不一致。
- 其余 WinRM-only 文件分发、远程目录浏览、远程盘符、分层凭据、Windows 凭据管理器、二进制流上传、CMD 工作目录、备份/SHA256/审计、品牌与启动页保持不变。

## v0.6.12-zh-CN - 2026-09-06

- 新增 WinRM 远程进程选择器：显示友好描述、镜像名、实例数、PID、可执行路径并支持搜索。
- 进程查询为只读 WinRM 操作；正式结束仍严格发生在固定流水线第③步。
- 选择语义与后端一致：按镜像名聚合，避免用户误以为只会结束某一个 PID。
- 多台目标主机时明确选择一个参考主机读取列表；最终同一组镜像名应用到所有勾选主机。
- 已配置但当前未运行的进程镜像名会继续保留在选择器中。

## v0.6.11-zh-CN - 2026-09-06

- 启动 Splash 重新设计：在首次打开 App 的启动页面中清晰展示 `NARI`、`国际业务部 / International Business Division`。
- 启动页新增作者信息：`作者：罗戈石布`。
- 清理旧 Splash 中遗留的硬编码旧版本 `v0.4.2`，版本号改为运行时动态绘制，后续升级不会再出现新旧版本叠字。
- 启动页技术标识调整为 `本地 · SFTP 源 · WinRM · 审计`，与当前正式业务全部通过 WinRM 的架构保持一致。
- 仍使用文字式 NARI / 国际业务部标识；未捆绑未经提供的官方图形 Logo 资产。后续如提供官方 PNG/SVG，可直接替换。
- 其余 v0.6.10 的 WinRM-only 分发、远程目录浏览、分层凭据、CMD 工作目录、执行流水线、备份/SHA256/审计等行为不变。

## v0.6.10-zh-CN - 2026-09-06

- 品牌展示：在主界面侧栏与顶部加入 `NARI`、`国际业务部 / International Business Division` 品牌标识；启动 Splash 同步显示 `NARI · 国际业务部`。未引入外部官方图形文件，避免把非官方图形误作正式 Logo；如后续提供官方 PNG/SVG，可直接替换为正式资产。
- 主机管理职责收敛：`WinRM 凭据`列改为纯状态展示，主机管理表禁止直接单元格编辑；账号/密码仍统一从“文件分发 → Windows 目标主机 → 凭据管理”配置。删除旧的“连接模式”展示列，业务通道固定为 WinRM。
- 远程操作界面重排为明确流水线，并与真实执行顺序一致：`WinRM 认证/目标预检查 → ① 分发前 CMD → ② 停止服务 → ③ 结束残留进程 → ④ 文件分发 → ⑤ 启动服务 → ⑥ 分发后 CMD`。
- `CMD 工作目录`放在命令流水线顶部，继续适配 `sys_ctl start fast` 等依赖当前目录的厂商启动器。
- 分发确认框补充完整执行顺序，避免用户误解“停止服务/结束进程/分发前 CMD”的先后关系。
- 保持 v0.6.9 WinRM 远程目录浏览器、v0.6.8 CMD 工作目录、v0.6.7 远程盘符、v0.6.5 分层凭据与 Windows 凭据管理器、WinRM-only 正式业务、二进制流上传、备份/SHA256/审计逻辑不变。


## v0.6.9-zh-CN - 2026-09-06

- 分发映射目标目录新增“浏览远程目录”，在 File Distribution Studio 内部通过 WinRM 只读浏览远程 Windows 盘符和目录树。
- 目录树采用懒加载：展开某个盘符/目录时只读取下一层子目录，不递归扫描整盘，降低大目录和多盘环境的延迟。
- 多主机场景支持选择“参考主机”进行浏览；选中的真实 Windows 路径仍会在正式分发前对所有勾选主机逐台执行写入与空间预检查。
- 浏览器显示盘符卷标和容量，点击目录即可回填 `D:\ADMS\bin` 等目标路径；也保留手工路径输入。
- 远程目录读取使用每台主机的有效 WinRM 凭据，只读、不创建目录、不使用 SMB/C$/D$/E$。
- 无权限/不存在目录只在浏览器中提示读取失败，不会修改远端。
- 保留 v0.6.8 的 CMD 工作目录能力以及此前 WinRM-only 文件分发、流式上传、凭据管理、盘符读取和审计能力。

## v0.6.8-zh-CN - 2026-09-06

- 新增“CMD 工作目录”，用于 WinRM 分发前/后自定义命令。
- `sys_ctl start fast` 等依赖当前目录的厂商启动器可设置例如 `D:\ADMS\bin`，程序会执行等价于 `cd /d "D:\ADMS\bin" && sys_ctl start fast` 的命令。
- 工作目录会保存在本地 `settings.json`，不包含密码；审计日志记录工作目录和命令，便于追溯。
- 使用帮助新增 WinRM 非交互式会话说明：GUI/桌面程序与 Windows 服务/后台控制程序的执行环境不同。
- 文件分发、目录、备份、校验、服务、进程仍全部使用 WinRM。

# Changelog

## v0.6.7-zh-CN - 2026-09-06

- 分发映射目标目录新增“远程盘符”自动读取与选择。
- 盘符、卷标、总容量和剩余空间全部通过 WinRM + `.NET DriveInfo` 读取，不依赖 SMB 管理共享。
- 单主机直接列出该机可用盘符；多主机显示每个盘符的覆盖数量，`✓` 表示所有勾选主机均存在，`⚠` 表示仅部分主机存在。
- 选择远程盘符会自动更新真实 Windows 目标路径前缀，同时保留已有子目录。
- 本地文件、目录以及 SFTP 源映射都支持相同的远程盘符选择能力。
- 读取盘符时按每台主机解析“默认凭据/自定义凭据”，密码仍不进入日志或配置文件。

## v0.6.6-zh-CN - 2026-09-06

- 主机管理表新增“选择”复选框列，并新增“全选 / 取消全选”；批量在线测试、主机名验证、删除等操作优先使用勾选主机，同时继续兼容旧的高亮行操作。刷新状态时保留主机管理复选框状态。
- 新增侧栏“使用帮助”页面，内置 WinRM quickconfig、服务查询/启停、Listener、5985、`winrm id`、启动类型，以及 `LocalAccountTokenFilterPolicy` 查询/开启/设 0/删除/再次确认的完整 CMD 命令和风险说明。
- 帮助页提供“复制 WinRM 命令”“复制 Remote UAC 命令”和“打开 WinRM 配置向导”。
- 解释并修复 `.fds_tmp` 残留：它是 WinRM 安全暂存目录，用于临时上传、大小/SHA256 校验和正式提交，避免直接覆盖损坏正式文件。
- 任务结束后现在无论成功或失败都会尽力清理映射级临时目录；随后仅在为空时删除任务级目录和 `.fds_tmp` 根目录，避免影响并发任务。若断网或强制退出导致无法清理，会写入审计提示。
- 正式目标侧业务仍为 WinRM-only；SMB/Ping/RDP/DNS/NetBIOS 继续只用于发现、识别和辅助诊断。

## v0.6.5-zh-CN - 2026-09-06

- 新增“默认 WinRM 凭据 + 每主机自定义凭据”模型：大多数主机直接使用顶部默认账号/密码，少数特殊主机可以单独覆盖。
- 目标主机表新增“凭据”列，显示“默认凭据 / 自定义凭据 / 缺少密码 / 未设置”，绝不显示密码。
- 新增“设置选中凭据”“批量设置凭据”“恢复默认凭据”。Ctrl/Shift 可高亮多行；批量按钮可直接对当前勾选的分发目标应用同一套自定义凭据。
- “测试 WinRM”、正式分发前认证、目标目录预检查、文件上传、备份、SHA256、服务/进程操作和前后命令全部使用每台主机解析后的同一套有效凭据，避免测试与执行凭据不一致。
- 默认账号/密码现在可以跨启动记住。用户名保存到 settings.json；密码仅写入当前 Windows 用户的 Windows 凭据管理器（Generic Credential），不进入 settings.json、SQLite、JSONL、审计日志或命令行。
- 自定义主机凭据也支持“安全记住”；主机自定义用户名保存在设置中，密码仍只进入 Windows 凭据管理器。若不勾选安全记住，密码仅保留到本次程序退出。
- 删除主机或恢复默认凭据时，会同步清理该主机在 Windows 凭据管理器中的 FDS 自定义凭据。
- 保留 v0.6.4 WinRM/WinRS stdin 二进制流上传及 WinRM-only 正式业务通道。

## v0.6.4-zh-CN - 2026-09-06

- 修复 WinRM 文件上传报 `The command line is too long`：旧实现把每个文件块先 Base64，再嵌入 PowerShell `run_ps` 命令，pywinrm 还会再次将整个脚本编码为 `-EncodedCommand`，因此几 KiB 文件也可能触发 Windows 命令行长度上限。
- 文件上传改为 **WinRM/WinRS stdin 二进制流**：远端只启动一个小型 PowerShell 接收器，文件正文通过 WSMan 标准输入流发送，不再进入命令行。
- 默认流式块大小 64 KiB，并限制在 16–64 KiB 的保守范围，避免 WSMan Envelope 过大。
- 上传结束必须收到远端 `FDS_UPLOAD_OK` 完成标记；异常时仍清理临时文件、WinRM command 和 shell。
- 正式业务仍保持 WinRM-only，不恢复 SMB/C$/D$/E$ 分发。

## v0.6.3-zh-CN - 2026-09-06

## v0.6.2-zh-CN - 2026-09-06
- 修复 WinRM 本地账号认证方式：简单用户名（如 `ADMS`）现在按输入原样发送，不再自动拼接主机发现得到的 Computer Name。现场已验证 `Get-Credential -UserName "ADMS" + Invoke-Command -Authentication Negotiate` 可成功，因此应用行为与该方式对齐。
- 避免长 Windows 主机名 / DNS 名称与 NetBIOS 本地账号 authority 不一致时，把有效的 `ADMS` 错误改写成不可认证的 `COMPUTER\ADMS`。
- “测试 WinRM”现在同时执行 `hostname`、`whoami` 和目录写入探针；成功消息会显示远端实际主机和登录身份。
- 新增“WinRM 配置向导”：可从应用内保存/复制目标机准备脚本。
- 新增 `TARGET_PREP_WINRM_LOCAL_ADMIN.cmd`：在用户明确确认后，为 ADMS 等本地管理员设置 `LocalAccountTokenFilterPolicy=1`；执行前保存原始状态。
- 新增 `TARGET_RESTORE_WINRM_LOCAL_ADMIN.cmd`：按保存的原始状态恢复该策略。
- `TARGET_PREP_WINRM.cmd` 继续只执行 `winrm quickconfig`、WinRM 服务、Listener、5985、`winrm id` 等标准准备，不修改注册表/UAC。
- PyInstaller 包现在包含 `resources/scripts`，发布后的 EXE 同样可从配置向导导出脚本。
- 正式业务写入路径进一步收紧为 WinRM-only：移除未使用的 SMB 写入兼容模块及管理员共享路径转换帮助函数。SMB/445 仅保留在主机发现、Windows 身份识别和辅助诊断。
- 主机编辑中的目标连接模式固定显示 WinRM，避免普通用户误选已不参与正式业务的旧 SMB/UNC 模式。

## v0.6.1-zh-CN
- 明确业务传输边界：文件上传、目录创建、备份、SHA256、停/启服务、杀进程、前后 CMD/PowerShell 全部只使用 WinRM。
- Ping、TCP 445/3389、DNS、NetBIOS、SMB/NTLM/WKSSVC 仅用于主机发现、Windows 身份识别和辅助诊断，不参与正式分发。
- 正式任务新增 WinRM 前置认证闸门：所有勾选主机必须先通过 WinRM 身份验证，才开始读取 SFTP/本地源、生成清单和 SHA256；避免认证已失败仍继续准备任务。
- WinRM 401/credentials rejected 中文诊断不再简单等同于“密码错误”，会明确提示本地非内置管理员可能受到 Windows WinRM/UAC 远程限制，并显示实际认证账号。
- 主机管理主界面移除“测试 SMB”操作入口，降低普通用户对 SMB 管理共享的依赖和认知负担；SMB 兼容代码仍保留用于识别/诊断。
- WinRM 正式目标路径限制为目标机本地绝对盘符路径（C:\\、D:\\、E:\\、F:\\...），避免 NTLM WinRM 访问集中 UNC 备份时触发 double-hop 问题。
- TARGET_PREP_WINRM.ps1 / .cmd 仅执行标准 WinRM quickconfig、服务、Listener 与端口检查，不修改注册表、TrustedHosts、UAC 或账号权限。


## v0.6.0 - 2026-09-06

- **目标 Windows 文件分发主通道改为 WinRM**：不再要求文件目标目录通过 `C$ / D$ / E$` 管理共享访问。
- 用户在映射中继续直接填写目标机真实路径，例如 `C:\Program Files\NARI`、`D:\ADMS\bin`、`E:\temp`、`F:\Update`。
- 文件上传、目录创建、写入探针、备份、临时文件、正式替换、文件大小和 SHA256 校验全部通过 WinRM / PowerShell 完成。
- 分发前 CMD、停止服务、结束进程、启动服务、分发后 CMD 与文件传输复用同一 WinRM 连接模型。
- 分发页移除“测试 SMB”作为前置步骤，改为 **测试 WinRM**：同时验证身份认证以及当前所有目标目录的实际写入能力。
- 新增主机 `winrm_status` 持久化状态；分发目标表直接显示“WinRM 状态 / 可分发”。
- 在线检测优先显示 `在线（WinRM 可达）`，仍保留 445/3389 等端口作为辅助诊断。
- 本地账号可以只输入用户名（例如 `ADMS`）；当主机库已有真实 Computer Name 时，程序自动使用 `COMPUTER\ADMS` 进行 WinRM NTLM 身份认证。域账号 `DOMAIN\user` / UPN 保持原样。
- 目标路径不再转换为 UNC 管理共享；支持任意 Windows 盘符的绝对路径以及显式 UNC 路径（是否可访问由远端 WinRM 账号权限决定）。
- 保留多源→多目标映射、SFTP 来源拉取、本地审计、覆盖前备份、失败重试、SHA256、目标冲突检查和刷新后勾选状态保持。
- 密码仍只存在当前进程内，不写入 SQLite、JSONL、任务审计或命令行参数。
- 兼容代码仍保留 SMB 诊断/旧主机信息，但 v0.6.0 正式分发流程不依赖 SMB。

## v0.5.2 - 2026-09-06

- 修复“测试在线状态”完成后目标主机全部被重新勾选的问题。
- 修复根因：`refresh_hosts()` 不再在每次刷新时无条件 `setChecked(True)`。
- 分发页主机勾选状态现在按 Host/IP 保存并在在线测试、SMB 测试、主机名验证、主机编辑/刷新后恢复。
- 首次加载仍保持默认全选；已经存在用户选择状态后，新出现的主机默认不自动勾选，防止意外加入分发。
- 刷新主机数据时保留分发主机表和主机管理表的滚动位置。

## v0.5.0 - 2026-09-06

- 将文件分发模型从“单一来源 + 单一目标目录”升级为“多源 → 多目标目录分发映射”。
- 一次任务可同时添加多个本地文件、多个本地目录和多条 SFTP 映射，每条映射拥有独立目标目录。
- 目录映射支持“仅复制目录内容”和“复制目录本身”两种模式。
- 所有勾选 Windows 主机会执行同一套映射计划，支持一次任务跨 D:/E: 等不同盘符目录。
- 主机管理取消“默认目标目录”的业务含义；旧数据库字段仅保留兼容，不再在 UI 中要求用户配置。
- 新增 task_mappings 表；文件审计新增 mapping_id、source_path、target_path。
- 分发前新增目标文件冲突检查：多个源最终映射到同一个 Windows 目标文件时直接阻止任务。
- 自定义备份根目录在多目标场景中按任务 ID + 原盘符 + 原目录结构保存，避免同名文件备份冲突。
- 新增“测试在线状态”：Ping + TCP 445/3389/5985/5986 组合判断，不把 Ping 失败简单视为离线。
- 在线测试同时加入文件分发页和主机管理页；测试结果持久化到 hosts 表。
- 主机管理新增在线状态、Ping、SMB、RDP、WinRM、SMB 写权限状态和最后测试时间。
- “测试 SMB”升级为对当前全部目标目录逐一执行认证和写入探针。
- 正式分发仍保持安全顺序：所有 SMB/目录/备份/空间预检查通过后才执行 WinRM 停服务/结束进程。
- 保留 v0.4.5 原生 Unicode SMB MPR API、错误码中文诊断、完整审计、SHA256、多阶段备份校验、主机名识别和输入滚轮保护。

## v0.4.5 - 2026-09-06

- 修复 SMB 错误信息乱码：不再用 UTF-8 解码中文 Windows `net use` 输出。
- SMB 会话改用 Windows 原生 Unicode MPR API（`WNetAddConnection2W` / `WNetCancelConnection2W`）。
- 显式 SMB 密码不再暴露在子进程命令行参数中。
- 新增“测试 SMB”：批量验证认证、管理共享访问、目标目录读写能力。
- Windows 错误 5/53/67/86/1219/1326/1330/1909 增加中文原因与操作建议。
- 调整部署安全顺序：先完成 SMB / 写权限 / 磁盘空间预检查，确认可分发后才执行 WinRM 停服务和结束进程。
- Splash 版本号改为运行时动态绘制。
- 保留 v0.4.4 输入控件滚轮保护与页面正常滚动。

## v0.4.4 - 2026-09-06

- 修正 v0.4.3 全局滚轮保护范围过大的问题。
- 现在只禁止输入控件使用鼠标滚轮修改值。
- 不再拦截 `QAbstractSlider` / `QScrollBar`。
- 主页面右侧滚动条、表格滚动条、列表滚动、只读日志滚动全部恢复正常。
- `QSpinBox` / `QDoubleSpinBox` / 日期时间输入、`QComboBox`、`QLineEdit` 和可编辑多行输入仍保持滚轮保护。

## v0.4.3 - 2026-09-06

- 全局禁用所有“输入控件”的鼠标滚轮操作，避免滚动页面时误改配置。
- 覆盖 QSpinBox / QDoubleSpinBox / 日期时间类 SpinBox、QComboBox、QSlider/QDial 类、QLineEdit。
- 可编辑 QTextEdit / QPlainTextEdit 同样不响应滚轮；只读执行日志仍保留滚轮浏览能力。
- 采用 QApplication 级事件过滤器统一实现，主窗口、弹窗、动态创建输入控件以及后续新增控件无需逐个修改。
- 重点修复 WinRM 端口、SFTP 端口、失败重试、主机并发数、扫描线程数、磁盘余量等数值框因滚轮误触导致参数变化的问题。
- 保留 v0.4.2 的跨网段主机名增强识别，以及 v0.4.0+ 的完整本地审计、可配置备份、多阶段校验和远程部署流程。

## v0.4.2 - 2026-09-06

- 修复跨网段 Windows 主机“445/3389 可达但计算机名仍未识别”的核心问题。
- 快速发现新增 **Windows 工作站 API + SMB/NTLM 计算机身份指纹**：当 TCP 445 可达时，先使用 `NetWkstaGetInfo(100)` 获取目标 Computer Name；若不可用，再从 SMB/NTLM Challenge 的 TargetInfo 中读取 DNS/NetBIOS 计算机身份。两个快速步骤都不需要 WinRM，也不提供密码。
- 快速名称识别优先级调整为：Windows NetWkstaGetInfo(100) → SMB/NTLM → DNS PTR → NetBIOS (`nbtstat -A`) → Windows 名称解析 (`ping -a`) → 未识别。
- 新增 **SMB/WKSSVC 深度验证**：使用一次性 Windows 凭据通过 TCP 445 调用 Workstation Service 获取 Windows Computer Name，不要求目标机开启 5985/5986 WinRM。
- “深度验证主机名”新增自动模式：优先 SMB/WKSSVC，失败后才回退 WinRM `hostname`。
- 主机名来源新增 `SMB/NTLM 指纹` 与 `SMB/WKSSVC 验证`，并继续持久化名称来源、验证状态和说明。
- 主机名验证账号/密码只驻留当前进程，不写入 SQLite、JSONL、任务日志或审计详情。
- 新增 `impacket` 依赖；升级后运行一次 `setup.bat --ensure` / `run.bat` 会自动补齐依赖。
- 保留 v0.4.1 的多 CIDR 扫描，以及 v0.4.0 的完整本地审计、可配置备份、多阶段大小/SHA256 校验和 WinRM 部署流程。

## v0.4.1 - 2026-09-06

- 增强 Windows 主机名识别，不再只依赖 `socket.gethostbyaddr()` / DNS PTR。
- 快速发现新增 DNS PTR → NetBIOS (`nbtstat -A`) → Windows 名称解析 (`ping -a`) 多级名称提示。
- 新增 WinRM 主机名深度验证：用户明确触发后在目标 Windows 主机执行 `hostname` 获取实际 Computer Name。
- 主机发现新增“名称来源”“名称状态”“名称说明”列。
- 主机管理新增“名称来源”“名称状态”“名称说明”，并支持多选后批量 WinRM 验证名称。
- 同一扫描中多个 IP 返回相同主机名时提示“名称重复，建议验证”。
- 对 NetBIOS 15 字符限制进行显式提示，避免把截断名称误认为完整 Windows 主机名。
- 主机数据库新增 `hostname_source`、`hostname_verified`、`hostname_note` 字段，并兼容旧 `fds.db` 自动迁移。
- WinRM 验证成功/失败均写入本地操作审计；密码不保存、不进入审计。
- 完整保留 v0.4.0 的本地全链路日志、可配置备份目录、多阶段 SHA256/大小校验、WinRM 部署工作流。

## v0.4.0 - 2026-09-06

- 新增本地全链路审计：SQLite + app.log + 每日 operations JSONL + 每任务独立审计目录。
- 新增 audit_events、task_backups、task_verifications 数据表，并兼容旧 fds.db 自动迁移。
- 每条操作审计自动记录操作用户、操作计算机和软件版本。
- 新增“审计日志”主页面，可按分类 / 状态 / 关键词查询并导出 CSV。
- 分发历史详情新增“备份记录”“校验记录”“完整审计”。
- 备份根目录可由用户指定：支持目标机路径（例如 E:\FDS_Backup）和集中 UNC 共享；集中共享按任务/主机隔离；留空继续使用目标目录\.fds_backup。
- 修正备份重试安全性：原始文件只备份一次，重试不会覆盖真正的回滚备份。
- 新增 Manifest SHA256 总体指纹。
- 新增分发前预检查：本地审计链、目标/备份写权限和磁盘空间；审计目录无法建立时不执行远程变更。
- 文件大小校验始终执行；SHA256 可执行源、现有目标、备份、临时文件、正式文件多阶段强校验。
- 审计序列化自动脱敏 password / token / secret 等常见敏感字段。
- 系统设置新增本地审计目录、默认远程备份根目录、磁盘空间安全余量。

## v0.3.2-zh-CN - 2026-09-06

- 修复中文主界面残留英文：`Windows Targets` 改为“Windows 目标主机”。
- 对应英文说明改为中文。
- `Application Settings` 改为“系统设置”，说明文字同步中文化。
- 文件清单、SFTP 下载、目标路径错误等运行日志改为中文。
- WinRM 自动生成的状态文本和退出码提示进一步中文化。
- TARGET_PREP_WINRM.ps1 的用户提示信息改为中文。
- 保留 Windows、SFTP、SMB、WinRM、CIDR、SHA256、UNC、RDP 等标准技术名词。


## v0.3.1 - 2026-09-06

- 默认语言调整为简体中文，程序启动后第一屏即为中文界面
- 中文化左侧导航、顶部说明、分发页、主机管理、主机发现、历史、设置和任务详情
- 中文化主要提示框、执行日志、扫描状态及环境初始化脚本
- 来源类型改为中文显示，但内部仍使用 LOCAL / SFTP 稳定代码值，保持业务逻辑不变
- History / Task Detail 表头改为中文，并在显示层翻译任务状态、文件动作、远程操作阶段
- 新主机默认分组使用“默认”，扫描加入的主机使用“自动发现”
- 默认 Windows 字体优先 Microsoft YaHei UI
- Splash 启动画面重新制作成中文版本
- 保留 v0.3.0 多 CIDR 扫描，可同时扫描 172.16.22.0/24、172.16.21.0/24 等多个网段
- 保留 v0.3.0 自动创建 / 修复 .venv 的 setup.bat --ensure 机制


## v0.3.0 - 2026-09-06

- Rebuilt the desktop shell with a professional sidebar/topbar/card UI.
- Added original File Distribution Studio branding and complete Windows icon assets.
- Added splash screen and stable Windows AppUserModelID/runtime icon handling.
- Fixed environment bootstrap: `run.bat` now auto-ensures `.venv` and dependencies.
- Removed mandatory `pip install --upgrade pip`, which could interrupt setup before dependencies were installed.
- Added `setup.bat --ensure`, `--verify`, and `--recreate`.
- Added Python 3.10 x64 support to match existing field workstations.
- Added multi-CIDR Discovery with persistent scan ranges and combined progress.
- Supports scanning `172.16.22.0/24` and `172.16.21.0/24` in one job.
- Added formal PyInstaller spec/build/release workflow with embedded ICO and SHA256 release file.
- Preserved v0.2.0 WinRM pre/post CMD, service stop/start, process kill, SMB transfer, SHA256 verification, backup, retry and SQLite audit behavior.

## v0.5.2-zh-CN - 2026-09-06

- 所有密码输入框新增“小眼睛”显示/隐藏按钮，包括：Windows SMB/WinRM 密码、SFTP 密码、SMB 测试密码、主机名深度验证密码。
- 密码显示切换只影响当前界面显示，不写入 SQLite、JSONL 或任务审计文件。
- 强化 Windows 错误 5（拒绝访问）诊断：明确区分“主机/445 可达”与“管理共享授权成功”，并针对未限定账号（如 `ADMS`）、D$/E$ 管理共享、Remote UAC、共享/NTFS 权限给出中文建议。
- 文件分发页增加管理共享提示：D$/E$ 需要远程管理员令牌，生产环境优先使用域部署账号或专用 SMB 共享。
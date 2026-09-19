# 文件分发工作台 v0.6.17 快速使用

## v0.6.16 推荐的 ADMS 远程更新顺序

建议设置：

```text
命令工作目录：D:\ADMS\bin
命令执行方式：登录桌面
① 结束目标进程：通过“选择远程进程”勾选实际 EXE
② 分发前 CMD：sys_ctl stop
③ 文件分发：自动
④ 分发后 CMD：sys_ctl start fast
```

登录桌面模式会通过 WinRM 识别真实 `explorer.exe` 桌面用户/Session（包括常见 RDP 登录），再在该交互会话中执行命令。


## v0.6.15 登录桌面命令流修复

“登录桌面”模式的调度编排脚本现在通过 WinRM/WinRS 标准输入流发送，不再把完整 PowerShell 脚本塞进 `-EncodedCommand`。这修复了 `sys_ctl stop` / `sys_ctl start fast` 等交互式桌面命令在启动前报 `The command line is too long` 的问题。控制通道仍然只有 WinRM。


## 浏览远程目标目录

添加或编辑分发映射时，先勾选目标主机并准备好 WinRM 凭据，然后点击目标目录旁的：

```text
浏览远程目录
```

程序会在本应用内显示参考主机的远程目录树：

```text
C:\
D:\
  ADMS
    bin
    conf
E:\
```

目录树按需加载：只有展开节点时才读取下一层。多台主机时可以选择“参考主机”，选中的路径最终仍会在所有目标主机上逐台预检查。该浏览过程只读并且只走 WinRM。

## 自动读取远程盘符

添加或编辑分发映射时，如果已经勾选目标主机并配置好 WinRM 凭据，目标目录对话框会自动通过 WinRM 读取远程 Windows 的可用盘符、卷标、总容量和剩余空间。

- 只勾选 1 台主机：直接显示该主机的 C:\、D:\、E:\ 等可用盘符。
- 同时勾选多台主机：同一列表会标记盘符在多少台主机上存在；`✓` 表示所有勾选主机都有该盘，`⚠` 表示只有部分主机存在。
- 选择盘符后会自动填入目标目录前缀，仍可继续输入子目录，例如选择 `E:\` 后填写 `E:\ADMS\bin`。
- 盘符读取仅走 WinRM，不使用 SMB/C$/D$/E$。正式分发前仍会逐台执行目标目录写入和空间预检查。

## 1. 启动

直接运行：

```text
run.bat
```

环境有问题时：

```text
setup.bat --ensure
run_console.bat
```

## 2. 添加目标主机

可以在“主机发现”中扫描多个网段，例如：

```text
172.16.22.0/24
172.16.21.0/24
```

也可以在“主机管理”手工添加 IP / 主机。

## 3. 添加分发映射

在“文件分发”点击：

```text
添加文件
添加目录
添加 SFTP
```

目标目录直接填写目标 Windows 的真实路径：

```text
E:\temp
D:\ADMS\bin
C:\Program Files\NARI
F:\Update\data
```

不需要填写：

```text
\\HOST\E$
\\HOST\share
```

## 4. 填写 Windows 凭据

本地账号可以直接填写：

```text
ADMS
```

v0.6.6 会按输入原样使用 `ADMS`，不再自动拼接主机名。
如果现场明确要求 `COMPUTER\user` 或 `DOMAIN\user`，可以直接完整填写，程序会保持原样。

域账号填写：

```text
DOMAIN\deployuser
```

密码框右侧小眼睛可以临时显示 / 隐藏密码。默认勾选“记住默认凭据”时，密码只保存到当前 Windows 用户的 Windows 凭据管理器，不会写入 settings.json、SQLite、JSONL 或审计日志。

如果某台机器账号/密码不同：在主机表中高亮该主机，点击“设置选中凭据”；多台特殊机器使用同一套账号时，也可以勾选后点击“批量设置凭据”。需要恢复统一账号时点击“恢复默认凭据”。

## 5. WinRM 端口

默认：

```text
HTTP 5985
```

目标使用 HTTPS 时勾选：

```text
HTTPS → 5986
```

## 6. 先测试 WinRM

勾选目标主机后点击：

```text
测试 WinRM
```

它会测试：

```text
WinRM 连接
身份认证
所有当前目标目录
创建目录
写入探针
读取探针
删除探针
```

成功后显示：

```text
WinRM 状态：可分发
```

如果 5985 / 5986 不可达，说明目标 Windows 当前没有可用的 WinRM Listener 或网络策略阻止访问；软件无法绕过 Windows 本身关闭的远程管理通道。

如果端口可达但提示“服务器拒绝认证账号”，说明 WinRM 服务已经正常，失败点在 Windows 的账号远程授权，而不是文件路径或 SMB。对于普通本地管理员账号，这种情况可能由 Windows 默认 WinRM/UAC 远程限制导致；软件不会自动修改注册表或安全策略。


## 6.1 目标机 WinRM 配置向导

如果目标机还没准备好 WinRM，可点击：

```text
WinRM 配置向导
```

普通机器导出“标准 WinRM 服务准备”脚本；如果 `ADMS` 是本地管理员，5985 已开放但远程仍 AccessDenied，可在明确授权后选择“本地管理员 WinRM 模式”。
该模式会保存原始 `LocalAccountTokenFilterPolicy` 状态，并可导出恢复脚本。应用不会在后台静默修改目标机注册表。

## 7. 分发策略

建议：

```text
☑ SHA256 强校验
☑ 覆盖前备份
☑ 分发前预检查
失败重试：1
主机并发数：4
```

目标主机备份根目录可留空，也可填写：

```text
F:\FDS_Backup
```

## 8. 结束目标进程（可选）

如果只是传文件，不需要勾选：

```text
启用分发前 / 后操作
```

如果更新文件前需要停止业务程序：

```text
☑ 启用分发前 / 后操作

结束目标进程：
adms.exe; helper.exe
```

推荐直接点击“选择远程进程”，通过 WinRM 只读查看参考主机当前进程并按真实 EXE 镜像名勾选。程序正式执行时会对所选镜像名运行 `taskkill /F /T /IM <image.exe>`。

固定执行顺序：

```text
WinRM 连接
→ 全部目录预检查
→ 分发前 CMD
→ 结束目标进程
→ WinRM 文件上传
→ 备份 / 校验 / 替换
→ 分发后 CMD
```

如果目标路径或写权限预检查失败，不会先结束进程。业务自己的优雅停止/启动命令（例如 `sys_ctl stop`、`sys_ctl start fast`）放在分发前/后 CMD。

## 9. 开始分发

确认映射和主机后点击：

```text
开始分发
```

例如：

```text
本地 C:\Patch\test.txt
→ 172.16.21.115 的 E:\temp\test.txt
```

全程不需要 `E$` 管理共享。

## 10. 日志和历史

任务结束后查看：

```text
分发历史
审计日志
```

默认本地数据：

```text
%LOCALAPPDATA%\FileDistributionStudio\
```

可以追踪：

```text
源文件
映射 ID
目标主机
真实目标路径
备份路径
文件大小
SHA256
服务 / 进程操作
CMD
错误信息
最终状态
```

> v0.6.4：未添加分发映射时也可以点击“测试 WinRM”，此时只验证 WinRM 连接/身份；添加映射后会额外验证每个真实目标目录的创建、写入、读取和删除。


## v0.6.4 上传说明

v0.6.4 已将文件正文改为 WinRM/WinRS stdin 二进制流，不再把文件内容拼进 PowerShell 命令行。若旧版出现 `The command line is too long`，请直接升级，不需要修改目标 Windows。


## 主机管理批量选择

主机管理页使用第一列复选框选择主机，可配合“全选 / 取消全选”执行在线测试、主机名验证和删除等批量操作。

## 临时目录 `.fds_tmp`

这是 WinRM 分发的安全暂存目录，不是备份目录。程序会先把文件上传到这里并校验，再提交到正式路径。v0.6.6 起任务结束自动清理；若断网/强制退出导致残留，确认没有任务运行后可删除。

完整 WinRM 和 Remote UAC 命令可直接在应用左侧“使用帮助”查看和复制。


## 远程启动程序的工作目录

如果本机进入 `D:\ADMS\bin` 后执行 `sys_ctl start fast` 正常，而 WinRM 执行后程序提示找不到 host/配置，请在文件分发页的“CMD 工作目录”填写 `D:\ADMS\bin`，再将 `sys_ctl start fast` 放到分发前或分发后 CMD。


## 远程选择要结束的进程

1. 在“Windows 目标主机”先勾选本次参考/分发主机，并确保 WinRM 凭据可用。
2. 到“WinRM 连接与远程操作 → ③ 结束残留进程”，点击“选择远程进程”。
3. 选择参考主机，程序通过 WinRM 只读读取进程。可按显示名称、exe、PID 或路径搜索。
4. 勾选需要结束的应用后点击“使用所选进程”。字段中保存的是实际 EXE 镜像名。
5. 正式分发第③步才会执行结束操作；同名 EXE 的多个实例会一起结束。

建议只选择明确属于本次应用/ADMS 的进程，不要随意选择 Windows 核心系统进程。
## v0.6.15 命令执行与记忆

- 软件会记住上次的分发前 CMD、分发后 CMD、已选择进程、命令工作目录和命令执行方式。
- `登录桌面（推荐）`：仍由 WinRM 控制，但临时通过 Windows 任务计划程序在目标机当前已登录桌面用户会话中执行 CMD，更接近人在目标机本地执行 `sys_ctl start fast` 的效果。
- `WinRM 后台`：保持传统非交互式 WinRM 命令方式，适合无 GUI、与桌面环境无关的后台命令。
- 登录桌面模式要求目标机已有用户登录，不安装 Agent，不在任务或命令行中保存密码。



## 目标机 ADMS 一键 WinRM 准备

1. 目标机必须已经存在本地 `ADMS` 账号并且你知道其密码。
2. 在“Windows 目标主机”区域直接点击“下载 ADMS 设置脚本”（也可以从 WinRM 配置向导导出）。
3. 将 `TARGET_PREP_ADMS_WINRM.cmd` 复制到目标 Windows，以管理员身份运行并确认。
4. 回到管理机，在 File Distribution Studio 中填写 `ADMS` + 原密码，点击“测试 WinRM”。
5. 如需撤销准备，点击“下载 ADMS 还原脚本”并在目标机以管理员身份运行；脚本会恢复设置前保存的 `LocalAccountTokenFilterPolicy`、WinRM 启动方式和服务运行状态，不修改任何 ADMS 账号、用户组或 RDP 设置。

注意：一键脚本不会创建账号，也不会修改密码；它会改变目标机远程管理权限，仅限授权的受控网络使用。


> 历史说明：旧版 ADMS 脚本曾只设置 WinRM 和 `LocalAccountTokenFilterPolicy`，且还原时无条件删除该值。当前脚本会先检查 ADMS 权限并保存原始状态，再执行配置；还原时恢复原始状态。


## v0.6.24：WinRM 脚本与账号权限边界

应用提供的 `TARGET_PREP_ADMS_WINRM.cmd` 会检查 ADMS 已存在、已启用且属于 Administrators，然后启用/启动 WinRM、验证 HTTP 5985/防火墙/Negotiate，并设置 `LocalAccountTokenFilterPolicy=1`；不会创建、启用、禁用 ADMS 或修改用户组。`TARGET_RESTORE_ADMS_WINRM.cmd` 会恢复准备前保存的原始状态，也不会修改 ADMS、Administrators、Remote Desktop Users 或 RDP 权限。

如需现场手工检查 ADMS，请在“使用帮助 → ADMS 账号检查与管理员组（手工操作）”查看 `net user ADMS`、Administrators 成员查询以及手工加入命令。

## 建议的现场执行方式

正式批量升级前，先在“执行与日志”点击 **Dry Run 预演**。预演不会写目标机，只检查源清单、WinRM、路径和磁盘空间等条件。预演无失败后再点击“开始执行所选任务”。如果正式任务只有部分主机失败，可点击 **重试失败主机**；完成后可点击 **导出任务结果** 保存 Excel 证据。

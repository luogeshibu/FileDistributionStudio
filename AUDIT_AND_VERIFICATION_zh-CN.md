# 审计、备份与校验设计

## 1. 本地审计分层

文件分发工作台同时保留四类本地证据：

1. **SQLite 结构化记录**：便于界面检索、筛选、统计。
2. **应用日志 `app.log`**：便于排查程序异常。
3. **每日操作 JSONL**：记录程序启动、主机扫描、主机维护、连接测试、设置修改、导出等操作。
4. **每任务独立审计目录**：记录一次具体分发从创建到结束的完整证据。

每条通用操作审计还会自动写入：操作用户、操作计算机、软件版本、时间、分类、动作、状态、任务 ID、目标主机、对象和详细数据。

每个任务目录包括：

```text
task.json          任务输入和策略（密码字段自动脱敏）
manifest.json      本次分发源文件清单和 Manifest SHA256
mapping_plan.json  多源多目标映射计划、每条映射指纹和目标目录
events.jsonl       任务事件流
distribution.log   人类可读任务日志
summary.json       最终结果摘要
```

## 2. SQLite 表

```text
tasks                 任务级
task_hosts            主机级
task_files            文件级
task_actions          WinRM / CMD / 服务 / 进程操作
task_backups          备份记录
task_verifications    各阶段校验记录
audit_events          通用操作审计（含操作人、操作终端、程序版本）
hosts                 主机资产
```

## 3. 备份策略

覆盖已有文件时：

```text
原文件
  ↓
只创建一次原始备份
  ↓
校验备份大小
  ↓
SHA256 强校验（启用时）
  ↓
再允许正式替换
```

即使文件分发重试，也不会再次用已经更新或损坏的文件覆盖原始备份。

备份根目录支持三种方式：

```text
留空                        -> 目标目录\.fds_backup\任务ID
E:\FDS_Backup              -> 每台目标机 E: 盘\FDS_Backup\任务ID
\\BACKUP-SRV\FDS_Backup -> 集中共享\任务ID\目标主机
```

集中 UNC 共享会额外按目标主机隔离，防止不同机器中的同名文件相互覆盖。

## 4. 校验阶段

默认大小校验始终执行；SHA256 强校验可配置，生产环境建议开启。

```text
SOURCE_SIZE
SOURCE_SHA256
MANIFEST_SHA256
TARGET_WRITE_PERMISSION
BACKUP_WRITE_PERMISSION
TARGET_FREE_SPACE
BACKUP_FREE_SPACE
EXISTING_SHA256
BACKUP_SIZE
BACKUP_SHA256
TEMP_SIZE
TEMP_SHA256
FINAL_SIZE
FINAL_SHA256
```

## 5. 分发前预检查

在真正覆盖文件前检查：

- WinRM 连接和身份验证通过
- 目标目录可创建 / 可写（WinRM 写入探针）
- 备份目录可创建 / 可写
- 目标卷空间
- 备份卷空间

磁盘空间检查默认带安全余量，可在系统设置修改。

## 6. 凭据保护

SFTP 密码仍只用于当前运行过程。WinRM 默认/自定义密码如用户选择“记住”，仅保存到当前 Windows 用户的 Windows 凭据管理器；任何密码都不会写入 settings.json、SQLite、JSONL 或任务审计。

任务 JSON / 操作审计对以下常见字段自动脱敏：

```text
password
passwd
pwd
secret
token
credential
authorization
```

注意：用户手工填写的远程 CMD 文本本身属于审计对象，因此不要在命令文本里直接写明文密码或 Token。


## v0.4.3 主机名验证审计

快速 SMB/NTLM 名称识别作为主机发现事件写入审计，记录 IP、名称来源、验证状态和说明。用户主动执行 SMB/WKSSVC 或 WinRM 深度验证时，记录验证方式、目标 IP、成功/失败和名称结果；Windows 密码不写入审计。

## v0.5.2 多源多目标映射审计

v0.5.2 新增 `task_mappings`，每条映射记录：

```text
mapping_id
source_type
source_path
target_path
source_kind
folder_mode
file_count
total_bytes
manifest_sha256
```

`task_files` 同时记录实际 `source_path` 和实际 `target_path`，因此文件级审计不再只依赖相对路径。

自定义备份根目录在多目标目录场景下保存原始盘符和目录结构，例如：

```text
D:\ADMS\dll\a.dll -> <Backup>\<Task>\D\ADMS\dll\a.dll
E:\NariTech\b.exe -> <Backup>\<Task>\E\NariTech\b.exe
```

任务开始前会对全部映射进行目标冲突检查；如果两个源最终写向同一目标文件，任务在任何远程变更发生前终止。

## v0.5.2 主机状态审计

主机库持久记录：

```text
online_status
ping_ok
smb_port_ok
rdp_port_ok
winrm_port_ok
smb_status
last_test_at
```

“在线测试”使用 Ping + 445/3389/5985/5986 组合判断。v0.6.2 正式分发页的“测试 WinRM”执行 WinRM 身份验证和真实目标目录写入探针，并持久记录 `winrm_status`。


## v0.6.1 WinRM 统一分发审计

v0.6.1 的正式文件分发不再依赖 `C$ / D$ / E$` 管理共享。每台主机在任何服务/进程变更之前，先记录并验证：

```text
WINRM CONNECT
TARGET_WRITE_PERMISSION
BACKUP_WRITE_PERMISSION
TARGET_FREE_SPACE
```

文件级审计仍记录 `mapping_id / source_path / target_path`，其中 `target_path` 是目标 Windows 的真实路径，例如 `E:\temp\a.dll`，不再记录转换后的管理共享路径。

临时上传目录：

```text
<目标目录>\.fds_tmp\<TaskID>\<MappingID>
```

覆盖旧文件时仍执行“先备份并验证，再正式替换”。显式集中 UNC 备份根目录按 `TaskID + Host + 原盘符/目录结构` 隔离；目标机本地备份盘按 `TaskID + 原盘符/目录结构` 保存。

WinRM 用户名可持久化；密码若选择“记住”仅进入 Windows 凭据管理器，否则只存在当前进程内。密码不会写入 settings.json、SQLite、JSONL、task.json、mapping_plan.json、命令行或操作审计。


## v0.6.2 WinRM 本地账号与配置向导审计边界

- 简单 WinRM 用户名（如 `ADMS`）按用户输入原样发送，不再根据发现到的主机名自动改写。
- 主机发现仍可使用 SMB/NTLM/WKSSVC，但仅用于身份识别；正式写入路径没有 SMB 管理共享代码。
- “WinRM 配置向导”只在本机导出/复制目标机脚本，不会静默远程修改目标机注册表。
- `TARGET_PREP_WINRM_LOCAL_ADMIN.cmd` 在目标机管理员明确确认后才设置 `LocalAccountTokenFilterPolicy=1`，并在 `%ProgramData%\FileDistributionStudio` 记录原始状态供恢复脚本使用。
- 配置脚本本身不包含 Windows 密码；应用仍不持久化 Windows 密码。


## v0.6.4 WinRM PowerShell 兼容性修复

目标目录创建不再使用 `New-Item -LiteralPath`。Windows PowerShell 的 `New-Item` 不提供该参数，因此 v0.6.4 统一使用 .NET `System.IO.Directory.CreateDirectory` 创建真实本地路径。写入探针、备份父目录和正式提交父目录均复用同一兼容策略。文件查询/复制/移动/删除仍使用各自支持的 `-LiteralPath`，以避免通配符路径被误解释。


## v0.6.4 WinRM 流式上传

文件内容不再进入 PowerShell 命令文本，而是经 WSMan/WinRS stdin 发送原始二进制。远端接收器以 `FileMode.Create` 写入任务临时文件，结束后返回 `FDS_UPLOAD_OK`。随后沿用原有临时文件大小/SHA256校验、备份、正式提交和最终文件校验链路。

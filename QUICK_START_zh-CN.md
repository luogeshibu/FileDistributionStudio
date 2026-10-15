# 快速开始

## 1. 构建发布包

在开发机安装 Python、.NET 8 SDK 后运行：

```powershell
.\setup.bat
.\build.ps1
```

发布包中必须能看到：

- `FileDistributionStudio.exe`
- `FileDistributionStudio.Agent.exe`
- `Install-FdsAgent.ps1`

如果没有 `FileDistributionStudio.Agent.exe`，说明没有使用新版 `build.ps1` 或本机缺少 .NET 8 SDK。

## 2. 目标主机首次安装

在每台目标 Windows 主机上，以管理员 PowerShell 执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-FdsAgent.ps1 -AgentExePath .\FileDistributionStudio.Agent.exe -AllowedRoot 'D:\ADMS'
```

完成后记录端口、证书 SHA-256 指纹和配对令牌。

## 3. 桌面端配对

在 FileDistributionStudio 中：

1. “主机管理” → 添加主机；
2. 通道选择 `FDS Agent（HTTPS）`；
3. 填写端口和证书指纹；
4. “主机管理” → 编辑目标主机，填写 Agent 端口、证书指纹和配对令牌；
5. 点击“全量测试并选中 Agent”。

## 4. 分发文件

1. 点击“添加文件”或“添加目录”；
2. 填写目标绝对路径，例如 `D:\ADMS\bin`；
3. 选中通过 Agent 测试的主机；
4. 点击“开始执行所选任务”。

分发源建议使用当前电脑上的本地文件。FTP、SFTP、Windows 共享中的文件应先下载到本机，避免目标主机再通过网络共享读取。

## 5. 远程文件

进入“远程文件”，连接主机后即可在 `AllowedRoot` 范围内浏览和传输文件。双击目录进入，勾选项目后使用上传或下载按钮。

## 6. 强制重启

在“文件分发”目标主机区域勾选主机，点击“强制重启选中主机”。目标机会立即关闭程序并重启，Agent 连接随后中断是正常现象。

## 7. 常见问题

- 找不到 Agent：重新构建，确认已安装 .NET 8 SDK，并检查发布目录根部是否有 `FileDistributionStudio.Agent.exe`。
- 401：Token 错误或未设置。
- TLS/证书错误：证书指纹必须使用安装脚本输出的 SHA-256 指纹，不是证书存储指纹。
- 连接超时：检查目标机服务、端口和防火墙规则。
- 路径被拒绝：目标路径必须位于安装 Agent 时指定的 `AllowedRoot` 内。

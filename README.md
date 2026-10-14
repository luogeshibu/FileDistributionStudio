# FileDistributionStudio

FileDistributionStudio 是 Windows 多主机文件分发与远程文件管理工具。

当前版本采用 **FDS Agent HTTPS** 作为唯一正式远程通道：文件上传、下载、目录管理、进程树结束、强制重启和多主机分发都由目标机 Agent 完成。客户机不需要启用或配置 WinRM、RDP 或 Windows 管理共享。

## 发布包内容

运行 `build.ps1` 后，发布目录会包含：

```text
FileDistributionStudio.exe
FileDistributionStudio.Agent.exe
AGENT_README.md
_internal\
```

Agent 是目标 Windows 主机上的后台 Windows Service，不是桌面端内部组件。它必须先安装到每台目标主机，桌面端才能连接。

## 构建要求

- Python 3.10–3.14
- .NET 8 SDK（仅安装 Runtime 不够）
- Windows PowerShell

执行：

```powershell
.\setup.bat
.\build.ps1
```

构建脚本会同时生成 PyInstaller 桌面端和自包含 `win-x64` Agent。目标主机不需要安装 .NET Runtime。

## 目标主机安装 Agent

把 `FileDistributionStudio.Agent.exe` 复制到目标 Windows 主机，直接双击并确认 UAC。
它是唯一需要用户操作的 Agent 入口，会自动把后台服务和配置放到同目录的隐藏 `.fds-agent` 目录。

如果需要高级脚本化部署，才使用源码目录中的 `Install-FdsAgent.ps1`，并将后台服务文件命名为 `FileDistributionStudio.Agent.Service.exe`：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-FdsAgent.ps1 -AgentExePath .\FileDistributionStudio.Agent.Service.exe -AllowedRoot 'D:\ADMS'
```

脚本会自动：

- 创建 HTTPS 自签名证书；
- 生成配对令牌；
- 创建受保护的 Agent 配置；
- 创建 Windows 防火墙入站规则；
- 注册并启动 `FileDistributionStudioAgent` 服务。

安装结束后保存屏幕上的三项信息：HTTPS 端口、证书 SHA-256 指纹、配对令牌。

`AllowedRoot` 是 Agent 允许读写的根目录，可以重复指定多个目录。不要把整个系统盘或用户目录加入允许范围。

## 桌面端配对

1. 启动 `FileDistributionStudio.exe`。
2. 进入“主机管理”，添加目标主机，通道保持 `FDS Agent（HTTPS）`。
3. 填写 Agent 端口，默认 `8743`，以及安装脚本输出的证书 SHA-256 指纹。
4. 进入“文件分发”，选择目标主机，点击“设置 Agent 令牌”，粘贴安装脚本输出的配对令牌。
5. 点击“全量测试并选中 Agent”，测试通过的主机会自动选中。

## 文件分发

在“文件分发”页面添加本地文件或目录，填写目标主机上的绝对目录，例如 `D:\ADMS\bin`，然后点击“开始执行所选任务”。

推荐先把 FTP、SFTP 或 Windows 共享中的压缩包下载到当前电脑，再从本地分发到目标主机，减少网络链路和失败重试次数。

## 远程文件和远程操作

- “远程文件”：连接目标主机后，在 Agent 允许的目录内浏览、搜索、上传、下载、新建目录、重命名和删除。
- “强制重启选中主机”：通过 Agent 直接请求 `shutdown /r /f /t 0`，不依赖桌面、`explorer.exe` 或远程桌面。
- 进程结束：Agent 按 EXE 镜像名递归结束匹配进程树，并保护系统关键进程。

## 排查顺序

如果 Agent 测试失败，请依次检查：

1. 目标机服务 `FileDistributionStudioAgent` 是否正在运行；
2. 客户机是否能访问目标机的 Agent 端口；
3. Token 是否复制完整；
4. 证书指纹是否填写的是安装脚本输出的 SHA-256 指纹；
5. 目标目录是否位于 `AllowedRoot` 之下。

Ping、SMB 和 RDP 只用于发现或辅助诊断，不代表 Agent 已经可以正式分发。

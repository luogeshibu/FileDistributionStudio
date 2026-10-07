# FileDistributionStudio Agent

这是目标 Windows 主机上的后台 Agent，计划替代 WinRM、RDP 和管理共享作为文件分发与远程操作通道。

当前分支提供 Agent-only 基础服务接口和目标机安装脚本；桌面端正式业务不再依赖 WinRM：

- Windows Service 宿主；
- 仅 HTTPS 监听，不默认启动明文 HTTP；
- 配对令牌认证；
- 健康状态与主机状态；
- 流式文件上传、临时文件写入和原子替换；
- 按 EXE 名称结束整个进程树；
- 明确确认后强制重启目标主机。

## 目标机安装

将编译后的 `FileDistributionStudio.Agent.exe` 和 `Install-FdsAgent.ps1` 一起复制到目标机，
在目标机的“管理员 PowerShell”中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-FdsAgent.ps1 -AgentExePath .\FileDistributionStudio.Agent.exe -AllowedRoot 'D:\ADMS'
```

脚本会自动生成每台机器独立的自签名 HTTPS 证书、随机配对令牌、受保护的配置文件、
Windows 防火墙入站规则和自动启动服务。脚本最后会显示配对令牌和证书指纹；把它们录入桌面端后，
桌面端即可测试 Agent、浏览允许目录、上传/下载文件、执行进程树结束/强制重启和多文件分发。
当前分支不会在 Agent 连接失败时回退到 WinRM；旧 WinRM 配置、账号密码和初始化脚本均不参与正式运行。

默认只允许写入 `C:\FDS-Data`。生产环境应通过 `-AllowedRoot` 明确限制到实际业务目录，
不要把系统盘根目录、`Windows` 或整个用户目录加入允许范围。

## 配置原则

Agent 只允许向 AllowedRoots 下写入文件，避免桌面端可以任意覆盖系统文件。正式安装程序需要负责：

1. 生成每台主机独立的配对令牌；
2. 在 LocalMachine\My 安装 HTTPS 证书；
3. 设置配置文件 ACL，使普通用户不能读取令牌；
4. 创建 Windows 防火墙入站规则；
5. 注册并启动 FileDistributionStudio Agent 服务。

appsettings.example.json 仅用于说明字段，不包含可用凭据或证书。

## API

除健康检查外，所有 API 都需要请求头：

    X-FDS-Agent-Token: <安装程序生成的配对令牌>

    GET  /api/v1/health
    GET  /api/v1/status
    PUT  /api/v1/files?path=D%3A%5CADMS%5Cbin%5Cexample.zip
    POST /api/v1/files/inspect
         { "path": "D:\\ADMS\\bin\\example.zip", "includeSha256": true }
    POST /api/v1/files/backup
         { "sourcePath": "D:\\ADMS\\bin\\example.zip", "backupPath": "D:\\ADMS\\.fds_backup\\..." }
    GET  /api/v1/files/list?path=D%3A%5CADMS
    GET  /api/v1/files/download?path=D%3A%5CADMS%5Cexample.zip
    POST /api/v1/files/directory
         { "path": "D:\\ADMS\\new-folder" }
    POST /api/v1/files/rename
         { "path": "D:\\ADMS\\old.zip", "newPath": "D:\\ADMS\\new.zip" }
    DELETE /api/v1/files?path=D%3A%5CADMS%5Cold.zip
    POST /api/v1/operations/process/kill
         { "imageName": "GridExplorer.exe" }
    POST /api/v1/operations/restart
         { "force": true }

这是 Agent-only 分支的目标机服务代码。正式分发前仍应由开发机完成 Agent 编译和人工验证，再制作安装包、证书配对和升级流程。

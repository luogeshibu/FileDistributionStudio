# FileDistributionStudio Agent

这是目标 Windows 主机上的后台 Agent，当前分支已经替代 WinRM、RDP 和管理共享，作为文件分发与远程操作的唯一正式通道。

当前分支提供 Agent-only 服务接口、目标机安装脚本和自动发布流程；桌面端正式业务不再依赖 WinRM：

- Windows Service 宿主；
- 仅 HTTPS 监听，不默认启动明文 HTTP；
- 配对令牌认证；
- 健康状态与主机状态；
- 流式文件上传、临时文件写入和原子替换；
- 远程目录浏览、远程文件下载和本机递归备份；
- 目标机主机名、系统时间和磁盘空间信息；
- 按 EXE 名称结束整个进程树；
- 明确确认后强制重启目标主机。

根目录的 `build.ps1` 会自动发布 Agent 并把它放进桌面端发布目录。如果只需要单独发布 Agent，也可以在仓库根目录执行：

```powershell
dotnet publish .\agent\FileDistributionStudio.Agent\FileDistributionStudio.Agent.csproj `
  -c Release -r win-x64 --self-contained true `
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true `
  -o .\build\agent-publish
```

生成文件位于 `build\agent-publish\FileDistributionStudio.Agent.Service.exe`；开发目录中的 `.cs` 是源码，不是可直接运行的目标机程序。

## 目标机安装（推荐：一键安装程序）

运行仓库根目录的 `build.ps1` 后，发布目录会自动生成：

- `FileDistributionStudio.Agent.exe`：唯一需要用户双击的一键入口；
- `FileDistributionStudio.Agent.Service.exe`：后台 Windows 服务本体，仅供安装器内部使用；
- `Install-FdsAgent.ps1`：高级用户的脚本安装备用方式。

将 `FileDistributionStudio.Agent.exe` 复制到目标机，直接双击并确认 UAC。
安装程序默认授权目标机所有本地固定磁盘和可移动磁盘，不需要再输入目录；也可以通过
`--allowed-root D:\ADMS` 将权限限制到指定目录。安装程序会自动：

1. 将后台 Agent 复制或升级到安装器 EXE 所在目录下的隐藏 `.fds-agent` 目录；
2. 首次安装时生成 HTTPS 证书和配对令牌，升级时默认复用已有配置；
3. 创建防火墙入站规则；
4. 注册并启动 `FileDistributionStudioAgent` Windows 服务。

运行时文件和 `appsettings.json` 位于安装器 EXE 所在目录下的隐藏 `.fds-agent` 目录。重新运行入口程序时，只检查这个隐藏目录：如果配置存在就自动保留端口、证书、配对令牌和允许目录；如果不存在才生成新配置。不会查找或迁移其他目录中的旧配置，整个过程不需要选择或输入参数。
每次覆盖配置前都会在同一目录留下一个带时间戳的备份文件。

安装结束后，把窗口显示的 HTTPS 端口、证书 SHA-256 指纹和配对令牌录入桌面端。
同时，入口程序会在自身目录生成 `FDS-Agent-Connection.txt`，可以直接打开复制这些信息；该文件包含配对令牌，请只在内部保存和传递。

Agent 安装完成后不需要保持安装窗口打开，后台服务会自动运行。

如果需要临时停止或恢复 Agent，不需要删除配置，也不需要重新安装。使用同一个入口程序执行：

```powershell
.\FileDistributionStudio.Agent.exe stop
.\FileDistributionStudio.Agent.exe start
.\FileDistributionStudio.Agent.exe restart
.\FileDistributionStudio.Agent.exe status
```

这些操作需要管理员权限；停止后桌面端会显示目标主机离线，重新启动后即可恢复。

## 高级脚本安装方式

如果需要脚本化部署，也可以将 `FileDistributionStudio.Agent.Service.exe` 和
`Install-FdsAgent.ps1` 复制到目标机，在“管理员 PowerShell”中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-FdsAgent.ps1 -AgentExePath .\FileDistributionStudio.Agent.Service.exe -AllowedRoot 'D:\ADMS'
```

脚本会自动生成每台机器独立的自签名 HTTPS 证书、随机配对令牌、受保护的配置文件、
Windows 防火墙入站规则和自动启动服务。脚本最后会显示配对令牌和证书 SHA-256 指纹；把它们录入桌面端后，
桌面端即可测试 Agent、浏览允许目录、上传/下载文件、执行进程树结束/强制重启和多文件分发。
当前分支不会在 Agent 连接失败时回退到 WinRM；旧 WinRM 配置、账号密码和初始化脚本均不参与正式运行。

默认授权所有本地固定磁盘和可移动磁盘，因此 Agent 具备目标机的全盘文件操作能力。
如果部署环境不允许全盘访问，请使用 `--allowed-root` 限制到实际业务目录。

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
    POST /api/v1/files/backup-tree
         { "sourcePath": "D:\\ADMS\\bin", "backupPath": "D:\\ADMS\\.fds_backup\\..." }
    GET  /api/v1/system/info
    GET  /api/v1/files/list?path=D%3A%5CADMS
    GET  /api/v1/files/download?path=D%3A%5CADMS%5Cexample.zip
    POST /api/v1/files/directory
         { "path": "D:\\ADMS\\new-folder" }
    POST /api/v1/files/rename
         { "path": "D:\\ADMS\\old.zip", "newPath": "D:\\ADMS\\new.zip" }
    DELETE /api/v1/files?path=D%3A%5CADMS%5Cold.zip
    GET  /api/v1/operations/process/list
    POST /api/v1/operations/process/kill
         { "imageName": "GridExplorer.exe" }
    POST /api/v1/operations/restart
         { "force": true }

这是 Agent-only 分支的目标机服务代码。正式分发前仍应由开发机完成 Agent 编译和人工验证，再制作安装包、证书配对和升级流程。

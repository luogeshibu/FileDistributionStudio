# FileDistributionStudio Agent

这是目标 Windows 主机上的后台 Agent，计划替代 WinRM、RDP 和管理共享作为文件分发与远程操作通道。

当前分支先提供基础服务接口：

- Windows Service 宿主；
- 仅 HTTPS 监听，不默认启动明文 HTTP；
- 配对令牌认证；
- 健康状态与主机状态；
- 流式文件上传、临时文件写入和原子替换；
- 按 EXE 名称结束整个进程树；
- 明确确认后强制重启目标主机。

## 配置原则

Agent 只允许向 AllowedRoots 下写入文件，避免桌面端可以任意覆盖系统文件。正式安装程序需要负责：

1. 生成每台主机独立的配对令牌；
2. 在 LocalMachine\My 安装 HTTPS 证书；
3. 设置配置文件 ACL，使普通用户不能读取令牌；
4. 创建 Windows 防火墙入站规则；
5. 注册并启动 FileDistributionStudio Agent 服务。

appsettings.example.json 仅用于说明字段，不包含可用凭据或证书。

## API 草案

除健康检查外，所有 API 都需要请求头：

    X-FDS-Agent-Token: <安装程序生成的配对令牌>

    GET  /api/v1/health
    GET  /api/v1/status
    PUT  /api/v1/files?path=D%3A%5CADMS%5Cbin%5Cexample.zip
    POST /api/v1/operations/process/kill
         { "imageName": "GridExplorer.exe" }
    POST /api/v1/operations/restart
         { "force": true }

这是 Agent 的第一阶段代码，当前尚未接入 Python 桌面端，也未提供安装器。请先由开发机完成编译和人工验证，再继续实现安装、配对、升级和桌面端适配。

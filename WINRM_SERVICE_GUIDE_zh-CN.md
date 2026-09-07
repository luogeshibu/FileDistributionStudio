# WinRM 服务约定（v0.6.10）

File Distribution Studio 的目标端业务操作统一依赖 Windows WinRM 服务。
正式文件写入、备份、校验、服务/进程控制、CMD/PowerShell 都只走 WinRM。

主机发现/识别可以继续使用 Ping、TCP 445/3389/5985/5986、DNS、NetBIOS、SMB/NTLM/WKSSVC 等只读辅助信号；这些方式不会用于修改目标机。

## 现场标准 WinRM 命令

```cmd
winrm quickconfig
sc query WinRM
sc start WinRM
sc stop WinRM
winrm enumerate winrm/config/listener
netstat -ano | findstr :5985
winrm id
sc config WinRM start= disabled
sc config WinRM start= auto
sc start WinRM
```

## 软件内配置向导

文件分发页新增“WinRM 配置向导”，可以导出三个 CMD：

1. `TARGET_PREP_WINRM.cmd`：只执行标准 WinRM 初始化/服务/Listener/5985/self-test，不改注册表/UAC。
2. `TARGET_PREP_WINRM_LOCAL_ADMIN.cmd`：适用于 ADMS 等本地 Administrators 成员账号。明确确认后设置 `LocalAccountTokenFilterPolicy=1`，执行前记录原始状态。
3. `TARGET_RESTORE_WINRM_LOCAL_ADMIN.cmd`：按保存的原始状态恢复该注册表值。

应用本身不会远程静默修改 `LocalAccountTokenFilterPolicy`。只有用户把脚本放到目标 Windows 上并以管理员身份运行时，才会执行对应改动。

## 用户名规则

从 v0.6.4 起，WinRM 用户名按输入原样发送：

```text
ADMS                 -> ADMS
DOMAIN\deployuser   -> DOMAIN\deployuser
COMPUTER\ADMS       -> COMPUTER\ADMS
user@example.com     -> user@example.com
```

程序不再用主机发现结果自动改写本地账号。这可以避免 DNS/显示主机名与 NetBIOS 本地账号 authority 不一致造成认证失败。

“测试 WinRM”会远程执行 `hostname` 和 `whoami`，并测试所有当前目标目录的创建/写入/读取/删除，从而验证账号确实具备本次分发所需权限。


## 常用 CMD 命令（与应用“使用帮助”一致）

```bat
:: 1. 初始化
winrm quickconfig

:: 2. 服务状态
sc query WinRM

:: 3. 启动
sc start WinRM

:: 4. 停止
sc stop WinRM

:: 5. Listener
winrm enumerate winrm/config/listener

:: 6. 5985
netstat -ano | findstr :5985

:: 7. 本机 WinRM
winrm id

sc config WinRM start= disabled
sc config WinRM start= auto
sc start WinRM
```

## 本地管理员 Remote UAC 策略

> 仅在 ADMS 等本地 Administrators 成员通过 WinRM 出现 AccessDenied 时考虑。修改前先查询当前状态。若原值本来不存在，删除该值才是最准确的恢复原状。

```bat
:: 查看当前状态
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy

:: 开启：允许本地管理员通过网络获得完整管理员令牌
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System ^
 /v LocalAccountTokenFilterPolicy ^
 /t REG_DWORD ^
 /d 1 ^
 /f

:: 设置为 0：恢复 Windows Remote UAC 默认过滤行为
:: 如果原来这个值不存在，严格意义上这不叫恢复原状
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System ^
 /v LocalAccountTokenFilterPolicy ^
 /t REG_DWORD ^
 /d 0 ^
 /f

:: 删除该值：若原来不存在，这是最准确的恢复原状
reg delete HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System ^
 /v LocalAccountTokenFilterPolicy ^
 /f

:: 再次确认
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy
```


## 登录桌面命令说明（v0.6.16）

`Win32_ComputerSystem.UserName` 在部分 RDP/控制台组合下可能为空，因此程序现在优先从目标机 `explorer.exe` 的进程所有者与 SessionId 判断真实交互桌面用户，并优先匹配 WinRM 用户。若确实没有登录桌面用户，才会明确提示无法使用“登录桌面”执行方式。

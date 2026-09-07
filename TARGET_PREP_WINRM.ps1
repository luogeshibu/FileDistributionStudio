# File Distribution Studio v0.6.10 - WinRM standard target preparation
# Run in an elevated PowerShell/CMD window on the target Windows host.
# This script ONLY initializes/starts the standard WinRM service and listener. For ADMS/local-admin AccessDenied, use TARGET_PREP_WINRM_LOCAL_ADMIN.cmd after explicit approval.
# It does NOT edit LocalAccountTokenFilterPolicy, TrustedHosts, users, passwords, or UAC policy.

$ErrorActionPreference = 'Continue'

Write-Host '1. 初始化 WinRM（等价于 winrm quickconfig）'
winrm quickconfig -quiet

Write-Host ''
Write-Host '2. WinRM 服务状态'
sc.exe query WinRM

Write-Host ''
Write-Host '3. 设置自动启动并启动 WinRM'
sc.exe config WinRM start= auto
sc.exe start WinRM

Write-Host ''
Write-Host '4. Listener'
winrm enumerate winrm/config/listener

Write-Host ''
Write-Host '5. 5985 监听检查'
netstat -ano | Select-String ':5985'

Write-Host ''
Write-Host '6. 本机 WinRM 身份'
winrm id

Write-Host ''
Write-Host 'WinRM 服务检查完成。'
Write-Host '说明：5985/5986 可达仅表示服务已开启；远程账号是否允许登录仍由 Windows WinRM/UAC/域策略决定。'

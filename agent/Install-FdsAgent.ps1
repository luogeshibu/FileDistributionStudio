[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$AgentExePath,

    [string]$InstallRoot = "$env:ProgramFiles\FileDistributionStudio Agent",
    [int]$Port = 8743,
    [string[]]$AllowedRoot = @("$env:SystemDrive\FDS-Data"),
    [string]$Token = ""
)

$ErrorActionPreference = 'Stop'
$serviceName = 'FileDistributionStudioAgent'
$displayName = 'FileDistributionStudio Agent'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '请在“管理员 PowerShell”中运行此安装脚本。'
    }
}

function New-PairingToken {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes)
}

Assert-Administrator
if ($Port -lt 1 -or $Port -gt 65535) { throw '端口必须在 1 到 65535 之间。' }
if (-not $Token) { $Token = New-PairingToken }
if ([string]::IsNullOrWhiteSpace($Token)) { throw '配对令牌不能为空。' }

$resolvedExe = (Resolve-Path -LiteralPath $AgentExePath).Path
$installPath = [IO.Path]::GetFullPath($InstallRoot)
$configPath = Join-Path $installPath 'appsettings.json'
$certName = "FDS Agent $env:COMPUTERNAME"

New-Item -ItemType Directory -Path $installPath -Force | Out-Null
Copy-Item -LiteralPath $resolvedExe -Destination (Join-Path $installPath 'FileDistributionStudio.Agent.exe') -Force

$certificate = New-SelfSignedCertificate `
    -DnsName $env:COMPUTERNAME, 'localhost' `
    -CertStoreLocation 'Cert:\LocalMachine\My' `
    -FriendlyName $certName `
    -NotAfter (Get-Date).AddYears(3)

$roots = @($AllowedRoot | ForEach-Object { [IO.Path]::GetFullPath($_) })
foreach ($root in $roots) { New-Item -ItemType Directory -Path $root -Force | Out-Null }

$config = [ordered]@{
    Agent = [ordered]@{
        AgentName = 'FileDistributionStudio Agent'
        AgentVersion = '0.1.0'
        ListenPort = $Port
        CertificateThumbprint = $certificate.Thumbprint
        Token = $Token
        AllowedRoots = $roots
        MaxUploadBytes = 21474836480
    }
}
$config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8

# 令牌和证书指纹只进入服务配置文件；普通用户不应读取该文件。
$acl = Get-Acl -LiteralPath $configPath
$acl.SetAccessRuleProtection($true, $false)
$acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
foreach ($principalName in @('SYSTEM', 'BUILTIN\Administrators')) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $principalName, 'Read', 'Allow'
    )
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $configPath -AclObject $acl

& netsh advfirewall firewall delete rule name="$displayName" | Out-Null
& netsh advfirewall firewall add rule name="$displayName" dir=in action=allow protocol=TCP localport=$Port profile=Domain,Private | Out-Null

if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    & sc.exe delete $serviceName | Out-Null
    Start-Sleep -Milliseconds 500
}
$serviceBinary = '"' + (Join-Path $installPath 'FileDistributionStudio.Agent.exe') + '"'
& sc.exe create $serviceName binPath= $serviceBinary start= auto DisplayName= $displayName | Out-Null
& sc.exe description $serviceName 'FileDistributionStudio 的 HTTPS 目标主机 Agent。' | Out-Null
Start-Service -Name $serviceName

Write-Host ''
Write-Host 'FDS Agent 安装完成。' -ForegroundColor Green
Write-Host "目标主机：$env:COMPUTERNAME"
Write-Host "HTTPS 端口：$Port"
Write-Host "证书 SHA-256 指纹：$($certificate.Thumbprint)"
Write-Host "配对令牌（请复制到桌面端，之后不要公开）：$Token" -ForegroundColor Yellow
Write-Host "允许写入目录：$($roots -join ', ')"
Write-Host '桌面端主机配置选择“FDS Agent（HTTPS）”，填入端口、证书指纹和上面的配对令牌。'

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$AgentExePath,

    [string]$InstallRoot = "$env:ProgramFiles\FileDistributionStudio Agent",
    [int]$Port = 0,
    [string[]]$AllowedRoot = @(),
    [string]$Token = ""
)

$ErrorActionPreference = 'Stop'
$serviceName = 'FileDistributionStudioAgent'
$displayName = 'FileDistributionStudio Agent'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this installer from an elevated Administrator PowerShell window.'
    }
}

function New-PairingToken {
    $bytes = New-Object byte[] 32
    # Keep this installer compatible with Windows PowerShell 5.1.
    # Do not depend on the .NET 5+ RandomNumberGenerator.Fill API.
    $rng = New-Object Security.Cryptography.RNGCryptoServiceProvider
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes)
}

Assert-Administrator
$resolvedExe = (Resolve-Path -LiteralPath $AgentExePath).Path
$installPath = [IO.Path]::GetFullPath($InstallRoot)
$configPath = Join-Path $installPath 'appsettings.json'
$certName = "FDS Agent $env:COMPUTERNAME"

New-Item -ItemType Directory -Path $installPath -Force | Out-Null

$reuseConfig = $false
$existing = $null
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    $reuseConfig = $true
    try {
        $existing = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    }
    catch {
        throw "Existing Agent configuration is invalid: $configPath. $($_.Exception.Message)"
    }
}

if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    & sc.exe delete $serviceName | Out-Null
    Start-Sleep -Milliseconds 1000
}

if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    $acl = Get-Acl -LiteralPath $configPath
    $acl.SetAccessRuleProtection($false, $true)
    Set-Acl -LiteralPath $configPath -AclObject $acl
    (Get-Item -LiteralPath $configPath).Attributes = 'Normal'
}

Copy-Item -LiteralPath $resolvedExe -Destination (Join-Path $installPath 'FileDistributionStudio.Agent.Service.exe') -Force

$certificate = $null
if ($reuseConfig -and $existing.Agent.CertificateThumbprint) {
    $wantedThumbprint = ($existing.Agent.CertificateThumbprint -replace '\s', '').ToUpperInvariant()
    $certificate = Get-ChildItem -Path 'Cert:\LocalMachine\My' |
        Where-Object { $_.Thumbprint -eq $wantedThumbprint -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date).AddMinutes(5) } |
        Select-Object -First 1
}
if (-not $certificate) {
    $certificate = New-SelfSignedCertificate `
        -DnsName $env:COMPUTERNAME, 'localhost' `
        -CertStoreLocation 'Cert:\LocalMachine\My' `
        -FriendlyName $certName `
        -NotAfter (Get-Date).AddYears(3)
}

if ($Port -le 0) {
    $Port = if ($reuseConfig -and $existing.Agent.ListenPort) { [int]$existing.Agent.ListenPort } else { 8743 }
}
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Port must be between 1 and 65535.' }

if ($AllowedRoot.Count -eq 0) {
    $AllowedRoot = if ($reuseConfig -and $existing.Agent.AllowedRoots) {
        @($existing.Agent.AllowedRoots)
    } else {
        @("$env:SystemDrive\FDS-Data")
    }
}
$roots = @($AllowedRoot | ForEach-Object { [IO.Path]::GetFullPath($_) } | Select-Object -Unique)
foreach ($root in $roots) { New-Item -ItemType Directory -Path $root -Force | Out-Null }

if (-not $Token) {
    $Token = if ($reuseConfig -and $existing.Agent.Token) { [string]$existing.Agent.Token } else { New-PairingToken }
}
if ([string]::IsNullOrWhiteSpace($Token)) { throw 'Pairing token cannot be empty.' }

$config = [ordered]@{
    Agent = [ordered]@{
        AgentName = 'FileDistributionStudio Agent'
        AgentVersion = '0.1.0'
        ListenPort = $Port
        CertificateThumbprint = $certificate.Thumbprint
        Token = $Token
        AllowedRoots = $roots
        MaxUploadBytes = if ($reuseConfig -and $existing.Agent.MaxUploadBytes) { [long]$existing.Agent.MaxUploadBytes } else { 21474836480 }
    }
}
$backupPath = "$configPath.backup-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff'))"
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    Copy-Item -LiteralPath $configPath -Destination $backupPath -Force
}
$config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8

# Restrict the configuration file because it contains the pairing token.
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

$serviceBinary = '"' + (Join-Path $installPath 'FileDistributionStudio.Agent.Service.exe') + '"'
& sc.exe create $serviceName binPath= $serviceBinary start= auto DisplayName= $displayName | Out-Null
& sc.exe description $serviceName 'FileDistributionStudio HTTPS target agent.' | Out-Null
Start-Service -Name $serviceName

$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $certificateSha256 = (($sha256.ComputeHash($certificate.RawData) |
        ForEach-Object { $_.ToString('X2') }) -join '')
}
finally {
    $sha256.Dispose()
}

Write-Host ''
Write-Host 'FDS Agent installation completed.' -ForegroundColor Green
Write-Host "Target host: $env:COMPUTERNAME"
Write-Host "HTTPS port: $Port"
Write-Host "Certificate SHA-256 fingerprint (copy to the desktop app): $certificateSha256"
Write-Host "Certificate store thumbprint (internal service value): $($certificate.Thumbprint)"
Write-Host "Pairing token (copy to the desktop app and keep it private): $Token" -ForegroundColor Yellow
Write-Host "Allowed roots: $($roots -join ', ')"
Write-Host 'In the desktop app, select FDS Agent (HTTPS) and enter the port, certificate fingerprint, and pairing token.'

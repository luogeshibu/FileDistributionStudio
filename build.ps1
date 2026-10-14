[CmdletBinding()]
param(
    [string]$SigningThumbprint = '',
    [string]$TimestampUrl = 'http://timestamp.digicert.com',
    [switch]$RequireSigning
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$Root=$PSScriptRoot
Set-Location $Root

# A certificate thumbprint can be supplied on the command line or through
# FDS_SIGNING_THUMBPRINT. Signing is optional by default so existing local
# builds keep working; use -RequireSigning for a release that must be signed.
if ([string]::IsNullOrWhiteSpace($SigningThumbprint) -and
    -not [string]::IsNullOrWhiteSpace([string]$env:FDS_SIGNING_THUMBPRINT)) {
    $SigningThumbprint = [string]$env:FDS_SIGNING_THUMBPRINT
}
$SigningThumbprint = ($SigningThumbprint -replace '\s', '').Trim()
if ($TimestampUrl -eq 'http://timestamp.digicert.com' -and
    -not [string]::IsNullOrWhiteSpace([string]$env:FDS_SIGNING_TIMESTAMP_URL)) {
    $TimestampUrl = [string]$env:FDS_SIGNING_TIMESTAMP_URL
}
if ([string]$env:FDS_REQUIRE_SIGNING -eq '1') { $RequireSigning = $true }

$Python=Join-Path $Root '.venv\Scripts\python.exe'
$Spec=Join-Path $Root 'packaging\windows\FileDistributionStudio.spec'
if (-not (Test-Path $Python)) { throw '未找到 .venv，请先运行 setup.bat。' }
& $Python -c "import PyInstaller,PySide6,paramiko,psutil; print('打包环境检查通过')"
if ($LASTEXITCODE -ne 0) { throw '打包环境无效，请重新运行 setup.bat。' }
$Dotnet = Get-Command dotnet.exe -ErrorAction SilentlyContinue
if (-not $Dotnet) {
    throw '未找到 .NET SDK。请安装 .NET 8 SDK（不是只有 Runtime），再重新运行 build.ps1。'
}
$DotnetSdks = @(& $Dotnet.Source --list-sdks 2>$null)
if (-not ($DotnetSdks | Where-Object { $_ -match '^\s*8\.' })) {
    throw '未找到 .NET 8 SDK。当前机器可能只有 .NET Runtime；请安装 .NET 8 SDK，再重新运行 build.ps1。'
}
$AgentProject = Join-Path $Root 'agent\FileDistributionStudio.Agent\FileDistributionStudio.Agent.csproj'
$AgentSetupProject = Join-Path $Root 'agent\FileDistributionStudio.Agent.Setup\FileDistributionStudio.Agent.Setup.csproj'
if (-not (Test-Path -LiteralPath $AgentProject -PathType Leaf)) {
    throw "未找到 Agent 项目：$AgentProject"
}
if (-not (Test-Path -LiteralPath $AgentSetupProject -PathType Leaf)) {
    throw "未找到 Agent 安装程序项目：$AgentSetupProject"
}
$Version=(& $Python -c "from app.version import APP_VERSION; print(APP_VERSION)").Trim()
$Build=Join-Path $Root 'build'
$Stage=Join-Path $Build 'stage'
$Work=Join-Path $Build 'pyinstaller'
$AgentStage=Join-Path $Build 'agent-publish'
$AgentSetupStage=Join-Path $Build 'agent-setup-publish'
$ArchiveStage=Join-Path $Build 'archive-stage'
$Release=Join-Path $Root ("release\v$Version")
Remove-Item $Stage,$Work,$AgentStage,$AgentSetupStage,$ArchiveStage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage,$Release,$AgentStage,$AgentSetupStage | Out-Null

Write-Host '正在发布 FDS Agent（自包含 win-x64）……'
& $Dotnet.Source publish $AgentProject --configuration Release --runtime win-x64 --self-contained true `
    --output $AgentStage /p:PublishSingleFile=true /p:IncludeNativeLibrariesForSelfExtract=true --nologo
if ($LASTEXITCODE -ne 0) { throw 'FDS Agent 发布失败。请查看上方 .NET/C# 编译错误并修复后重试。' }
$AgentExe=Join-Path $AgentStage 'FileDistributionStudio.Agent.Service.exe'
if (-not (Test-Path -LiteralPath $AgentExe -PathType Leaf)) { throw '未找到发布后的 FileDistributionStudio.Agent.Service.exe。' }

Write-Host '正在发布 FDS Agent 一键安装程序（自包含 win-x64）……'
& $Dotnet.Source publish $AgentSetupProject --configuration Release --runtime win-x64 --self-contained true `
    --output $AgentSetupStage /p:PublishSingleFile=true /p:IncludeNativeLibrariesForSelfExtract=true `
    "-p:AgentPayload=$AgentExe" --nologo
if ($LASTEXITCODE -ne 0) { throw 'FDS Agent 一键安装程序发布失败。请查看上方 .NET/C# 编译错误并修复后重试。' }
$AgentLauncherExe=Join-Path $AgentSetupStage 'FileDistributionStudio.Agent.exe'
if (-not (Test-Path -LiteralPath $AgentLauncherExe -PathType Leaf)) { throw '未找到发布后的 FileDistributionStudio.Agent.exe。' }

& $Python -m PyInstaller --noconfirm --clean --distpath $Stage --workpath $Work $Spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败。' }
$App=Join-Path $Stage 'FileDistributionStudio'
$Exe=Join-Path $App 'FileDistributionStudio.exe'
if (-not (Test-Path $Exe)) { throw '未找到打包后的 EXE。' }

# Windows Smart App Control / Defender can block loose Python source files that
# are loaded beside an unsigned EXE.  Application Python modules must live in
# PyInstaller's PYZ archive, never as runtime .py/.pyw files in the release tree.
# Some dependency/build combinations may emit _context_attributes.py as a loose
# helper. It is not part of File Distribution Studio's runtime contract, so
# remove it before the executable is ever started.  If removal makes the app
# unusable, the self-test below will fail the build instead of publishing it.
$KnownLooseHelpers = @('_context_attributes.py')
foreach ($helper in $KnownLooseHelpers) {
    Get-ChildItem -Path $App -Recurse -File -Filter $helper -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Host "移除打包产生的外部 Python 源文件：$($_.FullName)" -ForegroundColor Yellow
            Remove-Item $_.FullName -Force
        }
}

# Hard release guard: never ship loose Python source next to the EXE.  This
# prevents the same Windows Security warning from silently returning later.
$LoosePython = @(Get-ChildItem -Path $App -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @('.py', '.pyw') })
if ($LoosePython.Count -gt 0) {
    $Names = ($LoosePython | ForEach-Object { $_.FullName }) -join [Environment]::NewLine
    throw "发布目录包含外部 Python 源文件，已阻止发布：`n$Names"
}

# Smart App Control can still block an unsigned executable even after loose
# Python files have been removed. When a trusted certificate is configured,
# sign every PE payload before the self-test and verify every signature.
$Product="FileDistributionStudio-v$Version-Windows-x64"
$SigningRequested = $RequireSigning -or -not [string]::IsNullOrWhiteSpace($SigningThumbprint)
if ($SigningRequested) {
    if ([string]::IsNullOrWhiteSpace($SigningThumbprint)) {
        throw '已要求代码签名，但未提供证书指纹。请使用 -SigningThumbprint 或设置 FDS_SIGNING_THUMBPRINT。'
    }

    $SignTool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if (-not $SignTool) {
        throw '未找到 signtool.exe。请安装 Windows SDK，并将 signtool.exe 加入 PATH 后重试。'
    }

    $SignToolPath = $SignTool.Source
    $PeFiles = @(Get-ChildItem -Path $App -Recurse -File -ErrorAction Stop |
        Where-Object { $_.Extension.ToLowerInvariant() -in @('.exe', '.dll', '.pyd') })
    if ($PeFiles.Count -eq 0) { throw '发布目录中没有可签名的 EXE/DLL/PYD 文件。' }

    foreach ($PeFile in $PeFiles) {
        Write-Host "正在签名：$($PeFile.FullName)"
        & $SignToolPath sign /s My /sha1 $SigningThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 /d 'File Distribution Studio' $PeFile.FullName
        if ($LASTEXITCODE -ne 0) { throw "代码签名失败：$($PeFile.FullName)" }

        $Signature = Get-AuthenticodeSignature -LiteralPath $PeFile.FullName
        if ($Signature.Status -ne 'Valid') {
            throw "签名校验失败：$($PeFile.FullName)；状态：$($Signature.Status)"
        }
    }
    Write-Host "已完成代码签名：$($PeFiles.Count) 个文件" -ForegroundColor Green
} else {
    Write-Warning '未配置代码签名证书；发布包仍可能被 Windows Smart App Control 拦截。正式分发请使用 -SigningThumbprint，并配合受信任的代码签名证书。'
}

& $Exe --self-test
if ($LASTEXITCODE -ne 0) { throw '打包后的 EXE 自检失败。' }
$Target=Join-Path $Release $Product
if (Test-Path -LiteralPath $Target) {
    try {
        Remove-Item $Target -Recurse -Force -ErrorAction Stop
    } catch {
        throw "无法清理旧发布目录：$Target。请先退出正在运行的发布程序，然后重新执行 build.ps1。"
    }
}
Copy-Item $App $Target -Recurse
# 只交付桌面端和一键 Agent 安装程序；Agent 本体已经嵌入安装程序。
#   FileDistributionStudio.exe
#   FileDistributionStudio.Agent.exe
Copy-Item $AgentLauncherExe (Join-Path $Target 'FileDistributionStudio.Agent.exe') -Force
Copy-Item (Join-Path $Root 'agent\README.md') (Join-Path $Target 'AGENT_README.md') -Force

# Build the archive from an unlocked staging copy. The published application
# directory may still be open in File Explorer or running from the previous
# release, which can lock base_library.zip inside $Target.
New-Item -ItemType Directory -Force -Path $ArchiveStage | Out-Null
Copy-Item -Path (Join-Path $App '*') -Destination $ArchiveStage -Recurse -Force
Copy-Item $AgentLauncherExe (Join-Path $ArchiveStage 'FileDistributionStudio.Agent.exe') -Force
Copy-Item (Join-Path $Root 'agent\README.md') (Join-Path $ArchiveStage 'AGENT_README.md') -Force
$Zip=Join-Path $Release "$Product.zip"
Remove-Item $Zip -Force -ErrorAction SilentlyContinue
$archiveError = $null
$archiveReady = $false
for ($attempt = 1; $attempt -le 6; $attempt++) {
    try {
        Remove-Item $Zip -Force -ErrorAction SilentlyContinue
        Compress-Archive -Path "$ArchiveStage\*" -DestinationPath $Zip -CompressionLevel Optimal -ErrorAction Stop
        if (-not (Test-Path -LiteralPath $Zip -PathType Leaf)) {
            throw "压缩命令完成，但未找到 ZIP 文件。"
        }
        $null = Get-FileHash -LiteralPath $Zip -Algorithm SHA256 -ErrorAction Stop
        $archiveReady = $true
        break
    } catch {
        $archiveError = $_
        if ($attempt -lt 6) {
            Write-Warning "ZIP 文件暂时被占用，正在重试（$attempt/5）：$($_.Exception.Message)"
            Start-Sleep -Seconds 2
        }
    }
}
if (-not $archiveReady) {
    throw "发布 ZIP 失败：$($archiveError.Exception.Message)。请检查目标目录是否被其他程序锁定后重试。"
}
$Hash=(Get-FileHash -LiteralPath $Zip -Algorithm SHA256 -ErrorAction Stop).Hash
"$Hash  $Product.zip" | Set-Content (Join-Path $Release 'SHA256SUMS.txt') -Encoding ascii
Write-Host "发布包已生成：$Release" -ForegroundColor Green
Write-Host "ZIP：$Zip"

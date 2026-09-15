[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$Root=$PSScriptRoot
Set-Location $Root
$Python=Join-Path $Root '.venv\Scripts\python.exe'
$Spec=Join-Path $Root 'packaging\windows\FileDistributionStudio.spec'
if (-not (Test-Path $Python)) { throw '未找到 .venv，请先运行 setup.bat。' }
& $Python -c "import PyInstaller,PySide6,paramiko,psutil,winrm,impacket; print('打包环境检查通过')"
if ($LASTEXITCODE -ne 0) { throw '打包环境无效，请重新运行 setup.bat。' }
$Version=(& $Python -c "from app.version import APP_VERSION; print(APP_VERSION)").Trim()
$Build=Join-Path $Root 'build'
$Stage=Join-Path $Build 'stage'
$Work=Join-Path $Build 'pyinstaller'
$Release=Join-Path $Root ("release\v$Version")
Remove-Item $Stage,$Work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage,$Release | Out-Null
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

& $Exe --self-test
if ($LASTEXITCODE -ne 0) { throw '打包后的 EXE 自检失败。' }
$Product="FileDistributionStudio-v$Version-Windows-x64"
$Target=Join-Path $Release $Product
Remove-Item $Target -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item $App $Target -Recurse
$Zip=Join-Path $Release "$Product.zip"
Remove-Item $Zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "$Target\*" -DestinationPath $Zip -CompressionLevel Optimal
$Hash=(Get-FileHash $Zip -Algorithm SHA256).Hash
"$Hash  $Product.zip" | Set-Content (Join-Path $Release 'SHA256SUMS.txt') -Encoding ascii
Write-Host "发布包已生成：$Release" -ForegroundColor Green
Write-Host "ZIP：$Zip"

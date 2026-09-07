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

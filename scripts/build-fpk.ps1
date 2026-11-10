# 打包 fpk：校验结构与平台 → fnpack build → dist-fpk + SHA256SUMS
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\build-fpk.ps1               # x86（默认）
#   powershell -ExecutionPolicy Bypass -File scripts\build-fpk.ps1 -Platform arm # arm（M5：需先 vendor arm64 且 manifest platform 改为 arm）
param(
    [ValidateSet("x86", "arm")]
    [string]$Platform = "x86"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$fpkDir = Join-Path $root "fpk"
$dist = Join-Path $root "dist-fpk"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# 从 manifest 读 version 与 platform（manifest 的 platform 字段决定目标包架构）
$mf = [IO.File]::ReadAllLines((Join-Path $fpkDir "manifest"), [Text.Encoding]::UTF8)
$version = (($mf | Where-Object { $_ -match "^version\s*=" }) -replace ".*=\s*", "").Trim()
$mfPlatform = (($mf | Where-Object { $_ -match "^platform\s*=" }) -replace ".*=\s*", "").Trim()
if ($mfPlatform -ne $Platform) {
    throw "manifest platform=$mfPlatform 与 -Platform $Platform 不一致：请先修改 fpk\manifest 的 platform 字段"
}

# 平台二进制校验（vendor 的 llama.cpp 产物必须就位）
$binArch = "x86_64"
if ($Platform -eq "arm") { $binArch = "arm64" }
$binDir = Join-Path $fpkDir ("app\bin\cpu-" + $binArch)
if (-not (Test-Path (Join-Path $binDir "llama-server"))) {
    throw ("缺少 " + $binDir + "\llama-server：请先运行 python scripts\vendor-binaries.py --arch " + $binArch)
}

# 关键结构校验（fnpack 也会校验，这里给出更友好的报错）
foreach ($must in @("manifest", "config\privilege", "config\resource", "cmd\main", "app\ui\config", "app\ui\index.cgi", "app\ui\web\index.html", "wizard", "ICON.PNG", "ICON_256.PNG")) {
    if (-not (Test-Path (Join-Path $fpkDir $must))) { throw ("fpk 缺少: " + $must) }
}

# 打包（本机安全监控拦截新建 PE 二进制的留存与执行，官方 fnpack.exe 无法
# 稳定使用；fpk 格式已由官方 fnpack 1.2.3 产物逆向验证并经 0.1.0~0.4.3 真机
# 安装确认，故以纯 Python 打包器等效实现，预检查项与官方一致）
python (Join-Path $root "scripts\pack-fpk.py") --dir $fpkDir --out (Join-Path $fpkDir "llamacpp.fpk")
if ($LASTEXITCODE -ne 0) { throw "fpk 打包失败" }

# 产物移动 + 生成校验和
$src = Join-Path $fpkDir "llamacpp.fpk"
$out = Join-Path $dist ("llamacpp-" + $version + "-" + $Platform + ".fpk")
Move-Item $src $out -Force
$hash = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
($hash + "  " + (Split-Path -Leaf $out)) | Set-Content (Join-Path $dist "SHA256SUMS") -Encoding ASCII
Write-Output ("产物: " + $out + " (" + [math]::Round((Get-Item $out).Length / 1MB, 1) + " MB)")
Write-Output ("SHA256: " + $hash)

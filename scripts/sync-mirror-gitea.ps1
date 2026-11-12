# sync-mirror-gitea.ps1 —— 把 llama.cpp GitHub release 资产镜像到 Gitea
# 路径约定与 GitHub 完全一致：
#   https://github.com/ggml-org/llama.cpp/releases/download/<tag>/<file>
#   → http://<gitea>/<owner>/<repo>/releases/download/<tag>/<file>
# 用法（PowerShell 5.1+）：
#   .\scripts\sync-mirror-gitea.ps1 -Token <gitea_token>
#   可选：-Tag b12220 -GiteaBase http://192.168.0.114:3000 -Owner T366 -Repo llama.cpp-fnos
#   Tag 默认与 fpk/app/manager/variants.py 的 PINNED_TAG 保持一致
param(
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Tag = "b12220",
    [string]$GiteaBase = "http://192.168.0.114:3000",
    [string]$Owner = "T366",
    [string]$Repo = "llama.cpp-fnos",
    [string]$CacheDir = "$PSScriptRoot\.gh-cache"
)

$ErrorActionPreference = "Stop"

# 资产清单与 fpk/app/manager/variants.py 的 VARIANTS 保持一致
$Assets = @(
    "llama-$Tag-bin-ubuntu-x64.tar.gz",
    "llama-$Tag-bin-ubuntu-arm64.tar.gz",
    "llama-$Tag-bin-ubuntu-vulkan-x64.tar.gz",
    "llama-$Tag-bin-ubuntu-vulkan-arm64.tar.gz",
    "llama-$Tag-bin-ubuntu-cuda-12.8-x64.tar.gz",
    "cudart-llama-$Tag-bin-ubuntu-cuda-12.8-x64.tar.gz",
    "llama-$Tag-bin-ubuntu-cuda-13.5-x64.tar.gz",
    "cudart-llama-$Tag-bin-ubuntu-cuda-13.5-x64.tar.gz"
)

$GhBase = "https://github.com/ggml-org/llama.cpp/releases/download/$Tag"
$ApiRepos = "$GiteaBase/api/v1/repos/$Owner/$Repo"
$Headers = @{ Authorization = "token $Token" }
New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null

function Get-Release([string]$TagName) {
    # 取 release（不存在则创建）
    $r = Invoke-RestMethod -Headers $Headers `
        -Uri "$ApiRepos/releases/tags/$TagName" -ErrorAction SilentlyContinue
    if ($r) { return $r }
    Write-Host "[release] 创建 release $TagName"
    $body = @{ tag_name = $TagName; name = "llama.cpp $TagName"; 
               body = "llama.cpp binary release mirror (synced by sync-mirror-gitea.ps1)";
               draft = $false; prerelease = $false } | ConvertTo-Json
    return Invoke-RestMethod -Headers $Headers -Uri "$ApiRepos/releases" `
        -Method Post -Body $body -ContentType "application/json"
}

function Get-AssetNames($Release) {
    $set = @{}
    foreach ($a in $Release.assets) { $set[$a.name] = $true }
    return $set
}

$release = Get-Release -TagName $Tag
$existing = Get-AssetNames $release
$uploadBase = "$ApiRepos/releases/$($release.id)/assets"

foreach ($name in $Assets) {
    $local = Join-Path $CacheDir $name
    if (-not (Test-Path $local)) {
        Write-Host "[download] $name"
        & curl.exe -sSL --fail --retry 3 -o $local "$GhBase/$name"
        if ($LASTEXITCODE -ne 0) { throw "GitHub 下载失败：$name" }
    } else {
        Write-Host "[cached]   $name"
    }
    if ($existing.ContainsKey($name)) {
        Write-Host "[exists]   $name（Gitea 已有，跳过）"
        continue
    }
    Write-Host "[upload]   $name"
    # PS5.1 无 Invoke-RestMethod -Form，用 curl.exe multipart 上传
    & curl.exe -sS --fail -X POST `
        -H "Authorization: token $Token" `
        -F "attachment=@$local" `
        "$uploadBase`?name=$([uri]::EscapeDataString($name))" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Gitea 上传失败：$name" }
}
Write-Host "完成。镜像下载路径：$GiteaBase/$Owner/$Repo/releases/download/$Tag/<file>"

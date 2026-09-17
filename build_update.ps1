param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^\d+\.\d+\.\d+$')]
  [string]$Version,
  [string]$Notes = "Cập nhật tính năng và sửa lỗi.",
  [switch]$RuntimeUpdate,
  [switch]$Publish,
  [string]$Repository = "DungAnh1301/VideoSummarizerPro"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$root = $PSScriptRoot
$stageName = "update-payload-$Version-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$stage = Join-Path $root ("build\" + $stageName)
$release = Join-Path $root "release"
New-Item -ItemType Directory -Force -Path $stage, $release | Out-Null

$versionData = @{ version = $Version; channel = "stable" }
$versionData | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root "version.json") -Encoding UTF8

$files = @(
  "main.py", "compilation_processor.py", "ai_processor.py", "antigravity_processor.py", "batch_queue.py", "broll_semantic.py",
  "capcut_filters.py", "config_manager.py", "downloader_processor.py",
  "editor_ai.py", "editor_processor.py", "ensure_runtime.py",
  "hardware_manager.py", "local_source_processor.py", "market_profiles.py", "part_processor.py", "scene_mapper.py",
  "youtube_heatmap.py", "update_manager.py", "updater.py", "version.json", "requirements.txt",
  "config.example.json", "Chay_App.bat", "README_CAI_DAT.md",
  "font_manager.py", "part_splitter_config.py", "part_pruner_ai.py",
  "part_splitter_engine.py", "part_splitter_gui.py", "config_part_splitter.json", "PLAN_CHIA_PART.md"
)
foreach ($name in $files) {
  $source = Join-Path $root $name
  if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    Write-Error "Thiếu file bắt buộc: $name"
  }
  Copy-Item -LiteralPath $source -Destination (Join-Path $stage $name) -Force
}

foreach ($folder in @("utils", "assets", "luts")) {
  $source = Join-Path $root $folder
  if (Test-Path -LiteralPath $source -PathType Container) {
    Copy-Item -LiteralPath $source -Destination (Join-Path $stage $folder) -Recurse -Force
  }
}

$agyInstaller = Join-Path $root "runtime\antigravity-install.cmd"
if (-not (Test-Path -LiteralPath $agyInstaller -PathType Leaf)) {
  Write-Error "Thiếu bộ cài Antigravity: $agyInstaller"
}
New-Item -ItemType Directory -Force -Path (Join-Path $stage "runtime") | Out-Null
Copy-Item -LiteralPath $agyInstaller -Destination (Join-Path $stage "runtime\antigravity-install.cmd") -Force
# Updater của các bản cũ bảo vệ toàn bộ thư mục runtime nên sẽ bỏ qua file
# phía trên. Đặt thêm một bản ở root để chính updater cũ vẫn cài được;
# AntigravityProcessor ưu tiên runtime rồi tự fallback sang root.
Copy-Item -LiteralPath $agyInstaller -Destination (Join-Path $stage "antigravity-install.cmd") -Force

$packageName = "VideoSummarizerPro_Update_$Version.zip"
$packagePath = Join-Path $release $packageName
if (Test-Path -LiteralPath $packagePath) {
  Remove-Item -LiteralPath $packagePath -Force
}
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $packagePath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $packagePath).Length
$manifest = [ordered]@{
  version = $Version
  package = $packageName
  sha256 = $hash
  size = $size
  notes = $Notes
  runtime_update = [bool]$RuntimeUpdate
  minimum_setup_version = "1.1.0"
  repository = $Repository
  published_at = (Get-Date).ToString("o")
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $release "latest.json") -Encoding UTF8

# Chỉ dọn staging riêng của lần build này. Không đụng thư mục staging cũ có thể
# đang bị khóa hoặc được tạo bởi tài khoản Windows khác.
try {
  Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction Stop
} catch {
  Write-Warning "Không dọn được staging tạm: $stage. Có thể xóa thủ công sau."
}

Write-Host ""
Write-Host "UPDATE READY" -ForegroundColor Green
Write-Host "Tag GitHub : v$Version"
Write-Host "Upload     : $packagePath"
Write-Host "Upload     : $(Join-Path $release 'latest.json')"
Write-Host "SHA-256    : $hash"

if ($Publish) {
  $ghCommand = Get-Command gh.exe -ErrorAction SilentlyContinue
  if (-not $ghCommand) {
    $knownGh = "C:\Program Files\GitHub CLI\gh.exe"
    if (Test-Path -LiteralPath $knownGh -PathType Leaf) {
      $ghCommand = Get-Item -LiteralPath $knownGh
    }
  }
  if (-not $ghCommand) {
    Write-Error "Chưa cài GitHub CLI. Cài bằng: winget install --id GitHub.cli"
  }
  $gh = $ghCommand.Source
  if (-not $gh) { $gh = $ghCommand.FullName }

  & $gh auth status --hostname github.com | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "GitHub CLI chưa đăng nhập. Chạy: gh auth login"
  }

  $tag = "v$Version"
  # `gh release view` returns exit code 1 when the release does not exist.
  # Windows PowerShell can turn its stderr into a terminating NativeCommandError
  # because this script uses ErrorActionPreference=Stop, so suppress it only for
  # this expected existence check.
  $savedErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "SilentlyContinue"
    & $gh release view $tag --repo $Repository *> $null
    $releaseViewExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $savedErrorActionPreference
  }
  $releaseExists = $releaseViewExitCode -eq 0
  if ($releaseExists) {
    Write-Host "Release $tag đã có — cập nhật ghi chú và ghi đè assets..." -ForegroundColor Yellow
    & $gh release edit $tag --repo $Repository --title "Video Summarizer Pro $tag" --notes $Notes --latest
    if ($LASTEXITCODE -ne 0) { Write-Error "Không cập nhật được release $tag." }
    & $gh release upload $tag $packagePath (Join-Path $release "latest.json") --repo $Repository --clobber
    if ($LASTEXITCODE -ne 0) { Write-Error "Upload assets $tag thất bại." }
  } else {
    Write-Host "Tạo GitHub Release $tag và upload assets..." -ForegroundColor Cyan
    & $gh release create $tag $packagePath (Join-Path $release "latest.json") `
      --repo $Repository --title "Video Summarizer Pro $tag" --notes $Notes --latest
    if ($LASTEXITCODE -ne 0) { Write-Error "Tạo GitHub Release $tag thất bại." }
  }
  Write-Host "PUBLISHED   : https://github.com/$Repository/releases/tag/$tag" -ForegroundColor Green
}

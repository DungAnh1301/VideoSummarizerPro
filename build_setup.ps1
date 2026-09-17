$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "=== Build 1 file: dist\VideoSummarizerPro_Setup.exe ===" -ForegroundColor Cyan

$root = $PSScriptRoot
$payload = Join-Path $root "build\payload"
$dist = Join-Path $root "dist"
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Force -Path $payload, $dist | Out-Null

$copyFiles = @(
  "main.py", "setup.py", "ensure_runtime.py", "installer_entry.py", "hardware_manager.py",
  "ai_processor.py", "antigravity_processor.py", "editor_ai.py", "editor_processor.py", "broll_semantic.py",
  "downloader_processor.py", "local_source_processor.py", "config_manager.py",
  "capcut_filters.py", "batch_queue.py",
  "scene_mapper.py", "youtube_heatmap.py", "update_manager.py", "updater.py", "version.json",
  "Chay_App.bat", "config.example.json",
  "requirements.txt", "README_CAI_DAT.md",
  "sfx_config.txt"
)
foreach ($f in $copyFiles) {
  $src = Join-Path $root $f
  if (Test-Path $src) { Copy-Item $src -Destination $payload -Force }
  else { Write-Host "Skip missing: $f" -ForegroundColor DarkYellow }
}

foreach ($dirName in @("utils", "assets", "luts")) {
  $srcDir = Join-Path $root $dirName
  if (Test-Path $srcDir) {
    Write-Host "Pack folder $dirName..."
    Copy-Item $srcDir -Destination (Join-Path $payload $dirName) -Recurse -Force
  }
}

$tts = Join-Path $root "vendor\capcut_tts"
if (Test-Path (Join-Path $tts "capcut_tts_api\__init__.py")) {
  Write-Host "Pack CapCut TTS..."
  New-Item -ItemType Directory -Force -Path (Join-Path $payload "vendor") | Out-Null
  Copy-Item $tts -Destination (Join-Path $payload "vendor\capcut_tts") -Recurse -Force
}

$tools = Join-Path $payload "tools"
New-Item -ItemType Directory -Force -Path $tools | Out-Null
$pyInst = Join-Path $tools "python-3.12.10-amd64.exe"
$pyName = "python-3.12.10-amd64.exe"
$localPy = @(
  (Join-Path $root "tools\$pyName"),
  (Join-Path $root "temp\$pyName"),
  (Join-Path $root "runtime\cache\$pyName")
) | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1
if ($localPy) {
  Write-Host "Reuse local Python installer: $localPy"
  Copy-Item $localPy $pyInst -Force
} elseif (-not (Test-Path $pyInst)) {
  Write-Host "Download Python installer (curl / BITS / WebRequest)..."
  try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
  $pyUrls = @(
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe",
    "https://github.com/adang1345/PythonWin7/releases/download/3.12.10/python-3.12.10-amd64.exe"
  )
  $ok = $false
  foreach ($pyUrl in $pyUrls) {
    Write-Host "  $pyUrl"
    for ($i = 1; $i -le 5; $i++) {
      if (Test-Path $pyInst) { Remove-Item $pyInst -Force -ErrorAction SilentlyContinue }
      try {
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
          & curl.exe -L --retry 5 --retry-all-errors --retry-delay 3 --connect-timeout 30 -A "Mozilla/5.0" -o $pyInst $pyUrl
        } elseif (Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue) {
          Start-BitsTransfer -Source $pyUrl -Destination $pyInst -ErrorAction Stop
        } else {
          Invoke-WebRequest -Uri $pyUrl -OutFile $pyInst -UseBasicParsing -UserAgent "Mozilla/5.0"
        }
        if ((Test-Path $pyInst) -and ((Get-Item $pyInst).Length -gt 20000000)) { $ok = $true; break }
      } catch {
        Write-Host "  try $i failed: $($_.Exception.Message)" -ForegroundColor Yellow
      }
      Start-Sleep -Seconds (3 * $i)
    }
    if ($ok) { break }
  }
  if (-not $ok) {
    Write-Error "Download Python installer failed. Disable antivirus then rerun build_setup.ps1."
  }
  New-Item -ItemType Directory -Force -Path (Join-Path $root "tools") | Out-Null
  Copy-Item $pyInst (Join-Path $root "tools\$pyName") -Force
}

Write-Host "Create clean portable Python with all dependencies..."
$payloadPy = Join-Path $payload "runtime\python"
New-Item -ItemType Directory -Force -Path $payloadPy | Out-Null
$stagePyDir = Join-Path $root "build\python-stage"
$stagePyExe = Join-Path $stagePyDir "python.exe"
if (-not (Test-Path $stagePyExe)) {
  $pythonCandidates = @()
  $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($pythonCommand) { $pythonCandidates += $pythonCommand.Source }
  $pythonCandidates += (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
  $sourcePythonHome = $null
  foreach ($candidate in $pythonCandidates) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
    try {
      & $candidate -c "import tkinter,sys; print(sys.base_prefix)" | Out-Null
      if ($LASTEXITCODE -eq 0) {
        $sourcePythonHome = (& $candidate -c "import sys; print(sys.base_prefix)").Trim()
        if ($sourcePythonHome -and (Test-Path (Join-Path $sourcePythonHome "python.exe"))) { break }
      }
    } catch {}
  }

  if ($sourcePythonHome) {
    Write-Host "  Copy clean Python base from: $sourcePythonHome"
    New-Item -ItemType Directory -Force -Path $stagePyDir | Out-Null
    Copy-Item -Path (Join-Path $sourcePythonHome "*") -Destination $stagePyDir -Recurse -Force
    $stageSitePackages = Join-Path $stagePyDir "Lib\site-packages"
    if (Test-Path -LiteralPath $stageSitePackages) {
      Remove-Item -LiteralPath $stageSitePackages -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $stageSitePackages | Out-Null
    & $stagePyExe -m ensurepip --upgrade
  } else {
    New-Item -ItemType Directory -Force -Path $stagePyDir | Out-Null
    $pythonInstallArgs = @(
      "/quiet", "InstallAllUsers=0", "Include_launcher=0", "Include_test=0",
      "Include_pip=1", "Include_tcltk=1", "PrependPath=0", "Shortcuts=0",
      "TargetDir=$stagePyDir"
    )
    Write-Host "  Install Python stage at: $stagePyDir"
    $pythonInstall = Start-Process -FilePath $pyInst -ArgumentList $pythonInstallArgs -Wait -PassThru -WindowStyle Hidden
    if ($pythonInstall.ExitCode -ne 0 -or -not (Test-Path $stagePyExe)) {
      Write-Error "Cannot create staged Python runtime (installer exit $($pythonInstall.ExitCode))."
    }
  }
}

$agyInstaller = Join-Path $root "runtime\antigravity-install.cmd"
if (-not (Test-Path -LiteralPath $agyInstaller -PathType Leaf)) {
  Write-Error "Missing Antigravity bootstrap: $agyInstaller"
}
New-Item -ItemType Directory -Force -Path (Join-Path $payload "runtime") | Out-Null
Copy-Item -LiteralPath $agyInstaller -Destination (Join-Path $payload "runtime\antigravity-install.cmd") -Force
Copy-Item -Path (Join-Path $stagePyDir "*") -Destination $payloadPy -Recurse -Force
& (Join-Path $payloadPy "python.exe") -m pip install --disable-pip-version-check --no-cache-dir -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Error "Cannot install Python dependencies into payload." }

Write-Host "Pack FFmpeg + FFprobe into payload\bin ..."
$payloadBin = Join-Path $payload "bin"
New-Item -ItemType Directory -Force -Path $payloadBin | Out-Null
$ffmpegCmd = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
$ffprobeCmd = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
if ($ffmpegCmd -and $ffprobeCmd) {
  Copy-Item $ffmpegCmd.Source (Join-Path $payloadBin "ffmpeg.exe") -Force
  Copy-Item $ffprobeCmd.Source (Join-Path $payloadBin "ffprobe.exe") -Force
} else {
  $ffZip = Join-Path $root "build\ffmpeg-release.zip"
  if (-not (Test-Path $ffZip)) {
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $ffZip -UseBasicParsing
  }
  $ffStage = Join-Path $root "build\ffmpeg-stage"
  if (Test-Path $ffStage) { Remove-Item -LiteralPath $ffStage -Recurse -Force }
  Expand-Archive -LiteralPath $ffZip -DestinationPath $ffStage -Force
  $ffmpegFile = Get-ChildItem -LiteralPath $ffStage -Recurse -Filter ffmpeg.exe | Select-Object -First 1
  $ffprobeFile = Get-ChildItem -LiteralPath $ffStage -Recurse -Filter ffprobe.exe | Select-Object -First 1
  if (-not $ffmpegFile -or -not $ffprobeFile) { Write-Error "FFmpeg package is incomplete." }
  Copy-Item $ffmpegFile.FullName (Join-Path $payloadBin "ffmpeg.exe") -Force
  Copy-Item $ffprobeFile.FullName (Join-Path $payloadBin "ffprobe.exe") -Force
}

$hfCache = Join-Path $root "runtime\hf_cache"
if (Test-Path $hfCache) {
  Write-Host "Pack Whisper base model cache..."
  New-Item -ItemType Directory -Force -Path (Join-Path $payload "runtime") | Out-Null
  Copy-Item $hfCache -Destination (Join-Path $payload "runtime\hf_cache") -Recurse -Force
}

Write-Host "Build slim setup.exe bootstrap..."
& $stagePyExe -m pip install --quiet pyinstaller
& $stagePyExe -m PyInstaller --noconfirm --clean Installer.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$boot = Join-Path $root "dist\setup.exe"
if (-not (Test-Path $boot)) { Write-Error "Missing dist\setup.exe" }
Copy-Item $boot -Destination (Join-Path $payload "setup.exe") -Force

function Find-ISCC {
  $paths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe"
  )
  foreach ($p in $paths) { if (Test-Path $p) { return $p } }
  $cmd = Get-Command iscc -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

$iscc = Find-ISCC
if (-not $iscc) {
  Write-Host "Installing Inno Setup via winget..."
  winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
  $iscc = Find-ISCC
}

$outExe = Join-Path $dist "VideoSummarizerPro_Setup.exe"

if ($iscc) {
  Write-Host "Inno Setup compile: $iscc"
  & $iscc (Join-Path $root "installer.iss")
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
  Write-Host "Inno missing, using 7-Zip SFX..."
  $seven = "C:\Program Files\7-Zip\7z.exe"
  if (-not (Test-Path $seven)) { $seven = (Get-Command 7z).Source }
  $extraZip = Join-Path $root "temp\7z-extra.7z"
  New-Item -ItemType Directory -Force -Path (Join-Path $root "temp") | Out-Null
  if (-not (Test-Path $extraZip)) {
    Invoke-WebRequest -Uri "https://www.7-zip.org/a/7z2409-extra.7z" -OutFile $extraZip -UseBasicParsing
  }
  $extraDir = Join-Path $root "temp\7z-extra"
  New-Item -ItemType Directory -Force -Path $extraDir | Out-Null
  & $seven x $extraZip "-o$extraDir" -y | Out-Null
  $sfx = Get-ChildItem $extraDir -Recurse -Filter "7zS.sfx" | Select-Object -First 1
  if (-not $sfx) { Write-Error "Missing 7zS.sfx" }
  $archive = Join-Path $root "temp\payload.7z"
  if (Test-Path $archive) { Remove-Item $archive -Force }
  & $seven a -t7z -mx=7 $archive "$payload\*" | Out-Null
  $cfgCopy = Join-Path $root "temp\sfxconfig.txt"
  Copy-Item (Join-Path $root "sfx_config.txt") $cfgCopy -Force
  $bytes = [IO.File]::ReadAllBytes($sfx.FullName) + [IO.File]::ReadAllBytes($cfgCopy) + [IO.File]::ReadAllBytes($archive)
  [IO.File]::WriteAllBytes($outExe, $bytes)
}

if (-not (Test-Path $outExe)) { Write-Error "Failed to create output EXE" }

$visible = Join-Path $root "VideoSummarizerPro_Setup.exe"
Copy-Item $outExe $visible -Force
foreach ($fat in @(
    (Join-Path $root "CaiDat_VideoSummarizerPro.exe"),
    (Join-Path $root "setup.exe"),
    (Join-Path $dist "CaiDat_VideoSummarizerPro.exe")
)) {
  if (Test-Path $fat) { Remove-Item $fat -Force }
}

Get-Item $outExe, $visible | Format-List Name, Length, FullName
if (Test-Path -LiteralPath $dist) {
  Remove-Item -LiteralPath $dist -Recurse -Force
}
Write-Host "DONE. Only output: $visible" -ForegroundColor Green

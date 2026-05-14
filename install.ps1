# install.ps1 — Windows 一键安装 game-ad-imagegen skill
# 用法:cd 到本目录(D:\game-ad-imagegen),跑 .\install.ps1
#
# 行为:
#   1. 在 ~/.claude/skills/game-ad-imagegen/ 建 junction → 当前目录
#   2. 如果有 WorkBuddy,在 ~/.workbuddy/skills/game-ad-imagegen/ 也建 junction
#   3. 验证 Python / openai SDK / config.toml 状态
#   4. 提示下一步(WorkBuddy 重启 + 怎么用)

$ErrorActionPreference = 'Stop'
$SkillName = 'game-ad-imagegen'
$SkillDir = (Get-Location).Path

Write-Host "==> Installing $SkillName" -ForegroundColor Cyan
Write-Host "    Skill source = $SkillDir"

# 1. ~/.claude/skills/ junction
$ClaudeSkills = Join-Path $env:USERPROFILE ".claude\skills"
if (-not (Test-Path $ClaudeSkills)) {
  New-Item -ItemType Directory -Path $ClaudeSkills -Force | Out-Null
  Write-Host "    Created $ClaudeSkills"
}
$ClaudeTarget = Join-Path $ClaudeSkills $SkillName
if (Test-Path $ClaudeTarget) {
  $item = Get-Item $ClaudeTarget -Force
  if ($item.LinkType -eq 'Junction') {
    Write-Host "    [skip] $ClaudeTarget already a junction → $($item.Target)" -ForegroundColor Yellow
  } else {
    Write-Host "    ! $ClaudeTarget exists but not a junction. Rename it manually then re-run." -ForegroundColor Red
    Write-Host "      e.g.: Rename-Item '$ClaudeTarget' '${ClaudeTarget}.BAK'"
    exit 1
  }
} else {
  New-Item -ItemType Junction -Path $ClaudeTarget -Target $SkillDir | Out-Null
  Write-Host "    Created junction $ClaudeTarget → $SkillDir" -ForegroundColor Green
}

# 2. ~/.workbuddy/skills/ junction (如有)
$WbSkills = Join-Path $env:USERPROFILE ".workbuddy\skills"
if (Test-Path $WbSkills) {
  $WbTarget = Join-Path $WbSkills $SkillName
  if (Test-Path $WbTarget) {
    $item = Get-Item $WbTarget -Force
    if ($item.LinkType -eq 'Junction') {
      Write-Host "    [skip] $WbTarget already a junction → $($item.Target)" -ForegroundColor Yellow
    } else {
      Write-Host "    ! $WbTarget exists but not a junction. Skip or rename it manually." -ForegroundColor Yellow
    }
  } else {
    New-Item -ItemType Junction -Path $WbTarget -Target $SkillDir | Out-Null
    Write-Host "    Created junction $WbTarget → $SkillDir" -ForegroundColor Green
  }
} else {
  Write-Host "    [skip] No WorkBuddy install detected ($WbSkills missing)"
}

# 2b. _skillhub_meta.json — 让 WorkBuddy UI 在 skill 列表里能识别+显示本 skill
#     (没这个 metadata 文件 WB UI 不显示,即使 junction 已建。亲测 2026-05-14)
$MetaPath = Join-Path $SkillDir "_skillhub_meta.json"
if (Test-Path $MetaPath) {
  Write-Host "    [skip] _skillhub_meta.json already exists at $MetaPath" -ForegroundColor Yellow
} else {
  $now = [int64](([datetime]::UtcNow - (Get-Date "1970-01-01")).TotalMilliseconds)
  $metaObj = [ordered]@{
    name = $SkillName
    installedAt = $now
    source = "marketplace"
    iconSource = $SkillName
    version = "0.1.2"
  }
  $metaObj | ConvertTo-Json | Out-File -FilePath $MetaPath -Encoding UTF8
  Write-Host "    Created $MetaPath (lets WorkBuddy UI list this skill)" -ForegroundColor Green
}

# 3. 验证依赖
Write-Host ""
Write-Host "==> Verifying dependencies" -ForegroundColor Cyan
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  Write-Host "    Python not found — attempting auto-install via winget..." -ForegroundColor Yellow
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if (-not $winget) {
    Write-Host "    ! winget not available either. Install Python 3.10+ manually:" -ForegroundColor Red
    Write-Host "      Option 1: https://www.python.org/downloads/  (download installer, check 'Add to PATH')"
    Write-Host "      Option 2: Open Microsoft Store, search 'Python 3.12', install"
    Write-Host "      Then close PowerShell, reopen, and re-run .\install.ps1"
    exit 1
  }
  winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) {
    Write-Host "    ! winget install failed. Install Python 3.10+ manually from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
  }
  Write-Host "    Python installed via winget." -ForegroundColor Green
  Write-Host "    ⚠ Close this PowerShell window, open a new one, then re-run .\install.ps1" -ForegroundColor Yellow
  Write-Host "      (PATH won't refresh in the current session)"
  exit 0
}
$pyVer = (python --version) 2>&1
Write-Host "    Python: $pyVer"

$openai = python -c "import openai; print(openai.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "    openai SDK missing — installing now (pip install --user openai)..." -ForegroundColor Yellow
  python -m pip install --user --quiet openai
  if ($LASTEXITCODE -ne 0) {
    Write-Host "    ! pip install failed. Run manually:" -ForegroundColor Red
    Write-Host "      python -m pip install --user openai"
    exit 1
  }
  $openai = python -c "import openai; print(openai.__version__)" 2>&1
  Write-Host "    openai SDK installed: $openai" -ForegroundColor Green
} else {
  Write-Host "    openai SDK: $openai"
}

# 4. config.toml 状态
$CfgPath = Join-Path $env:USERPROFILE ".config\$SkillName\config.toml"
Write-Host ""
Write-Host "==> Config status" -ForegroundColor Cyan
if (Test-Path $CfgPath) {
  Write-Host "    config.toml exists at $CfgPath (key already set)" -ForegroundColor Green
} else {
  Write-Host "    config.toml NOT set yet — agent will ask you for the key on first run"
  Write-Host "    (will be written to $CfgPath)"
}

Write-Host ""
Write-Host "==> Done!" -ForegroundColor Green
Write-Host "    1. If WorkBuddy was running, restart it so the new skill loads"
Write-Host "    2. In WorkBuddy / Claude Code chat, say: '做几张游戏广告图' / '复刻这张爆款'"
Write-Host "    3. Agent will open the batch form + guide you through"

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

# 3. 验证依赖
Write-Host ""
Write-Host "==> Verifying dependencies" -ForegroundColor Cyan
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  Write-Host "    ! Python not found on PATH. Install Python 3.10+ first." -ForegroundColor Red
  exit 1
}
$pyVer = (python --version) 2>&1
Write-Host "    Python: $pyVer"

$openai = python -c "import openai; print(openai.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "    ! openai SDK missing. Install with: pip install openai" -ForegroundColor Red
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

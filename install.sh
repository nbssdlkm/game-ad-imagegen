#!/usr/bin/env bash
# install.sh — macOS / Linux 一键安装 game-ad-imagegen skill
# 用法:cd 到本目录,跑 ./install.sh

set -e
SKILL_NAME="game-ad-imagegen"
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Installing $SKILL_NAME"
echo "    Skill source = $SKILL_DIR"

# 1. ~/.claude/skills/ symlink
CLAUDE_SKILLS="$HOME/.claude/skills"
mkdir -p "$CLAUDE_SKILLS"
CLAUDE_TARGET="$CLAUDE_SKILLS/$SKILL_NAME"
if [ -e "$CLAUDE_TARGET" ] || [ -L "$CLAUDE_TARGET" ]; then
  if [ -L "$CLAUDE_TARGET" ]; then
    echo "    [skip] $CLAUDE_TARGET already a symlink → $(readlink "$CLAUDE_TARGET")"
  else
    echo "    ! $CLAUDE_TARGET exists but not a symlink. Rename then re-run."
    exit 1
  fi
else
  ln -s "$SKILL_DIR" "$CLAUDE_TARGET"
  echo "    Created symlink $CLAUDE_TARGET → $SKILL_DIR"
fi

# 2. ~/.workbuddy/skills/ symlink (if exists)
WB_SKILLS="$HOME/.workbuddy/skills"
if [ -d "$WB_SKILLS" ]; then
  WB_TARGET="$WB_SKILLS/$SKILL_NAME"
  if [ -e "$WB_TARGET" ] || [ -L "$WB_TARGET" ]; then
    if [ -L "$WB_TARGET" ]; then
      echo "    [skip] $WB_TARGET already a symlink"
    else
      echo "    ! $WB_TARGET exists but not a symlink. Skip."
    fi
  else
    ln -s "$SKILL_DIR" "$WB_TARGET"
    echo "    Created symlink $WB_TARGET → $SKILL_DIR"
  fi
else
  echo "    [skip] No WorkBuddy install detected ($WB_SKILLS missing)"
fi

# 3. Verify deps
echo ""
echo "==> Verifying dependencies"
if ! command -v python >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
  echo "    ! Python not found. Install Python 3.10+ then re-run ./install.sh"
  echo "      macOS:        brew install python@3.12   (or: https://www.python.org/downloads/)"
  echo "      Ubuntu/Debian: sudo apt install python3 python3-pip"
  echo "      Fedora/RHEL:   sudo dnf install python3 python3-pip"
  echo "      Arch:          sudo pacman -S python python-pip"
  exit 1
fi
PY="$(command -v python3 || command -v python)"
echo "    Python: $($PY --version 2>&1)"
if ! $PY -c "import openai" >/dev/null 2>&1; then
  echo "    openai SDK missing — installing now ($PY -m pip install --user openai)..."
  if ! $PY -m pip install --user --quiet openai; then
    echo "    ! pip install failed. Run manually: $PY -m pip install --user openai"
    exit 1
  fi
  echo "    openai SDK installed: $($PY -c 'import openai; print(openai.__version__)')"
else
  echo "    openai SDK: $($PY -c 'import openai; print(openai.__version__)')"
fi

# 4. config.toml status
CFG_PATH="$HOME/.config/$SKILL_NAME/config.toml"
echo ""
echo "==> Config status"
if [ -f "$CFG_PATH" ]; then
  echo "    config.toml exists at $CFG_PATH (key already set)"
else
  echo "    config.toml NOT set — agent will ask you for the key on first run"
fi

echo ""
echo "==> Done!"
echo "    1. Restart WorkBuddy if it was running"
echo "    2. Say in chat: '做几张游戏广告图' / '复刻这张爆款'"

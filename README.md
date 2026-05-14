# game-ad-imagegen

Claude Code / WorkBuddy / 任意 agent 框架的图片生成 skill。专做**游戏买量广告 / 海报 / 买量素材**(爆款复刻 + 多张系列 + 6 步 vision 工作流)。

通用图片任务(单图修改 / 0 图文生图 / 任意题材)请用兄弟 skill [`codex-imagegen-fork`](https://github.com/nbssdlkm/codex-imagegen-fork)(B 路径)。两个 skill 完全独立,可分别安装。

## 快速安装

### Windows (PowerShell)

```powershell
# 1. clone 仓库到 D:\game-ad-imagegen\(或任意位置)
git clone <repo-url> D:\game-ad-imagegen

# 2. 跑 install.ps1 自动建 junction 到 ~/.claude/skills/ 和 ~/.workbuddy/skills/
cd D:\game-ad-imagegen
.\install.ps1
```

### macOS / Linux

```bash
# 1. clone
git clone <repo-url> ~/game-ad-imagegen
# 2. 跑 install.sh
cd ~/game-ad-imagegen
./install.sh
```

`install` 脚本会:
- 在 `~/.claude/skills/game-ad-imagegen/` 建 junction(Windows)/ symlink(Unix)→ 当前目录
- 在 `~/.workbuddy/skills/game-ad-imagegen/`(如果 WorkBuddy 装了)同样建
- 验证 `python` 和 `openai` SDK 可用

## 首次使用

装完后,在 WorkBuddy / Claude Code 对话区直接说:

> "做几张游戏广告图" / "复刻这张爆款" / "出 N 张系列广告" / "我要生图(用 game-ad-imagegen)"

agent 会自动起 HTML 表单 + 引导 user 填图/prompt + 跑批 + 显示进度页。

**首次会要 API key**:agent 问你贴 ephone(或其他 OpenAI 兼容代理)的 API key,自动写到 `~/.config/game-ad-imagegen/config.toml`,以后不再问。

## 文件结构

```
game-ad-imagegen/
├── SKILL.md              ← 主体: 6 步 vision workflow + Batch UX 段
├── README.md             ← 本文件
├── install.ps1           ← Windows 安装脚本
├── install.sh            ← Unix 安装脚本
├── scripts/
│   ├── image_gen.py      ← 出图主程序(走 /v1/images/edits)
│   ├── _config.py        ← key 加载 3 路 fallback(env / config.toml)
│   ├── batch_runner.py   ← HTML 表单 config.json → 批量出图
│   ├── render_result_grid.py  ← 渲染进度页 + 最终 grid
│   └── ...(历史接口)
├── web/                  ← HTML 表单(用户配批量任务的入口)
│   ├── batch_form.html
│   ├── batch_form.js
│   ├── style.css
│   └── grid_template.html
└── fixtures/             ← 最小反例 JSON 用于自检
    ├── good_minimal.json
    ├── bad_empty_refs.json
    ├── bad_empty_prompt.json
    ├── bad_wrong_skill.json
    ├── bad_missing_path.json
    └── README.md
```

## 跟 B skill 的差异化

| | A: game-ad-imagegen | B: codex-imagegen-fork |
|---|---|---|
| 定位 | 游戏买量广告特化(爆款复刻 + 多张系列) | 通用图片任务(任意题材 / 单图修改 / 文生图) |
| 0 图 text2im | ❌ reject(需 ≥1 参考图) | ✅ `generate` 子命令 |
| 工作流 | 6 步 vision/拆解/选角/rewrite/调图/归档 | 18 步通用 workflow |
| 端点 | 写死 `/v1/images/edits` | `generate` / `edit` 子命令自动选 |
| 装哪个 | 主要做游戏广告 | 通用 / 单图修改 / 文生图 |

通常**都装上**,agent 根据用户话术自动路由到对应 skill。

## 依赖

- **Python 3.10+** —— 唯一要你手装的(`install.ps1` 会自动 `pip install --user openai`,设计师不用懂 pip)
- **OpenAI 兼容 API key**(ephone / OpenAI 官方 / 任意代理) —— 首次跑时 agent 会问你贴一次,写到 `~/.config/game-ad-imagegen/config.toml`,以后不再问

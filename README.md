# game-ad-imagegen

Claude Code / WorkBuddy / 任意 agent 框架的图片生成 skill。专做**游戏买量广告 / 海报 / 买量素材**(爆款复刻 + 多张系列 + 锁风格 anchor mode + 纯文字生买量素材)。

非游戏广告题材(产品 mockup / 企业 logo / 真实摄影 / infographic 等通用任意题材)请用兄弟 skill [`codex-imagegen-fork`](https://github.com/nbssdlkm/codex-imagegen-fork)(B 路径)。两个 skill 完全独立,可分别安装。

## v0.1.3 主要改动

- 🚨 **Hard Invariant**:进入 image API 的 prompt **必须**由 `scripts/rewrite_prompt.py` 产出。`scripts/image_gen.py` 入口双闸校验:SENTINEL marker (`# REWRITTEN-V1`) + CJK 字符占比兜底 (>10% 拒)。删除所有 bypass 旗子 (`--no-rewrite` / `prompt_already_rewritten`)。rewrite 失败整批 fail-fast,不再静默回退原中文
- ✨ **Anchor mode UI**:HTML 表单加 `anchor_candidates` 字段(默认 3,min=2)。n≥2 时自动启用 Phase 1 跑 M 张候选 → 用户挑 1 张作 anchor → Phase 3 锁 picked anchor 风格生 N-1 张系列(画风/UI/字效一致)
- ✨ **0 图 game-ad text2im**:A v0.1.3 起支持纯文字生买量素材(banner / 海报),走 `/v1/images/generations` 端点。保留 game-ad 特化 system prompt,对游戏广告 0 图请求比 B 的 generic taxonomy 更对口
- 🔧 Rewrite 模型升 `gpt-5.5` → `gpt-5.4` + `reasoning_effort="high"`(模型不支持时自动 fallback)

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
├── SKILL.md              ← 主体: Hard Invariant 段 + 6 步 vision workflow + Batch UX + anchor mode
├── README.md             ← 本文件
├── install.ps1           ← Windows 安装脚本
├── install.sh            ← Unix 安装脚本
├── scripts/
│   ├── image_gen.py            ← 出图主程序(自动选 /v1/images/generations 0 图 or /v1/images/edits ≥1 图)
│   ├── rewrite_prompt.py       ← vision + LM rewrite + SENTINEL 包装(invariant 唯一来源)
│   ├── _config.py              ← key 加载 3 路 fallback (env / config.toml)
│   ├── batch_runner.py         ← HTML 表单 config.json → 批量出图(含 anchor mode Phase 1/2/3)
│   ├── render_anchor_pick.py   ← anchor Phase 2 UI 渲染(候选缩略图 + radio button)
│   ├── render_result_grid.py   ← 渲染进度页 + 最终 grid
│   ├── launch_detached.py      ← Windows 长跑批进程 detach
│   ├── save_outputs.py         ← 归档 + contact sheet
│   └── image_gen_batch.py      ← [DEPRECATED v0.1.3] 早期 first-anchor 入口,v0.2 删
├── web/                  ← HTML 表单(用户配批量任务的入口)
│   ├── batch_form.html         ← 含 anchor_candidates 字段(v0.1.3 加)
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

| | A: game-ad-imagegen(本 skill) | B: codex-imagegen-fork |
|---|---|---|
| 定位 | 游戏买量广告特化(爆款复刻 + 多张系列 + 锁风格 anchor + 纯文字 banner) | 通用图片任务(任意题材 / 单图修改 / 文生图) |
| 0 图 text2im | ✅ v0.1.3 起支持(走 `/v1/images/generations`,game-ad system prompt 特化) | ✅ `generate` 子命令(generic taxonomy) |
| Anchor mode 锁风格 | ✅ Phase 1/2/3 form UI(系列广告画风一致) | ❌ 后端代码有但前端无入口(主场景单图无需) |
| Rewrite system prompt | game-ad 特化(CandidatePool of characters + T9 横版骨架 + 4-5 中文 text 位硬约束) | generic taxonomy(11+8 use-case slug:photorealistic-natural / product-mockup / ui-mockup / infographic / ads-marketing / logo-brand 等) |
| 端点 | 自动选: 0 图 `/v1/images/generations` / ≥1 图 `/v1/images/edits` | 自动选: `generate` / `edit` 子命令 |
| 装哪个 | 主要做**游戏广告**(含 0 图买量素材) | 主要做**非游戏题材通用图**(产品照 / logo / 写实 / infographic) |

通常**都装上**,agent 根据用户话术自动路由到对应 skill。

## 依赖

- **Python 3.10+** —— 唯一要你手装的(`install.ps1` 会自动 `pip install --user openai`,设计师不用懂 pip)
- **OpenAI 兼容 API key**(ephone / OpenAI 官方 / 任意代理) —— 首次跑时 agent 会问你贴一次,写到 `~/.config/game-ad-imagegen/config.toml`,以后不再问

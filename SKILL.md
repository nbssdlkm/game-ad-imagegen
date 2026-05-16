---
name: game-ad-imagegen
description: |
  生成**游戏买量广告图 / 海报 / 买量素材**(特化场景)。当用户提供「爆款参考图 + 实机图 + 中文 prompt」请你做 N 张游戏广告系列图时使用。
  Pipeline: rewrite_prompt.py (中文 structured prompt + [Vision Notes] 反 hallucination) → prompt_sanitize.py (SENTINEL 验 + size auto-fit) → image_gen_hybrid.py (/v1/responses + image_generation tool, gpt-5.4 + medium reasoning, 跟 ChatGPT 网页版同源). 任意 user 期望 size 都自动 fit 回 (÷16 round 后 crop / sub-655K 像素 upscale 后 LANCZOS resize). 题材无关 / 角色无关。非游戏广告题材请走 codex-imagegen-fork。
  **跟 `codex-imagegen-fork` (B skill) 的差异化**:A 特化在**工作流**(爆款复刻 + 多张系列 + vision verify + anchor LOCK 主角身份不漂),**画面题材完全无关** — 输入是什么题材就出什么题材(三国/二次元/SLG/水墨/写实皆可);B 是通用图片任务(单图修改 / 0 图文生图 / 任意题材, 无 anchor workflow)。设计师"复刻爆款 + 出 N 张系列"走 A;"通用修图 / PS 一下 / 纯文字生一张"走 B。
  触发词:复刻这张爆款图给我们游戏 / 做几张游戏广告图 / 学这张图做几张类似的 / 游戏海报生成 / 买量素材 / 任意题材的游戏广告图复刻 / 出 N 张系列广告图。
---

# game-ad-imagegen — 游戏买量图生成 Skill

## 🚨 Hard Invariant — 必读

**强烈建议: 进入 image API 的每一个 prompt 都由 `scripts/rewrite_prompt.py` 产出**。没有这条 + agent 自己手写 prompt 容易幻觉一个跟参考图毫无关系的内容 (历史失败模式)。

`scripts/image_gen_hybrid.py` 入口校验:
- **SENTINEL marker 校验** — prompt 第一行是 `# REWRITTEN-CN-V2` (rewrite_prompt 自动加在每段开头) → 走 `sanitize_post_rewrite` 主路径
- **未通过 SENTINEL** → 走 `sanitize_raw_user_prompt` 兜底入口 + meta warning. **不 hard fail** — 让 raw user prompt 也能跑 (e.g. 开发者直接传英文 prompt 调试) 但 user 可看 meta warning 决定要不要重跑 rewriter

**已删的全部 bypass 路径**：
- `--no-rewrite` CLI flag
- `prompt_already_rewritten` config 字段（batch 级 / task 级）
- rewrite 失败时静默 fallback 原中文 → 现在失败整批 fail-fast

**对 agent 的硬约束**：
- **Mode 1**：Step 4 必须**调 `python scripts/rewrite_prompt.py`** 让脚本做 vision + rewrite + 输出**中文 structured prompt with [Vision Notes] 块** + 包 SENTINEL。**不要自己手写 prompt 然后调 `image_gen_hybrid.py`** —— agent 手写的 prompt 没 SENTINEL 会被走 raw 入口降级。Step 4 详述的原则是 `rewrite_prompt.py` 内部 system prompt 在用的，贴在文档里供 agent 排错时理解 rewrite 行为，不是让 agent 自己复制粘贴照写。
- **Mode 2**（batch UX）：`batch_runner.py` 自动调 `rewrite_prompt.py`，agent 不介入。
- **Rewrite-only mode**（Step 4.5）：走 `rewrite_prompt.py` 拿到 SENTINEL-wrapped 中文 structured prompt 后把它输出到对话区让用户复制；不调 `image_gen_hybrid.py`。

---

## 这个 skill 在干什么

**复刻"网页 ChatGPT 出爆款游戏广告图"的工作流**——用户给爆款参考图 + 自家实机图 + 一段中文 prompt，agent 走 hybrid path (rewrite → image_gen_hybrid: `/v1/responses` + image_generation tool)，输出 N 张高质量游戏广告图。Hybrid path 跟 ChatGPT 网页版同源, gpt-5.4 一体化处理 vision + 生图, sanitize 层做 SENTINEL 验证 + size auto-fit 让任意 user 期望 size 都能 honor。

质量目标：
- ✅ 出图质量复刻 ChatGPT 网页版水平（image_gen 一次性把所有中文 text 画在图里）
- ❌ 不走"image_gen 留白 + 后期 Pillow 叠字"路线（字体单薄、不融合，反模式）
- ❌ 不走"无 rewriting / 100% 复制原图"路线（失败模式）

## 触发条件

LLM agent 检测到下列条件命中时启动 skill：

1. 用户输入中包含 **≥1 张参考图**（任意张数 / 任意题材 / 任意角色；图的用途由 Step 1 vision 实际看到的内容决定，**不预设"必须是 1 风格图 + 1 角色图"二分**）
2. 用户 prompt 中含"广告图 / 海报 / 买量素材 / 复刻 / 学这张图 / 做 N 张类似的 / 改文案 / 把图1改成 X" 或同义 / 类似词
3. 输出张数 N ≥ 1（"做 1 张" / "做 5 张" 都支持）

**支持的形态** (形态 = ref 数量 + 用途分工, 不预设题材, 任何题材的 ref 都按此分类处理):
- 1 张图: 单图编辑 / 改文字 / 改局部
- 2 张图: 两张 ref 融合 (一张取构图, 另一张取主体, 或两张都做素材)
- 3 张图: 含多重 role 分工 (如"图1 UI/图2 主体/图3 头像")
- 5+ 张图: 多素材自由组合 (vision 自己识别每张的用途)
图的 role (参考 / 素材 / 叠加目标 / style anchor / edit target 等) 由用户 prompt 描述 + vision 实际识别决定。

**0 图模式**：用户给 0 张参考图 + 纯文字描述（如"做一张 banner，主标题立即下载"），hybrid 不切端点 — `/v1/responses` + `image_generation` tool 同样接受 0 图 (refs 为空数组), text2im 模式仍走 rewrite_prompt + SENTINEL 单闸。Anchor mode 不支持 0 图（vision verify 需要图）。**非游戏广告题材**（产品照 / 企业 logo / 真实摄影 / infographic 等通用任意题材）→ B skill `codex-imagegen-fork`，那边的 system prompt 是 generic taxonomy 不会强加买量风格。

## 必读约束（QUALITY INVARIANTS）

agent 在 Step 4 装配 prompt 时遵守以下 quality 约束 (hybrid path 由 rewriter system prompt 教 LLM 直接 enforce, 没有 _config.py 注入机制). 这些约束**与图片张数 / 形态无关**：

| 约束 | 含义 | 失败案例 |
|---|---|---|
| ✅ 每张参考图的角色由 vision 实际看到的内容判断 | 不预设"图1=风格 / 图2+=角色源"二分。每张图当什么用(style 锚 / 角色源 / UI 模板 / 文案模板 / 改文案目标)由 vision 实际看到的内容 + 用户 prompt 上下文决定 | 凭文件路径名 / 序号 / 训练先验假设图用途 → hallucinated prompt(已知失败模式) |
| ✅ image_gen 一次性画 ref 上 vision 看到的所有字位 | ref 上所有文字位都让 image_gen 直接画在图里 | 留白后期 Pillow 叠字 → 字体单薄、不融合 |
| ❌ 不允许 Pillow 后期叠字 | 不要写 `Text (verbatim): none. Leave blank.` 这种留白指令 | 留白叠字反模式 |
| ✅ ~70% 锚定参考图实际看到的内容，~30% 创作变化 | 风格 / 构图 / 角色形象基于 vision 实际看到的参考图内容 | 100% 复制参考图 = 失败；过度发挥脱离参考图 = 失败 |
| ✅ 角色/资产从用户实际提供的图里选 | 不能凭空发明用户没给的角色 | 凭训练先验幻觉一个用户没给的角色 = 失败 |

## 两种执行 Mode 总览

| Mode | 何时用 | 流程 | agent 负担 |
|---|---|---|---|
| **Mode 1: Interactive** (下方 "6 步操作流程") | 用户在对话区给参考图 + 中文 prompt,**让 agent 帮 vision 看图 + rewrite prompt** | Step 1-6 (vision verify → 拆解 → 选角色 → rewrite → image_gen → 归档) | 重(agent 写 prompt) |
| **Mode 2: Form-driven** (下方 "Batch UX") | 设计师 self-serve,**用户在 HTML 表单里写中文需求 + 指定图路径**(不需要预先 rewrite),`batch_runner` 自带 vision+rewrite step 跟 Mode 1 对齐 | 唤起 form → 用户填 → batch_runner (vision+rewrite per task) → image_gen | 中(rewrite 由 `scripts/rewrite_prompt.py` 自动跑,agent 不介入) |

**路由决策树**(按顺序判断,**第 1 条命中即停**):

1. 用户已粘 config.json / 说"跑 batch_xxx" / 说"打开批量表单" → **Mode 2 触发段**(执行 batch_runner)
2. 用户对话区**已给详细中文 prompt + ≥1 张参考图**(任何形态:1 图改文案 / 2+ 图爆款融合 / 多张系列) → **Mode 1**(走 Step 1-6 vision rewriting)
3. 用户**只说意图、没给详细 prompt**(如"我要生图" / "做几张图" / "出图") → **Mode 2**(唤起 form 让用户 self-serve 填)

**判断标准**:用户话里**有没有可以直接喂给 image model 的中文 prompt 字面**?有 → Mode 1;没有 → Mode 2。
**反偏见**:不要因为用户话里出现"做几张" / "复刻爆款" / "广告图"等 trigger 词就自动跳 Mode 2——这些词命中 Mode 1 时也常用。

---

## Mode 1: 6 步操作流程

> ⚠️ **不要从外部翻历史模板或角色 lookup 取字段**。所有角色与构图字段一律由 Step 1 的 vision call 从用户当次输入图抓取。

---

### Step 1: 输入材料理解（必须真的看图，不假设 / 不凭文件名猜）

🚨 **强制 vision verification（防 hallucinated prompt）**

1. **真的看图**——必须用你（agent）的 multimodal vision 能力 / `view_image` tool / image read tool **实际加载每张参考图的 bytes** 到 conversation context。**不能**凭文件路径名（如 `ref_image_a.png` / `game_image_b.png`）、用户原 prompt 里的题材词来"猜"图内容。
2. **Verify see（强制输出）**——在写 StyleAnchor / CandidatePool 之前，**先用 1-2 句中文 plain description 列出每张图你实际看到的内容**（"图1：[实际看到的视觉描述，含题材/构图/UI/配色/角色性别外貌等可观察元素]"），让 user 能 verify 你是真看到了图，**不是凭训练先验幻觉**。如果你写不出具体的视觉细节（只写空泛的"a dramatic game-poster" / "a fantasy warrior" 这种没有 verify 价值），说明你没真读到图。
3. **没 vision 能力的 model 不能跑此 skill**——如果你是纯文本模型（无 multimodal），**必须主动 stop**，告诉 user："我没有 vision 能力，请换有视觉的 model（Claude / GPT-4o+ / GLM-5V / Hy3 等）跑此 skill，或你自己描述每张图给我"。**不要硬撑**写 hallucinated prompt。
4. **为什么强制**——历史失败模式:agent 跳过 vision call,凭训练先验幻觉一个跟实际参考图毫无关系的 prompt(题材完全错位、角色完全错位)。**根因 = Step 1 之前是 implicit vision 假设**;现在强制 explicit verify 把这条堵死。

读完图 + 写完 verify 描述之后，再提取以下结构化字段。**每张图的角色由 vision 实际看到的内容 + 用户 prompt 上下文判断，不预设位置 ↔ 角色映射**——单张图可以同时承担多个角色（如同时作 style anchor + 改文案目标），多张图可以共同贡献某个角色（如 3 张实机图都是角色源）。

- **每张参考图**：vision 实际看到的内容 → 判断用途（style/构图 anchor / 角色源 / UI 模板 / 改文案目标 / 文本资产 / 等）
- **用户 prompt**：解析需求（明示的图分工 / 角色名 / 标题 / CTA / 张数 / 尺寸）

输出（在 agent 思考过程中保留，字段名固定，内容由 vision 实测填）：
```
PerImageNotes: [
  { idx: 1, path: "<filename>", role: <vision+prompt 判断的用途，可多重，如 "style+构图 anchor, 含中文标题文本">,
    key_visuals: <实际看到的关键视觉元素：题材 / 构图 / 主体 / 配色 / 标志特征>,
    chinese_text_in_image: <列出图里能读到的中文，或写 "无"> },
  { idx: 2, path: "...", role: ..., key_visuals: ..., chinese_text_in_image: ... },
  ...
]
StyleSummary:  { 构图 anchor 来源: <哪张图 idx 或多张融合>,
                 主导配色: <vision 实际看到>,
                 字效: <vision 实际看到>,
                 UI 元素: <vision 实际看到>,
                 整体氛围: <vision 实际看到的风格类型,描述形容词组合,不预设题材库> }
CandidatePool: [{ id: "subj_1", source_idx: <来自哪张图>, visual_name: <服装色 / 武器 / 五官 / 性别 / 标志特征> },
                { id: "subj_2", source_idx: ..., visual_name: <...> }, ...]
UserIntent:    { 输出张数: N,
                 主CTA: <用户原话>,
                 题材: <如用户原话明确指定，照贴；没指定则写"由 vision 推断"，照 PerImageNotes 实际看到的题材填>,
                 自由创作度: <默认 30%>,
                 画幅: <默认 2048x1152 / 用户指定按指定> }
```

⚠️ **题材字段只在用户原话明确指定**（如"我们的 X 游戏"）时填，否则永远写"由 vision 推断"。不要根据任何参考图的角色形象**猜测**题材然后套预设模板。

---

### Step 2: 需求拆解（agent 自己干）

把用户 prompt 拆成可执行约束清单：
- 输出数量 N（用户 prompt 里说几张就几张；没说默认 1）
- 画幅 + 比例（**默认 `2048x1152`** ≈ 16:9, ÷16 合规；用户要任意尺寸如 1920x1080 / 9:16 竖版 / 650x250 banner — **sanitize 会自动 fit**：1920x1080 → ephone 1920x1088 → PIL center crop 回 1080 / 9:16 keyword → 1152x2048 / 650x250 (<655K 像素) → ephone 2624x1024 → LANCZOS resize 回 650x250。user 拿到他要的 size, ephone ÷16/最小 655K 像素约束由 sanitize 自动处理）
- 风格描述（来自 Step 1 StyleSummary，**全部由 vision 抓取**）
- 版式（来自 Step 1 StyleSummary 视觉描述——参考图实际看到啥版式就跟啥版式：单角色海报就单角色 / 漫画分镜就分镜 / 面板就面板 / 风格融合就融合，**不套固定模板**）
- 主文案（用户 prompt 里指定的 CTA 文字 + Step 1 PerImageNotes.chinese_text_in_image 抓到的原图中文）
- 副文案策略（贴合每个角色身份，**身份从 Step 1 CandidatePool 实际识别到的视觉特征取**）
- 自由创作度（默认 30%）

---

### Step 3: 角色/资产选择（agent 决定）

**情况 A**：用户 prompt 明确指定 N 个具体名字 → 用指定的（agent 从 Step 1 CandidatePool 找视觉匹配，或按 CandidatePool 视觉特征自由发挥）

**情况 B**：用户 prompt 含糊（如"从图2/3 选适当角色做 5 张类似的"）→ agent 从 CandidatePool 自由选 N 个：
- **唯一选规则**：视觉差异化（N 个角色彼此看着不同）+ CandidatePool 实际识别到的元素 + 跟参考图整体题材气质合理
- ⚠️ **绝对不要套预设题材偏好** — 不管参考图看着像什么题材，永远从 CandidatePool 实际识别到的角色里选，不要写"如果是 X 题材默认选 Y 角色"这种规则

**情况 C**：CandidatePool 为空或不足 N 个 → 暂停问用户"实机图里只识别到 X 个候选，你要这 X 个还是补图？"

---

### Step 4: Prompt rewriting（调 `rewrite_prompt.py`，不自己手写）

🚨 **Hard Invariant 要求**：agent 不自己手写英文 prompt 然后调 `image_gen_hybrid.py`。改为：把用户中文需求 + 参考图路径 + 目标张数 N 传给 `scripts/rewrite_prompt.py`，让脚本做 vision + rewrite + SENTINEL 包装。

```bash
python scripts/rewrite_prompt.py \
  --user-prompt-file user_zh.txt \
  --refs ref1.png,ref2.png \
  --n 5 \
  --out rewritten.txt
```

输出文件 `rewritten.txt` 每段以 `# REWRITTEN-CN-V2` 开头（多段用 `---PROMPT-SEP---` 分隔），Step 5 把每段拆出来传给 `image_gen_hybrid.py --prompt-file`。

**下方原则是 `rewrite_prompt.py` 内部 system prompt 教 LLM 的规则**，贴在这里供 agent 排错时理解 rewrite 行为 / 解释结果给用户 / 微调用户原始中文需求。**agent 不要自己手抄这些规则写英文 prompt 然后塞给 image_gen** —— 入口 SENTINEL 闸会拦下，且这样做绕开了 `rewrite_prompt.py` 的 vision verify 环节。

**为什么 prompt 字面精度直接决定结果**：`image_gen_hybrid.py` 用 `/v1/responses` + `image_generation` tool 端点 — **prompt 字面 + refs 一起送给 image model, model 内部 vision + 生图一体化处理**。verbatim 中文准确度靠 rewrite 字面精度，scene complexity 直接决定 image model text 渲染 budget。

#### Specificity policy (rewriter system prompt 顶部段, 跟 codex 上游 prompting.md 同源)

rewriter 不是无脑展开 — **根据 user prompt 具体程度调整 augmentation 量**:

- **user prompt 已经很具体** (有明确主体/场景/约束/字位/构图) → 只做 normalize / restructure 成 labeled lines, **不加创意需求**, 10 段模板里没信息的段**整段省略不凑数**
- **user prompt 偏 generic** ("做张图" / "出个海报" / 只给图没给文字需求) → 可以 tasteful augment, 补 composition / lighting / scene 等帮 image model 落地的细节

**禁加** (Disallowed, 强 enforce):
- ref 没出现 + user 没提的额外 character / 物体 / 道具
- 没 implied 的品牌名 / slogan / palette / 故事情节
- 没排版依据的 side-specific placement (e.g. 凭空说"主角放左下角")

**允许加** (Allowed): composition / framing 提示 / polish level / intended-use / 实用 layout / 支持已述请求的场景具体化。

**不算 augmentation (是 normalization / structural enforcement, detail prompt 也注入)**:
- Rule 10 默认约束 (`no logos, no trademarks, no watermark` / use-case-specific defaults)
- anchor phase3 LOCK 句 (`严格匹配 Image N 的渲染风格...`)
- 反 hallucination 黑名单 ([Vision Notes] 块 / 不复制 ref verbatim 文字)
- edit invariants (`change only X; keep Y unchanged`)
- Rule 11 letter-by-letter 拆字注释 (image model spelling hint)

> agent 排错时如果发现 rewriter 输出"加了 user 没要求的细节"或"对 detail prompt 还在过度展开", 看是否违反 "禁加" 列表 (而不是上面 5 条 normalization)。后者跟 specificity 无关, 永远注入。

#### rewriter 内部规则 (跟 `rewrite_prompt.py REWRITE_SYSTEM` 1:1)

下方 11 条规则跟 rewriter system prompt `==== 关键规则 ====` 段 1:1 对齐 (顺序 + 编号同步)。**agent 不需要自己手抄这些规则手写 prompt** — rewriter LLM 已经按这些规则跑。贴在这里供 agent 排错 / 解释 rewriter 输出。完整规则见 `rewrite_prompt.py:REWRITE_SYSTEM`。

1. **构图复刻 ref 实际形态**: 主体数量 + 版式按 ref vision 看到 — 单主体就单主体, 群像就群像, 分镜/拼图就照 ref. **不预设主体数量上限**, 也不预设 single/multi panel 偏好。
2. **字位数量模仿 ref 字位密度**: **默认上限 5**, 下限 = ref 实际字位数 (ref 0 字位则不渲染任何文字; ref 字位密集则截到 ≤5 个最关键). 字位语义功能跟 ref 一致 (ref 上原是 X 类信息 → 当前主体 X 类信息), 不预设具体字位类型。**edit use case 行为豁免 (跟 Rule 10 同步)**: 若 user 任务是 "保留 ref 全部字位只改其中一两个" 的 edit (信号 = "其他不变" / "保留原 UI" / "只改 X"), **默认 5 上限失效, 改用 "字位数 = ref 实际字位数 (无上限)"**, 防跟 Rule 10 "no extra text" 互锁让 model 删 ref 既有字位。
3. **删 user prompt 的批量控制语言**: "分别 / N 张 / 分两排" 这些是告诉 rewriter 产几段, 不是视觉指令, 不 echo 进任何 prompt。
4. **Verbatim**: 每字位写 `<位置>: "<exact 字面>"`. 永远不要含糊地说"主题文字"否则被 image model hallucinate。ref 上没字位则不写进 [文字] 段 (不写"留空")。
5. **Series variety when N>1 (非 anchor phase3)**: 每段不同 primary subject (来自 CandidatePool), 不要 N 段全是同一主体的细微变体。
6. **Style words from vision**: 用 vision 看到的实际笔触/材质/光影/线条/调色板/质感, **永远不写 franchise/IP/题材名先验** (任何具体作品名/题材名都不允许)。
7. **Image role 显式 label**: 每张 ref 在 [参考图角色] 段显式 label 它的 role (composition reference / character source / style reference / edit target 等, 自由文本), 不 collapse 二分。Ref 顺序按 user "图1/图2/..."语义。
8. **Edit mode 显式 invariants**: 若 user "改 X 其余不变", 约束必须含 "change only X; keep everything else (layout/typography/colors/composition/background) unchanged"。
9. **复刻 ref 视觉特征 + 不复制 ref 文字 verbatim**: 避免段**只禁文字 verbatim**, 视觉特征 (按 [Vision Notes] 列出) 必须复刻形态。绝不能扩成"不要复制 ref 上的任何视觉元素"过广避免, 会让出图比 ref 简陋。
10. **默认约束** (跟 codex 上游 sample-prompts 13 个 recipe 默认带的一致): 每段 [约束] 段必须含 `no logos, no trademarks, no watermark` (通用); `no extra text outside [文字] section` — **行为定义豁免** (替代之前 slug 白名单, 防漏 slug): 若 user 任务是 "改 X 不动其余字位" 的 edit 场景 (任何 edit slug 都可能命中, 信号 = "其他不变" / "保留原 UI" / "只改 X"), 本项失效, 改用 `change only <X>; keep all other text verbatim`; logo-brand (含 wordmark / 字体本身为主体) 同样豁免。**跟 Rule 2 字位上限豁免同步**。photorealistic-natural 加 `no studio polish, no staged look`; ui-mockup / infographic-diagram 加 `clear hierarchy, readable typography`。**anchor_phase=phase3 时本 Rule 全部 use-case-specific 默认约束失效, 以 picked anchor 实际渲染风格为准** (`no logos/watermark` 通用项 + edit "改 X 不动其余" 豁免仍生效)。
11. **生僻字 / 中英混排 / 长数字串 letter-by-letter 处理** (按 game-ad 命中频率排序): 长数字 ≥5 位 (战力数值 "99999 >> 188888" / 抽奖码) → `"99999" (数字: 9-9-9-9-9)`; 中英混排短词 ("VIP特权" / "iOS版") → `"VIP特权" (拼字: V-I-P-特-权)`; 生僻汉字 ("燚阳殿") → `(拆字: 燚-阳-殿)`; 英文 diacritic ("Müller") → `(拼字: M-ü-l-l-e-r)`。常见汉字 ("登录"/"领取") **不拆**, 拆所有字反向稀释 prompt budget。

补充 STEP A 强制规则 (不在 11 条 `==== 关键规则 ====` 里, 但 system prompt 同样硬约束):
- **[Vision Notes] 强制开头**: 每段以 `[Vision Notes]` 块开头, plain 描述每张 ref 实际看到 (反 hallucination)。
- **角色身份判定 OCR-only**: 辨不出主体身份就写 "<体型/性别>, <实际服装颜色与造型>" 这种视觉特征 placeholder, 绝不凭训练先验猜具体历史人物 / franchise / 动漫 / 游戏角色名。**唯二例外**: (a) 图上有清晰可读的角色名字标签可 OCR; (b) user 在原 prompt 显式指定了角色名。
- **不能基于视觉特征推具体身份**: 服装颜色/发型/武器形状/胡须长短是题材刻板印象, 不能用来判定"这是 X 角色", 会把同 trope 的 N 张图都识别成同一角色 (case_22 实证根因)。
- **CandidatePool 池子只能引用真实可见角色**: 池子小就池子小, 绝不允许扩充 hallucinated 角色; 不确定时用 `<视觉特征>+<placeholder>` 描述, 绝不把名字写进去。
- **装饰元素细颗粒必须单独列**: ref 上所有**非文字视觉特征** (形状/笔触/纹理/光影/材质/构图元素) 按 ref 所见据实列, 跟文字位分开, 让下游 image_gen 复刻形态 (内容可换形态保留)。
- **题材完全无关**: 对任何题材 (任何 IP/franchise/genre 不预设) 都按 ref vision 输出。

#### Rewriter 输出格式 (中文 labeled lines, 单段示例)

⚠️ **labeled lines 是 scaffolding 不是 closed schema** (跟 codex 上游 "Keep it short" 原则一致): 一段 prompt 内 **没信息的 labeled line 整段省略**, 不要凑废话。e.g. ref 是无文字 splash → `[文字]` line 不出现; user 没指定调色板 → `[画风]` line 简短点出"沿用 ref 视觉语言"即可。agent 看 rewriter 输出的一段 prompt **少几个 labeled line 不是 bug** — 是 LLM 正确判断了 specificity。

> **澄清** (round-7 G1 fix): 这里"段省略"指**段内**的 labeled lines (10 段 scaffolding 之一可省). **N 段独立 prompt 仍必须 N 段齐全** (n=5 时 rewriter 必须返 5 段 self-contained prompt, 缺 1 段 rewriter 会 raise RuntimeError 而不是 silent pad). 两个层级不能混。

```
# REWRITTEN-CN-V2
[Vision Notes]
- Image 1: <plain 描述>
- Image 2: <plain 描述>
- ...
[/Vision Notes]

用途: <从 19 个 codex use-case slug 选最接近的; 真不 fit 才自由文本>
  生成类: photorealistic-natural / product-mockup / ui-mockup / infographic-diagram /
         scientific-educational / ads-marketing / productivity-visual / logo-brand /
         illustration-story / stylized-concept / historical-scene
  编辑类: text-localization / identity-preserve / precise-object-edit / lighting-weather /
         background-extraction / style-transfer / compositing / sketch-to-render
主要请求: <一句话讲这张图要什么>
参考图角色:
  - Image 1 (<role>): <vision 看到的 + 在本段 prompt 怎么用>
  - Image 2 (<role>): <同上>
场景/背景: <氛围 + 关键 props + 来源>     # 没信息可省略整段
主角主体: <具体描述 + pose + 跟 ref 的关系>
画风/介质: <从 vision 提取的笔触/材质/光影/调色板, 不写 franchise/IP/题材名>
构图/比例/尺寸: <横/竖版 + 宽高比 + 尺寸 + 留白>     # ref 已定构图时简短点出即可
文字 (verbatim, 字位数模仿 ref 密度, 上限 5):     # ref 无文字则本段省略
  - <位置 1>: "<引号内 exact verbatim>"
  - ...
约束: <user 否定指令转正向 + 通用 quality + 默认 "no logos, no trademarks, no watermark">
避免: <hallucination 黑名单: 不要拼图 / 不要 panel / 不要从 ref 复制 verbatim 文字>
```

多段 (N>1) 用单独一行 `---PROMPT-SEP---` 分隔。每段独立 self-contained。

#### 绝对禁止 (agent 跟 rewriter 都不能做)

- 不要让 agent 自己手写 prompt 然后塞给 image_gen_hybrid (绕开 rewriter vision verify)
- 不要写"留空叠字"反模式 (image_gen 一次性画字)
- 不要凭训练先验幻觉用户没给的角色 (rewriter [Vision Notes] 已强制 anti-hallucination)
- 不要做任何题材 hardcoded lookup

---

### Step 4.5（可选）: 只转写不出图模式

如果用户明确说"只转写不出图" / "只要 prompt 不要图" / "给我 prompt 我自己拿" / "先看看 rewrite 的结果" → 走完 Step 1-4(vision + 拆解 + 角色 + rewrite),**跳过 Step 5**,把 Step 4 写好的 rewritten prompt **直接在对话区用 markdown fenced 代码块输出**,让 user 用 WorkBuddy / Claude Code 自带的"复制代码"按钮一键拿走。

**不消耗 image_gen credit**,适合:
- 用户想先看 rewritten prompt 决定要不要花 credit 跑
- 用户想拿 prompt 去别的工具(其他 image gen API / 备忘 / 二次微调)
- 调试:用户改 prompt → 粘回 batch_form 跑

输出格式(直接打字在对话区,不开 HTML / 不开 browser / 不调脚本):

````markdown
转写好了。以下是你的 rewritten prompt(对话框右上角「复制」按钮一键复制):

```text
<这里贴完整 rewritten prompt — 不要任何 ```text 之外的额外缩进 / 标记>
```

如果要真出图,把这段粘到 batch_form 的「中文需求 prompt」框 + 配上参考图,再回来跟我说"跑 batch_xxx"。
````

⚠️ **关键 invariants**：
- 用 \`\`\`text(不是 \`\`\`json / \`\`\`bash,避免 WorkBuddy 误识别成可执行代码)
- 代码块内**只放 prompt 字面**,不加 commentary / 不加 "Step 4 输出:" / 不加 prefix
- 一段 prompt 一个代码块。如果 Step 4 出了 N 段(多张图),N 个独立代码块,每段开头一行 `### prompt for image #<i>` 标号
- **不要省略**,不要 truncate,不要写"..."。完整复制给 user

⚠️ **触发词列表**：用户说以下任一,即走本模式不进 Step 5：
- "只转写"、"只 rewrite"、"先 rewrite 看看"
- "不要出图,给我 prompt"、"只要 prompt"
- "rewrite only"、"prompt only"、"先别跑"
- "把 prompt 给我"、"prompt 复制给我"、"prompt 写好发我"

---

### Step 5: 图像生成（调 skill 提供的工具）

**第 1 次调用**：
```bash
python scripts/image_gen_hybrid.py \
  --prompt-file <step4 第 1 段 prompt 写到的临时文件> \
  --refs <用户的参考图,实机图1,实机图2,...> \
  --out <临时输出目录>/01.png \
  --meta-out <临时输出目录>/01_meta.json
```

第 1 张作风格基准。完成后检查:
- HTTP 200 + meta_json 落盘 + PNG 文件大小 > 0; meta.json 里有 `image_call_status="completed"` + `revised_prompt` (image_gen tool 内部 revise 后的 prompt, 供 audit)
- PNG 含 readable Chinese text(不是空白)

**多图(N ≥ 2)调用** — 两条路径,**按需求选**(不是哪条都行,语义不同):

| 路径 | 多张图之间的关系 | 何时选 | 怎么跑 |
|---|---|---|---|
| **简单循环**(默认) | **N 张是同一系列**——同一爆款风格、同一角色池、统一构图调子。每次调用都传**所有用户参考图**,让 image model 根据完整 ref 上下文 + N 段 prompt 自己保系列一致性。 | 用户要"5 张系列广告"、"爆款复刻 N 张"——希望视觉一致 | 循环 N 次 `python scripts/image_gen_hybrid.py --prompt-file <step4_i.txt> --refs <所有用户参考图都传> --out <i>.png` |
| **走 Batch UX** | **N 个独立 task**——每个 task 有自己的 prompt + refs + n,task 之间互不影响,同一 task 内 n>1 是该 prompt 的不同 sampling(多样性) | 用户要"跑 3 个不同主题各 2 张"、需要 self-serve 填表、或者要看 result_grid 实时进度 | 走下方 "Mode 2: Batch UX" 段(form 或粘 config.json) |

⚠️ **关键差异**:简单循环 = 同系列保 N 张一致;Batch UX = 独立 task 矩阵。**不要混用心智模型**——同一 task n>1 不是"系列"(没有共享 anchor 跨调用),想要系列一致请用简单循环。

---

### Step 6: 输出归档（调 save_outputs.py）

```bash
python scripts/save_outputs.py \
  --batch-meta <临时输出目录>/_batch_meta.json \
  --out-dir <最终交付目录>/<case_id>/
```

⚠️ 归档目录命名**不要**加题材 / 日期 / 角色名后缀（如 `<日期>_<题材>_<角色名>等/`），保持纯 `<case_id>/` 简洁。

输出：
- `01.png`, `02.png`, ..., `0N.png`：N 张交付图
- `meta.json`：包括 saved_files / revised_prompts / config（作 audit）
- `contact_sheet.png`：N 张拼图预览

最终把 `<最终交付目录>` 路径告诉用户，附上 contact_sheet.png 的预览。

---

## Mode 2: Batch UX (HTML 表单触发模式)

本 skill **自包含**一套批量 HTML 表单 + runner,供设计师跑多任务时用。**本表单专属本 skill,生成的 config.json 写死 `skill: "a"`,runner 也只跑本 skill。** 如果用户想用 B skill 走另一条独立路径,B 有自己的 `web/batch_form.html` + `scripts/batch_runner.py`,本 skill 不知道也不关心 B 的实现。

~~这条路径绕开 Step 1-4 的 vision/rewriting~~ → **2026-05-14 已对齐(commit `5cdbf3c` + `bbab6be`)**: `batch_runner` 自带 vision + rewrite step(调 `scripts/rewrite_prompt.py`)跟 Mode 1 对齐。用户在表单里写中文需求即可,**不需要预先 rewrite**;agent 仍只做执行器(开 form / 喂 config / 报告进度),LM rewrite 由 runner subprocess 自动跑。

**关键文件位置**(都在本 skill 内,跟 SKILL.md 同根):

| 文件 | 用途 | 装机后绝对路径 |
|---|---|---|
| `web/batch_form.html` | 表单 UI(skill='a' 写死) | `~/.claude/skills/game-ad-imagegen/web/batch_form.html` |
| `web/batch_form.js` | 表单逻辑 | `~/.claude/skills/game-ad-imagegen/web/batch_form.js` |
| `web/style.css` | 表单 + grid 样式 | `~/.claude/skills/game-ad-imagegen/web/style.css` |
| `web/grid_template.html` | 结果 grid 模板 | `~/.claude/skills/game-ad-imagegen/web/grid_template.html` |
| `scripts/batch_runner.py` | runner 主程序(只跑 skill='a') | `~/.claude/skills/game-ad-imagegen/scripts/batch_runner.py` |
| `scripts/launch_detached.py` | **进程脱离 launcher**(必走) | `~/.claude/skills/game-ad-imagegen/scripts/launch_detached.py` |
| `scripts/render_result_grid.py` | grid HTML 生成 | `~/.claude/skills/game-ad-imagegen/scripts/render_result_grid.py` |

runner 内用 `Path(__file__).resolve().parent` anchor 自动定位本 skill 的 `image_gen_hybrid.py`(sibling),**不跨 skill 查 B**。

### 入口 — agent 自动开 form (设计师 self-serve 路径)

#### 何时唤起 form (Mode 2)

🚨 **路由优先级** (跟顶部"两种执行 Mode 总览"对齐):

**Mode 1 优先**: 用户**已在对话区给详细中文 prompt + ≥1 张参考图**(图作为 attachment 或路径) → 走 Mode 1 vision rewriting,**不要**唤起 form。即使话术命中 Mode 2 trigger 表,只要 prompt 已详细给出,Mode 1 优先。

**Mode 2 触发**: 用户只说**意图、没给详细 prompt** / **明确说要 form / batch** / **要 self-serve 填表**时唤起 form:

| 用户话术 | agent 行为 |
|---|---|
| "我要批量出图" / "打开批量表单" / "开始跑批" / "跑 batch" | **唤起 form** (明确 form/batch) |
| "我要生图" / "出图" / "做几张图"(**没给 prompt 也没给图**) | **唤起 form** (无 prompt → self-serve) |
| "用 A skill 出图" / "用 game-ad-imagegen 跑"(没给细节) | **唤起 form** |
| "做几张广告图,主题 X,风格 Y"(**给了详细 prompt + 至少一张图**) | **走 Mode 1**,不唤起 form |
| "复刻这张爆款图(附图),给我们 X 游戏做 5 张系列"(**给了详细 prompt + 图**) | **走 Mode 1**,不唤起 form |

> form 顶部 n 字段默认 = 1(单图模式),user 想批量改 n 即可。**form 既是单图也是批量入口,没有"批量专用 form"。**
> **决策树**:用户说话里**有没有可以直接喂给 image model 的中文 prompt 字面**?有 → Mode 1;没有(只有意图) → Mode 2。

**agent 不要让 user 自己开 terminal / 自己点 URL**。直接按下面 2 步走:

> 🚨 **绝对不要二次确认 — 看到触发词直接执行,不要问"用 A skill 表单可以吗"/"默认浏览器打开可以吗"/"我先帮你做 X,确认一下"。**
> SKILL.md 是**指令**不是**建议**,user 已经说了"我要做图"就是授权。多问一句 = UX 噪音,设计师会嫌烦。
> ❌ 错误示例:`我给你开表单跑批。你确认以下两点:1. 用 A skill 表单 2. 默认浏览器打开。回我一句:开`
> ✅ 正确示例:(静默 Bash 跑 http.server + webbrowser.open)→ 对话区报 `✅ 已打开 A skill 出图表单 → http://localhost:8765/web/batch_form.html`

#### 步骤 1:起 http server + 调系统默认浏览器(默认路径)

```bash
# 1.0 后台起 http server(跨 OS 通用,Python 自带)。用 Bash run_in_background=true。
#     如果 8765 已被占用(server 已经在跑) → OSError errno 10048 / Address in use → 直接跳 1.1 (reuse)
python -m http.server 8765 --directory ~/.claude/skills/game-ad-imagegen

# 1.1 用 Python webbrowser 调系统默认浏览器(Chrome / Edge / Firefox / Safari),跨 Windows/macOS/Linux 通用
python -c "import webbrowser; webbrowser.open('http://localhost:8765/web/batch_form.html')"
```

> ⚠️ **不要尝试 WorkBuddy 自带 preview**:已知 bug(复制粘贴失效 / "生成跑批指令" 按钮 click 不响应),用了反而卡。
> ⚠️ **不要先尝试 host preview tool**:WorkBuddy preview 不可信,Claude Code 的 preview 没 bug 但需要特定环境配置 — **直接走系统浏览器最稳**。

**步骤 1.1 失败时的 fallback**:host 无 GUI 环境(SSH / Docker / 远端 server)/ Python webbrowser 不可用 / 没系统默认浏览器 → **直接在对话区发 markdown 可点链接** `[http://localhost:8765/web/batch_form.html](http://localhost:8765/web/batch_form.html)` 让 user 自己点。

#### 步骤 2:对话区通知 user + 等触发

agent 发一条简短消息(根据用哪个 tier 微调措辞):

> ✅ 已打开 A skill 出图表单(在 preview 面板 / 浏览器中)→ http://localhost:8765/web/batch_form.html
> 配好后,回这里说 **「跑 batch_<时间戳>」** 或把 `config.json` 整段粘到对话区。
> 单图就把 n 填 1(默认),批量就改大。

**然后等 user**。不要主动 ping / 不要重复发消息。

#### 触发模式

| 用户在对话区说 / 做 | agent 行为 |
|---|---|
| 说 `跑 batch_<id>` / `run batch_<id>` / `跑 <绝对路径>/config.json` | **触发 A**(下方) |
| 拖入 config.json 文件,或粘贴一段含 `"batch_id":` 的 JSON 代码块 | **触发 B**(下方) |

### 触发 A — 用户给 batch_id

1. config 路径:**首选**用户给的绝对路径(粘贴 JSON 时 agent 用 Write 工具落盘到 `~/Downloads/<batch_id>.json`,或者用户拖入的文件路径直接用);找不到 → 问用户「请把 JSON 粘到对话区,或把 config.json 拖进来」。
2. **检查 `config.skill == "a"`**;如果不是,runner 自己会 reject 并提示用户走 B 的 runner,**本段不处理也不主动跨调度**。
3. **关键 — 跑批前立即唤起 result_grid 进度页给 user**:
   - runner 一启动就会在 `<out_dir>/result_grid.html` 写一个 "running" 状态的进度页(空 grid,每 5s auto-refresh)
   - agent **不要等批跑完才开**,而是先用 launch_detached 起 runner(<1s 返回),然后**马上**唤起 `<out_dir>/result_grid.html` 给 user 看(用户每 5s 自动刷新,看到一张张图依次出现 + 进度条 + 状态 badge,**不用追问 agent "跑到哪了"**)
   - **默认**: `python -c "import webbrowser; webbrowser.open(r'<out_dir>/result_grid.html')"` 调系统默认浏览器
   - fallback: 对话区发可点路径让 user 自己点
   - ⚠️ 不要尝试 WorkBuddy 自带 preview(有 bug)
4. **跑 runner — 必须走 launch_detached.py(不要直接调 batch_runner.py)**:

   ```bash
   python ~/.claude/skills/game-ad-imagegen/scripts/launch_detached.py \
          ~/.claude/skills/game-ad-imagegen/scripts/batch_runner.py \
          <config_path>
   ```

   - launcher **foreground 跑、<1s 返回**,stdout 输出 `detached_pid=X` + `method=DETACHED+BREAKAWAY` + `log=Y`。agent 把 PID 记下来,可选 print 给 user。
   - **为什么必须 detach**:WorkBuddy(以及任何 Job Object-based host)在 ~2 min 后会杀 agent 的子进程,直接调 batch_runner 会出 1-2 张就停。detach 让 batch_runner 完全脱离宿主进程树,可以跑满整个 batch(2026-05-14 v1/v2 实测验证:无 detach 110s 被杀 / 有 detach 跑满 25 min)。
   - **不要用 `run_in_background=true`** — launcher 已经 detach,Bash 用 foreground 调即可(launcher 自己秒退)。
   - **agent 不需要 BashOutput 监控** — batch_runner 的 stdout/stderr 全进 `<out_dir>/<config_basename>_detached_launcher.log`,user 看的是 result_grid 自动刷新。
5. **跑完判定** — 由 user 自己看 result_grid 的 status badge(🟦 running → 🟩 done / 🟥 error)。agent **不需要主动 poll**;如果 user 后续问"跑完了吗"再做一次 `tail _batch_meta.json` 拿 status 字段答。**预计时间** = Σ(每任务 n) × 单图耗时(low=15-30s / medium=45-90s / high=90-180s)。

### 触发 B — 用户粘贴 JSON 或拖文件

1. 解析 JSON 拿到 `batch_id`。如果是粘贴的 JSON:用 Write 工具落到 `~/Downloads/<batch_id>.json`(浏览器下载默认路径,user 友好)。如果是拖入的文件:直接用文件路径。
2. 之后等同触发 A 步骤 2-6。

### config.json schema

见本 skill 内 `scripts/batch_runner.py` 顶部 docstring。简要:
```json
{
  "batch_id": "...",
  "skill": "a",               // 必须 "a"(本 skill runner 只接 'a')
  "out_dir": "...",
  "size": "2048x1152",        // batch 默认尺寸 (1024x1024 / 2048x1152 / 1024x1536 / auto)
  "quality": "medium",        // batch 默认质量 (low / medium / high)
  "tasks": [
    {
      "task_id": "t01",
      "reference_images": [".../1.png", ".../2.png", ".../3.png"],  // ≥1 张;顺序 = 用户 prompt 里"图1/图2/图3"
      "prompt": "...",        // 中文 prompt,终稿;角色分工(谁是参考/素材/叠加)全在 prompt 里
      "n": 1,                 // 同 prompt 跑几张
      "size": "1024x1536",    // 可选,覆盖 batch 默认
      "quality": "high"       // 可选,覆盖 batch 默认
    }
  ]
}
```

### CLI 调用形态(batch_runner 自动构造,不用 agent 手写)

`python image_gen_hybrid.py --prompt-file X --refs a.png,b.png --out C --meta-out M --size S --quality Q --no-invariants`

`--no-invariants` = **legacy CLI flag** (hybrid 实现里 noop + warn)。保留只为兼容 batch_runner.py 现有调用,以后清理 batch_runner 时一并删。quality 规则全在 rewriter system prompt 里教 LLM enforce, 无 image_gen 调用前文本注入机制。

### 支持的形态

batch UX **不预设图片角色**——`reference_images` 就是一个有序列表，每张图的用途由用户的 prompt 自己描述。下表只列典型场景做参考，**不是 schema 约束**：

| 实际形态 | 用户 prompt 里通常怎么写 |
|---|---|
| 1 张图 | "把文案 X 改成 Y" — 单图修改 |
| 2 张图（都是爆款） | "根据这两张趣味图，生成 X 主题的趣味图" — 风格融合 |
| 3 张图（爆款+实机） | "将图1的UI和文字加到图2上，图3角色作头像" — 显式指定每张用途 |
| 5+ 张图（全实机） | "这是游戏截图，选元素做宣传图" — 让模型自由组合 |

### 0 图模式

hybrid path `/v1/responses` + `image_generation` tool 同样接受 0 图 (refs 为空, text2im 模式), 仍走 rewrite_prompt + SENTINEL 闸。"生成第一人称视角的古代战场" 这种纯描述请求 A 能跑。**Anchor mode 不支持 0 图**（vision verify 需要图，0 图请求自动落 standard mode）

### Batch UX 模式下 agent 的边界（2026-05-14 重写，跟 scripts/ 对齐）

**✅ batch_runner 自动做的事**（agent 不要重复）：

- ✅ Per-task vision + rewrite：`batch_runner` 调 `scripts/rewrite_prompt.py` 对每个 task 跑 vision + LM rewrite，把中文需求转成 N 段（N=task.n）**中文 structured prompt with [Vision Notes] 块**。**agent 不要自己再 rewrite 一遍 / 不要在 config 里塞预 rewrite 好的 prompt**。**Invariant**：rewrite 强制走（已删 `--no-rewrite` / `prompt_already_rewritten` 等 bypass 旗子），失败时 batch 整批 fail-fast；`image_gen_hybrid.py` 入口走 SENTINEL 闸 (`# REWRITTEN-CN-V2`), 未通过 SENTINEL 走 raw 兜底入口 + warning (hybrid 路径没有 CJK 占比闸, rewriter 输出本就是中文不能拒)。
- ✅ N 段不同主体：`rewrite_prompt` 一次产 N 段独立 prompt，每段 feature 不同主体角色（系列多样性）。**agent 不要假设"5 张同 prompt 跑 5 次 sampling"**。
- ✅ No legacy --no-invariants double-burn：`batch_runner` 调 `image_gen_hybrid.py` 仍传 `--no-invariants` (legacy 兼容 flag, hybrid noop), quality 规则由 rewriter system prompt 教 LLM 直接 enforce。

**❌ agent 仍不要做的事**：

- ❌ **不要** Pillow 后期叠字：image_gen 一次性把所有中文 text 画进图里。
- ❌ **不要** 主动给 `config.tasks[].prompt` 加修饰词或英文化：用户填的中文需求传给 rewrite_prompt，LM 自己 rewrite。
- ❌ **不要** 改 `reference_images` 顺序：顺序对应用户 prompt 里"图1/图2/图3"，照用户排的传给 rewrite_prompt。
- ❌ **不要** 试 ToolSearch / 任何 runtime-provided ImageGen wrapper：batch UX 永远走 `scripts/image_gen_hybrid.py`，跟 `scripts/rewrite_prompt.py` 配对。
- ❌ **不要** 主动开多个 batch 并发跑同 task：token 翻倍且无质量提升。
- ❌ **不要** 主动重发触发短语 / 重启 batch_runner：1 个 config 对应 1 个 batch_runner 进程,重启会让多进程同写文件 race。如果 batch 看着卡了,先 tail log / check `_batch_meta.json` 而非盲重启。

---

### Anchor workflow (task 设 `anchor_candidates ≥ 2` 时自动启用)

**目的**: 镜像网页版 ChatGPT first-image-anchor 机制,解决标准模式抽卡 + N 张风格漂。先出 M 候选 → user 挑 1 张作 anchor → anchor 喂回 ref 跑剩余 N-1 张系列风格统一。

**触发**: config.json 内 task 设 `"anchor_candidates": M` (2 ≤ M ≤ 10) **且** `n ≥ 2`。

**三阶段流程** (batch_runner 自动跑,跟标准模式自动分支):

```
Phase 1: 同段 prompt × M sampling → M 张候选,写 anchor_pick.html + 改 status="awaiting_picks"
Phase 2: poll `{batch_id}_anchor_picks.json` (30s/round, timeout 30min);user 浏览器挑 → 保存 JSON 到 out_dir
Phase 3: copied picked → `{task_id}_01.png` + rewrite N-1 段 anchor-locked → 跑 N-1 张 series
```

**🚨 Phase 3 三个 LOCK** (hybrid path 反转旧 main bug + round-6 补完):
- **主角身份 LOCK**: 旧 main 路径 Phase 3 描述 "angle/sidekick may vary" 实际让主角身份也 vary (实测旧版 series 跨张主角变成完全不同的另一个角色). hybrid path 改为**主角身份严格 LOCK**, N-1 段 rewrite 都跟 picked anchor 保持同一主角, 只 vary pose/scene/小道具.
- **字位密度 LOCK** (round-6 R6-5): N-1 段 [文字] 段密度跟 picked anchor 实际渲染的字位数对齐 (anchor 是无文字 splash → N-1 段也不渲染文字; anchor 有 3 字位 → N-1 段也维持 3 字位; 防 phase1 anchor 空文字而 phase3 自由发挥加字位).
- **use-case 默认约束失效** (round-6 R6-2): Rule 10 里 photorealistic-natural 的 "no studio polish" / ui-mockup 的 "clear hierarchy" 等 use-case-specific 默认约束在 phase3 **全部失效**, 以 picked anchor 实际渲染风格为准 (anchor 本就 studio polish 时 phase3 不该再反约束). 通用 `no logos/trademarks/watermark` + edit "改 X 不动其余" 豁免仍生效.

三个 LOCK 都 codify 在 `rewrite_prompt.py` anchor_phase="phase3" 段 system prompt + Rule 10 末项里。

**🚨 Phase 2 — agent 绝对不能代办**:

agent 看到 `_batch_meta.json` 内 `status="awaiting_picks"` = 等用户挑选的标志,不是卡死。

- ✅ 通知 user 去浏览器打开 `anchor_pick.html` 挑选,picks JSON 应保存到 `<out_dir>/<batch_id>_anchor_picks.json`
- ❌ **绝对不要 agent 自己写 picks JSON** — anchor pick 必须 user 决定(审美 + 业务判断,LLM 评不出"塔夫满意的那张")。agent 代办 = 完全失去 anchor workflow 的意义。

### 失败时的 fallback

- 步骤 1 3 tier 全部失败 → 对话区直接发 markdown 可点 URL 让 user 自己点:`[http://localhost:8765/web/batch_form.html](http://localhost:8765/web/batch_form.html)`
- batch_runner.py 校验失败（参考图不存在 / 0 张图 / prompt 空）→ 把 `! 校验失败` 清单贴给用户，让他回表单改，**不要**自己猜路径或自己补 prompt
- launcher 输出 method=`DETACHED_NO_BREAKAWAY` 而不是 `DETACHED+BREAKAWAY` → 父 Job 不允许 breakaway,**fallback 已生效**(仍 detach,只是没逃出 Job),通常也能活,但如果 batch 跑到中途被杀 → 这是 host 仍在杀,需要更暴力手段(scheduled task / WMI),回报给开发者
- batch 跑到中途停 + result_grid badge 卡在 🟦 几分钟没变 → tail `<out_dir>/<config>_detached_launcher.log` 看 batch_runner 是不是 crash 了。如果 log 末尾是 traceback,把 traceback 贴出来调
- config.json 解析失败 → 把原始 JSON 错误位置告诉用户，让他回表单页重生成

## 图片迭代修改（基于已生成图再加工）

设计师跑完一批后看 `result_grid.html`,想"第 3 张头改一下 / 第 5 张文字改一下 / 这张作为新参考再做 2 张变体"——技术基础已 free,**`/v1/responses` + `image_generation` tool 端点天然支持把任意已生成 PNG 当下次 input**(把它写到 task 的 `reference_images` 列表就行,不区分"原图"还是"AI 生成图")。

三种典型迭代场景:

**场景 1 — 单张图局部修改**(改头 / 改文字 / 改气泡)
- 用户在 result_grid 里点开第 3 张,拿到路径 `<out_dir>/t03_01.png`
- 用户回表单建新 batch,task 的 `reference_images = ["<out_dir>/t03_01.png", "<新角色参考图>"]`, `prompt = "把这张图的主角头部换成图2里识别出的角色形象,其他保持"`
- batch_runner 把 ref_images 全发给 `/v1/responses` + `image_generation` tool, image model 自己识别"主图 + 参考图"语义

**场景 2 — 用已生成图当风格 anchor 再做 N 张系列**
- 用户拿满意的第 5 张当 series anchor: `reference_images = ["<out_dir>/t05_01.png"]`, `prompt = "保持这张图的画风/配色/版式,改成另一个角色 X 的版本",n = 3`
- 多张图共享 anchor,系列一致性强

**场景 3 — fork 同一 task 跑多版本**
- 用户对第 1 张的 90% 满意但 prompt 想微调:复用 t01 的 `reference_images`,但 prompt 改新版本,再跑一遍

**当前 UX 限制**(2026-05-13):
- 用户**手贴路径**:从 result_grid 复制 `t03_01.png` 的绝对路径 → 粘到 form 的 `reference_images` 输入框。能 work,但繁琐。
- 没有"加载已有 batch 复用 tasks"按钮:想改第 3 张只能从头建 batch,改不动旧的。

**未来可升级**(按工程量 P0→P2,需用户决策):
1. **(P0,~30 min)** form 加"加载已有 batch"按钮:输入或选择 `<out_dir>/_batch_meta.json` → JS 反向 populate tasks 到表单 → 用户改完再下载新 config(新 batch_id)。无后端,纯 JS。
2. **(P1,~2h)** result_grid 缩略图旁加"复用这张当参考图"按钮:点击 → 把图路径 + 模板 prompt 写到 localStorage / URL hash → form 自动 populate 新 task。
3. **(P2,~半天)** 起 Flask 后端服务,form 直接 POST → runner → push 实时 stdout 到浏览器。最丝滑但工程量大。

设计师推广**起步阶段建议先用现状**(场景 1-3 都能通过"手贴路径"完成),等真实跑 case 后再决定升级哪条 UX。

## 失败模式 / 异常处理

| 现象 | 诊断 | 处理 |
|---|---|---|
| image_gen HTTP 4xx/5xx | API key 无效 / 配额耗尽 / 参考图过大 | 检查 EPHONE_API_KEY；缩小参考图 |
| `[rewrite-cn] WARN: model={model} 不支持 reasoning_effort, fallback to no-reasoning` | rewrite 模型不支持 `reasoning_effort` 参数 (e.g. 直连 OpenAI 用 gpt-4o 而非 gpt-5.4) | 正常 fallback,**不消耗 transient retry 预算** (R7-2: insert 0-sleep + pop 尾部 sleep slot 保净 budget=3 不变), 输出质量略降但不阻塞 batch |
| 图片字体单薄 / 错位 | 走错路线了，不要做 Pillow 后期叠字！ | 检查 Step 4 prompt 里是否误加了"留白"指令；image_gen 必须一次性画字 |
| 输出图过度像参考图（人物特征 ≈ 参考图人物变体） | 参考图过度复刻：prompt 不够具体 | 强化 Step 4 的"角色身份从 Step 1 PerImageNotes 实际看到的图源取"约束 |
| N 张序列视觉风格不一致 | 没把所有参考图都传给每次调用 | 简单循环模式：每次 `--refs <所有用户参考图>` 全传；走 Phase 1 老接口的话 `anchor_strategy="first"` |
| 角色漂移（生成的角色跟实际图源不像） | rewriting 时没把实际角色视觉描述堆进 prompt | Step 4 加 `based on Image <N> visual identity: <服装色> <武器> <表情>` —— `<N>` 是 PerImageNotes 里识别为角色源的图编号 |
| 选角色总是同一批（如总是同样 5 个名字）| ⚠ **触发了预设偏见 bug** | 检查 Step 1 CandidatePool 是否真的从 vision 抓取，**不是从记忆 / 训练先验 lookup**。绝对不要从外部翻历史角色表照抄 |

## 配置准备（首次使用）

### Agent 行为：First-time setup（零负担推广路径）

如果 `scripts/image_gen_hybrid.py` exit code 2 + stderr 报 `! credentials missing: ...` (`_credentials.CredentialsError`),agent **必须**按以下流程处理(不要假设用户懂环境变量 / 不要让用户去翻系统设置):

1. **问用户一次**(用用户的母语，对设计师友好措辞):
   > "我需要你的 ephone API key 才能跑这个 skill。请把 key 贴在对话里(以 `sk-` 开头)，我会帮你保存到本机配置文件 `~/.config/game-ad-imagegen/config.toml`，以后自动用，不用再问。"
2. **等用户回复**。如果用户贴的不像 key(没 `sk-` 前缀 / 太短 / 空)，再问一次，仍不对就停止。
3. **用 file write tool 写**(不是 PowerShell `setx` / 不是系统 env / 不要碰 `~/.bashrc`):
   - 路径：`%USERPROFILE%\.config\game-ad-imagegen\config.toml`（Windows）/ `~/.config/game-ad-imagegen/config.toml`（Unix）
   - 内容：
     ```toml
     ephone_api_key = "<user 贴的 key>"
     ```
   - 如果 user 贴的 key 是真 OpenAI key（`sk-proj-...` 前缀 / 已知是 platform.openai.com 的）→ 改写 `openai_api_key = "..."` + `openai_base_url` 字段缺省（让 SDK 走 OpenAI 默认）
4. **不要**在 chat 里 echo 完整 key（确认收到说"已保存"即可）。
5. **重新跑** `python scripts/image_gen_hybrid.py ...` — `_credentials.load_credentials()` 会自动读到 `~/.config/game-ad-imagegen/config.toml` (路 3 fallback)，不需要重启 WorkBuddy / 不需要设 env。

### 手动配置(给已经懂的开发者参考)

任选一种(脚本会按优先级 0 → 4 找):

- (路 0) `OPENAI_API_KEY` env + `OPENAI_BASE_URL` env(标准 OpenAI SDK 兼容)
- (路 1) `GAME_AD_IMAGEGEN_EPHONE_KEY` env(skill 专用)
- (路 2) `EPHONE_API_KEY` env(项目通用)
- (路 3) `~/.config/game-ad-imagegen/config.toml`(agent setup wizard 写的)

### 装依赖

```bash
pip install openai pillow
```

## 已验证用例 + 演化路径 + 设计文档

skill 内**故意不放 examples**(防"试跑命中"陷阱)。需要 case 参考、设计原则、实验结果等开发者文档,查 `git log` 或开 issue。

#!/usr/bin/env python
"""
rewrite_prompt.py (hybrid worktree v2) — LLM-driven 转写层,**输出中文 structured prompt**

跟 main 版区别:
  - main 版: 输出英文长段 prompt → 给 gpt-image-2 /v1/images/edits
  - hybrid 版: 输出**中文** structured prompt (codex prompting "labeled lines" 模板) → 给 /v1/responses + image_gen tool

为什么不英文化:
  - 英文化是 lossy 包装(几何细节抽象成"tilted"丢方向 — 实测 case_08 票根方向漂移根因)
  - /v1/responses + image_gen tool 内部 vision 是多模态,中文 prompt 它能直接处理
  - 中文 verbatim 文字也不需要翻译

保留 main 版的核心机制:
  - STEP A: vision verify (hybrid 改为强制输出 [Vision Notes] 块, 反 hallucination)
  - STEP B: CandidatePool 选角 (N>1 时产 series variety 的关键)
  - STEP C: 产 N 段 self-contained prompt
  - Rule 2: 文字位数 ≤5 + 模仿 ref 字位密度 (ref 无文字则不渲染)
  - Rule 4: Verbatim
  - Rule 6: Style words from vision, never genre/franchise/IP 先验
  - SENTINEL 防野调用

反转 main 版的 bug:
  - Phase 3 ANCHOR LOCK MODE 旧版鼓励"角色 vary",hybrid 改"角色 LOCK(从 picked anchor 复制),只 vary pose/scene"

CLI:
  python rewrite_prompt.py --user-prompt-file in.txt --refs r1.png,r2.png --out rewritten.txt --n 5
  python rewrite_prompt.py --user-prompt-file in.txt --refs r1.png,r2.png,picked_anchor.png --out rewritten.txt --n 4 --anchor-phase phase3 --anchor-idx 3
"""
import argparse
import base64
import os
import re
import sys
import time
from pathlib import Path

from openai import OpenAI

PROMPT_SEP = "---PROMPT-SEP---"
SENTINEL = "# REWRITTEN-CN-V2"  # 跟 main 版 REWRITTEN-V1 区分

DEFAULT_MODEL = os.environ.get("REWRITE_MODEL", "gpt-5.4")
DEFAULT_TIMEOUT = 180


from _credentials import load_credentials as _load_credentials, CredentialsError  # noqa: E402


REWRITE_SYSTEM = f"""你是 `game-ad-imagegen` skill 内部的 prompt 重写 agent。
你的最终目标: **让输出 prompt 喂给 image model 的出图质量,跟 ChatGPT 网页版处理同一组输入的出图一致**。
这意味着 augment 要克制有度: 既不能过 generic 浪费 prompt budget, 也不能凭空塞 user/ref 没有的信息把 model 带偏。

==== Specificity policy (来自 codex 上游 prompting.md) ====
判断 user prompt 的具体程度, 决定 augmentation 量:
- **user prompt 已经很具体** (有明确主体/场景/约束/字位/构图) → 只做 normalize / restructure 成 labeled lines, **不要加创意需求**。10 段模板里没信息的段直接**整段省略**, 不要填废话凑数。
- **user prompt 偏 generic** ("做张图" / "出个海报" / 只给图没给文字需求) → 可以 tasteful augment, 补 composition / lighting / scene concreteness 等帮 image model 落地的细节。

**禁加** (Disallowed augmentation, 强 enforce):
- ref 没出现 + user 没提的额外 character / 物体 / 道具 / 角色
- 没 implied 的品牌名 / slogan / palette / 故事情节
- 没排版依据的 side-specific placement (e.g. 凭空说"主角放左下角")

**允许加** (Allowed augmentation):
- composition / framing 提示 (wide / close / top-down 等)
- polish level / intended-use 提示
- 实用 layout 提示 (e.g. "give negative space for headline if needed")
- 支持已述请求的合理场景具体化

==== 输入 ====
- 一组参考图 (按顺序编号 Image 1, Image 2, ...)
- 一段用户的中文需求
- 目标段数 N (要产几段独立的图像生成 prompt)
- 可选 anchor 模式标志 (phase1 / phase3)

==== 你的工作 ====
产出 **N 段中文 structured prompt**,每段对应 1 张输出图。每段 prompt 独立 self-contained
(它们会被分别送到 /v1/responses + image_generation tool, 不共享上下文)。

==== STEP A: 强制 vision verify 每张图 (不再静默) ====
**这是反 hallucination 的最关键步骤** — 在每段 prompt 顶部强制输出 `[Vision Notes]` 块,
每张图 plain 描述 1-3 行,**绝不能基于 franchise / game / 角色名 先验编造**。

写法 (必须在 [Vision Notes] 块内出现, 不能跳过):

```
[Vision Notes]
- Image 1: <plain 描述 — 性别/年龄/发色/发型/服装颜色与款式/武器具体形状/可见装饰/可见文字 verbatim>
- Image 2: <同上, 不基于训练先验, 看到啥写啥>
- ...
[/Vision Notes]
```

**反 hallucination 规则 (强 enforce, 题材无关)**:
1. 看到角色辨不出具体身份 → 写 "<体型/性别>, <实际服装颜色与造型>" — **绝不可凭训练先验猜任何具体历史人物/franchise/动漫/游戏角色名**, 除非:
   - (a) 图上有清晰可读的角色名字标签, 你能 OCR 出来 verbatim, 或
   - (b) user 在原 prompt 显式指定了角色名
2. 角色身份识别**只能基于 OCR'd 文字标签 / 名牌 / 标识**, **不能基于服装颜色/发型/武器形状/胡须长短等视觉特征推理具体身份** (这是题材刻板印象, 会把同 trope 的 N 张图都识别成同一角色)
3. 候选角色池中**只能引用 vision 真实可见 + OCR 确认**的身份, 池子小就池子小, **绝不允许扩充 hallucinated 角色**
4. 如果实在不确定, 用 "<视觉特征>+<placeholder>" 描述 (如 "绿色服装+持长柄武器+长发的男性角色"), **绝不要把名字写进去**

下游 prompt 中**所有"参考图角色"段必须基于 [Vision Notes] 推导**, 不能引入 [Vision Notes] 没提到的细节。

记录每张图的:
- 主体内容 (角色/物体/场景)
- 风格 (画风/笔触/调色板/光影/材质)
- UI 元素 (按 ref 所见据实列出 ref 上的图形装置, 不预设种类)
- 可见的中文文字 (verbatim 准确抄录, 包括卡牌名/标题/角色名)
- 在 user 需求里的角色 (composition reference / character source / style reference / edit target /
  compositing element / mask reference 等 — 优先用 user 显式描述,如 "图1构图" → composition reference)
- **装饰元素细颗粒 (必须单独列, 不算文字位)**: ref 上所有**非文字视觉特征**——按 ref 所见据实列出, 不预设种类。包括但不限于: ref 上可观察到的形状/笔触/纹理/光影/材质/构图元素等。这些是 ref 的**视觉语言**, 跟文字位分开, 必须列出来让下游 image_gen 复刻形态 (内容可以换但形态保留)。

==== STEP B: 构建 CandidatePool ====
扫描所有 refs 里可用的角色/主体:
- N=1 时: 选 1 个最 fit user 意图的角色当主角
- N>=2 时:
  - 默认 "N 个不同主角" (各 ref 各 1 个,产生 series variety) — 除非 user 明显在要求"5 张全 X 角色"或单角色多 pose
  - anchor_phase=phase3 时: **角色严格 LOCK** (从 picked_anchor 复制角色身份),只 vary pose/scene/sidekick
    - 此规则反转旧版"angle/sidekick may vary"导致主角身份跨张漂移的问题

==== STEP C: 产 N 段中文 structured prompt ====
**Keep it short** (来自 codex 上游 "Augmentation rules"): labeled lines 是 **scaffolding 不是 closed schema** — 只填**有信息的字段**, 没信息的段**整段省略不要拼凑废话**。
具体来说: 如果 ref 是纯图像无文字 → [文字] 段省略; user 没指定调色板 → 不要瞎编 "深红+金色"; ref 已经清楚定了构图 → [构图] 段简短点出"沿用 ref 构图"即可。
**add only details that materially improve the image** — 不是字段填满就质量高, image model prompt budget 有限, 废话稀释关键信号。

**每段 prompt 必须以 `[Vision Notes]` 块开头** (STEP A 输出), 然后才是 labeled-lines 模板 (按需省略空段):

```
[Vision Notes]
- Image 1: ...
- Image 2: ...
[/Vision Notes]

用途: <从下列 19 个 codex use-case slug 选一个最接近的; 真的不 fit 才自由文本>
  生成类: photorealistic-natural / product-mockup / ui-mockup / infographic-diagram /
         scientific-educational / ads-marketing / productivity-visual / logo-brand /
         illustration-story / stylized-concept / historical-scene
  编辑类: text-localization / identity-preserve / precise-object-edit / lighting-weather /
         background-extraction / style-transfer / compositing / sketch-to-render
主要请求: <一句话讲这张图要什么>
参考图角色:
  - Image 1 (<role>): <vision 看到的 + 在本段 prompt 怎么用>
  - Image 2 (<role>): <同上>
  - ...
场景/背景: <氛围 + 关键 props + 来源>     # 没信息可省略整段
主角主体: <具体描述 + pose + 跟 ref 的关系>
画风/介质: <从 vision 提取的风格描述 (笔触/材质/光影/线条/调色板/质感), 不写任何 franchise/IP/题材名>
构图/比例/尺寸: <宽高比 + 尺寸 + 关键构图元素位置>   # ref 已定构图时简短点出即可
文字 (verbatim, 字位数模仿 ref 字位密度, 上限 5; ref 无文字则本段省略):
  - <位置 1>: "<引号内 exact verbatim>"
  - <位置 2>: "<...>"
  - ... (≤5 个; 没有的位置直接不写出来)
约束: <从 user 否定指令转成正向 + 通用 quality 约束 + 默认 "no logos, no trademarks, no watermark">
避免: <hallucination 黑名单: 不要拼图 / 不要 panel / 不要从 ref 复制其他文字 / ...>
```

==== 关键规则 (严格 enforce) ====
1. **构图复刻 ref 实际形态**: 主体数量 + 版式按 ref vision 看到的实际形态 — ref 是单主体就单主体, 群像就群像, 分镜/拼图/split-screen 就照 ref 实际形态。**不预设任何主体数量上限**, 也不预设单 panel/多 panel 偏好。
2. **文字位数量 = 模仿 ref 字位密度**: image model 在 ≥6 文字位时文字渲染塌, 所以**上限 5 个文字位**。下限 = ref 实际字位数 (ref 几位就几位, 0 位也允许, 意思整图不渲染任何文字)。两条规则:
   - **不要无中生有加字位**: ref 是无文字 splash / concept art / 纯视觉 KV → 输出也不加文字, 哪怕 user prompt 提到 quoted 字面, 也只放到 user 显式指定的一个字位 (没就空)
   - **字位密度高的 ref 也别超 5**: ref 上有 8-10 字位密集 → 输出截到 ≤5 个最关键字位 (优先 user 显式指定的 > ref 上最显著的字位 > ref 上较次的字位, 其他略)
  每个保留字位填什么:
   - (a) user 显式指定的 quoted 字面 → verbatim
   - (b) ref 上有字位但 user 没指定内容 → 按 ref 字位的**语义功能**填对应内容 (ref 上原是 X 类信息 → 当前主体对应 X 类信息). 具体语义类型由 vision 实际识别决定, 不预设
3. **删 user prompt 的批量控制语言**: "分别" / "5 张" / "做 N 张" / "分两排" 这些不是视觉指令, 是告诉你产几段。**不要 echo 进任何 prompt**。
4. **Verbatim**: 每个文字位的内容写引号内 exact verbatim (中文/英文/数字皆可)。永远不要含糊地说"主题文字"或别的占位短语 — 会被 image model hallucinate。如果某位置 ref 上没字, 该位置直接**不写进 [文字] 段**即可 (不需要写"留空")。
5. **Series variety when N>1 (非 anchor phase3)**: 每段不同 primary subject (来自 CandidatePool), 不要 N 段全是同一个主体的细微变体。
6. **Style words from your vision**: 用 vision 看到的实际视觉特征描述 (笔触/材质/光影/线条/调色板/质感)。**永远不要用 franchise / IP / 题材标签先验** (任何具体作品名/题材名都不允许)。
7. **Image role 显式 label**: 每张 ref 在 prompt 里必须显式 label 它的 role (avoid model 自由猜测 role 导致漂移)。
8. **Edit mode 显式 invariants**: 若 use case 是 edit (用户说"改 X 其余不变" / "把 X 改成 Y"), 约束必须含 "change only X; keep everything else (layout/typography/colors/composition/background) unchanged" 这种 invariant。
9. **复刻 ref 视觉特征 + 不复制 ref 文字 verbatim**: image model 看 ref 时会把 ref 上的文字直接 copy 进新图。约束必须含 "do NOT copy any **text content** from reference images; only use text from the [文字 verbatim] section below"。**关键**: "避免"段**只禁文字 verbatim 内容**, **绝不能扩成"不要复制 ref 上的任何视觉特征"** — 过广避免会让 model 同时 strip ref 的视觉元素, 导致出图比 ref 简陋。正确写法: "不要复制 ref 上的 verbatim 文字内容; 复刻 ref 的视觉特征 (按 [Vision Notes] 里列出的视觉元素清单)"。
10. **默认约束** (跟 codex 上游 sample-prompts 13 个 recipe 默认带的一致): 除非 user 显式说"要 logo / 要 watermark", 每段 prompt 的 [约束] 段必须含:
   - `no logos, no trademarks, no watermark` (所有 use case 通用)
   - `no extra text outside [文字] section` (除 logo-brand / wordmark 等文字本身就是主体的 use case)
   - 若 use case 是 photorealistic-natural → 加 `no studio polish, no staged look` 让 model 走自然摄影质感
   - 若 use case 是 ui-mockup / infographic-diagram → 加 `clear hierarchy, readable typography`
   这些默认约束**不算 augmentation 而算 normalization** — 是 codex 上游已 codify 的 baseline, 不算"凭空加细节"。
11. **生僻字 / 英文人名 / 长数字串 letter-by-letter 处理**: image model 看 verbatim 字符串容易吞字 / 漏字 / 错字。文字位含以下任一情况时, 在该字位的 verbatim 内容后加 letter-by-letter 拆分注释:
   - 生僻汉字 (e.g. "燚" / "鬵" / "蓥") → `"燚阳殿" (拆字: 燚-阳-殿)`
   - 英文人名带 diacritic (e.g. "Müller" / "Naïve") → `"Müller" (拼字: M-ü-l-l-e-r)`
   - 长数字串 ≥5 位 (e.g. "85618" / "135728") → `"85618" (数字: 8-5-6-1-8)`
   - 中英混排短词 (e.g. "VIP特权" / "iOS版") → `"VIP特权" (拼字: V-I-P-特-权)`
   常见汉字 (e.g. "登录"/"领取"/"挑战") **不需要**拆分, 拆所有字会反向稀释 prompt budget。

==== Anchor 模式特殊处理 ====
- anchor_phase="phase1" (出 M 候选给 user 挑):
  - 产 1 段标准 prompt (single hero, concrete character)
  - sampling 自动产生 M 张细节不同的候选 (用户 batch_runner 跑同段 prompt × M 次)
  - 不要"故意留模糊" — sampling 已经会产生 variety
  - 字位按 Rule 2 (模仿 ref 字位密度, ref N 位就 N 位, 0 也允许, 上限 5)。不要为了"留 phase3 余地"刻意空着 — phase3 会重新跑 rewrite, 这里空着只让 phase1 候选图字位空白让 user 没法挑
- anchor_phase="phase3" (用 picked anchor 锁风格生 N-1 张系列):
  - refs 列表里 Image {{anchor_idx}} 是用户挑的 picked anchor (来自 Phase 1 候选)
  - 风格 LOCK 到 Image {{anchor_idx}}: 渲染技法/调色/排版/ref 的视觉特征 (按 [Vision Notes] 列出的视觉元素清单) 全部严格匹配
  - **主体 LOCK** (反转旧版 bug): 跟 picked anchor 同一主体身份, 不要换. 只 vary pose/场景/小道具。
  - 每段 prompt 必须显式写: "严格匹配 Image {{anchor_idx}} 的渲染风格/调色/排版/视觉特征; 本张主体与 Image {{anchor_idx}} 保持同一身份, 只换 pose 和场景细节"

==== 输出格式 ====
- N == 1: 直接输出 1 段中文 structured prompt (纯文本,无 markdown fence)
- N >= 2: N 段中文 prompt,用单独一行 `{PROMPT_SEP}` 分隔。例:
  ```
  用途: <自由文本, 不预设>
  主要请求: ...
  ... (第 1 段完整 labeled lines)
  {PROMPT_SEP}
  用途: <自由文本, 不预设>
  主要请求: ...
  ... (第 2 段)
  {PROMPT_SEP}
  ...
  ```

绝不要前导废话 ("Here are the prompts:")、绝不要 markdown ```fences```、绝不要编号 ("Prompt 1:")。
分隔符那行是唯一的 structure marker。每段会被 verbatim 喂给 image_gen tool。
"""


def _encode_image(p: Path) -> dict:
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = p.suffix.lower()
    mime = (
        "image/jpeg" if ext in (".jpg", ".jpeg") else
        "image/webp" if ext == ".webp" else
        "image/png"
    )
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _wrap_with_sentinel(prompt: str) -> str:
    return f"{SENTINEL}\n{prompt.strip()}"


def rewrite(user_prompt: str, reference_images, n: int = 1,
            model: str = None, verbose: bool = False,
            anchor_phase: str = None, anchor_idx: int = None) -> list[str]:
    """
    输入:
      user_prompt: 用户中文 (可含 prompt_zh.md header,内部自动 strip)
      reference_images: list of Path
      n: 期望输出段数
      anchor_phase: None / "phase1" / "phase3"
      anchor_idx: phase3 时指定 picked anchor 在 refs 里的 1-indexed idx (默认最后一张)

    输出:
      list[str] 长度 n,每段含 SENTINEL header
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")

    # Strip prompt_zh.md YAML frontmatter (---\n...\n---\n), 不吞正文里的 "---" 分隔线
    _frontmatter_pat = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
    user_prompt = _frontmatter_pat.sub("", user_prompt, count=1).strip()

    base_url, api_key = _load_credentials()
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=DEFAULT_TIMEOUT)
    model = model or DEFAULT_MODEL
    ref_paths = [Path(p) for p in reference_images]
    image_contents = [_encode_image(p) for p in ref_paths]

    user_text = user_prompt
    if n > 1:
        user_text += (
            f"\n\n[runner 指令] 产出 {n} 段独立的中文 structured prompt (每段对应 1 张输出图),"
            f"用单独一行 `{PROMPT_SEP}` 分隔。每段要 feature 不同的 primary character "
            f"(从 CandidatePool 选),非 anchor phase3 模式下要给 series 视觉 variety。"
            f"从 user 输入中删除批量控制词 ('分别' / '{n} 张' 等)。"
        )
    else:
        user_text += "\n\n[runner 指令] 产出 1 段中文 structured prompt (单张输出)。"

    if anchor_phase == "phase1":
        user_text += (
            "\n\n[ANCHOR CANDIDATE MODE — Phase 1] 这段 prompt 将被用于产 M 张候选 "
            "(同段 prompt × M sampling),让 user 挑出最满意的作为后续 Phase 3 系列的视觉 anchor。"
            "写 1 段精心打磨的中文 prompt,明确角色选定 (不要故意模糊 — sampling 会自然提供 pose/细节 variety)。"
        )
    elif anchor_phase == "phase3":
        if anchor_idx is None:
            anchor_idx = len(ref_paths)
        if anchor_idx < 1 or anchor_idx > len(ref_paths):
            raise ValueError(f"anchor_idx={anchor_idx} 超出 refs 范围 1..{len(ref_paths)}")
        user_text += (
            f"\n\n[ANCHOR LOCK MODE — Phase 3] Image {anchor_idx} 是用户在 Phase 1 候选轮挑的 "
            f"**picked anchor 图**,它代表本批次的 LOCKED 视觉风格 — 渲染技法/调色/排版/ref 上的视觉特征 "
            f"(按 [Vision Notes] 列出的视觉元素清单)。"
            f"你的 {n} 段 prompt 必须视觉风格严格匹配 Image {anchor_idx} (**~85% faithful 而不是普通 70%**)。"
            f"**主角身份 LOCK** (反转旧版 bug — 不允许主角 vary): {n} 段 prompt 的主角都跟 Image {anchor_idx} "
            f"保持同一身份,只 vary pose/场景/小道具。其他 ref (Image ≠ {anchor_idx}) "
            f"提供额外 material 但不改变主角身份。"
            f"每段 prompt 的 [约束] 部分必须显式写: "
            f"`严格匹配 Image {anchor_idx} 的渲染风格/调色/排版/视觉特征; 本张主体与 Image {anchor_idx} 保持同一身份`。"
            f"最终批次 (picked anchor + {n} 张新图) 应该看起来像 coherent set,而不是 {n+1} 张不相关的图。"
        )

    user_content = image_contents + [{"type": "text", "text": user_text}]

    if verbose:
        print(f"  [rewrite-cn] model={model} refs={len(ref_paths)} n={n} phase={anchor_phase} anchor_idx={anchor_idx}", flush=True)

    msgs = [
        {"role": "system", "content": REWRITE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    # transient retry (429/500/502/503/504/网络) + reasoning_effort fallback.
    # rewrite 调用也 ¥-cost (model gpt-5.4), 一次 502 不应直接杀整批 (跟 image_gen_hybrid
    # 的 _TRANSIENT_STATUSES + 2-step backoff 一致, round-4 blind #6 抓).
    _TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
    _RETRY_BACKOFF_SEC = [5, 15]
    attempts = [0] + _RETRY_BACKOFF_SEC
    use_reasoning = True
    response = None
    last_exc = None
    for attempt_idx, sleep_before in enumerate(attempts):
        if sleep_before > 0:
            print(f"  [rewrite-cn] retry attempt {attempt_idx + 1}/{len(attempts)} after {sleep_before}s...", file=sys.stderr, flush=True)
            time.sleep(sleep_before)
        try:
            kwargs = {"model": model, "messages": msgs}
            if use_reasoning:
                kwargs["extra_body"] = {"reasoning_effort": "medium"}
            response = client.chat.completions.create(**kwargs)
            break
        except Exception as e:
            last_exc = e
            # 检测"模型不支持 reasoning_effort"的 400 — 切到 no-reasoning 重试 (不算 transient retry)
            is_reasoning_unsupported = False
            try:
                status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
                body = getattr(e, "body", None) or {}
                err_param = (body.get("error") or {}).get("param") if isinstance(body, dict) else None
                if status == 400 and err_param and "reasoning" in str(err_param).lower():
                    is_reasoning_unsupported = True
            except Exception:
                pass
            if not is_reasoning_unsupported:
                emsg = str(e).lower()
                if "reasoning" in emsg and any(s in emsg for s in ("unknown", "unsupported", "invalid", "400")):
                    is_reasoning_unsupported = True
            if is_reasoning_unsupported and use_reasoning:
                # 切到 no-reasoning + 让外层 loop 走下一轮 (用 backoff). 不再立即同步重试 —
                # 避免烧 retry slot + 让 transient 检查只看 last_exc 这一个状态.
                print(f"  [rewrite-cn] WARN: model={model} 不支持 reasoning_effort, fallback to no-reasoning (重试将在下一轮 backoff)", file=sys.stderr, flush=True)
                use_reasoning = False
                continue  # 下一轮 attempt (会用 use_reasoning=False, 不带 backoff 因为 attempt_idx 没变 — 实际下次会带, 让 _RETRY_BACKOFF_SEC 决定)
            # 判断是否是 transient: HTTP status code in _TRANSIENT_STATUSES
            status_code = getattr(last_exc, "status_code", None) or getattr(getattr(last_exc, "response", None), "status_code", None)
            if status_code in _TRANSIENT_STATUSES:
                continue  # 下一轮 backoff
            # 非 transient 直接抛
            raise
    if response is None:
        raise last_exc if last_exc else RuntimeError("rewrite-cn unknown failure after retries")

    text = _strip_fence(response.choices[0].message.content or "")

    if n == 1:
        if not text:
            raise RuntimeError("rewrite-cn LLM 返回空")
        return [_wrap_with_sentinel(text)]

    raw_parts = [p.strip() for p in text.split(PROMPT_SEP)]
    parts = [_strip_fence(p) for p in raw_parts if p.strip()]

    if len(parts) == 0:
        snippet = (text[:200] + "...") if text else "(empty)"
        raise RuntimeError(f"rewrite-cn 无法 parse (n={n}>=2 但没找到 PROMPT_SEP): {snippet!r}")

    if len(parts) < n:
        # 之前用 "重复最后一段 pad" silently fix → 失 series variety + WARN 容易被 batch_runner 吞.
        # 改 raise 让 runner 决定 retry / 报 user, 不静默掩盖 LLM 输出问题.
        snippet = "\n---\n".join(p[:120] for p in parts)
        raise RuntimeError(
            f"rewrite-cn 期望 {n} 段, LLM 只返 {len(parts)} 段. 建议: 检查 system prompt 是否要求"
            f"明确 N 段输出 / 增大 max_tokens / 让 user retry. 已得 {len(parts)} 段 preview:\n{snippet}"
        )
    if len(parts) > n:
        print(f"  [rewrite-cn] WARN: 期望 {n} 段, LLM 返 {len(parts)} 段, 截断到前 {n}", file=sys.stderr, flush=True)
        parts = parts[:n]

    return [_wrap_with_sentinel(p) for p in parts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-prompt-file", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--model", default=None)
    ap.add_argument("--anchor-phase", default=None, choices=[None, "phase1", "phase3"])
    ap.add_argument("--anchor-idx", type=int, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    user_prompt = Path(args.user_prompt_file).read_text(encoding="utf-8")
    refs = [Path(p) for p in args.refs.split(",")]
    for p in refs:
        if not p.exists():
            print(f"! ref not found: {p}", file=sys.stderr)
            return 2

    try:
        prompts = rewrite(user_prompt, refs, n=args.n, model=args.model,
                          verbose=args.verbose, anchor_phase=args.anchor_phase,
                          anchor_idx=args.anchor_idx)
    except CredentialsError as e:
        print(f"! credentials missing:\n{e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"! rewrite-cn failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out_text = (f"\n{PROMPT_SEP}\n").join(prompts) if args.n > 1 else prompts[0]
    Path(args.out).write_text(out_text, encoding="utf-8")
    print(f"OK: {args.out} ({len(out_text)} chars, {len(prompts)} 段)")
    if args.verbose:
        for i, p in enumerate(prompts, 1):
            print(f"--- 段 {i}/{len(prompts)} (前 300 字) ---")
            print(p[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())

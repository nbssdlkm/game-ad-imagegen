"""
prompt_sanitize.py (hybrid worktree v3) — light validation + 兜底层

architectural 重构: 主对话责任 = `rewrite_prompt.py` 产中文 structured prompt(含 SENTINEL header)。
本模块降级为 light validation + 兜底:
  1. SENTINEL marker 验证 (防野调用 image_gen_hybrid 跳过 rewriter)
  2. 兜底 strip 批量词 (rewriter LLM 偶尔漏掉)
  3. size/aspect ratio 提取 (识别 "9:16" / "竖版" / "WxH" / "宽高比 X:Y")
  4. size ÷16 校验 + auto-round 到合规
  5. 否定 → 正向 (rewriter 应该处理,这里兜底)

不再做的 (上移到 rewriter):
  - Image role labeling (rewriter LLM vision 直接 label)
  - CandidatePool (rewriter STEP B)
  - Edit mode invariants (rewriter Rule 8)
  - Verbatim text constraint (rewriter Rule 4)

⚠️ 题材无关 — 所有 regex 只看 prompt 结构关键词,不预设角色/题材/风格。
"""
import re
from math import gcd as _gcd  # stdlib

# rewriter 输出必带的 marker
SENTINEL = "# REWRITTEN-CN-V2"


# === 兜底批量词 strip (rewriter 应已删,这里 safety net) ===
BATCH_PATTERNS = [
    (re.compile(r"[做生出]\s*成?\s*(\d+)\s*张\s*(相同|系列|不同|类似)?(的?图)?"), "做一张"),
    (re.compile(r"(\d+)\s*张\s*(相同|系列|不同|类似)?\s*(的?图)?"), "一张"),
    (re.compile(r"分别\s*"), ""),
    (re.compile(r"分\s*[两三四五六七八九十\d]+\s*排"), ""),
    (re.compile(r"分成\s*\d+\s*张"), ""),
]


def _strip_batch_words(text: str) -> str:
    for pat, repl in BATCH_PATTERNS:
        text = pat.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()


# === 否定 → 正向 (兜底, 题材无关) ===
NEGATIVE_REWRITES = [
    (re.compile(r"(UI\s*)?文(本|字)\s*(全)?\s*(去掉|去除|删除|不要)"), "画面不渲染任何中英文字符"),
    (re.compile(r"(去掉|去除|删除|不要)\s*(UI\s*)?文(本|字)"), "画面不渲染任何中英文字符"),
    (re.compile(r"不要\s*大模型颗粒感|不要\s*AI\s*颗粒感|不要.*?颗粒感"), "渲染高清平滑无 AI 噪点伪影"),
]


def _negate_to_positive(text: str) -> str:
    for pat, repl in NEGATIVE_REWRITES:
        text = pat.sub(repl, text)
    return text


# === Size 提取 (题材无关) ===
SIZE_PATTERNS = [
    # "WxH" 字面 (任何形式) — 加 anchor check 跑 (见 _extract_and_normalize_size)
    (re.compile(r"\b(\d{3,4})\s*[xX×\*]\s*(\d{3,4})\b"), "explicit_wxh"),
    # "宽高比 X:Y" / "X:Y" — 必须有 anchor word (避免误匹配 "技能 3:7 概率" / "标:文" 等)
    (re.compile(r"宽高比\s*[:：]?\s*(\d{1,2})\s*[:：]\s*(\d{1,2})"), "ratio"),
    (re.compile(r"(?:比例|画幅|尺寸比|横纵比|aspect)\s*[:：]?\s*(\d{1,2})\s*[:：]\s*(\d{1,2})"), "ratio"),
    (re.compile(r"(\d{1,2})\s*[:：]\s*(\d{1,2})\s*(?:版|横|竖|portrait|landscape)"), "ratio_with_anchor"),
]

# Size 附近的 anchor 词 — WxH 匹配后必须 ±20 char 内有这些之一才采纳 (防 verbatim 文本"挑战1080x720" leak)
_SIZE_NEARBY_ANCHORS = re.compile(
    r"尺寸|画幅|大小|分辨率|size|resolution|输出|宽高|比例|横版|竖版|landscape|portrait|"
    r"宽|高|配图|要|长方形|正方形|做|生成|图"
)

# 关键字 → 比例 hint
ORIENTATION_KEYWORDS = {
    "竖版": (9, 16),
    "竖屏": (9, 16),
    "portrait": (9, 16),
    "横版": (16, 9),
    "横屏": (16, 9),
    "landscape": (16, 9),
    "正方": (1, 1),
    "square": (1, 1),
}


def _round_to_multiple_of_16(n: int) -> int:
    """Round n 到最近的 16 的倍数,保留合规."""
    return int(round(n / 16) * 16)


# ephone image_gen tool 接受的常见合规尺寸 (codex image-api.md line 30-39)
COMMON_SIZES = {
    (1, 1): "1024x1024",
    (16, 9): "2048x1152",
    (9, 16): "1152x2048",
    (3, 2): "1536x1024",
    (2, 3): "1024x1536",
}


def _aspect_to_size(w: int, h: int, target_short: int = 1152) -> str:
    """根据宽高比算合规 size (÷16 + ≤3840 边 + 总像素在 [655K, 8.3M])。
    target_short 默认 1152 → 16:9 出 2048x1152 / 9:16 出 1152x2048 跟 COMMON_SIZES 一致。"""
    gcd_val = _gcd(w, h)
    w_norm, h_norm = w // gcd_val, h // gcd_val

    if (w_norm, h_norm) in COMMON_SIZES:
        return COMMON_SIZES[(w_norm, h_norm)]

    short = _round_to_multiple_of_16(target_short)
    if w >= h:
        long_edge = _round_to_multiple_of_16(int(short * w / h))
        return f"{long_edge}x{short}"
    else:
        long_edge = _round_to_multiple_of_16(int(short * h / w))
        return f"{short}x{long_edge}"


# (math.gcd 已在文件顶部 import, 旧自实现 _gcd 已删)


def _extract_and_normalize_size(text: str) -> tuple[str | None, str, str | None, bool]:
    """
    返回 (normalized_size, source_hint, target_size, upscaled)
    - normalized_size: "WxH" or None — ephone 用的 ÷16 合规 size
    - source_hint: 来源描述,用于 log
    - target_size: "WxH" or None — user 真实意图的 size (没 round 也没 upscale); 跟 normalized 不等时,
      image_gen 写图后需要 PIL post-resize / crop 到 target_size。None 表示不需要 post-resize。
    - upscaled: True 表示走的 upscale 分支 (sub-655K 像素), image_gen 应用 LANCZOS resize;
      False 表示走 round 分支 (÷16 微调) 或无需 post-resize, image_gen 应用 center crop
    """
    # Step 1: 显式 WxH (用户最明确意图)
    # 加 nearby anchor check: WxH ±20 char 内有 size-related 词才采纳 (防 verbatim 文本 leak)
    for m in SIZE_PATTERNS[0][0].finditer(text):
        start, end = m.span()
        window = text[max(0, start - 20):min(len(text), end + 20)]
        if not _SIZE_NEARBY_ANCHORS.search(window):
            continue  # 跳过没 anchor 的 wxh (e.g. verbatim 文案"挑战1080x720")
        w, h = int(m.group(1)), int(m.group(2))
        target = f"{w}x{h}"
        # 校验 + auto-round to 16
        w_rounded = _round_to_multiple_of_16(w)
        h_rounded = _round_to_multiple_of_16(h)
        # 校验最大边 <= 3840
        if max(w_rounded, h_rounded) > 3840:
            if w_rounded >= h_rounded:
                h_rounded = _round_to_multiple_of_16(int(3840 * h_rounded / w_rounded))
                w_rounded = 3840
            else:
                w_rounded = _round_to_multiple_of_16(int(3840 * w_rounded / h_rounded))
                h_rounded = 3840
        # 校验比例 <= 3:1
        if max(w_rounded, h_rounded) / min(w_rounded, h_rounded) > 3:
            return None, f"显式 {w}x{h} 比例 >3:1 不合规", None, False
        # 校验 total pixels >= 655360 (ephone gpt-image-2 min pixel), 不够则按比例 upscale
        total = w_rounded * h_rounded
        upscaled = False
        if total < 655_360:
            ratio = w_rounded / h_rounded
            if ratio >= 1:
                h_rounded = 1024
                w_rounded = _round_to_multiple_of_16(int(1024 * ratio))
            else:
                w_rounded = 1024
                h_rounded = _round_to_multiple_of_16(int(1024 / ratio))
            upscaled = True
        rounded = f"{w_rounded}x{h_rounded}"
        if upscaled:
            return rounded, f"显式 {w}x{h} 像素 < 655K → ephone 用 {rounded} → PIL resize 回 {target}", target, True
        if rounded != target:
            return rounded, f"显式 {target} → ephone 用 ÷16 合规 {rounded} → PIL crop 回 {target}", target, False
        return rounded, f"显式 {target}", None, False

    # Step 2: 宽高比 X:Y (SIZE_PATTERNS[1:] 全是 ratio)
    for pat, kind in SIZE_PATTERNS[1:]:
        m = pat.search(text)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            return _aspect_to_size(w, h), f"宽高比 {w}:{h} ({kind})", None, False

    # Step 3: 关键字
    for kw, (w, h) in ORIENTATION_KEYWORDS.items():
        if kw in text:
            return _aspect_to_size(w, h), f"关键字 '{kw}' → {w}:{h}", None, False

    return None, "未提取到 size hint", None, False


# === Main entry: validate + sanitize ===
def validate_rewritten(prompt: str) -> bool:
    """检查 prompt 是否含 rewriter SENTINEL header。用于 image_gen_hybrid 防野调用。"""
    return prompt.lstrip().startswith(SENTINEL)


def sanitize_post_rewrite(prompt: str) -> tuple[str, dict]:
    """
    rewriter 输出后的 light sanitize:
      - 验 SENTINEL
      - 兜底 strip 批量词 + 否定→正向
      - 提取 size hint
    """
    info = {"sentinel_ok": False, "size": None, "size_source": None, "target_size": None,
            "upscaled": False, "warnings": []}

    if not validate_rewritten(prompt):
        info["warnings"].append(f"missing SENTINEL '{SENTINEL}' — 可能没经过 rewriter")
    else:
        info["sentinel_ok"] = True

    # SENTINEL 通过 → 信任 rewriter 输出, 不再跑 _negate_to_positive (避免双重 regex sub
    # 把 rewriter 写的正向指令再 substitute 一遍). 只 strip 显式批量数字词 (rewriter 偶发漏).
    cleaned = _strip_batch_words(prompt)

    # Size 提取 (从原 prompt 而非 cleaned, 避免清洗破坏数字)
    size, source, target, upscaled = _extract_and_normalize_size(prompt)
    info["size"] = size
    info["size_source"] = source
    info["target_size"] = target  # 非 None 时 image_gen 需 post-resize 到该尺寸
    info["upscaled"] = upscaled  # True 走 LANCZOS resize; False 走 center crop (或无 post-resize)

    return cleaned, info


def sanitize_raw_user_prompt(prompt: str) -> tuple[str, dict]:
    """
    旧 API 兼容入口 (没经过 rewriter 的 raw user prompt)。
    返回 cleaned + 一份 minimal info dict 让 image_gen_hybrid 不挂。
    """
    info = {
        "sentinel_ok": False,
        "size": None,
        "size_source": None,
        "target_size": None,
        "upscaled": False,
        "warnings": ["called sanitize_raw_user_prompt - rewriter should be used instead for full pipeline"],
    }

    # Strip YAML frontmatter (---\n...\n---\n), 不吞正文里的 "---" 分隔线
    # 跟 rewrite_prompt.py round-3 fix 同步 — 旧版 split("---") 太广吞 prompt 主体
    _frontmatter_pat = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
    s = _frontmatter_pat.sub("", prompt, count=1).strip()

    s = _negate_to_positive(s)
    s = _strip_batch_words(s)

    size, source, target, upscaled = _extract_and_normalize_size(prompt)
    info["size"] = size
    info["size_source"] = source
    info["target_size"] = target
    info["upscaled"] = upscaled

    return s, info


# === self-test ===
if __name__ == "__main__":
    tests = [
        ("size 9:16", "把文案改一下,宽高比9:16", None),
        ("size 1920x1080", "做广告图,1920x1080,横版", None),
        ("size 1080x1080", "正方形,1080x1080", None),
        ("size 650x250", "通用素材封面图,650*250", None),
        ("size 5000x3000", "超大图 5000x3000", None),
        ("size 竖屏", "做一张竖屏头像", None),
        ("rewritten check OK", f"{SENTINEL}\n用途: ad\n主要请求: 主角立绘\n构图: 1920x1080 横版", None),
        ("rewritten check FAIL", "用途: ad (没有 sentinel)\n做主角立绘,2048x1152", None),
    ]
    for name, prompt, _ in tests:
        if SENTINEL in prompt:
            cleaned, info = sanitize_post_rewrite(prompt)
        else:
            cleaned, info = sanitize_raw_user_prompt(prompt)
        print(f"=== {name} ===")
        print(f"  raw: {prompt[:60]}{'...' if len(prompt)>60 else ''}")
        print(f"  info: {info}")
        print()

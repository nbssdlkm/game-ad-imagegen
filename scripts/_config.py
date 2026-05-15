"""
game-ad-imagegen skill - 配置层
============================

API 凭据从环境变量读，本地不存 secret（这个 skill 在 git 共享仓里）。

优先级：
  0. 环境变量 OPENAI_API_KEY + OPENAI_BASE_URL（标准 OpenAI SDK 兼容路径）
  1. 环境变量 GAME_AD_IMAGEGEN_EPHONE_KEY / GAME_AD_IMAGEGEN_BASE_URL
  2. 环境变量 EPHONE_API_KEY / EPHONE_BASE_URL（通用）
  3. ~/.config/game-ad-imagegen/config.toml
  4. 报错提示（agent 应按 SKILL.md First-time setup 段处理）
"""
import os
import sys
from pathlib import Path

# ============================================================
# 默认参数（image_gen 调用层）
# ============================================================

DEFAULT_BASE_URL = "https://api.ephone.ai/v1"   # ephone API key 池
DEFAULT_LM_MODEL = "gpt-5.4"                     # rewriter / 工具调度。rewrite_prompt 同时尝试 reasoning_effort="high"(若模型支持则启用思考模式),不支持时自动 fallback 到默认
DEFAULT_IMG_SIZE = "1536x1024"                   # gpt-image-2 合法横版尺寸；默认映射到最接近 16:9 的 legal landscape
DEFAULT_IMG_QUALITY = "medium"                   # medium 比 high 省约 60% 成本，质量差距不明显；正式 demo / 终稿可手动切 high
DEFAULT_TIMEOUT_SEC = 600                        # 单次 image_gen 大约 30-90s，留余量

# ============================================================
# 质量约束（system message 注入，避免 T5/T10 反模式）
# ============================================================

QUALITY_INVARIANTS = """
[QUALITY INVARIANTS — read these before processing the user request]

1. Image labeling:
   - Each reference image is numbered Image 1, Image 2, ... in the order the user provided.
   - The ROLE of each image (style/composition anchor / character source / UI template / text-edit target / etc) is determined by what the image actually shows + the user's prompt context — NOT by position.
   - Do NOT assume "first image = style anchor, subsequent = character sources" — that is one possible form, but the user may also provide 2 style references, 5 in-game shots with no separate anchor, 1 image to text-edit, etc. Read each image's actual content and infer its role.
   - When the user mentions "图1" / "图2" they map to Image 1 / Image 2 in the given order.

2. Generate readable text directly in the image:
   - DO NOT leave title / CTA / speech bubble / banner areas blank for later overlay.
   - DO NOT instruct the image model to use placeholder text or skip Chinese characters.
   - DO NOT write reverse instructions like "do NOT render readable words" — that produces blank-box artifacts (2026-05-13 case_22 failure mode).
   - The image model (gpt-image-2) renders Chinese text well in 2026 — use it. Quote target Chinese text verbatim in double-quotes, character-by-character.
   - Verified failure mode: leaving text blank + Pillow post-overlay → thin fonts, off-position bubbles, unprofessional CTA.

3. Style and composition:
   - Identify which image(s) serve as the style/composition anchor based on actual visual content, not position.
   - Reference that anchor's composition, UI element layout, color palette, font style, panel structure faithfully (~70%).
   - Allow ~30% creative variation in character pose / FX / background details, NOT in the overall style/composition scaffold.

4. Character / asset sourcing:
   - Pick characters / items from the reference image(s) the user actually provided.
   - Do not invent characters that do not visually trace to any input image — hallucinated characters with no source in any input image is a known failure mode.

5. Text rendering — enumerate EVERY visible text position (CRITICAL):
   - Look at the reference image and identify every visible text region: main title, subtitle, character nameplate, CTA button, banner stamp, decorative seal, etc.
   - For EACH text region in the output image, the prompt MUST explicitly state ONE of:
     (a) verbatim Chinese characters to render at that position, in double-quotes — e.g., `<role nameplate>: "<exact chars>"`
     (b) "leave this position empty / no text here"
   - Never leave any text region unconstrained — gpt-image-2 will fill it with plausible-looking Chinese from training priors (often generic historical / fictional names), which usually mismatch the actual character identity in the image.
   - Known failure mode: prompt only specifies the main slogan but not the secondary text positions. Output renders the correct character visually but prints a hallucinated name on the nameplate because that position was unconstrained.
"""

# ============================================================
# 凭据加载
# ============================================================

def _normalize_base_url(url: str) -> str:
    """规范化 base URL —— 确保以 /v1 结尾。
    用户系统 env EPHONE_BASE_URL 可能是 `https://api.ephone.ai`（不带 /v1）
    或 `https://api.ephone.ai/v1`（带）。两种都接受。
    """
    url = url.rstrip('/')
    if not url.endswith('/v1'):
        url = url + '/v1'
    return url

def load_credentials():
    """返回 (base_url, api_key)。失败抛 RuntimeError。base_url 已规范化以 /v1 结尾。

    优先级见模块 docstring(OPENAI_API_KEY → GAME_AD_IMAGEGEN_* → EPHONE_* → config.toml)。
    """
    # 路 0:OPENAI_API_KEY env(标准 OpenAI SDK 兼容路径)
    # 这条让外部已经设好 OPENAI_API_KEY + OPENAI_BASE_URL 的用户(包括 ephone redirect 用户)直接 work。
    if os.environ.get("OPENAI_API_KEY"):
        base_url = (
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("GAME_AD_IMAGEGEN_BASE_URL")
            or os.environ.get("EPHONE_BASE_URL")
            or DEFAULT_BASE_URL
        )
        return _normalize_base_url(base_url), os.environ["OPENAI_API_KEY"]

    # 路 1-2:专用 env / 通用 EPHONE env
    base_url = (
        os.environ.get("GAME_AD_IMAGEGEN_BASE_URL")
        or os.environ.get("EPHONE_BASE_URL")
        or DEFAULT_BASE_URL
    )
    base_url = _normalize_base_url(base_url)
    api_key = (
        os.environ.get("GAME_AD_IMAGEGEN_EPHONE_KEY")
        or os.environ.get("EPHONE_API_KEY")
    )

    # 路 3:本地 config.toml(agent setup wizard 写的)
    if not api_key:
        toml_path = Path.home() / ".config" / "game-ad-imagegen" / "config.toml"
        if toml_path.exists():
            try:
                import tomllib
                with open(toml_path, "rb") as f:
                    data = tomllib.load(f)
                api_key = data.get("ephone_api_key") or data.get("openai_api_key")
                base_url = data.get("base_url") or data.get("openai_base_url") or base_url
                base_url = _normalize_base_url(base_url)
            except Exception as e:
                print(f"  [warn] 读取 {toml_path} 失败: {e}", file=sys.stderr)

    # 路 4:都没有 → 报错(agent 应触发 SKILL.md First-time setup)
    if not api_key:
        raise RuntimeError(
            "缺 API key。任选一种方式配置:\n"
            "  (A) 设系统环境变量 OPENAI_API_KEY(若走 ephone 同时设 OPENAI_BASE_URL=https://api.ephone.ai/v1)\n"
            "  (B) 设系统环境变量 EPHONE_API_KEY\n"
            "  (C) 写入 ~/.config/game-ad-imagegen/config.toml:\n"
            '      ephone_api_key = "sk-..."\n'
            "Agent 行为(SKILL.md First-time setup 段):若 (A)(B)(C) 都未配,主动问 user 一次要 key,然后写到 (C) 那个文件。"
        )

    return base_url, api_key

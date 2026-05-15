#!/usr/bin/env python
"""
game-ad-imagegen / rewrite_prompt.py — vision + rewrite 模块（多段 prompt 版）
============================================================================

把"用户中文需求 + 参考图"→ N 段独立英文 image-gen prompt（每段对应 1 张图，不同角色 / 不同 pose）。
由 batch_runner.py 内部调用让 Mode 2 (form / 跑批) 也享受 skill 的核心 rewrite 能力。

调用方式:
  CLI:
    python rewrite_prompt.py --user-prompt-file in.txt --refs r1.png,r2.jpg --out rewritten.txt --n 5

  模块:
    from rewrite_prompt import rewrite
    prompts = rewrite(user_prompt="...中文...", reference_images=[Path("r1.png"), ...], n=5)
    # prompts is list[str] of length n

内部: ephone /v1/chat/completions(OpenAI SDK + base_url redirect)，多模态(image_url base64)。
"""
import argparse
import base64
import sys
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
from _config import DEFAULT_LM_MODEL, DEFAULT_TIMEOUT_SEC, load_credentials


# n>1 时各段之间的分隔符。LLM 必须在独立行上输出此标记。
PROMPT_SEP = "---PROMPT-SEP---"

# Proof-of-origin marker prepended to every rewritten prompt. image_gen.py
# verifies this marker before sending the prompt to the image API — any prompt
# lacking the marker is refused (caller must run rewrite_prompt.py first).
# image_gen.py strips this line after verification so it does not reach the model.
SENTINEL = "# REWRITTEN-V1"


def _wrap_with_sentinel(prompt: str) -> str:
    """Prefix the rewritten prompt with the SENTINEL marker line."""
    return f"{SENTINEL}\n{prompt.strip()}"


REWRITE_SYSTEM = f"""You are the prompt-rewriting agent inside the `game-ad-imagegen` skill.

USER GIVES YOU:
- One or more reference images, numbered Image 1, Image 2, ... in the order provided
- A Chinese natural-language request describing the desired output
- A target count N (how many independent image-gen prompts to produce)

YOUR JOB: produce **N detailed English image-generation prompts**, one per output image.
Each prompt is sent verbatim to gpt-image-2 (/v1/images/edits) as a separate API call —
they do NOT share state, so each prompt must be self-contained.

== STEP A: Vision verify each image (silently) ==
For each input image, internally note:
- key_visuals (subject, pose, composition, UI elements, visible Chinese text verbatim)
- style_summary (rendering style, palette, mood)
- role in the request (composition anchor / character source / UI template / text-edit target / etc.) — infer from content + user's wording (e.g. "图1 = 构图参考" → "Image 1 is composition anchor")

== STEP B: Build a CandidatePool of characters ==
Scan all input images for available characters. Pick which to feature (typically N different characters if N>1 to give series variety; or N variations of one character if user asks for character study). Lean toward **N different characters** unless the user is clearly asking for the same character N ways.

== STEP C: Write N independent prompts ==
For each prompt use this skeleton:

```
Create a polished {{orientation}} {{asset type}} in {{WxH}}, aspect ratio {{ratio}}.
Image 1 (<role>): <what Image 1 actually shows>.
Image 2 (<role>): <what Image 2 actually shows>.
{{... one line per reference image ...}}

Design a brand-new composition echoing the style anchor image's visual language while
adapting to {{orientation/ratio}}. Keep about 70% faithful, 30% creative.

Main content requirements:
- <Central hero: visual description + pose, traced to specific input image>
- <Background: atmosphere + key props>
- Large stylized title at top: "<verbatim Chinese title>"
- Main promotional banner: "<verbatim Chinese promo line>"
- <Optional speech bubble OR small inset stamp>: "<verbatim Chinese>"

Quality and style requirements:
- All Chinese text rendered crisply and readably DIRECTLY in the image.
- Do NOT leave any text container blank / use placeholder pseudo-Chinese / use English subtitles.
- No raw screenshot artifacts / phone UI / FPS overlay / watermarks / app-store badges / blank text containers.
- <Polished commercial finish, style notes>.
- {{Orientation}} composition only, {{WxH}}.
```

== CRITICAL RULES ==
1. **Single hero focus per prompt**: 1 main character + ≤2 supporting elements (inset / sidekick). NEVER write multi-panel / split-screen / N-grid / collage in a single prompt.
2. **STRICTLY 4-5 Chinese text positions per prompt, NEVER MORE THAN 5**. Pick from: large title, main promotional banner, character nameplate, speech bubble, small stamp/tag, fine-print. Fewer than 4 = empty-looking; more than 5 = dilutes gpt-image-2 text rendering budget (visible failure mode in case_24). Be aggressive about cutting — when in doubt, drop a text position.
3. **Strip batch-control language from user's Chinese**: words like "分别"/"5张"/"做N张"/"each" are NOT visual instructions — they tell you how many independent prompts to produce. Do NOT echo them into any prompt.
4. **Verbatim Chinese**: every text position must specify either (a) the exact Chinese characters in double-quotes, OR (b) explicit "leave this position empty / no text here". Never leave a position unspecified — gpt-image-2 will hallucinate generic Chinese.
5. **Series variety when N>1**: each of the N prompts should feature a different primary character (drawn from CandidatePool) OR a clearly different pose / scene. Avoid producing N prompts that read like minor variations of the same character.
6. **Style words from your vision** (e.g. `polished 2D illustration`, `semi-realistic painterly CG`) — never from genre stereotypes / franchise priors.

== OUTPUT FORMAT ==
- If N == 1: output ONLY the single English prompt as plain text. No preamble, no markdown fences.
- If N >= 2: output N prompts separated by a line containing exactly `{PROMPT_SEP}` (no other characters on that line). Example for N=3:

  ```
  Create a polished landscape ... (full prompt 1) ... 1920x1080.
  {PROMPT_SEP}
  Create a polished landscape ... (full prompt 2) ... 1920x1080.
  {PROMPT_SEP}
  Create a polished landscape ... (full prompt 3) ... 1920x1080.
  ```

NO preamble like "Here are the prompts:", NO markdown ```fences``` around individual prompts, NO numbering ("Prompt 1:"). The separator line is the only structure marker. Each prompt is sent verbatim to gpt-image-2 as-is.
"""


def _encode_image(p: Path) -> dict:
    """把图片文件 encode 成 OpenAI chat 多模态 message 的 image_url part。"""
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = p.suffix.lower()
    mime = (
        "image/jpeg" if ext in (".jpg", ".jpeg") else
        "image/webp" if ext == ".webp" else
        "image/gif" if ext == ".gif" else
        "image/png"
    )
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


def _strip_fence(text: str) -> str:
    """剥掉 LLM 偶尔仍包的 ```...``` fence(出现在整段 output 顶层)。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def rewrite(user_prompt: str, reference_images, n: int = 1, model: str = None, verbose: bool = False, anchor_phase: str = None, anchor_idx: int = None) -> list:
    """vision + rewrite。返回 list[str] of length n。

    输入:
      user_prompt: 用户中文需求(也可英文)
      reference_images: list of Path or str(1+ 张参考图)
      n: 期望输出几段独立 prompt(每段对应 1 张图)
      model: 覆盖默认 LM model
      verbose: 打印元信息
      anchor_phase: None (默认,标准 multi-segment mode)
                    "phase1" (anchor workflow Phase 1: 出 M 候选给用户挑;
                              系统层面等同 n=1 标准 rewrite,但 batch_runner 会用这同一段
                              prompt 跑 M 次 sampling 出 M 张候选)
                    "phase3" (anchor workflow Phase 3: reference_images 列表**最后一张**
                              是用户在 Phase 2 挑选的 anchor png,LLM 必须严格锁定 anchor
                              的画风/UI/调色/字体作为系列锚,N-1 段 prompt 每段不同主体角色
                              但视觉风格 ~85% faithful to anchor)

    返回:
      list[str]:
        - n=1: 长度 1 的 list
        - n>=2: 长度 n 的 list,每段独立 self-contained prompt(不同角色 / pose)
        - 如果 LLM 没返回足够段数,fallback 复用最后一段填到 n 个

    特性:
      - 一次 LLM 调用产 N 段(省 vision token)
      - 系列多样性:每段尽量不同角色(rule 5)
      - text 位置严约束:≤5 个/段(rule 2)
      - anchor_phase="phase3" 时强制锁 anchor 风格,避免 N 张系列画风漂
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")

    base_url, api_key = load_credentials()
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=DEFAULT_TIMEOUT_SEC)

    model = model or DEFAULT_LM_MODEL
    ref_paths = [Path(p) for p in reference_images]

    image_contents = [_encode_image(p) for p in ref_paths]

    # 用户消息附加 N 的说明 + 强调多样性
    user_text = user_prompt
    if n > 1:
        user_text += (
            f"\n\n[runner instruction] Produce {n} independent prompts (one per output image), "
            f"separated by `{PROMPT_SEP}` on its own line. Each prompt should feature a DIFFERENT "
            f"primary character drawn from the input images so the {n}-image series gives visual "
            f"variety. Strip any '分别' / '{n} 张' counting words from the input — those tell you "
            f"how many prompts to make, not what to render."
        )
    else:
        user_text += f"\n\n[runner instruction] Produce 1 prompt (single output image)."

    # Anchor workflow Phase 3: caller 指定 anchor_idx (或默认最后一张) — caller 必须保证该 idx 处是 picked anchor
    if anchor_phase == "phase3" and len(ref_paths) >= 1:
        # caller 显式传 anchor_idx 时用 caller 的;不传则默认最后一张(向后兼容)
        if anchor_idx is None:
            anchor_idx = len(ref_paths)
        if anchor_idx < 1 or anchor_idx > len(ref_paths):
            raise ValueError(f"anchor_idx={anchor_idx} 超出 refs 范围 1..{len(ref_paths)}")
        user_text += (
            f"\n\n[ANCHOR LOCK MODE — Phase 3] Image {anchor_idx} (out of {len(ref_paths)} ref images) is the "
            f"user-picked **anchor image** from a Phase 1 candidate round. It represents the LOCKED "
            f"visual style for this entire series — rendering technique, color palette, lighting, UI "
            f"layout, typography, composition language. Your {n} prompts MUST visually echo Image "
            f"{anchor_idx}'s style very tightly (**~85% faithful to anchor instead of the usual 70%**) "
            f"— only the primary character identity / pose / sidekick may vary across prompts. "
            f"All other reference images (not Image {anchor_idx}) provide character source material as before. "
            f"In each prompt, **explicitly write** at the end: "
            f"`Strictly match Image {anchor_idx}'s rendering style, palette, UI plate styling, and typography.` "
            f"The final output series (picked anchor + {n} new images) should look like one coherent "
            f"set, not {n+1} unrelated images."
        )
    elif anchor_phase == "phase1":
        # Phase 1 = 出 M 候选给用户挑;让 LLM 写一段标准 prompt (single hero),
        # batch_runner 用同段 prompt 跑 M 次 sampling 自然出 M 张候选(细节不同)
        user_text += (
            f"\n\n[ANCHOR CANDIDATE MODE — Phase 1] This prompt will be used to generate M candidate "
            f"variants (same prompt × M sampling), letting the user pick the best one as anchor for "
            f"a subsequent Phase 3 series. Write a single well-crafted prompt with concrete character "
            f"choice (don't artificially leave it vague — sampling will provide pose/detail variety)."
        )

    user_content = image_contents + [{"type": "text", "text": user_text}]

    if verbose:
        print(f"  [rewrite] model={model}  refs={len(ref_paths)}  n={n}  user_prompt_chars={len(user_prompt)}", flush=True)

    # reasoning_effort="high" 让 gpt-5.4 系列开思考模式提升 rewrite 质量;
    # 模型 / 代理不支持该参数(旧模型 / 非 reasoning model / ephone 透传配置缺失)时
    # 自动 fallback 到默认调用,保证向后兼容。其他错误(quota / network / 401 等)原样 raise。
    _msgs = [
        {"role": "system", "content": REWRITE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    try:
        response = client.chat.completions.create(
            model=model, messages=_msgs, extra_body={"reasoning_effort": "high"},
        )
    except Exception as _e:
        _emsg = str(_e).lower()
        _unsupported = "reasoning" in _emsg and any(
            s in _emsg for s in ("unknown", "unsupported", "invalid", "bad request", "400")
        )
        if not _unsupported:
            raise
        print(f"  [rewrite] WARN: model={model} rejects reasoning_effort='high'; retrying without it", file=sys.stderr, flush=True)
        response = client.chat.completions.create(model=model, messages=_msgs)
    text = _strip_fence(response.choices[0].message.content or "")

    if n == 1:
        if not text:
            raise RuntimeError("rewrite LLM returned empty output for n=1")
        return [_wrap_with_sentinel(text)]

    # n>=2: 用 PROMPT_SEP 拆。容忍 leading/trailing whitespace + 可能的 "Prompt 1:" prefix
    raw_parts = [p.strip() for p in text.split(PROMPT_SEP)]
    parts = [_strip_fence(p) for p in raw_parts if p.strip()]

    # Invariant: rewrite 输出必须能拆出 ≥1 段。空 / 无 SEP → raise(不 fallback 到原中文)。
    if len(parts) == 0:
        snippet = (text[:200] + "...") if text else "(empty)"
        raise RuntimeError(
            f"rewrite LLM produced unparseable output (no PROMPT_SEP for n={n}>=2): {snippet!r}"
        )

    if len(parts) < n:
        print(f"  [rewrite] WARN: expected {n} prompts, got {len(parts)}; padding with last", file=sys.stderr, flush=True)
        while len(parts) < n:
            parts.append(parts[-1])
    elif len(parts) > n:
        print(f"  [rewrite] WARN: expected {n} prompts, got {len(parts)}; truncating", file=sys.stderr, flush=True)
        parts = parts[:n]

    return [_wrap_with_sentinel(p) for p in parts]


def main():
    ap = argparse.ArgumentParser(description="rewrite 中文需求 + 参考图 → N 段英文 image-gen prompt")
    ap.add_argument("--user-prompt-file", required=True, help="中文需求 prompt 文件")
    ap.add_argument("--refs", required=True, help="comma-separated reference image paths (1+ 张)")
    ap.add_argument("--out", required=True, help="输出文件(N 段用 `---PROMPT-SEP---` 分隔)")
    ap.add_argument("--n", type=int, default=1, help="期望输出几段独立 prompt(默认 1)")
    ap.add_argument("--model", default=None, help=f"override LM model (default: {DEFAULT_LM_MODEL})")
    ap.add_argument("--anchor-phase", default=None, choices=[None, "phase1", "phase3"],
                    help="anchor workflow mode: phase1 (候选生成) / phase3 (anchor 锁风格,refs 最后一张是 picked anchor)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    user_prompt = Path(args.user_prompt_file).read_text(encoding="utf-8")
    refs = [Path(p) for p in args.refs.split(",")]
    for p in refs:
        if not p.exists():
            print(f"! ref image not found: {p}", file=sys.stderr)
            return 2

    try:
        prompts = rewrite(user_prompt, refs, n=args.n, model=args.model, verbose=args.verbose, anchor_phase=args.anchor_phase)
    except Exception as e:
        print(f"! rewrite failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out_text = (f"\n{PROMPT_SEP}\n").join(prompts) if args.n > 1 else prompts[0]
    Path(args.out).write_text(out_text, encoding="utf-8")
    print(f"OK: wrote {args.out} ({len(out_text)} chars, {len(prompts)} prompts)")
    if args.verbose:
        for i, p in enumerate(prompts, 1):
            print(f"--- prompt {i}/{len(prompts)} (first 200 chars) ---")
            print(p[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

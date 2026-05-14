#!/usr/bin/env python
"""
game-ad-imagegen / rewrite_prompt.py — vision + rewrite 模块
============================================================

把"用户中文需求 + 参考图"→"英文 image-gen prompt"（按 SKILL.md Step 4 T9 模板）。
由 batch_runner.py 内部调用，让 Mode 2 (form / 跑批) 也能享受 skill 的核心 rewrite 能力，
不再要求用户自己提前 rewrite 好再填表。

调用方式：
  CLI:
    python rewrite_prompt.py --user-prompt-file in.txt --refs r1.png,r2.jpg --out rewritten.txt

  模块：
    from rewrite_prompt import rewrite
    english = rewrite(user_prompt="...中文...", reference_images=[Path("r1.png"), ...])

内部：调 ephone /v1/chat/completions（OpenAI SDK + base_url 重定向），model = DEFAULT_LM_MODEL。
多模态消息格式（image_url base64 data URL）。
"""
import argparse
import base64
import sys
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
from _config import DEFAULT_LM_MODEL, DEFAULT_TIMEOUT_SEC, load_credentials


REWRITE_SYSTEM = """You are the prompt-rewriting agent inside the `game-ad-imagegen` skill.

USER GIVES YOU:
- One or more reference images, numbered Image 1, Image 2, ... in the order provided
- A Chinese natural-language request describing the desired output

YOUR JOB: produce ONE detailed English image-generation prompt for gpt-image-2 (/v1/images/edits).
The text you output is sent verbatim to gpt-image-2 — no preamble, no markdown fences, no explanation.

== STEP A: Vision verify each image (silently) ==
For each input image, internally note:
- key_visuals (subject, pose, composition, UI elements, visible Chinese text verbatim)
- style_summary (rendering style, palette, mood)
- role in the request (composition anchor / character source / UI template / text-edit target / etc.) — infer from content + user's wording (e.g. "图1 = 构图参考" maps to "Image 1 acts as composition anchor")

== STEP B: Pick characters (only if request needs them) ==
Pick from what you actually saw in the user's images. Do NOT invent characters not visually traceable to any input image.

== STEP C: Write the English prompt using this skeleton ==

```
Create a polished {orientation} {asset type} in {WxH}, aspect ratio {ratio}.
Image 1 (<role>): <what Image 1 actually shows — concise visual description>.
Image 2 (<role>): <what Image 2 actually shows>.
{... one line per reference image ...}

Design a brand-new composition echoing the style anchor image's visual language while
adapting to {orientation/ratio}. Keep about 70% faithful, 30% creative.

Main content requirements:
- <Central hero: visual description + pose, traced to specific input image>
- <Background: atmosphere + key props>
- Large stylized title at top: "<verbatim Chinese title>"
- Main promotional banner: "<verbatim Chinese promo line>"
- <Optional speech bubble>: "<verbatim Chinese>"
- <Optional small inset / stamp>: "<verbatim Chinese>"

Quality and style requirements:
- All Chinese text rendered crisply and readably DIRECTLY in the image.
- Do NOT leave any text container blank / use placeholder pseudo-Chinese / use English subtitles.
- No raw screenshot artifacts / phone UI / FPS overlay / watermarks / app-store badges / blank text containers.
- <Polished commercial finish, style notes from your vision StyleSummary>.
- {Orientation} composition only, {WxH}.
```

== CRITICAL RULES ==
1. Single hero focus: 1 main character + ≤2 supporting elements (inset / sidekick). NEVER write multi-panel / split-screen / N-grid / collage / "5 panels showing..." — that wrecks text rendering and produces collage-style output.
2. Strip batch-control language from user's Chinese: words like "分别"/"5张"/"做N张"/"each" are about how many independent images to generate — they are NOT visual instructions. Output a single-image description regardless of how many the user asked for; the batch runner handles N via independent sampling.
3. Every visible text region must specify either:
   (a) verbatim Chinese characters in double-quotes, OR
   (b) explicit "leave this position empty / no text here"
   Never leave text regions unconstrained — gpt-image-2 will fill blanks with hallucinated Chinese.
4. Style words come from your vision (e.g. `polished 2D illustration`, `semi-realistic painterly CG`, `stylized concept art`) — never from genre stereotypes / training-prior assumptions about the franchise.
5. 4-7 explicit Chinese text positions total (title + main CTA + optional speech bubble + small caption / stamp). <3 looks empty; >7 dilutes text rendering budget.
6. Output ONLY the English prompt as plain text. NO ```fences```, NO preamble like "Here is the prompt:", NO explanation. The full text you output is forwarded verbatim to gpt-image-2.
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


def rewrite(user_prompt: str, reference_images, model: str = None, verbose: bool = False) -> str:
    """vision + rewrite。

    输入:
      user_prompt: 用户中文需求(也可英文,会过 rewrite 整理)
      reference_images: list of Path or str(1+ 张参考图路径)
      model: 覆盖默认 LM model
      verbose: 打印 rewrite 元信息

    返回: 纯英文 image-gen prompt(无 markdown fences)
    """
    base_url, api_key = load_credentials()
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=DEFAULT_TIMEOUT_SEC)

    model = model or DEFAULT_LM_MODEL
    ref_paths = [Path(p) for p in reference_images]

    image_contents = [_encode_image(p) for p in ref_paths]
    user_content = image_contents + [{"type": "text", "text": user_prompt}]

    if verbose:
        print(f"  [rewrite] model={model}  refs={len(ref_paths)}  user_prompt_chars={len(user_prompt)}", flush=True)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    )
    text = (response.choices[0].message.content or "").strip()

    # 防御：剥掉模型偶尔仍包的 ```...``` fence
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # 去掉首行 ```...
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return text


def main():
    ap = argparse.ArgumentParser(description="rewrite 中文需求 + 参考图 → 英文 image-gen prompt")
    ap.add_argument("--user-prompt-file", required=True, help="中文需求 prompt 文件")
    ap.add_argument("--refs", required=True, help="comma-separated reference image paths (1+ 张)")
    ap.add_argument("--out", required=True, help="输出英文 prompt 写到这个文件")
    ap.add_argument("--model", default=None, help=f"override LM model (default: {DEFAULT_LM_MODEL})")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    user_prompt = Path(args.user_prompt_file).read_text(encoding="utf-8")
    refs = [Path(p) for p in args.refs.split(",")]
    for p in refs:
        if not p.exists():
            print(f"! ref image not found: {p}", file=sys.stderr)
            return 2

    try:
        english = rewrite(user_prompt, refs, model=args.model, verbose=args.verbose)
    except Exception as e:
        print(f"! rewrite failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    Path(args.out).write_text(english, encoding="utf-8")
    print(f"OK: wrote {args.out} ({len(english)} chars)")
    if args.verbose:
        print("--- preview ---")
        print(english[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

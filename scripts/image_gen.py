#!/usr/bin/env python
"""
game-ad-imagegen / image_gen.py - 单图生成（recipe 配方版 2026-05-12）
============================================

调用方式：

  CLI:
    python image_gen.py --prompt-file rewritten.txt --refs ref1.png,ref2.png --out out.png

  作模块：
    from image_gen import generate
    result = generate(
        rewritten_prompt="...",
        reference_images=[Path("ref1.png"), Path("ref2.png")],
        out_path=Path("out.png"),
    )
    # result = {"out_path": ..., "revised_prompt": None, "usage": ..., "http_status": 200}

内部：调 ephone /v1/images/edits（OpenAI SDK 标准 + base_url 重定向到 ephone）。
直接 gpt-image-2，**绕开 L2 server-side rewriter**（避错字 + 风格塌）。
注入 QUALITY_INVARIANTS 到 prompt 开头。

改造说明：原 /v1/responses + image_generation tool 路径在长 prompt 场景下被 L2 简化 / 翻译，
破坏中文 text verbatim + 多 panel 设计。recipe 调研确认 /v1/images/edits 不经 L2，输出稳定。
"""
import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Optional

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
from _config import (
    DEFAULT_IMG_SIZE, DEFAULT_IMG_QUALITY,
    DEFAULT_TIMEOUT_SEC, QUALITY_INVARIANTS, load_credentials,
)

DEFAULT_IMG_MODEL = "gpt-image-2"  # /v1/images/edits 实际 model（不再用 gpt-5.5 作 L2）

# Must match SENTINEL in rewrite_prompt.py — proof-of-origin marker that the prompt
# was produced by rewrite_prompt.py rather than typed by an agent / pasted by user.
SENTINEL = "# REWRITTEN-V1"


def _assert_and_strip_sentinel(prompt: str) -> str:
    """Hard rail #1 (strong invariant): the prompt MUST start with SENTINEL marker,
    proving it came from rewrite_prompt.py. Missing marker → caller didn't rewrite
    (agent偷懒手写 / 用户直接粘贴 / 拼音蒙混 / 任何其他来源) → raise. After
    verification, strip the marker line so it does not leak into the image API."""
    stripped = prompt.lstrip()
    if not stripped.startswith(SENTINEL):
        raise RuntimeError(
            f"prompt invariant violation: missing sentinel marker {SENTINEL!r} on first "
            f"non-blank line. Every prompt sent to the image API MUST be produced by "
            f"rewrite_prompt.py (which prepends this marker). first 200 chars: {prompt[:200]!r}"
        )
    nl = stripped.find('\n')
    if nl == -1:
        return ""
    return stripped[nl + 1:]


def _assert_prompt_is_rewritten(prompt: str) -> None:
    """Hard rail: 进 image API 的 prompt 必须是 rewrite_prompt.py 产出的英文版本。
    raw 中文 user prompt 直接进 gpt-image-2 会塌(case_22 / case_24)。
    rewritten prompt 里 verbatim text positions 用 quote 包,CJK 占比远低于 10%。"""
    cjk = sum(1 for c in prompt if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
    if cjk == 0:
        return
    total = sum(1 for c in prompt if not c.isspace())
    if total == 0 or cjk / total <= 0.10:
        return
    raise RuntimeError(
        f"prompt invariant violation: {cjk} CJK chars / {total} non-ws ({cjk/total:.0%}); "
        f"refusing raw Chinese prompt at image API. Caller must pre-rewrite via "
        f"rewrite_prompt.py. first 200 chars: {prompt[:200]!r}"
    )


def generate(
    rewritten_prompt: str,
    reference_images: list[Path],
    out_path: Path,
    *,
    size: str = DEFAULT_IMG_SIZE,
    quality: str = DEFAULT_IMG_QUALITY,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    inject_quality_invariants: bool = True,
) -> dict:
    """
    单图生成（recipe 版 — /v1/images/edits 直走 gpt-image-2，无 L2 rewriter）。

    参数：
      rewritten_prompt: 已经过 LLM rewriting 的英文 prompt（详细字段化版本）
      reference_images: 参考图列表（第 1 张 = Image A 风格 anchor，其余 = 角色/资产源）
      out_path: 输出 PNG 路径

    返回 dict 含：out_path / revised_prompt(=None) / usage / http_status
    """
    rewritten_prompt = _assert_and_strip_sentinel(rewritten_prompt)
    _assert_prompt_is_rewritten(rewritten_prompt)
    base_url, api_key = load_credentials()

    # 构造最终 prompt（QUALITY_INVARIANTS 注入到开头，跟原路径同语义）
    prompt_parts = []
    if inject_quality_invariants:
        prompt_parts.append(QUALITY_INVARIANTS.strip())
    prompt_parts.append(rewritten_prompt)
    final_prompt = "\n\n".join(prompt_parts)

    print(f"  POST {base_url.rstrip('/')}/images/edits  model={DEFAULT_IMG_MODEL}  size={size}  quality={quality}  refs={len(reference_images)}")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_sec)

    result = {
        "out_path": str(out_path),
        "http_status": None,
        "img_model": DEFAULT_IMG_MODEL,
        "size": size,
        "quality": quality,
        "reference_image_count": len(reference_images),
        "revised_prompt": None,  # /v1/images/edits 无 L2 rewriter
    }

    # 打开参考图 file handles（list of 1+）
    file_handles = [open(p, "rb") for p in reference_images]
    try:
        try:
            response = client.images.edit(
                model=DEFAULT_IMG_MODEL,
                image=file_handles if len(file_handles) > 1 else file_handles[0],
                prompt=final_prompt,
                size=size,
                quality=quality,
                n=1,
            )
            result["http_status"] = 200
        except Exception as exc:
            result["http_status"] = getattr(exc, "status_code", 500)
            result["error_body"] = str(exc)[:5000]
            raise RuntimeError(f"image_gen edit failed: {exc}") from exc
    finally:
        for f in file_handles:
            try:
                f.close()
            except Exception:
                pass

    # usage 字段（SDK 可能不暴露，best-effort）
    try:
        result["usage"] = response.usage.model_dump() if hasattr(response, "usage") and response.usage else None
    except Exception:
        result["usage"] = None

    if not response.data:
        raise RuntimeError("image_gen 返回但 data 为空")
    img_b64 = response.data[0].b64_json
    if not img_b64:
        raise RuntimeError("image_gen 返回但无 b64_json")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(img_b64))
    print(f"  -> {out_path} ({out_path.stat().st_size / 1024:.0f} KB)")

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", help="rewritten prompt 字符串（直接传）")
    ap.add_argument("--prompt-file", help="prompt 文件路径（推荐，避免 shell 转义）")
    ap.add_argument("--refs", required=True, help="参考图路径，逗号分隔")
    ap.add_argument("--out", required=True, help="输出 PNG 路径")
    ap.add_argument("--meta-out", help="meta JSON 输出路径（可选）")
    ap.add_argument("--size", default=DEFAULT_IMG_SIZE, help=f"输出尺寸，默认 {DEFAULT_IMG_SIZE}")
    ap.add_argument("--quality", default=DEFAULT_IMG_QUALITY, help="质量")
    ap.add_argument("--no-invariants", action="store_true", help="禁用 QUALITY_INVARIANTS 注入（调试用）")
    args = ap.parse_args()

    if args.prompt_file:
        rewritten_prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    elif args.prompt:
        rewritten_prompt = args.prompt
    else:
        ap.error("必须传 --prompt 或 --prompt-file")

    refs = [Path(p.strip()) for p in args.refs.split(",") if p.strip()]
    for r in refs:
        if not r.exists():
            ap.error(f"参考图不存在：{r}")

    result = generate(
        rewritten_prompt=rewritten_prompt,
        reference_images=refs,
        out_path=Path(args.out),
        size=args.size,
        quality=args.quality,
        inject_quality_invariants=not args.no_invariants,
    )

    if args.meta_out:
        Path(args.meta_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  meta -> {args.meta_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

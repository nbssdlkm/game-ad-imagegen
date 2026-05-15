#!/usr/bin/env python
"""
⚠️ [DEPRECATED v0.1.3 2026-05-15] image_gen_batch.py — 早期 first-anchor 批量入口
================================================================================

本脚本是 v0.1.0 早期的"first-anchor 策略"批量入口,功能跟新的 anchor mode 概念重叠。

**改用新路径**(全功能等价 + UI + invariant 保护):
  - 简单批量:    `scripts/batch_runner.py` + `web/batch_form.html` (HTML 表单驱动)
  - Anchor 锁风格系列广告: 同上 + form 设 anchor_candidates >= 2 (Phase 1/2/3 自动跑)

本脚本保留只为向后兼容(已有 caller 不动),不再加新功能。**调用时会向 stderr 打 DEPRECATED warning**。
calls into `image_gen.generate()` 仍走完整 invariant 闸(SENTINEL + CJK),不破 invariant。

================================================================================
原 docstring:

模拟 T8 操作流程的"第二步 batch_requests":
  1. 第一段 prompt 调 image_gen.py → 拿到第 1 张图 + revised_prompt
  2. 第二段~第 N 段 prompt:每段调 image_gen.py,参考图 = 原参考图 + 第 1 张图(anchor)
  3. 这样 N-1 张共享同一系列锚点,视觉风格更一致

调用:
  python image_gen_batch.py --config batch.json

batch.json 格式:
  {
    "rewritten_prompts": ["...第1段...", "...第2段...", ...],
    "reference_images": ["ref1.png", "ref2.png"],
    "out_dir": "outputs/runX",
    "out_basename": "ad",          # 可选,默认 ad
    "anchor_strategy": "first",    # first / none
    "size": "1536x1024",           # 可选,默认合法横版尺寸
    "quality": "high"              # 可选
  }
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from image_gen import generate
from _config import DEFAULT_IMG_SIZE, DEFAULT_IMG_QUALITY


def run_batch(
    rewritten_prompts: list[str],
    reference_images: list[Path],
    out_dir: Path,
    *,
    out_basename: str = "ad",
    anchor_strategy: str = "first",
    size: str = DEFAULT_IMG_SIZE,
    quality: str = DEFAULT_IMG_QUALITY,
) -> list[dict]:
    """
    批量生成 N 张图。

    anchor_strategy:
      "first": 第 1 张作 anchor，第 2 张起每次把第 1 张追加到 reference_images
      "none":  N 张独立调用，无 anchor

    返回 N 个 result dict 的列表
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    anchor_path = None

    for i, prompt in enumerate(rewritten_prompts, 1):
        out_path = out_dir / f"{out_basename}_{i:02d}.png"

        # 决定本次调用的参考图
        refs = list(reference_images)
        if anchor_strategy == "first" and anchor_path is not None:
            refs.append(anchor_path)

        print(f"\n=== 第 {i}/{len(rewritten_prompts)} 张 ===")
        t0 = time.time()
        try:
            result = generate(
                rewritten_prompt=prompt,
                reference_images=refs,
                out_path=out_path,
                size=size,
                quality=quality,
            )
        except Exception as e:
            print(f"  ! 第 {i} 张失败：{e}")
            result = {
                "out_path": str(out_path),
                "error": str(e),
                "http_status": None,
            }
            results.append(result)
            continue

        result["seq"] = i
        result["elapsed_sec"] = round(time.time() - t0, 1)
        result["used_anchor"] = (anchor_strategy == "first" and i > 1)
        results.append(result)

        # 第一张成功 → 当 anchor
        if anchor_strategy == "first" and i == 1 and result.get("http_status") == 200:
            anchor_path = out_path

    return results


def main():
    print(
        "⚠️ [DEPRECATED v0.1.3] image_gen_batch.py is deprecated. "
        "Use `scripts/batch_runner.py` + `web/batch_form.html` instead (anchor mode UI built-in). "
        "This script will be removed in v0.2.",
        file=sys.stderr,
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="batch JSON 配置路径")
    ap.add_argument("--meta-out", help="批量 meta JSON 输出路径（默认 out_dir/_batch_meta.json）")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    rewritten_prompts = cfg["rewritten_prompts"]
    reference_images = [Path(p) for p in cfg["reference_images"]]
    out_dir = Path(cfg["out_dir"])
    out_basename = cfg.get("out_basename", "ad")
    anchor_strategy = cfg.get("anchor_strategy", "first")
    size = cfg.get("size", DEFAULT_IMG_SIZE)
    quality = cfg.get("quality", DEFAULT_IMG_QUALITY)

    for r in reference_images:
        if not r.exists():
            ap.error(f"参考图不存在：{r}")

    print(f"=== batch run: {len(rewritten_prompts)} prompts ===")
    print(f"  refs: {[str(r) for r in reference_images]}")
    print(f"  out_dir: {out_dir}")
    print(f"  anchor_strategy: {anchor_strategy}")

    results = run_batch(
        rewritten_prompts=rewritten_prompts,
        reference_images=reference_images,
        out_dir=out_dir,
        out_basename=out_basename,
        anchor_strategy=anchor_strategy,
        size=size,
        quality=quality,
    )

    meta = {
        "config": cfg,
        "results": results,
        "ok_count": sum(1 for r in results if r.get("http_status") == 200),
        "total": len(results),
    }
    meta_path = Path(args.meta_out) if args.meta_out else (out_dir / "_batch_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== batch done: {meta['ok_count']}/{meta['total']} ===")
    print(f"  meta -> {meta_path}")


if __name__ == "__main__":
    main()

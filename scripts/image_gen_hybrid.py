"""
image_gen_hybrid.py (v3) — hybrid path image generator

调用形态:
  POST /v1/responses + tools=[{type:image_generation, quality, size}]
  model 默认 gpt-5.4 + reasoning_effort=medium

输入 prompt 来源 (二选一):
  (a) rewriter 输出 (含 SENTINEL `# REWRITTEN-CN-V2` header) — 推荐主路径
  (b) raw user prompt — 兜底,会调 sanitize_raw_user_prompt() 做 light cleanup

CLI:
  python image_gen_hybrid.py --prompt-file rewritten.txt --refs r1.png,r2.png --out out.png --meta-out out_meta.json
  默认 --size 2048x1152 --quality high (codex-aligned defaults; ephone tool 要求 ÷16)

输出 PNG + meta.json (含 actual_size / revised_prompt / status / usage / sanitize_info)
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from prompt_sanitize import (  # noqa: E402
    SENTINEL, validate_rewritten,
    sanitize_post_rewrite, sanitize_raw_user_prompt,
)

DEFAULT_MODEL = "gpt-5.4"
DEFAULT_REASONING = "medium"


def _load_credentials() -> tuple[str, str]:
    """ephone env-based credentials, friendly error if missing."""
    key = os.environ.get("EPHONE_API_KEY")
    if not key:
        raise SystemExit(
            "EPHONE_API_KEY 未设置。请在系统 env 配置:\n"
            "  Windows: setx EPHONE_API_KEY \"sk-...\"  (重开终端生效)\n"
            "  Linux/Mac: export EPHONE_API_KEY=\"sk-...\""
        )
    base = os.environ.get("EPHONE_BASE_URL", "https://api.ephone.ai")
    if not base.endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    return base, key


def _encode_image(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def _build_image_input(p: Path) -> dict:
    ext = p.suffix.lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
    return {"type": "input_image", "image_url": f"data:{mime};base64,{_encode_image(p)}"}


def _read_actual_size(png_path: Path) -> tuple[int, int] | None:
    """PIL 读 PNG actual size。失败返 None (PIL 缺失 OR 文件损坏)。"""
    try:
        from PIL import Image
        with Image.open(png_path) as im:
            return im.size  # (width, height)
    except Exception:
        return None


def call_responses(prompt_cn: str, refs: list[Path], model: str,
                   size: str, quality: str, reasoning_effort: str) -> dict:
    base_url, api_key = _load_credentials()
    image_inputs = [_build_image_input(p) for p in refs]
    body = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt_cn}, *image_inputs]}],
        "tools": [{"type": "image_generation", "quality": quality, "size": size}],
        "tool_choice": {"type": "image_generation"},
        "reasoning": {"effort": reasoning_effort},
    }
    r = requests.post(
        f"{base_url}/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body, timeout=600,
    )
    return {
        "http_status": r.status_code,
        "raw": r.text if r.status_code != 200 else None,
        "json": r.json() if r.status_code == 200 else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--refs", default="", help="逗号分隔的参考图路径,可空")
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta-out", required=True)
    ap.add_argument("--size", default="2048x1152", help="default 16:9 (ephone tool 要求 ÷16)")
    ap.add_argument("--quality", default="high", help="codex-aligned default")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--reasoning-effort", default=DEFAULT_REASONING, choices=["low", "medium", "high"])
    ap.add_argument("--no-invariants", action="store_true", help="(legacy, hybrid 无 invariants)")
    args = ap.parse_args()

    prompt_raw = Path(args.prompt_file).read_text(encoding="utf-8")
    refs = [Path(p.strip()) for p in args.refs.split(",") if p.strip()]
    for r in refs:
        if not r.exists():
            print(f"! ref not found: {r}", file=sys.stderr)
            return 2

    # ============================================================
    # Sanitize: 二选一入口
    # ============================================================
    if validate_rewritten(prompt_raw):
        cleaned, sanitize_info = sanitize_post_rewrite(prompt_raw)
        print(f"=== sanitize (post-rewrite): SENTINEL ✓ ===", flush=True)
    else:
        cleaned, sanitize_info = sanitize_raw_user_prompt(prompt_raw)
        print(f"=== sanitize (raw user prompt, no SENTINEL): light cleanup applied ===", flush=True)

    # ============================================================
    # Size override: sanitize 提取的 size 优先 CLI default
    # ============================================================
    effective_size = sanitize_info.get("size") or args.size
    if sanitize_info.get("size") and sanitize_info["size"] != args.size:
        print(f"  ⚠ size override: CLI {args.size} → prompt-extracted {effective_size} ({sanitize_info.get('size_source')})", flush=True)

    print(f"=== sanitize info: {sanitize_info} ===", flush=True)
    print(f"=== cleaned prompt ({len(cleaned)} chars):\n{cleaned[:500]}{'...' if len(cleaned) > 500 else ''}\n===", flush=True)

    t0 = time.time()
    print(f"=> POST /responses model={args.model} refs={len(refs)} size={effective_size} quality={args.quality} reasoning={args.reasoning_effort}", flush=True)
    res = call_responses(cleaned, refs, args.model, effective_size, args.quality, args.reasoning_effort)
    elapsed = round(time.time() - t0, 1)
    print(f"<= HTTP {res['http_status']} elapsed={elapsed}s", flush=True)

    out_path = Path(args.out)
    meta_path = Path(args.meta_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "hybrid": True,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "elapsed_sec": elapsed,
        "http_status": res["http_status"],
        "size_requested": effective_size,
        "quality_requested": args.quality,
        "n_refs": len(refs),
        "prompt_chars": len(cleaned),
        "sanitize_info": sanitize_info,
    }

    if res["http_status"] != 200:
        meta["error"] = res["raw"][:2000] if res["raw"] else "non-200 no body"
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"! HTTP {res['http_status']}: {res['raw'][:800]}", file=sys.stderr)
        return 1

    data = res["json"]
    output_items = data.get("output", [])
    meta["response_output_types"] = [o.get("type") for o in output_items]
    meta["usage"] = data.get("usage")

    # ============================================================
    # Status branch handling (codex reviewer 提的 P2 fix)
    # ============================================================
    image_call = None
    for item in output_items:
        if item.get("type") == "image_generation_call":
            image_call = item
            break

    if not image_call:
        meta["error"] = f"no image_generation_call in response output"
        meta["raw_output_preview"] = json.dumps(output_items[:2], ensure_ascii=False)[:1500]
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"! no image_generation_call in response. output types: {meta['response_output_types']}", file=sys.stderr)
        return 1

    # capture revised_prompt + status + safety_reason 等 metadata
    meta["image_call_status"] = image_call.get("status")
    meta["revised_prompt"] = image_call.get("revised_prompt")  # 这是 image_gen tool 内部 revise 后的 prompt — 网页版品质好的关键!
    meta["image_call_size"] = image_call.get("size")
    meta["image_call_quality"] = image_call.get("quality")
    meta["image_call_output_format"] = image_call.get("output_format")
    if image_call.get("safety_reason"):
        meta["safety_reason"] = image_call["safety_reason"]

    status = image_call.get("status")
    result_b64 = image_call.get("result")

    if status != "completed":
        meta["error"] = f"image_generation_call.status={status} (期望 'completed')"
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"! image_call.status={status}, result={'有' if result_b64 else '空'}", file=sys.stderr)
        if not result_b64:
            return 1
        # 即使 status 异常但有 result,降级保存(soft success)
        print(f"  ⚠ soft success: status 异常但 result 非空,保存图片继续", flush=True)

    if not result_b64:
        meta["error"] = f"image_call.result 为空 (status={status})"
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"! no image result in response", file=sys.stderr)
        return 1

    # 写 PNG
    out_path.write_bytes(base64.b64decode(result_b64))
    meta["size_bytes"] = out_path.stat().st_size

    # PIL 读 actual_size (验证 tool 是否真 honor size 参数)
    actual = _read_actual_size(out_path)
    if actual:
        meta["actual_size"] = f"{actual[0]}x{actual[1]}"
        if meta["actual_size"] != effective_size:
            print(f"  ⚠ actual_size {meta['actual_size']} ≠ requested {effective_size}", flush=True)

    # Post-resize: 让 user 拿到他要的尺寸 (ephone ÷16 round 或 upscale 出来的尺寸跟 user 要的不一致)
    # 两种 case:
    #   (a) Round case (1920x1080 → ephone 1920x1088): actual ≥ target → PIL center crop
    #   (b) Upscale case (650x250 → ephone 2624x1024): actual > target on both axes → PIL LANCZOS resize
    target_size = sanitize_info.get("target_size")
    if target_size and meta.get("actual_size") and target_size != meta["actual_size"]:
        try:
            from PIL import Image
            tw, th = (int(x) for x in target_size.split("x"))
            with Image.open(out_path) as im:
                aw, ah = im.size
                # 判断 case: aspect ratio 跟 target 接近 (差 < 5%) → upscale case 用 resize
                # 否则 → round case 用 center crop
                target_ratio = tw / th
                actual_ratio = aw / ah
                ratio_diff = abs(target_ratio - actual_ratio) / target_ratio
                if ratio_diff < 0.05 and (aw > tw * 1.5 or ah > th * 1.5):
                    # upscale case: 比例一致 + actual 比 target 大 1.5x 以上 → LANCZOS downsize
                    im.resize((tw, th), Image.LANCZOS).save(out_path)
                    meta["post_resized"] = {"from": meta["actual_size"], "to": target_size, "method": "LANCZOS"}
                    print(f"  ↘ post-resize {aw}x{ah} → {target_size} (LANCZOS, 保 user 期望 size)", flush=True)
                elif aw >= tw and ah >= th:
                    # round case: center crop
                    left = (aw - tw) // 2
                    top = (ah - th) // 2
                    im.crop((left, top, left + tw, top + th)).save(out_path)
                    meta["post_cropped"] = {"from": meta["actual_size"], "to": target_size}
                    print(f"  ✂ post-crop {aw}x{ah} → {target_size} (居中)", flush=True)
                else:
                    print(f"  ⚠ target {target_size} 跟 actual {aw}x{ah} 不兼容, skip", flush=True)
                    target_size = None  # 留 actual_size 不变
                if target_size:
                    meta["actual_size"] = target_size
                    meta["size_bytes"] = out_path.stat().st_size
        except Exception as e:
            print(f"  ⚠ post-resize 失败 (忽略,保留原图): {type(e).__name__}: {e}", flush=True)

    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK -> {out_path} ({meta['size_bytes']//1024} KB, actual={meta.get('actual_size','?')})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

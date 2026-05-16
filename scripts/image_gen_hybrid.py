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
from _credentials import load_credentials as _load_credentials, CredentialsError  # noqa: E402

DEFAULT_MODEL = "gpt-5.4"
DEFAULT_REASONING = "medium"


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


_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
_RETRY_BACKOFF_SEC = [5, 15]  # 2 次 retry,5s 后 + 15s 后. image gen 单调用 ¥0.1+ 不浪费


def call_responses(prompt_cn: str, refs: list[Path], model: str,
                   size: str, quality: str, reasoning_effort: str) -> dict:
    """POST /v1/responses + image_generation tool. 含 transient retry (502/503/504/429/500
    或 network error). 单图成本 ¥0.1+, 不能因为一次瞬时网络抖动直接放弃."""
    base_url, api_key = _load_credentials()
    image_inputs = [_build_image_input(p) for p in refs]
    body = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt_cn}, *image_inputs]}],
        "tools": [{"type": "image_generation", "quality": quality, "size": size}],
        "tool_choice": {"type": "image_generation"},
        "reasoning": {"effort": reasoning_effort},
    }
    url = f"{base_url}/responses"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    attempts = [0] + _RETRY_BACKOFF_SEC  # [first try, +5s, +15s] = 共 3 次尝试
    last_exc = None
    last_resp = None
    for attempt_idx, sleep_before in enumerate(attempts):
        if sleep_before > 0:
            print(f"  ⟳ retry attempt {attempt_idx + 1}/{len(attempts)} after {sleep_before}s backoff...", flush=True)
            time.sleep(sleep_before)
        try:
            r = requests.post(url, headers=headers, json=body, timeout=600)
            if r.status_code == 200:
                return {"http_status": 200, "raw": None, "json": r.json(), "retry_count": attempt_idx}
            last_resp = r
            if r.status_code not in _TRANSIENT_STATUSES:
                # non-transient (400/401/403/404 等) → 直接返回不 retry
                break
            print(f"  ⚠ HTTP {r.status_code} (transient), 准备 retry...", flush=True)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            print(f"  ⚠ network error ({type(e).__name__}: {e}), 准备 retry...", flush=True)
    # 全部失败
    if last_resp is not None:
        return {"http_status": last_resp.status_code, "raw": last_resp.text, "json": None,
                "retry_count": len(attempts) - 1}
    # network exception 用尽 retry
    return {"http_status": None, "raw": f"network exception: {type(last_exc).__name__}: {last_exc}",
            "json": None, "retry_count": len(attempts) - 1}


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
    ap.add_argument("--no-invariants", action="store_true",
                    help="(legacy CLI flag for backward compat with batch_runner / main-branch image_gen.py; "
                         "hybrid 无 QUALITY_INVARIANTS 注入机制, flag noop)")
    args = ap.parse_args()
    if args.no_invariants:
        print("  ⚠ --no-invariants 是 legacy flag (兼容 main 分支 image_gen.py), hybrid 无 invariants 注入机制, noop", flush=True)

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
    # 只在 size 值真不等时才报 override (避免 CLI 给 2048x1152 + prompt 也提到 2048x1152 时无谓 warn)
    # ============================================================
    extracted = sanitize_info.get("size")
    effective_size = extracted or args.size
    if extracted and extracted != args.size:
        print(f"  ⚠ size override: CLI {args.size} → prompt-extracted {extracted} ({sanitize_info.get('size_source')})", flush=True)

    print(f"=== sanitize info: {sanitize_info} ===", flush=True)
    # prompt 预览: hybrid 中文 structured prompt 常 1500+ chars, 截 500 看不到约束段, 改 1500
    print(f"=== cleaned prompt ({len(cleaned)} chars):\n{cleaned[:1500]}{'...' if len(cleaned) > 1500 else ''}\n===", flush=True)

    t0 = time.time()
    print(f"=> POST /responses model={args.model} refs={len(refs)} size={effective_size} quality={args.quality} reasoning={args.reasoning_effort}", flush=True)
    try:
        res = call_responses(cleaned, refs, args.model, effective_size, args.quality, args.reasoning_effort)
    except CredentialsError as e:
        print(f"! credentials missing:\n{e}", file=sys.stderr)
        return 2
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
        if not result_b64:
            # hard fail: 既无完成 status 也无 result
            meta["error"] = f"image_generation_call.status={status} (期望 'completed') + result 为空"
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"! image_call.status={status}, result=空", file=sys.stderr)
            return 1
        # soft success: status 异常但有 result. 写 warning (不写 error, 避免下游 batch_runner
        # 既看到 meta.error 又看到 rc=0 + PNG 落盘的契约冲突)
        meta["warning"] = f"image_generation_call.status={status} (期望 'completed') 但 result 非空, soft success 保存"
        print(f"  ⚠ soft success: status={status} 但 result 非空, 保存图片继续 (meta.warning 记录)", flush=True)

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
                # 比例差 <5% (容差 ÷16 round 引入的微小比例偏移) + 某轴超 target 1.5x
                # → 判定为 upscale case (sub-655K user request 被 ephone 放大到 ≥1024 短边),
                # 走 LANCZOS downsize 回 user 期望. 比例差 >5% 或者尺寸跟 target 接近
                # → 走 round case, center crop (跟 ÷16 round 引入的小幅尺寸差兼容)
                if ratio_diff < 0.05 and (aw > tw * 1.5 or ah > th * 1.5):
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
                    msg = f"target {target_size} 跟 actual {aw}x{ah} 不兼容 (target > actual on some axis, ratio_diff={ratio_diff:.3f})"
                    print(f"  ⚠ {msg}, skip", flush=True)
                    meta["post_resize_skipped"] = msg
                    target_size = None  # 留 actual_size 不变
                if target_size:
                    meta["actual_size"] = target_size
                    meta["size_bytes"] = out_path.stat().st_size
        except Exception as e:
            print(f"  ⚠ post-resize 失败 (忽略,保留原图): {type(e).__name__}: {e}", flush=True)
            meta["post_resize_error"] = f"{type(e).__name__}: {e}"

    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK -> {out_path} ({meta['size_bytes']//1024} KB, actual={meta.get('actual_size','?')})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

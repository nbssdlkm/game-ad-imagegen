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


_REF_MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _build_image_input(p: Path) -> dict:
    ext = p.suffix.lower()
    mime = _REF_MIME_MAP.get(ext)
    if mime is None:
        raise ValueError(
            f"unsupported ref ext {ext!r} for {p.name}; ephone image_generation tool 接受 "
            f".jpg/.jpeg/.png/.webp. 其他格式 (gif/bmp/tiff/heic) 请先转换 PNG/JPG."
        )
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
        except requests.exceptions.RequestException as e:
            # 其他 RequestException (TooManyRedirects/InvalidURL/SSLError 等) — 不 retry,
            # 直接返回让 main() 写 meta + exit 1 (这些是配置错误, retry 没用)
            return {"http_status": None,
                    "raw": f"non-retryable request error: {type(e).__name__}: {e}",
                    "json": None, "retry_count": attempt_idx}
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
    ap.add_argument("--size", default=None,
                    help="explicit size 'WxH'. 不传则用默认 2048x1152, 或被 sanitize 提取的 prompt size 覆盖. "
                         "传了 --size 则视为 batch 显式意图, prompt 内 size 不再 override (避免 batch config "
                         "size 被 prompt verbatim 文本意外 override)")
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
    # Exit code 区分 (round-5 blind #2): ref 路径错 = exit 3, 不跟 CredentialsError (exit 2)
    # 冲突 — 否则 SKILL.md setup wizard 看 exit 2 误以为缺 key
    for r in refs:
        if not r.exists():
            print(f"! ref not found: {r}", file=sys.stderr)
            return 3
    # Ref 预检 (round-5 blind #17): 防 50MB 大图 → 600s timeout × 3 retry = 30min 浪费 credit
    _REF_MAX_BYTES = 20 * 1024 * 1024  # ephone 端实测上限 ~20MB
    for r in refs:
        size_b = r.stat().st_size
        if size_b > _REF_MAX_BYTES:
            print(f"! ref too large: {r.name} = {size_b // (1024*1024)} MB > 20 MB. "
                  f"请用 PIL/ffmpeg/手动压缩到 <20MB 再传 (避免 ephone 600s timeout × N retry 烧 credit)",
                  file=sys.stderr)
            return 3

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
    # Size 解析优先级:
    #   1. --size 显式传 (batch_runner 配的)  → 用它, prompt 内 size 只 log warn 不 override
    #   2. sanitize 提取的 size (user prompt 里写"1920x1080")  → 用它
    #   3. 默认 2048x1152
    # ============================================================
    extracted = sanitize_info.get("size")
    DEFAULT_SIZE = "2048x1152"
    size_override_note = None  # 写进 meta 给 audit trail (round-5 blind #8)
    if args.size:  # 显式传 (batch 配)
        effective_size = args.size
        if extracted and extracted != args.size:
            size_override_note = (
                f"explicit --size {args.size} 优先, prompt 内 size {extracted} 被忽略 "
                f"(原因: {sanitize_info.get('size_source')})"
            )
            print(f"  ⚠ {size_override_note}", flush=True)
            # 既然忽略 sanitize 的 size, target_size 也清掉防止 post-resize 跑错
            sanitize_info["target_size"] = None
            sanitize_info["upscaled"] = False
    elif extracted:
        effective_size = extracted
        print(f"  ↪ size from prompt: {extracted} ({sanitize_info.get('size_source')})", flush=True)
    else:
        effective_size = DEFAULT_SIZE

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
        "size_override_note": size_override_note,  # 非 None 时记录 prompt-size 被 --size override 的事实
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

    # result=None hard fail — 防 base64.b64decode(None) TypeError (含 status="completed"
    # 但 result 缺失的 safety-policy partial response case, round-4 blind #1 抓)
    if not result_b64:
        meta["error"] = f"image_call.result 为空 (status={status})"
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"! image_call.result=空 (status={status})", file=sys.stderr)
        return 1

    if status != "completed":
        # soft success: status 异常但有 result. 写 warning (不写 error 避免契约冲突)
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

    # Post-resize: 让 user 拿到他要的尺寸. 用 sanitize_info["upscaled"] flag 决定分支
    # (不再 re-derive from dimensions — 防边缘 case 如 1700x950 错走 crop 丢内容,
    # round-4 blind #4 抓):
    #   (a) upscaled=True: user 要 sub-655K size → ephone 放大到 ≥1024 短边 → PIL LANCZOS resize 回 target
    #   (b) upscaled=False + target≠actual: round case (÷16 round 微调) → PIL center crop 回 target
    target_size = sanitize_info.get("target_size")
    use_lanczos = bool(sanitize_info.get("upscaled"))
    actual_size_str = meta.get("actual_size")
    if target_size and actual_size_str and target_size != actual_size_str:
        try:
            from PIL import Image
            tw, th = (int(x) for x in target_size.split("x"))
            aw, ah = (int(x) for x in actual_size_str.split("x"))
            with Image.open(out_path) as im:
                if use_lanczos:
                    im.resize((tw, th), Image.LANCZOS).save(out_path)
                    meta["post_resized"] = {"from": actual_size_str, "to": target_size, "method": "LANCZOS"}
                    print(f"  ↘ post-resize {aw}x{ah} → {target_size} (LANCZOS, sanitize upscaled=True)", flush=True)
                elif aw >= tw and ah >= th:
                    left = (aw - tw) // 2
                    top = (ah - th) // 2
                    im.crop((left, top, left + tw, top + th)).save(out_path)
                    meta["post_cropped"] = {"from": actual_size_str, "to": target_size}
                    print(f"  ✂ post-crop {aw}x{ah} → {target_size} (居中, round case)", flush=True)
                else:
                    msg = f"target {target_size} 跟 actual {aw}x{ah} 不兼容 (target > actual on some axis); sanitize upscaled=False"
                    print(f"  ⚠ {msg}, skip", flush=True)
                    meta["post_resize_skipped"] = msg
                    target_size = None
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

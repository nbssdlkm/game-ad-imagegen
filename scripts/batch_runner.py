#!/usr/bin/env python
"""
batch_runner.py — 跑 HTML 表单（web/batch_form.html）生成的 batch config.json

⚠️ 本脚本只跑本 skill (game-ad-imagegen)。不跨 skill 调度。
   B skill (codex-imagegen-fork) 有自己独立的 batch_runner.py + web/。

物理位置:本脚本随 game-ad-imagegen skill 一起分发(`<skill_root>/scripts/`),
设计师装机后通过 `~/.claude/skills/game-ad-imagegen/scripts/batch_runner.py` 调到(install.ps1 自动建 junction)。

工作流：
  1. 读 config.json（由本 skill 的 web/batch_form.html 生成,config.skill 写死 "a"）
  2. 校验 skill 字段必须是 "a"（否则停止 + 提示走 B 的 runner）
  3. 对每个 task：reference_images (≥1) + prompt，跑 n 次,调本 skill 的 scripts/image_gen.py
  4. 把每张图的 meta 汇总到 out_dir/_batch_meta.json
  5. 渲染 out_dir/result_grid.html（缩略图网格）

CLI:
  python scripts/batch_runner.py <config.json>
  python scripts/batch_runner.py <config.json> --dry-run

config.json schema:
  {
    "batch_id": "batch_20260513_1430",
    "skill": "a",                 # 必须 "a";其他值 reject
    "out_dir": "...",
    "size": "1536x1024",          # batch 默认
    "quality": "medium",          # batch 默认
    "tasks": [
      {
        "task_id": "t01",
        "reference_images": [".../a.png", ".../b.png"],   # ≥1 张（A 不支持 0 图 text2im）
        "prompt": "...",            # 中文 prompt（终稿，原样传入，不改）
        "n": 1,
        "size": "1024x1536",      # 可选，覆盖 batch 默认
        "quality": "high"         # 可选，覆盖 batch 默认
      }
    ]
  }

调用形态:
  CLI: `image_gen.py --prompt-file X --refs a.png,b.png --out C --size S --quality Q --no-invariants`
  端点：固定 /v1/images/edits（必须 ≥1 张图）
  0 张图 → reject 提示用户用 B skill 的 batch_form

通用规则:
- 同一 task 内 n 次共享 size/quality 设置
- prompt 原样传（--no-invariants 模式不注入 QUALITY_INVARIANTS）
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# Windows console 默认 cp936，强制 stdout/stderr 用 UTF-8（Bash 工具按 UTF-8 解码）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except (AttributeError, Exception):
    pass

# ============================================================
# Skill 定位 — 本 skill 自包含,不跨 skill 找 sibling
# ============================================================
SCRIPTS_DIR = Path(__file__).resolve().parent             # .../game-ad-imagegen/scripts/
THIS_SKILL_ID = "a"                                        # 本 runner 只接 skill=a 的 config
IMAGE_GEN_PY = SCRIPTS_DIR / "image_gen.py"


def build_cmd(prompt_file: Path, refs: list[Path],
              out_path: Path, meta_path: Path, size: str, quality: str) -> list[str]:
    """构造 A 的 image_gen.py CLI。A 必须 ≥1 张图（校验阶段已保证）。"""
    refs_arg = ",".join(str(r) for r in refs)
    return [
        sys.executable, "-u", str(IMAGE_GEN_PY),
        "--prompt-file", str(prompt_file),
        "--refs", refs_arg,
        "--out", str(out_path),
        "--meta-out", str(meta_path),
        "--size", size,
        "--quality", quality,
        "--no-invariants",
    ]


def run_one(prompt_file: Path, refs: list[Path],
            out_path: Path, meta_path: Path, size: str, quality: str) -> dict:
    """跑一张图。**防御设计**: 任何异常都返回 error dict 而不 raise,这样主循环始终能走到结尾写 _batch_meta.json。

    历史失败模式:t01 5 张全跑出来了,t02 child 偶发返回非 0 / meta_path 损坏导致 json.loads raise
    → 整个 batch_runner exit=1 + 不写 _batch_meta.json + 不渲染 result_grid.html。修法:全包 try。
    """
    cmd = build_cmd(prompt_file, refs, out_path, meta_path, size, quality)
    t0 = time.time()
    print(f"  $ image_gen.py --out {out_path.name} --size {size} --quality {quality}", flush=True)

    import os as _os
    import traceback as _tb
    env = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    stdout_lines = []

    try:
        # ⚠️ stderr=STDOUT 把 child 的 stderr 合并到 stdout 走同一个 pipe。
        # 否则 child 大量 stderr 输出会填满 Windows 4KB pipe buffer 导致 child block on write
        # → 主进程 proc.wait() 永远等不到 → 死锁(这是 subprocess 经典 pitfall)。
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=env, bufsize=1,
            creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
        )
        # iter(proc.stdout.readline, '') 比 `for line in proc.stdout` 更稳
        for line in iter(proc.stdout.readline, ''):
            line = line.rstrip("\r\n")
            stdout_lines.append(line)
            print(f"    | {line}", flush=True)
        proc.wait()
        returncode = proc.returncode
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        return {
            "out_path": str(out_path),
            "http_status": None,
            "elapsed_sec": elapsed,
            "error": f"subprocess 异常: {type(e).__name__}: {e}\n{_tb.format_exc()[-1500:]}",
        }

    elapsed = round(time.time() - t0, 1)

    if returncode != 0:
        # child 非 0 退出。但 child 可能已经写出了 PNG(写完文件后 cleanup 时报错)
        # → 检查 out_path 是否真的成功生成,如果是,视为成功(soft success)
        err_tail = "\n".join(stdout_lines[-20:]) if stdout_lines else f"exit code {returncode}"
        if out_path.exists() and out_path.stat().st_size > 0:
            print(f"    ⚠️ child returncode={returncode} 但 PNG 实际成功生成({out_path.stat().st_size // 1024} KB),视为 soft success", flush=True)
            return {
                "out_path": str(out_path),
                "http_status": 200,
                "elapsed_sec": elapsed,
                "size_bytes": out_path.stat().st_size,
                "soft_success": True,
                "child_returncode": returncode,
                "child_warn_tail": err_tail[-1000:],
            }
        return {
            "out_path": str(out_path),
            "http_status": None,
            "elapsed_sec": elapsed,
            "error": err_tail[-2000:],
        }

    # child returncode==0,正常路径。读 meta_path 或 fallback 到 out_path 检查
    try:
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        elif out_path.exists() and out_path.stat().st_size > 0:
            meta = {"out_path": str(out_path), "http_status": 200, "size_bytes": out_path.stat().st_size}
        else:
            meta = {"out_path": str(out_path), "http_status": None, "error": "out file missing or empty"}
    except (json.JSONDecodeError, OSError) as e:
        # meta_path 损坏(JSON 解析失败)或者 IO 异常 → 回退看 out_path 是否真生成
        if out_path.exists() and out_path.stat().st_size > 0:
            meta = {"out_path": str(out_path), "http_status": 200, "size_bytes": out_path.stat().st_size,
                    "warn": f"meta_path corrupt but PNG OK: {type(e).__name__}: {e}"}
        else:
            meta = {"out_path": str(out_path), "http_status": None, "error": f"meta read failed + no PNG: {e}"}
    meta["elapsed_sec"] = elapsed
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config", help="config.json (由本 skill 的 batch_form.html 生成)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只做校验 + 打印将执行的命令,不真调 image_gen.py(省 credit)")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="跳过 vision + rewrite step，直接把 config 里的 prompt 字面喂给 image_gen.py。"
                         "向后兼容已自己 rewrite 好英文 prompt 的旧 config。"
                         "也可在 config 里加 \"prompt_already_rewritten\": true 全批跳过，"
                         "或在单个 task 里加同名字段单 task 跳过。")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        # 业务校验失败用 print + return 2,不走 argparse usage 风格(避免被误以为是 CLI 语法错)
        print(f"\n! 配置失败:config 不存在: {cfg_path}", file=sys.stderr)
        return 2
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    # 校验 skill 字段必须是本 skill (A) — 业务校验,不走 argparse
    skill = (cfg.get("skill") or "").lower()
    if skill != THIS_SKILL_ID:
        print(
            f"\n! 配置失败:config.skill = {skill!r},本 runner 只跑 skill='a' (game-ad-imagegen)。\n"
            f"  - 如果你想用 B skill,请用 ~/.claude/skills/codex-imagegen-fork/scripts/batch_runner.py\n"
            f"  - 如果是误填,请把 config.skill 改成 'a' 或回 batch_form 重新生成",
            file=sys.stderr,
        )
        return 2

    if not IMAGE_GEN_PY.exists():
        print(f"\n! 配置失败:image_gen.py 不在: {IMAGE_GEN_PY}(本 skill 安装不完整)", file=sys.stderr)
        return 2

    batch_id = cfg["batch_id"]
    # expanduser 让 form 里的 ~/Desktop/... 之类 token 展开成跨 OS 绝对路径
    out_dir = Path(cfg["out_dir"]).expanduser()
    batch_size = cfg.get("size", "1536x1024")
    batch_quality = cfg.get("quality", "medium")
    tasks = cfg["tasks"]

    out_dir.mkdir(parents=True, exist_ok=True)

    # ===== 校验阶段（先把所有问题都列出来，不要跑一半才报错）=====
    errors = []
    for t in tasks:
        tid = t.get("task_id", "?")
        refs = t.get("reference_images") or []
        if not isinstance(refs, list):
            errors.append(f"{tid}: reference_images 必须是 list")
            continue
        # A skill 必须 ≥1 张图(走 /v1/images/edits),不支持纯文字生图
        if len(refs) == 0:
            errors.append(
                f"{tid}: A skill 不支持纯文字生图(0 图)。"
                f"请加至少 1 张图,或切到 B skill (codex-imagegen-fork) 跑这个 task"
            )
            continue
        for p in refs:
            if not Path(p).exists():
                errors.append(f"{tid}: 参考图不存在 — {p}")
        if not (t.get("prompt") or "").strip():
            errors.append(f"{tid}: prompt 不能空")
        n = int(t.get("n", 1))
        if n < 1 or n > 10:
            errors.append(f"{tid}: n 应在 1-10(实际 {n})")

    if errors:
        print(f"\n! 校验失败 ({len(errors)} 个问题):", file=sys.stderr)
        for e in errors:
            print(f"    - {e}", file=sys.stderr)
        return 2

    # ===== 跑批 =====
    n_images_total = sum(t.get("n", 1) for t in tasks)
    print(f"=== batch {batch_id} [skill=a · game-ad-imagegen]: {len(tasks)} 任务 × n = {n_images_total} 张图 ===", flush=True)
    print(f"  image_gen.py: {IMAGE_GEN_PY}", flush=True)
    print(f"  out_dir: {out_dir}", flush=True)
    print(f"  defaults: size={batch_size}, quality={batch_quality}", flush=True)
    if args.dry_run:
        print("  [dry-run] 仅校验 + 打印计划,不真调 image_gen.py\n", flush=True)
    else:
        print(flush=True)

    all_results = []
    t_batch_start = time.time()
    img_seq = 0

    # 增量进度: 每张图跑完就写一次 _batch_meta.json + 渲染 result_grid.html
    # 让 user 在 preview 刷新就能看到实时进度,不用等整批跑完
    def _write_incremental_progress(results_so_far, n_total, status="running"):
        partial_meta = {
            "batch_id": batch_id,
            "out_dir": str(out_dir),
            "config": cfg,
            "results": results_so_far,
            "ok_count": sum(1 for r in results_so_far if r.get("http_status") == 200),
            "total": n_total,
            "completed": len(results_so_far),
            "status": status,  # "running" / "done" / "error"
            "elapsed_sec": round(time.time() - t_batch_start, 1),
        }
        meta_p = out_dir / "_batch_meta.json"
        try:
            meta_p.write_text(json.dumps(partial_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"  ! 增量写 _batch_meta.json 失败(忽略,继续跑): {e}", file=sys.stderr, flush=True)
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from render_result_grid import render as _render
            _render(out_dir, partial_meta)
        except Exception as e:
            print(f"  ! 增量渲染 result_grid.html 失败(忽略,继续跑): {e}", file=sys.stderr, flush=True)
        return partial_meta

    # 跑批开始就先写一次空 grid,user preview 刷新能立即看到"running" 状态
    if not args.dry_run:
        _write_incremental_progress([], n_images_total, status="running")
        print(f"  📊 进度页已就绪: {out_dir / 'result_grid.html'}\n", flush=True)

    for t_idx, task in enumerate(tasks, 1):
        task_id = task["task_id"]
        refs = [Path(p) for p in task["reference_images"]]
        prompt = task["prompt"]
        n = int(task.get("n", 1))
        task_size = task.get("size") or batch_size
        task_quality = task.get("quality") or batch_quality

        # === Vision + Rewrite (Mode 2 → 跟 Mode 1 对齐的核心能力) ===
        # 默认把用户中文需求 rewrite 成详细英文 image-gen prompt(T9 模板),
        # 让 Mode 2 (form / 跑批) 也享受 skill 的核心 rewrite 能力。
        # 跳过条件:
        #   - CLI --no-rewrite flag (向后兼容)
        #   - cfg.prompt_already_rewritten == true (全批跳过)
        #   - task.prompt_already_rewritten == true (单 task 跳过)
        skip_rewrite = (
            args.no_rewrite
            or cfg.get("prompt_already_rewritten")
            or task.get("prompt_already_rewritten")
        )

        if skip_rewrite or args.dry_run:
            rewritten_prompt = prompt
            if skip_rewrite and not args.dry_run:
                print(f"\n[task {task_id}] skip rewrite (prompt_already_rewritten / --no-rewrite)", flush=True)
        else:
            print(f"\n[task {task_id}] vision + rewrite step ({len(refs)} refs)...", flush=True)
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from rewrite_prompt import rewrite as _do_rewrite
                rewritten_prompt = _do_rewrite(prompt, refs)
                # 存原始中文 prompt 作 debug trail
                (out_dir / f"{task_id}_prompt_original.txt").write_text(prompt, encoding="utf-8")
                print(f"[task {task_id}] rewrite done ({len(rewritten_prompt)} chars)", flush=True)
            except Exception as e:
                print(f"! [task {task_id}] rewrite failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                print(f"  falling back to original prompt — image quality may suffer", file=sys.stderr, flush=True)
                rewritten_prompt = prompt

        prompt_file = out_dir / f"{task_id}_prompt.txt"
        prompt_file.write_text(rewritten_prompt, encoding="utf-8")

        for i in range(1, n + 1):
            img_seq += 1
            out_path = out_dir / f"{task_id}_{i:02d}.png"
            meta_path = out_dir / f"{task_id}_{i:02d}_meta.json"
            print(f"\n--- {img_seq}/{n_images_total}  ({task_id} {i}/{n}) ---", flush=True)
            print(f"  refs ({len(refs)}): {[r.name for r in refs]}", flush=True)
            print(f"  size={task_size}, quality={task_quality}", flush=True)
            print(f"  prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}", flush=True)

            if args.dry_run:
                preview_cmd = build_cmd(prompt_file, refs, out_path, meta_path, task_size, task_quality)
                print(f"  $ [dry-run] {' '.join(str(c) for c in preview_cmd[:6])}{' ...' if len(preview_cmd) > 6 else ''}", flush=True)
                res = {
                    "out_path": str(out_path),
                    "http_status": 200,
                    "elapsed_sec": 0.0,
                    "_dry_run": True,
                }
            else:
                # 防御:即使 run_one 内部还有 raise(理论上不该,但万一)也接住
                try:
                    res = run_one(prompt_file, refs, out_path, meta_path, task_size, task_quality)
                except Exception as e:
                    import traceback as _tb2
                    print(f"\n    ! run_one 内异常(catch + 继续下一张): {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                    res = {
                        "out_path": str(out_path),
                        "http_status": None,
                        "elapsed_sec": 0.0,
                        "error": f"run_one raised: {type(e).__name__}: {e}\n{_tb2.format_exc()[-1500:]}",
                    }
            res.update({
                "task_id": task_id,
                "skill": THIS_SKILL_ID,
                "task_seq_in_batch": t_idx,
                "image_seq_in_task": i,
                "reference_images": [str(r) for r in refs],
                "prompt": prompt,
                "size": task_size,
                "quality": task_quality,
            })
            all_results.append(res)

            # 增量写进度: 每张图跑完就更新 _batch_meta.json + result_grid.html
            if not args.dry_run:
                _write_incremental_progress(all_results, n_images_total, status="running")

    # 最终收尾(status="done")
    batch_meta = {
        "batch_id": batch_id,
        "out_dir": str(out_dir),
        "config": cfg,
        "results": all_results,
        "ok_count": sum(1 for r in all_results if r.get("http_status") == 200),
        "total": len(all_results),
        "completed": len(all_results),
        "status": "done",
        "elapsed_sec": round(time.time() - t_batch_start, 1),
    }
    meta_path = out_dir / "_batch_meta.json"
    try:
        meta_path.write_text(json.dumps(batch_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"\n! 写最终 _batch_meta.json 失败: {e}", file=sys.stderr, flush=True)
    print(f"\n=== batch done: {batch_meta['ok_count']}/{batch_meta['total']} OK in {batch_meta['elapsed_sec']}s ===", flush=True)
    print(f"  meta -> {meta_path}", flush=True)

    # 最终渲染 result_grid.html
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from render_result_grid import render
        grid_path = render(out_dir, batch_meta)
        print(f"  grid -> {grid_path}", flush=True)
    except Exception as e:
        print(f"  ! 最终 render_result_grid 失败(增量版本已落盘可看): {e}", file=sys.stderr, flush=True)

    return 0 if batch_meta["ok_count"] == batch_meta["total"] else 1


if __name__ == "__main__":
    sys.exit(main())

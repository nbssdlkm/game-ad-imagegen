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
        "prompt": "...",            # 用户原始中文需求；batch_runner 会先调 rewrite_prompt 转 N 段英文 prompt 再喂 image_gen
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
- 用户中文 prompt 先经 rewrite_prompt.rewrite() 转 N 段英文 prompt（带 SENTINEL marker），再喂 image_gen.py
- image_gen.py 传 `--no-invariants` 避免 QUALITY_INVARIANTS 双重注入（rewrite 已在 system prompt 里约束 quality）
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
# Hybrid: 改调 image_gen_hybrid (POST /v1/responses + image_gen tool + 中文 structured prompt)
IMAGE_GEN_PY = SCRIPTS_DIR / "image_gen_hybrid.py"


def _ensure_scripts_in_syspath():
    """idempotent: 把 SCRIPTS_DIR 加到 sys.path 一次,防 inner 反复 insert 让 sys.path 无限增长。"""
    s = str(SCRIPTS_DIR)
    if s not in sys.path:
        sys.path.insert(0, s)


_ensure_scripts_in_syspath()


def build_cmd(prompt_file: Path, refs: list[Path],
              out_path: Path, meta_path: Path, size: str, quality: str) -> list[str]:
    """构造 A 的 image_gen.py CLI。
    refs 空 → 不传 --refs,image_gen.py 自动切到 0 图 text2im 模式(/v1/images/generations);
    refs ≥1 张 → 传 --refs,走 /v1/images/edits。
    """
    cmd = [
        sys.executable, "-u", str(IMAGE_GEN_PY),
        "--prompt-file", str(prompt_file),
        "--out", str(out_path),
        "--meta-out", str(meta_path),
        "--size", size,
        "--quality", quality,
        "--no-invariants",
    ]
    if refs:
        cmd.extend(["--refs", ",".join(str(r) for r in refs)])
    return cmd


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
    seen_task_ids = set()
    for i, t in enumerate(tasks):
        tid = t.get("task_id")
        if not tid or not isinstance(tid, str) or not tid.strip():
            errors.append(f"tasks[{i}]: 缺少 'task_id' 字段或为空(必填,会被用作 prompt 文件名 / 图片文件名前缀)")
            continue
        if tid in seen_task_ids:
            errors.append(f"tasks[{i}]: task_id '{tid}' 重复(每个 task 必须唯一,否则文件名碰撞)")
            continue
        seen_task_ids.add(tid)
        refs = t.get("reference_images") or []
        if not isinstance(refs, list):
            errors.append(f"{tid}: reference_images 必须是 list")
            continue
        # A skill 支持 0 图 text2im(走 /v1/images/generations,游戏广告纯文字生买量素材)
        # 和 ≥1 图 edit / composite(走 /v1/images/edits)。anchor mode 仍需 ≥1 图(下方校验)
        for p in refs:
            if not Path(p).exists():
                errors.append(f"{tid}: 参考图不存在 — {p}")
        if not (t.get("prompt") or "").strip():
            errors.append(f"{tid}: prompt 不能空")
        # ⚠️ Product 决策: anchor 是默认且唯一模式(user 反复强调)。
        # 删除了 standard mode 回退路径 — 凡 task 必走 anchor: Phase 1 候选 → 人工挑 → Phase 3 锁风格生剩余。
        # 0 图 text2im / 单图 edit 这类无法锁风格的场景,请走 B skill (codex-imagegen-fork)。
        n = int(t.get("n", 1))
        if n < 2:
            errors.append(f"{tid}: n={n} 无效 — anchor 是默认且唯一模式, n 必须 ≥2 (候选 + 锁风格 series)。0 图 text2im / 单图 edit 请用 B skill")
        if n > 10:
            errors.append(f"{tid}: n={n} 超过上限 10")
        if len(refs) == 0:
            errors.append(f"{tid}: 至少 1 张参考图 — anchor 需要 vision 抽候选 + Phase 3 锁风格 ref")
        # anchor_candidates 默认 3 (user 不填 → 自动注入)
        DEFAULT_ANCHOR_CANDIDATES = 3
        M_anchor = int(t.get("anchor_candidates", 0)) or DEFAULT_ANCHOR_CANDIDATES
        t["anchor_candidates"] = M_anchor  # 写回 task,统一下游读取
        if M_anchor < 2:
            errors.append(f"{tid}: anchor_candidates={M_anchor} 无效, 必须 ≥2")
        if M_anchor > 10:
            errors.append(f"{tid}: anchor_candidates={M_anchor} 超过上限 10 (太多候选浪费 token)")

    if errors:
        print(f"\n! 校验失败 ({len(errors)} 个问题):", file=sys.stderr)
        for e in errors:
            print(f"    - {e}", file=sys.stderr)
        return 2

    # ===== 跑批 =====
    # Anchor workflow (默认且唯一模式, 凡 task 必走):
    #   Phase 1: 跑 M 张候选(同段 prompt × M sampling), user 挑 1 张
    #   Phase 2: poll {batch_id}_anchor_picks.json
    #   Phase 3: picked anchor → t01.png + rewrite N-1 段 with picked anchor in refs → 跑 N-1 张
    # 单 task 总图数 = M (candidates) + n (1 picked-anchor copy + n-1 generated series)
    def _task_image_count(t):
        return int(t["anchor_candidates"]) + int(t["n"])

    n_images_total = sum(_task_image_count(t) for t in tasks)
    print(f"=== batch {batch_id} [skill=a · game-ad-imagegen · anchor-only]: {len(tasks)} 任务 × ~ = {n_images_total} 张图 ===", flush=True)
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
            _ensure_scripts_in_syspath()
            from render_result_grid import render as _render
            _render(out_dir, partial_meta)
        except Exception as e:
            print(f"  ! 增量渲染 result_grid.html 失败(忽略,继续跑): {e}", file=sys.stderr, flush=True)
        return partial_meta

    # 跑批开始就先写一次空 grid,user preview 刷新能立即看到"running" 状态
    if not args.dry_run:
        _write_incremental_progress([], n_images_total, status="running")
        print(f"  📊 进度页已就绪: {out_dir / 'result_grid.html'}\n", flush=True)

    # Anchor mode 累积:Phase 1 跑完后等 user 挑选,Phase 3 再继续
    anchor_pending_tasks = []

    for t_idx, task in enumerate(tasks, 1):
        task_id = task["task_id"]
        refs = [Path(p) for p in task["reference_images"]]
        prompt = task["prompt"]
        n = int(task["n"])
        task_size = task.get("size") or batch_size
        task_quality = task.get("quality") or batch_quality
        M = int(task["anchor_candidates"])

        # 把 refs 复制到 out_dir,让 anchor_pick.html 用相对路径能 load ref 缩略图
        # (否则 user 看到 anchor_pick.html 时 ref 缩略图全 broken,需要手动 copy refs)
        import shutil as _shutil
        for _ref in refs:
            _ref_dst = out_dir / Path(_ref).name
            if not _ref_dst.exists():
                try:
                    _shutil.copy(_ref, _ref_dst)
                except Exception as _e:
                    print(f"  ! [task {task_id}] copy ref {_ref.name} → out_dir 失败(继续): {_e}", file=sys.stderr, flush=True)

        # === Vision + Rewrite + Phase 1: 同段 prompt × M 次 sampling 出候选 ===
        # Invariant: 实跑路径必走 rewrite。失败 → 异常 propagate → batch 整批 fail-fast。
        # dry-run 不发 API,可用原 prompt 仅作 schema 预览。
        if args.dry_run:
            cand_prompt = prompt
        else:
            print(f"\n[task {task_id}] (anchor Phase 1) vision + rewrite ({len(refs)} refs, M={M} candidates)...", flush=True)
            _ensure_scripts_in_syspath()
            from rewrite_prompt import rewrite as _do_rewrite
            _cand_prompts = _do_rewrite(prompt, refs, n=1, anchor_phase="phase1")
            cand_prompt = _cand_prompts[0]
            (out_dir / f"{task_id}_prompt_original.txt").write_text(prompt, encoding="utf-8")
            print(f"[task {task_id}] Phase 1 rewrite done ({len(cand_prompt)} chars)", flush=True)

        cand_prompt_file = out_dir / f"{task_id}_anchor_cand_prompt.txt"
        cand_prompt_file.write_text(cand_prompt, encoding="utf-8")

        candidate_paths = []
        for ci in range(1, M + 1):
            img_seq += 1
            cand_out = out_dir / f"{task_id}_anchor_cand_{ci:02d}.png"
            cand_meta = out_dir / f"{task_id}_anchor_cand_{ci:02d}_meta.json"
            candidate_paths.append(cand_out)

            print(f"\n--- {img_seq}/{n_images_total}  ({task_id} anchor cand {ci}/{M}) ---", flush=True)
            print(f"  refs ({len(refs)}): {[r.name for r in refs]}", flush=True)
            print(f"  size={task_size}, quality={task_quality}", flush=True)
            print(f"  prompt: {cand_prompt[:100]}{'...' if len(cand_prompt) > 100 else ''}", flush=True)

            if args.dry_run:
                preview_cmd = build_cmd(cand_prompt_file, refs, cand_out, cand_meta, task_size, task_quality)
                print(f"  $ [dry-run] {' '.join(str(c) for c in preview_cmd[:6])} ...", flush=True)
                res = {"out_path": str(cand_out), "http_status": 200, "elapsed_sec": 0.0, "_dry_run": True}
            else:
                try:
                    res = run_one(cand_prompt_file, refs, cand_out, cand_meta, task_size, task_quality)
                except Exception as e:
                    import traceback as _tb2
                    print(f"\n    ! run_one(anchor cand) 异常: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                    res = {"out_path": str(cand_out), "http_status": None, "elapsed_sec": 0.0,
                           "error": f"run_one raised: {type(e).__name__}: {e}\n{_tb2.format_exc()[-1500:]}"}
            res.update({
                "task_id": task_id,
                "skill": THIS_SKILL_ID,
                "task_seq_in_batch": t_idx,
                "image_seq_in_task": -ci,  # 负数 = anchor candidate
                "is_anchor_candidate": True,
                "anchor_candidate_idx": ci,
                "reference_images": [str(r) for r in refs],
                "prompt": prompt,
                "size": task_size,
                "quality": task_quality,
            })
            all_results.append(res)
            if not args.dry_run:
                _write_incremental_progress(all_results, n_images_total, status="running")

        # Stash 给后面 Phase 2+3 用
        anchor_pending_tasks.append({
            "t_idx": t_idx,
            "task": task,
            "refs": refs,
            "candidate_paths": candidate_paths,
            "task_size": task_size,
            "task_quality": task_quality,
        })

    # ==========================================================
    # === Anchor mode Phase 2: render anchor_pick.html + poll picks JSON ===
    # === Anchor mode Phase 3: 跑 N-1 张 series with anchor 锁风格 ===
    # ==========================================================
    if anchor_pending_tasks and not args.dry_run:
        # 渲染 anchor_pick.html (默认 UI;技术团队可自行替换)
        try:
            _ensure_scripts_in_syspath()
            from render_anchor_pick import render as _render_pick
            pick_html = _render_pick(out_dir, batch_id, anchor_pending_tasks)
            print(f"\n📋 anchor_pick.html ready: {pick_html}", flush=True)
        except Exception as e:
            print(f"! 渲染 anchor_pick.html 失败(继续 poll JSON 文件): {e}", file=sys.stderr, flush=True)

        picks_file = out_dir / f"{batch_id}_anchor_picks.json"
        ready_file = out_dir / f"{batch_id}_anchor_pick_ready.txt"
        ready_msg_lines = [
            f"Phase 1 done at {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Anchor candidates ready for {len(anchor_pending_tasks)} task(s).",
            "",
            f"NEXT: open anchor_pick.html in browser to pick anchor for each task,",
            f"   submit → save the JSON output to: {picks_file}",
            "",
            "OR write JSON manually with this schema:",
            "{",
        ]
        for ap in anchor_pending_tasks:
            tid = ap["task"]["task_id"]
            M_ = int(ap["task"]["anchor_candidates"])
            ready_msg_lines.append(f'  "{tid}": <1..{M_}>,')
        ready_msg_lines.extend(["}", ""])
        ready_file.write_text("\n".join(ready_msg_lines), encoding="utf-8")

        # Poll timeout 防 user 忘记挑选 / 浏览器关导致 batch_runner 永远挂着
        ANCHOR_POLL_TIMEOUT_SEC = 1800  # 30 min
        ANCHOR_POLL_INTERVAL_SEC = 30

        print(f"\n⏸️ Phase 1 done — waiting for anchor picks (timeout {ANCHOR_POLL_TIMEOUT_SEC // 60} min)", flush=True)
        print(f"   1. Open: {out_dir / 'anchor_pick.html'}", flush=True)
        print(f"   2. Pick one anchor per task → save JSON to: {picks_file.name}", flush=True)
        print(f"   batch_runner polls every {ANCHOR_POLL_INTERVAL_SEC}s...\n", flush=True)

        # 首次写一次 "awaiting_picks" 状态到 _batch_meta.json,让前端
        # (result_grid.html / WB UI 等) 立刻能看到 status 变化 — 否则 30 min poll 期间
        # status 一直停 "running" 让 user 误以为卡死(minimax-m2.7 review 抓出)
        _write_incremental_progress(all_results, n_images_total, status="awaiting_picks")

        # Poll loop with timeout
        _poll_start = time.time()
        picks = None
        while True:
            _elapsed = round(time.time() - _poll_start, 0)
            if picks_file.exists():
                try:
                    picks = json.loads(picks_file.read_text(encoding="utf-8"))
                    print(f"\n✅ picks received at {int(_elapsed)}s: {picks}", flush=True)
                    break
                except Exception as e:
                    print(f"  ! picks JSON 解析失败 at {int(_elapsed)}s (will retry): {e}", file=sys.stderr, flush=True)
            if _elapsed >= ANCHOR_POLL_TIMEOUT_SEC:
                print(f"\n⏰ Anchor pick TIMEOUT after {int(_elapsed)}s — abort batch (Phase 3 skipped for {len(anchor_pending_tasks)} task(s))",
                      file=sys.stderr, flush=True)
                print(f"   Phase 1 候选图保留在 out_dir,user 可后续手动挑选 + 重新跑 batch_runner --continue (待实现)",
                      file=sys.stderr, flush=True)
                # Write timeout marker for diagnostics
                (out_dir / f"{batch_id}_anchor_pick_TIMEOUT.txt").write_text(
                    f"Timed out at {time.strftime('%Y-%m-%d %H:%M:%S')} after {int(_elapsed)}s waiting for {picks_file.name}",
                    encoding="utf-8",
                )
                picks = None
                break
            # print 用 sleep 前的当前 elapsed(语义最清晰: "已等了 X 秒,timeout Y 秒,下次 check 在 +30s 后")
            print(f"  ⏳ polling for {picks_file.name} ({int(_elapsed)}s elapsed / timeout {ANCHOR_POLL_TIMEOUT_SEC}s, next check in {ANCHOR_POLL_INTERVAL_SEC}s)...", flush=True)
            # 持续更新 _batch_meta.json 的 elapsed_sec 让前端看到时间在动(否则 status 卡 awaiting_picks 30 min user 误以为死了)
            _write_incremental_progress(all_results, n_images_total, status="awaiting_picks")
            time.sleep(ANCHOR_POLL_INTERVAL_SEC)

        # 如果 timeout 没拿到 picks,跳过 Phase 3 但仍写最终 meta(候选图保留供后续手动 review)
        if picks is None:
            anchor_pending_tasks = []  # 让下面 Phase 3 循环不执行

        # Phase 3: 对每个 anchor task 跑 N-1 张 series
        for pending in anchor_pending_tasks:
            task = pending["task"]
            t_idx = pending["t_idx"]
            task_id = task["task_id"]
            refs = pending["refs"]
            candidate_paths = pending["candidate_paths"]
            task_size = pending["task_size"]
            task_quality = pending["task_quality"]
            n = int(task.get("n", 1))
            prompt = task["prompt"]

            # 容错: picks JSON 可能写 "3" (string) 而不是 3 (int), 尝试转 int
            picked_idx_raw = picks.get(task_id)
            try:
                picked_idx = int(picked_idx_raw) if picked_idx_raw is not None else None
            except (ValueError, TypeError):
                picked_idx = None
            if picked_idx is None or picked_idx < 1 or picked_idx > len(candidate_paths):
                print(f"! [task {task_id}] invalid pick {picked_idx_raw!r} (need int 1..{len(candidate_paths)}), skipping Phase 3", file=sys.stderr, flush=True)
                continue

            picked_path = candidate_paths[picked_idx - 1]
            if not picked_path.exists():
                print(f"! [task {task_id}] picked candidate {picked_path} not found", file=sys.stderr, flush=True)
                continue

            # 复制 picked candidate → t0X_01.png (第 1 张正式输出)
            import shutil as _shutil
            first_out = out_dir / f"{task_id}_01.png"
            _shutil.copy(picked_path, first_out)
            img_seq += 1
            print(f"\n[task {task_id}] Phase 3: picked cand {picked_idx} → {first_out.name}", flush=True)
            all_results.append({
                "out_path": str(first_out),
                "http_status": 200,
                "elapsed_sec": 0.0,
                "is_anchor_picked": True,
                "anchor_candidate_source": picked_idx,
                "task_id": task_id,
                "skill": THIS_SKILL_ID,
                "task_seq_in_batch": t_idx,
                "image_seq_in_task": 1,
                "reference_images": [str(r) for r in refs],
                "prompt": prompt,
                "size": task_size,
                "quality": task_quality,
            })
            _write_incremental_progress(all_results, n_images_total, status="running")

            # 防御性 check: 当前校验段要求 n>=2 所以 n-1>=1,但加 check 防未来回归
            n_series = n - 1
            if n_series < 1:
                print(f"[task {task_id}] Phase 3 skipped (n={n} → n-1={n_series} < 1, anchor + 0 series, picked anchor 已 copy 为唯一输出)", flush=True)
                continue

            # Rewrite N-1 段 with anchor_phase="phase3"
            # refs dedup: picked_path 罕见情况下可能已在 refs 列表(用户用某 ref 同一文件做 anchor candidate),
            # 加 dedup 防重复喂 vision token + **显式传 anchor_idx** 给 rewrite,
            # 否则 rewrite 硬编码 "最后一张 = anchor" 在 dedup 不 append 时指向错误图(glm review 抓出)
            new_refs = list(refs)
            if picked_path in new_refs:
                # picked 已在 refs 列表 → 用其原位置作为 anchor_idx
                anchor_idx = new_refs.index(picked_path) + 1  # 1-based
            else:
                # 正常情况 → append 末尾 + anchor_idx = 末尾位置
                new_refs.append(picked_path)
                anchor_idx = len(new_refs)
            _ensure_scripts_in_syspath()
            from rewrite_prompt import rewrite as _do_rewrite
            series_prompts = _do_rewrite(prompt, new_refs, n=n_series, anchor_phase="phase3", anchor_idx=anchor_idx)
            total_chars = sum(len(p) for p in series_prompts)
            print(f"[task {task_id}] Phase 3 rewrite done ({len(series_prompts)} prompts, {total_chars} chars, anchor-locked)", flush=True)

            while len(series_prompts) < n_series:
                series_prompts.append(series_prompts[-1])

            # 跑 N-1 张 series with picked anchor 在 refs 列表末尾
            for i in range(2, n + 1):
                img_seq += 1
                prompt_for_this = series_prompts[i - 2]
                prompt_file = out_dir / f"{task_id}_{i:02d}_prompt.txt"
                prompt_file.write_text(prompt_for_this, encoding="utf-8")
                out_path = out_dir / f"{task_id}_{i:02d}.png"
                meta_path = out_dir / f"{task_id}_{i:02d}_meta.json"
                print(f"\n--- {img_seq}/{n_images_total}  ({task_id} Phase 3 series {i}/{n}) ---", flush=True)
                print(f"  refs ({len(new_refs)}): {[r.name for r in new_refs]}", flush=True)
                print(f"  size={task_size}, quality={task_quality}", flush=True)
                print(f"  prompt: {prompt_for_this[:100]}{'...' if len(prompt_for_this) > 100 else ''}", flush=True)

                try:
                    res = run_one(prompt_file, new_refs, out_path, meta_path, task_size, task_quality)
                except Exception as e:
                    import traceback as _tb2
                    print(f"\n    ! run_one(Phase 3 series) 异常: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                    res = {"out_path": str(out_path), "http_status": None, "elapsed_sec": 0.0,
                           "error": f"run_one raised: {type(e).__name__}: {e}\n{_tb2.format_exc()[-1500:]}"}
                res.update({
                    "task_id": task_id,
                    "skill": THIS_SKILL_ID,
                    "task_seq_in_batch": t_idx,
                    "image_seq_in_task": i,
                    "is_anchor_series": True,
                    "anchor_image_used": str(picked_path),
                    "reference_images": [str(r) for r in new_refs],
                    "prompt": prompt,
                    "size": task_size,
                    "quality": task_quality,
                })
                all_results.append(res)
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
        _ensure_scripts_in_syspath()
        from render_result_grid import render
        grid_path = render(out_dir, batch_meta)
        print(f"  grid -> {grid_path}", flush=True)
    except Exception as e:
        print(f"  ! 最终 render_result_grid 失败(增量版本已落盘可看): {e}", file=sys.stderr, flush=True)

    return 0 if batch_meta["ok_count"] == batch_meta["total"] else 1


if __name__ == "__main__":
    sys.exit(main())

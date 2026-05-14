#!/usr/bin/env python
"""
render_result_grid.py - 把 _batch_meta.json + outputs/<batch>/*.png 渲染成 result_grid.html

CLI:
  python scripts/render_result_grid.py <batch_out_dir>

被 batch_runner_html.py 主动调用，也可独立跑。
"""
import argparse
import html
import json
import sys
from collections import defaultdict
from pathlib import Path

TEMPLATE_PATH = Path(__file__).parent.parent / "web" / "grid_template.html"


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _build_task_blocks(out_dir: Path, results: list[dict]) -> str:
    # 按 task_id 分组
    by_task = defaultdict(list)
    for r in results:
        by_task[r.get("task_id", "?")].append(r)

    blocks = []
    for task_id in sorted(by_task.keys()):
        items = by_task[task_id]
        first = items[0]
        prompt = first.get("prompt", "")
        # 新 schema: reference_images list；兼容旧 schema: ref + sources
        ref_paths = first.get("reference_images")
        if ref_paths is None:
            ref = first.get("ref", "")
            sources = first.get("sources") or []
            ref_paths = ([ref] if ref else []) + list(sources)
        ref_names = [Path(p).name for p in ref_paths]
        size_str = first.get("size", "")
        quality_str = first.get("quality", "")

        cells = []
        for r in items:
            out_path = Path(r.get("out_path", ""))
            rel_name = out_path.name
            i = r.get("image_seq_in_task", "?")
            status = r.get("http_status")
            err = r.get("error") or ""
            if status == 200 and out_path.exists():
                cells.append(
                    f'<div class="cell">'
                    f'<img src="{_esc(rel_name)}" alt="{_esc(rel_name)}" loading="lazy">'
                    f'<div class="caption">'
                    f'<strong>#{i}</strong>'
                    f'<a class="open-link" href="{_esc(rel_name)}" target="_blank">在新页打开</a>'
                    f'</div></div>'
                )
            else:
                short_err = err[:200] + ("..." if len(err) > 200 else "")
                cells.append(
                    f'<div class="cell error">'
                    f'<img src="">'
                    f'<div class="caption"><strong>#{i} 失败</strong><span>{_esc(short_err)}</span></div>'
                    f'</div>'
                )

        refs_html = (
            " + ".join(f"<code>{_esc(n)}</code>" for n in ref_names)
            if ref_names else "（无参考图，text2im 路径）"
        )
        meta_inline_parts = []
        skill_str = first.get("skill", "")
        if skill_str:
            skill_label = {"a": "A 自研", "b": "B OpenAI 原版"}.get(skill_str, skill_str)
            meta_inline_parts.append(f"skill={skill_label}")
        if size_str:
            meta_inline_parts.append(size_str)
        if quality_str:
            meta_inline_parts.append(quality_str)
        meta_inline = ""
        if meta_inline_parts:
            meta_inline = f' <span style="color:#888">[{_esc(" · ".join(meta_inline_parts))}]</span>'

        block = (
            f'<section class="task-block">'
            f'<div class="task-head">任务 {_esc(task_id)} — {len(items)} 张{meta_inline}</div>'
            f'<div class="task-refs">参考图（{len(ref_names)}）：{refs_html}</div>'
            f'<p class="task-prompt">{_esc(prompt)}</p>'
            f'<div class="grid">{"".join(cells)}</div>'
            f'</section>'
        )
        blocks.append(block)

    return "\n".join(blocks)


_STATUS_LABEL_MAP = {
    "running": "🟦 进行中",
    "awaiting_picks": "🟨 等选 anchor",
    "done": "🟩 完成",
    "error": "🟥 错误",
}
_STATUS_HINT_MAP = {
    "running": "<strong>进度页 5 秒后自动刷新看新出的图。</strong>跑批进行中,出一张就更新一张,可以一直开着这页等。",
    "awaiting_picks": (
        '<strong style="color:#ffd866">🟨 Phase 1 候选图已生成,等你挑选 anchor 中。</strong><br>'
        '👉 <a href="anchor_pick.html" style="color:#ffd866;font-weight:bold;text-decoration:underline;font-size:14px">📋 点这里打开 anchor 挑选页 (anchor_pick.html)</a><br>'
        '挑 1 张作为系列 anchor → 提交后保存 picks JSON 到本目录 (`<batch_id>_anchor_picks.json`),batch_runner 30s 内自动接力跑 Phase 3 series。<br>'
        '<span style="color:#aaa;font-size:12px">⚠️ 注意:awaiting_picks 状态下本页 5s 仍刷新,但不会有新图出 — 这是等用户操作的暂停态,不是卡死。你挑完 anchor 后 status 才会切回 running 继续出图。</span>'
    ),
    "done": "全部跑完。喜欢哪几张右键图片「另存为」或直接复制路径。要再跑一批:回对话区,重开 batch_form 配新任务。",
    "error": "跑批有错误。看下面失败 cell 的红色 caption,或对话区 stderr 输出排查。",
}


def render(out_dir: Path, batch_meta: dict) -> Path:
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")

    batch_id = batch_meta.get("batch_id", out_dir.name)
    results = batch_meta.get("results", [])
    ok_count = batch_meta.get("ok_count", sum(1 for r in results if r.get("http_status") == 200))
    total = batch_meta.get("total", len(results))
    completed = batch_meta.get("completed", len(results))
    elapsed = batch_meta.get("elapsed_sec", 0)
    status = batch_meta.get("status", "done")  # 兼容旧 batch_meta 没 status 字段的情况
    progress_pct = round(100 * completed / total) if total else 100

    # 本 grid 渲染器只用于本 skill(game-ad-imagegen)。runner 已 reject 非 'a' 的 config,默认 'a'。
    skill_id = batch_meta.get("config", {}).get("skill") or (results[0].get("skill") if results else "a")
    skill_label = "skill A · game-ad-imagegen" if skill_id == "a" else f"skill {skill_id}"

    # 进行中 / 等挑选 anchor 时浏览器每 5s 自动刷新(awaiting_picks 时不会有新图,但 user 写完 picks 后能立刻看到 status 切回 running)
    auto_refresh = '<meta http-equiv="refresh" content="5">' if status in ("running", "awaiting_picks") else ""

    task_blocks = _build_task_blocks(out_dir, results)

    rendered = (
        tpl
        .replace("__BATCH_ID__", _esc(batch_id))
        .replace("__OUT_DIR__", _esc(str(out_dir)))
        .replace("__OK_COUNT__", str(ok_count))
        .replace("__TOTAL__", str(total))
        .replace("__COMPLETED__", str(completed))
        .replace("__ELAPSED__", str(elapsed))
        .replace("__SKILL_LABEL__", _esc(skill_label))
        .replace("__STATUS__", _esc(status))
        .replace("__STATUS_LABEL__", _STATUS_LABEL_MAP.get(status, status))
        .replace("__STATUS_HINT__", _STATUS_HINT_MAP.get(status, ""))
        .replace("__PROGRESS_PCT__", str(progress_pct))
        .replace("__AUTO_REFRESH_META__", auto_refresh)
        .replace("__TASK_BLOCKS__", task_blocks)
    )

    grid_path = out_dir / "result_grid.html"
    grid_path.write_text(rendered, encoding="utf-8")
    return grid_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", help="batch 输出目录 (含 _batch_meta.json)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    meta_path = out_dir / "_batch_meta.json"
    if not meta_path.exists():
        ap.error(f"_batch_meta.json 不存在: {meta_path}")

    batch_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    grid_path = render(out_dir, batch_meta)
    print(f"  -> {grid_path}")


if __name__ == "__main__":
    main()

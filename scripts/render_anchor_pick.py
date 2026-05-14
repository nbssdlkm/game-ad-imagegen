"""
render_anchor_pick.py — 默认 anchor-pick UI 渲染器

把 Phase 1 跑出来的 M 张候选 (每 task) 渲染成一个 HTML 单选页,
用户挑选后生成 picks JSON 让 batch_runner 接力 Phase 3。

UX:
- 每 task 一个 fieldset,M 张候选缩略图 + radio button
- 提交 → JS 生成 picks JSON
- 三种方式落盘: "Copy to clipboard" / "Download anchor_picks.json" / 显示 PowerShell 命令
- 用户把 JSON 写到 `{batch_id}_anchor_picks.json` 文件,batch_runner poll 到就接力

技术团队接手后可以替换成自己的 web UI (POST 接口写同样的 JSON 文件)。
batch_runner 只认 disk 上的 JSON,UI 完全解耦。
"""
import json
from html import escape
from pathlib import Path


def render(out_dir: Path, batch_id: str, anchor_pending_tasks: list) -> Path:
    """渲染 anchor_pick.html 到 out_dir 根目录。

    anchor_pending_tasks: list of dict, 每个含:
        - task: 原 config task dict (task_id / anchor_candidates / prompt / ...)
        - candidate_paths: list of Path (绝对路径,本 skill out_dir 内)
        - refs: list of Path (参考图)

    返回写好的 anchor_pick.html 绝对路径。
    """
    out_dir = Path(out_dir)
    html_path = out_dir / "anchor_pick.html"
    picks_file_name = f"{batch_id}_anchor_picks.json"

    # 收集 task 数据 (JSON-safe + 路径转 basename 让 HTML <img src> 相对)
    tasks_data = []
    for ap in anchor_pending_tasks:
        t = ap["task"]
        tid = t["task_id"]
        prompt_excerpt = t.get("prompt", "")[:200] + ("..." if len(t.get("prompt", "")) > 200 else "")
        cand_names = [Path(p).name for p in ap["candidate_paths"]]
        ref_names = [Path(p).name for p in ap.get("refs", [])]
        tasks_data.append({
            "task_id": tid,
            "M": int(t.get("anchor_candidates", len(cand_names))),
            "prompt_excerpt": prompt_excerpt,
            "candidates": cand_names,
            "refs": ref_names,
        })

    # JSON for embedding in <script>
    # 防 XSS / 页面破碎: 把 `</` 转义成 `<\/` 防 prompt 或 task_id 含 `</script>` 子串导致 script 块提前关闭
    tasks_json = json.dumps(tasks_data, ensure_ascii=False).replace("</", "<\\/")
    # 完整 picks_file path 给 user 落盘参考(JS 端在 Submit 后会动态生成"包含真实 picks 的 PowerShell 命令")
    picks_file_full_path = str((out_dir / picks_file_name).resolve()).replace("\\", "\\\\")

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Anchor Pick — {escape(batch_id)}</title>
<style>
  :root {{
    --fg: #1a1a1a;
    --muted: #666;
    --border: #ddd;
    --bg: #fafafa;
    --accent: #2563eb;
    --accent-hover: #1e40af;
    --picked: #dbeafe;
    --picked-border: #2563eb;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    font: 14px/1.5 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--fg);
  }}
  h1 {{ margin: 0 0 8px; font-size: 22px; }}
  .meta {{ color: var(--muted); font-size: 13px; margin-bottom: 24px; }}
  .meta code {{ background: #fff; padding: 2px 6px; border-radius: 3px; border: 1px solid var(--border); }}

  fieldset.task {{
    margin: 0 0 24px;
    padding: 16px 18px;
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 6px;
  }}
  fieldset.task legend {{
    padding: 4px 12px;
    background: #1f2937;
    color: #fff;
    font-weight: 600;
    border-radius: 3px;
  }}
  .prompt-excerpt {{
    margin: 8px 0 16px;
    padding: 10px 12px;
    background: #f3f4f6;
    border-left: 3px solid var(--accent);
    font-size: 12px;
    color: #374151;
    white-space: pre-wrap;
  }}
  .ref-strip {{
    display: flex; gap: 8px; flex-wrap: wrap;
    margin-bottom: 14px;
    padding: 8px 0;
    border-bottom: 1px dashed var(--border);
  }}
  .ref-strip .ref {{
    display: flex; flex-direction: column; align-items: center; gap: 3px;
  }}
  .ref-strip .ref img {{ width: 88px; height: 50px; object-fit: cover; border-radius: 3px; border: 1px solid var(--border); }}
  .ref-strip .ref .lbl {{ font-size: 10px; color: var(--muted); }}

  .candidates {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 12px;
  }}
  .cand-card {{
    border: 2px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
    cursor: pointer;
    transition: all 0.15s;
    background: #fff;
  }}
  .cand-card:hover {{ border-color: #93c5fd; }}
  .cand-card.picked {{
    border-color: var(--picked-border);
    background: var(--picked);
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
  }}
  .cand-card img {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; }}
  .cand-card .label {{
    padding: 8px 10px;
    display: flex; align-items: center; gap: 8px;
    font-size: 13px;
    border-top: 1px solid var(--border);
  }}
  .cand-card input[type=radio] {{ accent-color: var(--accent); }}

  .submit-area {{
    position: sticky; bottom: 0;
    margin: 32px -24px -24px;
    padding: 16px 24px;
    background: #fff;
    border-top: 2px solid var(--accent);
    box-shadow: 0 -2px 8px rgba(0,0,0,0.08);
  }}
  .submit-area button {{
    padding: 10px 18px;
    background: var(--accent);
    color: #fff;
    border: 0;
    border-radius: 4px;
    font-size: 14px;
    cursor: pointer;
    margin-right: 8px;
  }}
  .submit-area button:hover {{ background: var(--accent-hover); }}
  .submit-area button.secondary {{ background: #6b7280; }}
  .submit-area button.secondary:hover {{ background: #4b5563; }}

  #jsonOut {{
    margin-top: 12px;
    padding: 10px 12px;
    background: #1f2937;
    color: #f9fafb;
    border-radius: 4px;
    font-family: Consolas, "JetBrains Mono", monospace;
    font-size: 12px;
    white-space: pre-wrap;
    display: none;
    max-height: 200px;
    overflow: auto;
  }}
  #jsonOut.show {{ display: block; }}

  details {{ margin-top: 10px; font-size: 12px; color: var(--muted); }}
  details summary {{ cursor: pointer; }}
  details code {{
    display: block;
    margin-top: 6px;
    padding: 8px 10px;
    background: #1f2937;
    color: #f9fafb;
    border-radius: 3px;
    font-size: 11px;
    word-break: break-all;
    white-space: pre-wrap;
  }}
</style>
</head>
<body>

<h1>📋 Anchor Pick — batch <code>{escape(batch_id)}</code></h1>
<div class="meta">
  Phase 1 已跑完候选,**请为每个 task 挑选 1 张作为系列广告的 anchor**(其余 N-1 张会以该 anchor 为风格锁继续生成)。<br>
  挑完点底部「生成 JSON」,把 JSON 保存到 <code>{escape(picks_file_name)}</code> (跟本 HTML 同目录),batch_runner 自动接力跑 Phase 3。
</div>

<form id="pickForm"></form>

<div class="submit-area">
  <button type="button" id="genJson">📝 生成 picks JSON</button>
  <button type="button" id="downloadBtn" class="secondary" disabled>💾 下载 JSON 文件</button>
  <button type="button" id="copyBtn" class="secondary" disabled>📋 复制到剪贴板</button>
  <span id="status" style="margin-left:12px;color:#6b7280;font-size:12px;"></span>

  <pre id="jsonOut"></pre>

  <details>
    <summary>📂 不想下载/移动文件?Submit 后用 PowerShell 一行落盘(下方动态生成,含你实际挑选的 picks)</summary>
    <code id="psCmd" style="display:none">(先点上面「📝 生成 picks JSON」)</code>
  </details>
</div>

<script>
const TASKS = {tasks_json};
const PICKS_FILE = "{escape(picks_file_name)}";
const PICKS_FILE_FULL_PATH = "{picks_file_full_path}";

const form = document.getElementById('pickForm');
TASKS.forEach((task) => {{
  const fs = document.createElement('fieldset');
  fs.className = 'task';
  fs.dataset.taskId = task.task_id;

  const legend = document.createElement('legend');
  legend.textContent = `${{task.task_id}} (M=${{task.M}} 候选)`;
  fs.appendChild(legend);

  if (task.prompt_excerpt) {{
    const p = document.createElement('div');
    p.className = 'prompt-excerpt';
    p.textContent = `原 prompt: ${{task.prompt_excerpt}}`;
    fs.appendChild(p);
  }}

  if (task.refs && task.refs.length) {{
    const refStrip = document.createElement('div');
    refStrip.className = 'ref-strip';
    refStrip.innerHTML = '<div style="font-size:11px;color:#666;margin-right:6px">refs:</div>';
    task.refs.forEach((rname, ri) => {{
      const wrap = document.createElement('div');
      wrap.className = 'ref';
      const img = document.createElement('img');
      img.src = rname;
      img.onerror = () => {{ img.style.background = '#fee'; img.alt = '?'; }};
      const lbl = document.createElement('div');
      lbl.className = 'lbl';
      lbl.textContent = `图${{ri+1}}`;
      wrap.appendChild(img);
      wrap.appendChild(lbl);
      refStrip.appendChild(wrap);
    }});
    fs.appendChild(refStrip);
  }}

  const grid = document.createElement('div');
  grid.className = 'candidates';
  task.candidates.forEach((candName, idx) => {{
    const ci = idx + 1;
    const card = document.createElement('label');
    card.className = 'cand-card';
    card.dataset.candIdx = ci;
    // 用 DOM API 而非 innerHTML 拼字符串 — 防 candName / task_id 含 quote/< 时
    // XSS 注入(minimax-m2.7 review 抓出的真问题:innerHTML 拼接的攻击面)
    const cardImg = document.createElement('img');
    cardImg.src = candName;
    cardImg.onerror = () => {{ cardImg.style.background = '#fee'; cardImg.alt = '?'; }};
    card.appendChild(cardImg);

    const labelDiv = document.createElement('div');
    labelDiv.className = 'label';
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = `pick_${{task.task_id}}`;
    radio.value = String(ci);
    radio.required = true;
    labelDiv.appendChild(radio);
    const span = document.createElement('span');
    span.textContent = `候选 #${{ci}}`;
    labelDiv.appendChild(span);
    card.appendChild(labelDiv);

    card.addEventListener('change', () => {{
      grid.querySelectorAll('.cand-card').forEach(c => c.classList.remove('picked'));
      card.classList.add('picked');
    }});
    grid.appendChild(card);
  }});
  fs.appendChild(grid);

  form.appendChild(fs);
}});

const jsonOut = document.getElementById('jsonOut');
const downloadBtn = document.getElementById('downloadBtn');
const copyBtn = document.getElementById('copyBtn');
const statusEl = document.getElementById('status');

document.getElementById('genJson').addEventListener('click', () => {{
  const picks = {{}};
  let missing = [];
  TASKS.forEach((t) => {{
    const selected = form.querySelector(`input[name="pick_${{t.task_id}}"]:checked`);
    if (selected) {{
      picks[t.task_id] = parseInt(selected.value, 10);
    }} else {{
      missing.push(t.task_id);
    }}
  }});
  if (missing.length) {{
    statusEl.textContent = `⚠️ 还有 ${{missing.length}} 个 task 没挑选: ${{missing.join(', ')}}`;
    statusEl.style.color = '#dc2626';
    return;
  }}
  const jsonText = JSON.stringify(picks, null, 2);
  jsonOut.textContent = jsonText;
  jsonOut.classList.add('show');
  downloadBtn.disabled = false;
  copyBtn.disabled = false;
  statusEl.textContent = `✅ JSON 已生成 (${{Object.keys(picks).length}} 个 picks)。`;
  statusEl.style.color = '#16a34a';
  window._currentJsonText = jsonText;

  // 动态更新 PowerShell 一行命令(含实际 picks),user 可选复制粘贴到 terminal
  // PS here-string @'...'@ 里 ' 字面要写成 '' (single → double single quote)
  const psCmd = document.getElementById('psCmd');
  const jsonForPS = jsonText.replace(/'/g, "''");
  const psBody = '$json = @\\'\\n' + jsonForPS + '\\n\\'@ ; Set-Content -LiteralPath \\'' +
                 PICKS_FILE_FULL_PATH + '\\' -Value $json -Encoding utf8';
  psCmd.textContent = psBody;
  psCmd.style.display = 'block';
}});

downloadBtn.addEventListener('click', () => {{
  if (!window._currentJsonText) return;
  const blob = new Blob([window._currentJsonText], {{ type: 'application/json;charset=utf-8' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = PICKS_FILE;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  statusEl.textContent = `💾 已下载到 Downloads — 把 ${{PICKS_FILE}} 移到本 HTML 同目录即可。`;
}});

copyBtn.addEventListener('click', async () => {{
  if (!window._currentJsonText) return;
  try {{
    await navigator.clipboard.writeText(window._currentJsonText);
    statusEl.textContent = `📋 JSON 已复制到剪贴板。粘贴到 ${{PICKS_FILE}} 文件即可。`;
  }} catch (e) {{
    statusEl.textContent = `❌ 复制失败: ${{e.message}} — 请手动选中 JSON 文本复制。`;
  }}
}});
</script>
</body>
</html>
"""

    html_path.write_text(html, encoding="utf-8")
    return html_path

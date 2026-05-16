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

  /* Warning banner — 顶部红色边框警告,把 "本页不会自动提交" 这件事打到 user 脸上 */
  .warning-banner {{
    margin: 0 0 20px;
    padding: 14px 18px;
    background: #fef2f2;
    border: 2px solid #dc2626;
    border-left-width: 6px;
    border-radius: 6px;
    color: #7f1d1d;
    font-size: 13px;
    line-height: 1.7;
  }}
  .warning-banner .warning-title {{
    font-weight: 700;
    font-size: 15px;
    margin-bottom: 6px;
    color: #b91c1c;
  }}
  .warning-banner .warning-body code {{
    background: #fff;
    padding: 1px 6px;
    border-radius: 3px;
    border: 1px solid #fca5a5;
    color: #7f1d1d;
    font-size: 12px;
  }}
  .warning-banner .warning-body b {{ color: #991b1b; }}

  /* Verify button — 落盘验证成功时变绿,失败时变红 */
  .submit-area button.verify {{ background: #6b7280; }}
  .submit-area button.verify:hover {{ background: #4b5563; }}
  .submit-area button.verify.ok {{ background: #16a34a; }}
  .submit-area button.verify.ok:hover {{ background: #15803d; }}
  .submit-area button.verify.fail {{ background: #dc2626; }}
  .submit-area button.verify.fail:hover {{ background: #b91c1c; }}
</style>
</head>
<body>

<h1>📋 Anchor Pick — batch <code>{escape(batch_id)}</code></h1>
<div class="meta">
  Phase 1 已跑完候选,**请为每个 task 挑选 1 张作为系列广告的 anchor**(其余 N-1 张会以该 anchor 为风格锁继续生成)。
</div>

<div class="warning-banner">
  <div class="warning-title">⚠️ 重要:radio 点中 ≠ 已提交</div>
  <div class="warning-body">
    本页是<b>纯前端 HTML</b>,挑选后必须执行最后一步把 JSON <b>真正写到磁盘文件</b> <code>{escape(picks_file_name)}</code>,batch_runner 才会接续 Phase 3。<br>
    正确流程:<b>ⓐ</b> 给每个 task 挑一张 → <b>ⓑ</b> 点「📋 复制 PowerShell 命令(推荐)」 → <b>ⓒ</b> 到 terminal 粘贴 + Enter → <b>ⓓ</b> 点「✅ 验证已落盘」确认。<br>
    <b>不要只点「复制 JSON」就走</b> — 那只把 JSON 字符串放进剪贴板,文件没生成。
  </div>
</div>

<form id="pickForm"></form>

<div class="submit-area">
  <div style="margin-bottom: 10px; font-weight: 600; font-size: 13px; color: #374151;">
    挑完后请走以下 4 步任一组合(推荐 ⓑ → ⓒ → ⓓ):
  </div>
  <button type="button" id="copyPsBtn" disabled>📋 ⓑ 复制 PowerShell 命令(推荐,自动落盘)</button>
  <button type="button" id="downloadBtn" class="secondary" disabled>💾 备选:下载 JSON 文件(需自己 move 到本目录)</button>
  <button type="button" id="copyBtn" class="secondary" disabled>📋 备选:复制 JSON 文本(需自己粘到文件)</button>
  <button type="button" id="verifyBtn" class="verify" disabled>✅ ⓓ 验证已落盘</button>
  <span id="status" style="margin-left:12px;color:#6b7280;font-size:12px;"></span>

  <pre id="jsonOut"></pre>

  <details open>
    <summary>📂 PowerShell 一行命令预览(点 ⓑ 自动复制,也可手动选中复制)</summary>
    <code id="psCmd" style="display:none">(先在上面挑选候选)</code>
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
const copyPsBtn = document.getElementById('copyPsBtn');
const verifyBtn = document.getElementById('verifyBtn');
const statusEl = document.getElementById('status');
const psCmd = document.getElementById('psCmd');

// 全 task 都挑选完 → 自动生成 picks JSON + PowerShell 命令,启用所有"落盘"按钮
// (旧 UX 让 user 先点「生成 JSON」再选落盘方式,user 容易跳步 / 误以为完成。
//  新 UX 挑完即生成,落盘按钮高亮,验证按钮等落盘后绿灯)
function _regenerateOnPick() {{
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
    // 还没挑完 → 维持原状(按钮 disabled / status 不变)
    return;
  }}
  const jsonText = JSON.stringify(picks, null, 2);
  jsonOut.textContent = jsonText;
  jsonOut.classList.add('show');
  copyPsBtn.disabled = false;
  downloadBtn.disabled = false;
  copyBtn.disabled = false;
  verifyBtn.disabled = false;
  statusEl.textContent = `✅ 挑选完成 (${{Object.keys(picks).length}} picks) — 现在请点「📋 ⓑ 复制 PowerShell 命令」走 ⓒⓓ。`;
  statusEl.style.color = '#16a34a';
  window._currentJsonText = jsonText;

  // PowerShell here-string @'...'@ 里 ' 字面要写成 '' (单引号 → 双单引号)
  const jsonForPS = jsonText.replace(/'/g, "''");
  const psBody = '$json = @\\'\\n' + jsonForPS + '\\n\\'@ ; Set-Content -LiteralPath \\'' +
                 PICKS_FILE_FULL_PATH + '\\' -Value $json -Encoding utf8';
  psCmd.textContent = psBody;
  psCmd.style.display = 'block';
}}

// 监听所有 radio 改变,挑完自动 regenerate
form.addEventListener('change', _regenerateOnPick);

// ⓑ 复制 PowerShell 命令 — 推荐落盘方式 (自动落盘到正确路径)
copyPsBtn.addEventListener('click', async () => {{
  const psBody = psCmd.textContent;
  if (!psBody || psBody.startsWith('(')) return;
  try {{
    await navigator.clipboard.writeText(psBody);
    statusEl.textContent = `📋 PowerShell 命令已复制 — 请到 terminal 粘贴 + 按 Enter,然后回来点「✅ ⓓ 验证已落盘」。`;
    statusEl.style.color = '#2563eb';
  }} catch (e) {{
    statusEl.textContent = `❌ 自动复制失败 (${{e.message}}) — 请手动选中下方 PowerShell 命令文本复制。`;
    statusEl.style.color = '#dc2626';
  }}
}});

// 备选 ⓑ': 下载 JSON 文件
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
  statusEl.textContent = `💾 已下载到 Downloads — 把 ${{PICKS_FILE}} 移到本 HTML 同目录后,点「✅ ⓓ 验证」。`;
  statusEl.style.color = '#2563eb';
}});

// 备选 ⓑ'': 复制 JSON 文本(user 需要自己粘到文件)
copyBtn.addEventListener('click', async () => {{
  if (!window._currentJsonText) return;
  try {{
    await navigator.clipboard.writeText(window._currentJsonText);
    statusEl.textContent = `📋 JSON 已复制 — 请创建文件 ${{PICKS_FILE}} 粘贴内容,然后点「✅ ⓓ 验证」。`;
    statusEl.style.color = '#2563eb';
  }} catch (e) {{
    statusEl.textContent = `❌ 复制失败: ${{e.message}} — 请手动选中 JSON 文本复制。`;
    statusEl.style.color = '#dc2626';
  }}
}});

// ⓓ 验证已落盘 — fetch 试读同目录 PICKS_FILE
// file:// 协议在 Chrome 默认拦 fetch (CORS),Firefox 默认允许同目录读;
// 拦截时给 user fallback 文案 (PowerShell Test-Path 一行 verify) — 不闭环但比静默好
verifyBtn.addEventListener('click', async () => {{
  if (!window._currentJsonText) return;
  verifyBtn.classList.remove('ok', 'fail');
  statusEl.textContent = `⏳ 验证中...`;
  statusEl.style.color = '#6b7280';
  try {{
    const resp = await fetch(PICKS_FILE, {{cache: 'no-store'}});
    if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
    const actualText = await resp.text();
    const actualJson = JSON.parse(actualText);
    const expectedJson = JSON.parse(window._currentJsonText);
    // 对比 keys + values
    const keysOK = JSON.stringify(Object.keys(actualJson).sort()) === JSON.stringify(Object.keys(expectedJson).sort());
    const valuesOK = Object.keys(expectedJson).every(k => parseInt(actualJson[k], 10) === parseInt(expectedJson[k], 10));
    if (keysOK && valuesOK) {{
      verifyBtn.classList.add('ok');
      verifyBtn.textContent = '✅ 已落盘且内容一致';
      statusEl.textContent = `🎉 picks.json 已确认落盘,batch_runner 30s 内会发现并接续 Phase 3 (如果还在 poll)。`;
      statusEl.style.color = '#16a34a';
    }} else {{
      verifyBtn.classList.add('fail');
      verifyBtn.textContent = '⚠️ 文件存在但内容不一致';
      statusEl.textContent = `⚠️ 文件存在但 picks 跟当前选择不一致 — 可能是旧 picks。请重新点 ⓑⓒ 落盘最新选择。`;
      statusEl.style.color = '#dc2626';
    }}
  }} catch (e) {{
    verifyBtn.classList.add('fail');
    verifyBtn.textContent = '❌ 验证失败';
    // file:// CORS 拦时 fetch 抛 TypeError;给一行 PS 让 user 自己 verify
    statusEl.innerHTML = `❌ 自动验证不可用 (浏览器 file:// 限制,${{e.message}})。请到 terminal 跑: ` +
                         `<code style="background:#fff;padding:1px 4px;border:1px solid #fca5a5;border-radius:3px">Test-Path '${{PICKS_FILE_FULL_PATH}}'</code> 看是否 True。`;
    statusEl.style.color = '#dc2626';
  }}
}});
</script>
</body>
</html>
"""

    html_path.write_text(html, encoding="utf-8")
    return html_path

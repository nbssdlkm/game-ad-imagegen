'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function timestamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function makeBatchId() {
  return `batch_${timestamp()}`;
}

$('#batch_id').value = makeBatchId();

let taskCounter = 0;

function addTask() {
  taskCounter += 1;
  const tpl = document.getElementById('task_template');
  const node = tpl.content.firstElementChild.cloneNode(true);
  node.dataset.taskIdx = String(taskCounter);
  node.querySelector('.task-id').textContent = `任务 t${String(taskCounter).padStart(2, '0')}`;
  node.querySelector('.btn-del').addEventListener('click', () => {
    node.remove();
    renumberTasks();
  });
  $('#task_list').appendChild(node);
}

function renumberTasks() {
  const cards = $$('#task_list .task-card');
  cards.forEach((card, i) => {
    const idx = i + 1;
    card.dataset.taskIdx = String(idx);
    card.querySelector('.task-id').textContent = `任务 t${String(idx).padStart(2, '0')}`;
  });
  taskCounter = cards.length;
}

function splitLines(text) {
  return (text || '')
    .split(/[\r\n]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

// 本 form 专属 A skill (game-ad-imagegen)。生成的 config.json 写死 skill='a'。
// B skill 有自己独立的 batch_form.html(不共用)。
const THIS_SKILL = 'a';

function buildConfig() {
  const batchId = $('#batch_id').value.trim();
  let outRoot = $('#out_root').value.trim().replace(/[\\/]+$/, '');
  // 留空 → fallback 到 ~/Desktop/game-ad-imagegen-out/(由 runner 解析 ~)
  // 这里用一个占位符 token,runner 会展开
  if (!outRoot) {
    outRoot = '~/Desktop/game-ad-imagegen-out';
  }
  const defaultSize = $('#default_size').value;
  const defaultQuality = $('#default_quality').value;

  const tasks = [];
  const cards = $$('#task_list .task-card');
  cards.forEach((card, i) => {
    const reference_images = splitLines(card.querySelector('.reference_images').value);
    const prompt = card.querySelector('.prompt').value.trim();
    const n = parseInt(card.querySelector('.n').value, 10) || 1;
    const anchorCandidates = parseInt(card.querySelector('.anchor_candidates').value, 10) || 0;
    const sizeOverride = card.querySelector('.size_override').value;
    const qualityOverride = card.querySelector('.quality_override').value;

    const task = {
      task_id: `t${String(i + 1).padStart(2, '0')}`,
      reference_images,
      prompt,
      n,
    };
    // anchor_candidates: 只在 anchorCandidates >= 2 AND n >= 2 时写入。
    // 单图场景(n=1)即使 form 默认 anchor=3 也不写,避免 batch_runner 报
    // "anchor_candidates=3 但 n=1" 校验错误 — n=1 不可能跑 anchor mode(没 series 可生)。
    if (anchorCandidates >= 2 && n >= 2) task.anchor_candidates = anchorCandidates;
    if (sizeOverride) task.size = sizeOverride;
    if (qualityOverride) task.quality = qualityOverride;
    tasks.push(task);
  });

  return {
    batch_id: batchId,
    skill: THIS_SKILL,
    out_dir: `${outRoot}/${batchId}/`,
    size: defaultSize,
    quality: defaultQuality,
    tasks,
  };
}

// 检测 prompt 是否字面写了"N 张/N幅/N个" 之类多数词,会触发 image model 拼图行为
const MULTI_COUNT_RE = /([0-9]+|[一二三四五六七八九十两])\s*(张|幅|个|份|套)\s*(图|宣传图|海报|广告图|图片|画面|panel|frame)?/;

function validateConfig(cfg) {
  const errs = [];
  if (!cfg.batch_id) errs.push('批次 ID 为空');
  if (!cfg.out_dir) errs.push('输出根目录为空');
  if (cfg.tasks.length === 0) errs.push('至少要一个任务');
  cfg.tasks.forEach((t) => {
    const label = `任务 ${t.task_id}`;
    if (!t.prompt) errs.push(`${label}:中文 prompt 不能空`);
    if (t.n < 1 || t.n > 10) errs.push(`${label}:出图数应在 1-10`);
    // A skill 现支持 0 图(text2im 买量素材) + ≥1 图(edit)。anchor mode 仍需 ≥1 图。
    if (t.reference_images.length === 0 && t.anchor_candidates) {
      errs.push(`${label}:anchor 模式需要 ≥1 张参考图(0 图 text2im 不支持 anchor 流程)。删 anchor 字段或加图`);
    }
  });
  return errs;
}

// 软 warning,不 block 提交,只提示
function softWarnings(cfg) {
  const warns = [];
  cfg.tasks.forEach((t) => {
    const m = t.prompt.match(MULTI_COUNT_RE);
    if (m) {
      warns.push(`任务 ${t.task_id} prompt 含 "${m[0]}" — image model 可能把多张拼成 1 张 PNG 拼图。建议改成"做一张..."(单数),数量靠"出图数"字段控制。`);
    }
  });
  return warns;
}

async function copyToClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) {
    // 落到 fallback
  }
  // Fallback: execCommand
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch (e) {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}

function setStatus(msg, ok) {
  const el = $('#status_msg');
  el.textContent = msg;
  el.className = ok ? 'status ok' : 'status err';
}

async function generate() {
  const cfg = buildConfig();
  const errs = validateConfig(cfg);
  if (errs.length) {
    alert('请先修正以下问题：\n\n- ' + errs.join('\n- '));
    return;
  }

  // 软 warning: 提示但不 block,user 确认才继续
  const warns = softWarnings(cfg);
  if (warns.length) {
    const proceed = confirm('⚠️ 检测到 prompt 写了多数词,可能会触发"拼图"现象:\n\n- ' + warns.join('\n- ') + '\n\n要继续生成吗?(取消 = 回去改 prompt;确定 = 知道了,就是这么要)');
    if (!proceed) return;
  }

  const json = JSON.stringify(cfg, null, 2);
  $('#json_preview').textContent = json;
  $('#trigger_phrase').textContent = `跑 ${cfg.batch_id}`;

  // 下载链接
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = $('#btn_download');
  link.href = url;
  link.download = `${cfg.batch_id}.json`;

  $('#output_box').style.display = 'block';
  $('#output_box').scrollIntoView({ behavior: 'smooth', block: 'start' });

  const copied = await copyToClipboard(json);
  setStatus(copied ? '✅ 已复制到剪贴板' : '⚠️ 复制失败，请用「下载」或手动复制下面 JSON', copied);

  // 把当前 batch_id 顺手刷掉，方便下次连跑（避免重复）
  // 不刷 — 用户可能想再点一次，留着方便核对
}

$('#btn_add').addEventListener('click', addTask);
$('#btn_generate').addEventListener('click', generate);
$('#btn_copy').addEventListener('click', async () => {
  const text = $('#json_preview').textContent;
  const ok = await copyToClipboard(text);
  setStatus(ok ? '✅ 已重新复制' : '⚠️ 复制失败', ok);
});

// 启动时加一行默认任务
addTask();

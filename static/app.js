/* ai-xiaoshuo · 以 skill 为核心的番茄 AI 写作平台 —— 前端 */
const $ = (s, r = document) => r.querySelector(s);
const STATE = { flows: [], flow: null, book: null, steps: [], step: null, chapter: null,
                models: [], genTimer: null, kbMods: [] };

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// 只放行 http/https：web_url 是用户可填字段，不校验协议的话 javascript: 形态会被点开执行
const safeUrl = (u) => (/^https?:\/\//i.test(String(u || '')) ? String(u) : '');

async function api(path, opts = {}) {
  const o = Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts);
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const res = await fetch(path, o);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

/* ---------------- 页面内弹窗（预览面板里原生 confirm 会被拦截） ---------------- */
let _modalResolve = null;
function showModal(msg, { withInput = false, value = '', okText = '确定', cancel = true } = {}) {
  $('#modal-msg').textContent = msg;
  const inp = $('#modal-input');
  inp.style.display = withInput ? 'block' : 'none';
  inp.value = value;
  $('#modal-ok').textContent = okText;
  $('#modal-cancel').style.display = cancel ? '' : 'none';
  $('#mask').classList.remove('hidden');
  return new Promise((r) => { _modalResolve = r; if (withInput) setTimeout(() => inp.focus(), 30); });
}
function closeModal(v) { $('#mask').classList.add('hidden'); const r = _modalResolve; _modalResolve = null; if (r) r(v); }
const alertBox = (m) => showModal(m, { cancel: false, okText: '知道了' }).then(() => {});
const confirmBox = (m) => showModal(m);
$('#modal-ok').addEventListener('click', () => closeModal($('#modal-input').style.display === 'none' ? true : $('#modal-input').value));
$('#modal-cancel').addEventListener('click', () => closeModal(false));

/* ---------------- 页签 ---------------- */
function goTab(name, keepHash) {
  if (!keepHash) { try { history.replaceState(null, '', '#' + name); } catch (e) { /* file:// 下可能失败 */ } }
  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${name}`));
  if (name === 'kb') kbLoad();
  if (name === 'checks') loadCheckBooks();
  if (name === 'short') shortLoad();
  // 切回「写作流程」时刷新书架：否则刚在「短故事」里建的书不会出现在下拉里，
  // 用户误以为是没建成，继续在旧书上操作（实测踩过：产出写进了上一本书）。
  if (name === 'flow') loadBooks();
  if (name === 'models') loadCostOverview();
}
document.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', () => goTab(b.dataset.tab)));

/* ==================== 知识库 ==================== */
async function kbLoad() {
  try {
    const info = await api('/api/kb/info');
    STATE.kbMods = info.modules || [];
    $('#kb-badge').textContent = `${info.modules.length} 模块 · ${(info.total_chars / 10000).toFixed(1)} 万字 · ${info.total_sections} 节`;
    $('#kb-routing').innerHTML = (info.routing || []).map((r) => `
      <div class="item" data-kb="${esc(r.file)}">
        <div class="n">${esc(r.tag)} · ${esc(r.intent)}</div>
        <div class="d">→ 《${esc(r.file)}》</div>
      </div>`).join('');
    $('#kb-modules').innerHTML = STATE.kbMods.map((m) => `
      <div class="item" data-kb="${esc(m.file)}">
        <div class="n">${esc(m.tag)} ${esc((m.name || '').replace(/^\S+\s*/, ''))}</div>
        <div class="d">${esc(m.purpose)}｜${m.sections} 节 / ${m.chars} 字</div>
      </div>`).join('');
  } catch (e) { $('#kb-badge').textContent = '读取失败：' + e.message; }
}

async function kbRead(name) {
  $('#kb-msg').textContent = '读取中…';
  try {
    const r = await api('/api/kb/module?name=' + encodeURIComponent(name));
    STATE.kbCur = { name, sections: r.sections, text: r.text };
    $('#kb-title').textContent = `阅读：${name}`;
    // 默认只显示「模块说明」节（大模块整体塞进页面会卡，改成点节读单节）
    const head = (r.sections || [])[0];
    $('#kb-out').textContent = head ? `【${head.heading}】\n\n` + r.text.split('## 📄')[0].trim() : r.text.slice(0, 4000);
    $('#kb-sections').innerHTML = (r.sections || []).map((s) => `
      <div class="item" data-kbsec="${esc(s.heading)}">
        <div class="n">${esc(s.heading)}</div>
        <div class="d">${s.chars} 字</div>
      </div>`).join('');
    $('#kb-msg').textContent = `${r.chars} 字 · ${(r.sections || []).length} 节（点下方节名读单节）`;
  } catch (e) { $('#kb-msg').textContent = '失败：' + e.message; }
}

async function kbSection(heading) {
  if (!STATE.kbCur) return;
  try {
    const r = await api(`/api/kb/section?name=${encodeURIComponent(STATE.kbCur.name)}&heading=${encodeURIComponent(heading)}`);
    $('#kb-out').textContent = `【${heading}】\n\n` + r.text;
    $('#kb-title').textContent = `阅读：${STATE.kbCur.name} · ${heading}`;
    $('#kb-msg').textContent = `${r.text.length} 字`;
  } catch (e) { $('#kb-msg').textContent = '失败：' + e.message; }
}

async function kbWhole() {
  if (!STATE.kbCur) { await alertBox('先点左侧一个模块'); return; }
  $('#kb-out').textContent = STATE.kbCur.text;
  $('#kb-msg').textContent = `整篇 ${STATE.kbCur.text.length} 字（大模块建议用分节浏览）`;
}

async function kbSearch() {
  const q = $('#kb-q').value.trim();
  if (!q) return;
  $('#kb-msg').textContent = '检索中…';
  try {
    const r = await api('/api/kb/search', { method: 'POST', body: { query: q } });
    $('#kb-title').textContent = `检索：${q}`;
    $('#kb-out').textContent = r.report;
    $('#kb-msg').textContent = `命中 ${r.hits} 处 · 显示 ${r.shown} 条 · ${r.chars} 字`;
  } catch (e) { $('#kb-msg').textContent = '失败：' + e.message; }
}

async function kbBlocks() {
  const mods = $('#kb-mods').value.split(/[,，\s]+/).filter(Boolean);
  const kws = $('#kb-kws').value.split(/[,，\s]+/).filter(Boolean);
  if (!mods.length) { await alertBox('请填模块，如：02d,05'); return; }
  $('#kb-blocks-out').innerHTML = '<div class="hint">抽取中…</div>';
  try {
    const r = await api('/api/kb/blocks', { method: 'POST', body: { modules: mods, keywords: kws } });
    if (!r.count) { $('#kb-blocks-out').innerHTML = '<div class="hint">没有命中。换个关键词或模块试试。</div>'; return; }
    $('#kb-blocks-out').innerHTML = `<div class="hint">命中 ${r.count} 块 / ${r.chars} 字</div>` +
      r.blocks.map((b, i) => `
        <details class="g"><summary>《${esc(b.module)}》· ${esc(b.heading)}（${b.chars} 字）</summary>
          <div class="row"><button data-copy="${i}">复制原文</button></div>
          <pre>${esc(b.text)}</pre>
        </details>`).join('');
    $('#kb-blocks-out').querySelectorAll('[data-copy]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const b = r.blocks[+btn.dataset.copy];
        try { await navigator.clipboard.writeText(b.directive || b.text); btn.textContent = '已复制 ✓'; }
        catch { await alertBox('浏览器拒绝了复制，请手动选中文本复制。'); }
      });
    });
  } catch (e) { $('#kb-blocks-out').innerHTML = `<div class="err">失败：${esc(e.message)}</div>`; }
}

/* ==================== 写作流程 ==================== */
async function loadFlows() {
  const r = await api('/api/flows');
  STATE.flows = r.flows || [];
  $('#book-flow-sel').innerHTML = STATE.flows.map((f) => `<option value="${f.id}">${esc(f.name)}</option>`).join('');
}

async function loadBooks(keep) {
  const r = await api('/api/books');
  const books = r.books || [];
  const sel = $('#book-sel');
  // 没指定也没选过时，自动打开最近在写的那本（否则新用户进来是一片空白，看不到任何产出）
  const cur = keep || sel.value || (books[0] && books[0].id) || '';
  sel.innerHTML = '<option value="">（未选择）</option>' + books.map((b) =>
    `<option value="${b.id}">${esc(b.name)}｜${b.chapters} 章 / ${b.artifacts} 项产出</option>`).join('');
  if (cur) sel.value = cur;
  if (cur && books.some((b) => b.id === cur)) await openBook(cur);
  else $('#steps').innerHTML = '<div class="hint">还没有书。用上面的「新建」建一本。</div>';
  $('#flow-hint').textContent = `${books.length} 本书`;
}

async function createBook() {
  const name = $('#book-name').value.trim();
  if (!name) { await alertBox('请填书名'); return; }
  const r = await api('/api/books', { method: 'POST', body: {
    name, genre: $('#book-genre').value.trim(), brief: $('#book-brief').value.trim(),
    audience: $('#book-aud').value, flow_id: $('#book-flow-sel').value } });
  $('#book-name').value = ''; $('#book-genre').value = ''; $('#book-brief').value = '';
  await loadBooks(r.book.id);
  await kbLoad();  // 书名变了，无所谓；保持一致性
}

async function deleteBook() {
  if (!STATE.book) { await alertBox('先选一本书'); return; }
  if (!(await confirmBox(`删除《${STATE.book.name}》？连同它的产出、章节、检查记录一起删。`))) return;
  await api('/api/books/' + STATE.book.id, { method: 'DELETE' });
  STATE.book = null; STATE.steps = []; STATE.step = null;
  await loadBooks('');
}

async function openBook(id) {
  const r = await api(`/api/books/${id}/flow`);
  STATE.book = r.book; STATE.flow = r.flow; STATE.steps = r.steps;
  const nc = r.book.name_check || { chars: (r.book.name || '').length, ok: true, over: 0, bad_punct: [] };
  const nameWarn = nc.ok ? '' :
    `　<span class="err">⚠️ 书名 ${nc.chars}/15 字${nc.over ? `，超限 ${nc.over} 字` : ''}`
    + `${nc.bad_punct && nc.bad_punct.length ? `，含英文标点：${nc.bad_punct.join(' ')}` : ''}`
    + `（番茄硬限制，发布必被拦）</span>`;
  $('#book-meta').innerHTML = `当前书：<b>${esc(r.book.name)}</b>　<span class="hint">书名 ${nc.chars}/15 字</span>`
    + `｜题材 ${esc(r.book.genre || '未填')}｜`
    + `受众 ${({ male: '男频', female: '女频', none: '不限' })[r.book.audience] || '不限'}｜`
    + `流程 ${esc(r.flow.name)}｜章节 ${r.chapters.length} 章${nameWarn}`;
  $('#steps-flow').textContent = r.flow.source || '';
  $('#steps-count').textContent = `${r.steps.filter((s) => s.has_output).length}/${r.steps.length} 步有产出`;
  $('#steps').innerHTML = r.steps.map((s) => `
    <div class="item${s.is_current ? ' active' : ''}" data-step="${s.id}">
      <div class="n"><span class="dot ${s.has_output ? 'done' : (s.is_current ? 'cur' : '')}"></span>${s.no}. ${esc(s.name)}
        ${s.locked ? '🔒' : ''}</div>
      <div class="d">${s.has_output ? `已产出 ${s.chars} 字` : '未开始'}${s.stage === 'chapter' ? '｜章节级' : ''}</div>
    </div>`).join('');
  await loadChaptersForBook(id);
  // 打开当前步骤（或第一步）
  const cur = r.steps.find((s) => s.is_current) || r.steps[0];
  if (cur) await openStep(cur.id);
}

function chapterStep(kind) {
  const want = kind === 'deai' ? ['deai', 'polish', 'hook', 'deai_fix'] : ['chapter', 'draft'];
  return (STATE.steps || []).find((s) => s.stage === 'chapter' && want.includes(s.id))
      || (STATE.steps || []).find((s) => s.stage === 'chapter');
}

async function openStep(stepId, chapterId, flowId) {
  if (!STATE.book) return;
  STATE.chapterForStep = chapterId || null;
  STATE.stepFlow = flowId || '';
  const qs = [];
  if (chapterId) qs.push('chapter_id=' + chapterId);
  if (flowId) qs.push('flow=' + flowId);
  const q = qs.length ? '?' + qs.join('&') : '';
  const d = await api(`/api/books/${STATE.book.id}/step/${stepId}${q}`);
  STATE.step = d.step;
  $('#step-title').innerHTML = `${d.step.no}. ${esc(d.step.name)} <span class="badge">${d.step.stage === 'chapter' ? '章节级' : '全书级'}</span>`;
  $('#step-goal').textContent = d.step.goal;
  $('#step-mods').innerHTML = `依据模块：${(d.modules || []).join('、')}｜关键词：${(d.step.keywords || []).join('、')}`
    + `｜流程出处：${esc((STATE.flow || {}).source || '')}`;
  $('#art-text').value = d.artifact.content || '';
  $('#art-lock').checked = !!d.artifact.locked;
  $('#art-state').textContent = d.artifact.content ? `${d.artifact.content.length} 字${d.artifact.locked ? ' · 已锁定' : ''}` : '（空）';
  $('#gen-area').innerHTML = '';
  // 本步默认模型绑定
  $('#step-model').innerHTML = '<option value="">（不绑定，用全局默认）</option>' +
    STATE.models.filter((m) => m.enabled).map((m) =>
      `<option value="${m.id}">${esc(m.name)}${m.is_default ? '（全局默认）' : ''}</option>`).join('');
  $('#step-model').value = d.step_model_id || '';
  STATE.stepModelId = d.step_model_id || '';
  $('#step-model-msg').textContent = d.step_model_id ? '已绑定（生成时优先用它）' : '';
  document.querySelectorAll('#steps .item').forEach((el) => el.classList.toggle('active', el.dataset.step === stepId));

  // skill 原文指令块
  $('#step-blocks').innerHTML = (d.blocks || []).length
    ? `<h2 style="margin-top:12px">手册原文指令（本步依据，${d.blocks.length} 块）</h2>` +
      d.blocks.map((b, i) => `
        <details class="g"${i === 0 ? ' open' : ''}>
          <summary>《${esc(b.module)}》· ${esc(b.heading)}（${b.chars} 字）</summary>
          <div class="row"><button data-copyblock="${i}">复制指令原文</button></div>
          <pre>${esc(b.text)}</pre>
        </details>`).join('')
    : '<div class="hint">本步没有抽取到手册原文块（可换个关键词，或到「知识库 → 指令块抽取器」手动抽）。</div>';
  $('#step-blocks').querySelectorAll('[data-copyblock]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const b = d.blocks[+btn.dataset.copyblock];
      try { await navigator.clipboard.writeText(b.directive || b.text); btn.textContent = '已复制 ✓'; }
      catch { await alertBox('浏览器拒绝了复制，请手动选中复制。'); }
    });
  });

  // 提示词预览
  $('#gen-prompt').onclick = async () => {
    const r = await api(`/api/books/${STATE.book.id}/step/${stepId}${q}`);
    $('#gen-area').innerHTML = `<details class="g" open><summary>将发送给模型的完整提示词（${r.prompt.length} 字）</summary>`
      + `<div class="row"><button id="copy-prompt">复制提示词</button></div><pre>${esc(r.prompt)}</pre></details>`;
    $('#copy-prompt').onclick = async () => {
      try { await navigator.clipboard.writeText(r.prompt); $('#copy-prompt').textContent = '已复制 ✓'; }
      catch { await alertBox('浏览器拒绝了复制。'); }
    };
  };

  renderStepChecks(d.checks || {});
}

function renderStepChecks(checks) {
  const box = $('#step-checks');
  const keys = Object.keys(checks);
  if (!keys.length) { box.innerHTML = ''; return; }
  box.innerHTML = '<h2 style="margin-top:14px">最近检查结果</h2>' + keys.map((k) => {
    const c = checks[k];
    return `<details class="g"><summary>${c.kind} · ${c.ok ? '<span class="okk">通过</span>' : '<span class="err">未通过</span>'}
      <span class="hint">${esc(c.created_at)}</span></summary><pre>${esc(c.report)}</pre></details>`;
  }).join('');
}

async function saveArtifact() {
  if (!STATE.book || !STATE.step) return;
  try {
    const r = await api(`/api/books/${STATE.book.id}/artifacts/${STATE.step.id}`, { method: 'PUT', body: {
      content: $('#art-text').value, locked: $('#art-lock').checked ? 1 : 0 } });
    $('#art-state').textContent = `${r.chars} 字${r.locked ? ' · 已锁定' : ''}`;
    await refreshSteps();
  } catch (e) { await alertBox('保存失败：' + e.message); }
}

async function refreshSteps() {
  const r = await api(`/api/books/${STATE.book.id}/flow`);
  STATE.steps = r.steps;
  $('#steps-count').textContent = `${r.steps.filter((s) => s.has_output).length}/${r.steps.length} 步有产出`;
  $('#steps').innerHTML = r.steps.map((s) => `
    <div class="item${STATE.step && s.id === STATE.step.id ? ' active' : ''}" data-step="${s.id}">
      <div class="n"><span class="dot ${s.has_output ? 'done' : (s.is_current ? 'cur' : '')}"></span>${s.no}. ${esc(s.name)} ${s.locked ? '🔒' : ''}</div>
      <div class="d">${s.has_output ? `已产出 ${s.chars} 字` : '未开始'}</div>
    </div>`).join('');
}

async function genStep(stepId, chapterId) {
  if (!STATE.book) { await alertBox('先选一本书'); return; }
  const body = { step_id: stepId, extra: $('#gen-extra').value.trim() || '',
                 model_id: $('#gen-model').value || '' };
  if (chapterId) body.chapter_id = chapterId;
  if (STATE.stepFlow) body.flow = STATE.stepFlow;
  if (!stepId) { await alertBox('先在左侧选一个步骤'); return; }
  $('#gen-area').innerHTML = '<div class="hint">已提交，生成中…</div>';
  $('#gen-run').disabled = true;
  try {
    const r = await api(`/api/books/${STATE.book.id}/generate`, { method: 'POST', body });
    if (r.awaiting) { renderWebChannel(r); return; }
    if (r.error) { $('#gen-area').innerHTML = `<div class="err">${esc(r.error)}</div>`; return; }
    $('#gen-area').innerHTML = `<div class="hint">模型：${esc(r.model)}｜引用手册原文 ${r.blocks_used} 块｜提示词 ${r.prompt_chars} 字。生成中…</div>`;
    pollGen(r.gen_id, async (g) => {
      if (g.status === 'success') {
        $('#gen-area').innerHTML = `<div class="hint okk">✓ 完成（${g.chars} 字，${g.model_name}）</div>`
          + `<details class="g"><summary>生成结果（已自动写入产出）</summary><pre>${esc(g.output)}</pre></details>`;
        if (chapterId) await openChapter(chapterId); else { $('#art-text').value = ''; await openStep(stepId); }
        await refreshSteps();
      } else {
        $('#gen-area').innerHTML = `<div class="err">✗ 失败：${esc(g.error)}</div>`
          + `<details class="g"><summary>查看提示词</summary><pre>${esc(g.prompt || '')}</pre></details>`;
      }
    });
  } catch (e) {
    $('#gen-area').innerHTML = `<div class="err">提交失败：${esc(e.message)}</div>`;
  } finally { $('#gen-run').disabled = false; }
}

function renderWebChannel(r) {
  const url = safeUrl(r.web_url);
  $('#gen-area').innerHTML = `
    <div class="g">网页版人工通道：复制下面的提示词 → 到网页 AI 执行 → 把结果粘回这里提交。
      ${url ? `入口：<a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a>` : ''}
      <div class="row"><button id="web-copy">复制提示词</button></div>
      <pre>${esc(r.prompt)}</pre>
      <textarea id="web-out" rows="8" placeholder="把网页 AI 的回复粘到这里，然后点提交"></textarea>
      <div class="row"><button id="web-submit" class="primary">提交结果</button></div>
    </div>`;
  $('#web-copy').onclick = async () => {
    try { await navigator.clipboard.writeText(r.prompt); $('#web-copy').textContent = '已复制 ✓'; }
    catch { await alertBox('浏览器拒绝了复制。'); }
  };
  $('#web-submit').onclick = async () => {
    const out = $('#web-out').value.trim();
    if (!out) { await alertBox('先把结果粘进来'); return; }
    const res = await api(`/api/gens/${r.gen_id}/submit`, { method: 'POST', body: { output: out } });
    await alertBox('已保存：' + (res.note || ''));
    if (STATE.chapter) await openChapter(STATE.chapter.id); else await openStep(STATE.step.id);
  };
}

function pollGen(gid, done) {
  clearInterval(STATE.genTimer);
  STATE.genTimer = setInterval(async () => {
    try {
      const r = await api('/api/gens/' + gid);
      const g = r.gen;
      if (g.status !== 'running') { clearInterval(STATE.genTimer); await done(g); }
    } catch (e) { clearInterval(STATE.genTimer); await done({ status: 'failed', error: e.message }); }
  }, 2500);
}

/* ==================== 章节 ==================== */
async function loadChaptersForBook(id) {
  const sel = $('#ch-book');
  const r = await api('/api/books');
  sel.innerHTML = (r.books || []).map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
  sel.value = id;
  await loadChapters();
}

async function loadChapters() {
  const pid = $('#ch-book').value;
  if (!pid) { $('#ch-list').innerHTML = '<div class="hint">先建一本书</div>'; return; }
  const r = await api(`/api/books/${pid}/chapters`);
  STATE.chapters = r.chapters;
  $('#ch-meta').textContent = `${r.chapters.length} 章｜共 ${r.chapters.reduce((s, c) => s + c.words, 0)} 字`;
  $('#ch-list').innerHTML = r.chapters.length ? r.chapters.map((c) => `
    <div class="item${STATE.chapter && c.id === STATE.chapter.id ? ' active' : ''}" data-ch="${c.id}">
      <div class="n">第 ${c.no} 章 ${esc(c.title || '')}</div>
      <div class="d">${c.words} 字 · ${c.status === 'done' ? '已成稿' : '草稿'}</div>
    </div>`).join('') : '<div class="hint">还没有章节，点右上「＋ 新建章节」</div>';
  if (STATE.chapter) {
    const still = r.chapters.find((c) => c.id === STATE.chapter.id);
    if (!still) { STATE.chapter = null; $('#ch-text').value = ''; $('#ch-head').textContent = '选择或新建一章'; }
  }
}

async function createChapter() {
  const pid = $('#ch-book').value;
  if (!pid) { await alertBox('先在「写作流程」里建一本书'); return; }
  const r = await api(`/api/books/${pid}/chapters`, { method: 'POST', body: {
    title: $('#ch-title').value.trim(), brief: $('#ch-brief').value.trim() } });
  $('#ch-title').value = ''; $('#ch-brief').value = '';
  if (!r.created) await alertBox(r.message);
  await loadChapters();
  await openChapter(r.chapter.id);
}

async function openChapter(cid) {
  const r = await api('/api/chapters/' + cid);
  STATE.chapter = r.chapter;
  $('#ch-head').innerHTML = `第 ${r.chapter.no} 章 <span class="badge">${esc(r.chapter.title || '无标题')}</span>`
    + ` <span class="badge">${r.chapter.status === 'done' ? '已成稿' : '草稿'}</span>`;
  $('#ch-text').value = r.chapter.content;
  $('#ch-words').textContent = `${r.chapter.words} 字（不计标题行）`;
  $('#ch-summary').value = r.chapter.summary || '';
  $('#ch-summary-badge').textContent = r.chapter.summary ? `${r.chapter.summary.length} 字` : '（未生成）';
  $('#ch-gen-area').innerHTML = '';
  $('#ch-task').innerHTML = '';
  try {
    const pid = $('#ch-book').value;
    const f = await api('/api/books/' + pid + '/foreshadows');
    $('#ch-fore').innerHTML = f.count
      ? '伏笔：待回收 <b>' + f.count + '</b> 条' + (f.warnings.length ? '　⚠️ ' + esc(f.warnings.join('；')) : '')
      : '伏笔：暂无待回收';
  } catch (e) {
    $('#ch-fore').textContent = '';
  }
  await loadChapters();
  await loadGens();
}

async function loadGens() {
  const pid = $('#ch-book').value;
  if (!pid) { $('#ch-gens').innerHTML = ''; return; }
  const r = await api(`/api/books/${pid}/gens?limit=15`);
  $('#ch-gens-badge').textContent = `${r.gens.length} 条`;
  $('#ch-gens').innerHTML = r.gens.length ? r.gens.map((g) => `
    <div class="item" data-gen="${g.id}">
      <div class="n">${esc(g.step_id)} · ${esc(g.model_name)}
        ${g.status === 'success' ? '<span class="okk">✓</span>' : (g.status === 'running' ? '⏳' : '<span class="err">✗</span>')}
        <span class="hint">${g.chars || 0} 字 · ${esc((g.started_at || '').replace('T', ' '))}</span></div>
      <div class="d">${esc(g.preview || '')}</div>
    </div>`).join('') : '<div class="hint">还没有生成记录</div>';
}

async function showGen(gid) {
  const r = await api('/api/gens/' + gid);
  const g = r.gen;
  if (g.status === 'awaiting') { renderWebChannel({ gen_id: gid, prompt: g.prompt }); return; }
  $('#ch-gen-area').innerHTML = `
    <details class="g" open><summary>${esc(g.step_id)} · ${esc(g.model_name)} · ${g.status} · ${g.chars || 0} 字</summary>
      <div class="row">
        <button id="gen-copy-out">复制输出</button>
        <button id="gen-copy-prompt">复制提示词</button>
        <button id="gen-retry">按原提示词重试</button>
        <button id="gen-adopt" title="把这条结果写回它的目标（正文 / 产出 / 摘要 / 伏笔台账）——改写被字数保护拦下时、或想回滚到上一版时用。">采用这条结果</button>
      </div>
      ${g.error ? `<pre class="err">${esc(g.error)}</pre>` : ''}
      <div class="hint">—— 输出 ——</div><pre>${esc(g.output || '（无输出）')}</pre>
      <div class="hint">—— 本次提示词 ——</div><pre>${esc(g.prompt || '')}</pre>
    </details>`;
  const cp = async (text, btn) => {
    try { await navigator.clipboard.writeText(text); btn.textContent = '已复制 ✓'; }
    catch { await alertBox('浏览器拒绝了复制，请手动选中文本复制'); }
  };
  $('#gen-copy-out').onclick = () => cp(g.output || '', $('#gen-copy-out'));
  $('#gen-copy-prompt').onclick = () => cp(g.prompt || '', $('#gen-copy-prompt'));
  $('#gen-adopt').onclick = async () => {
    try {
      const rr = await api(`/api/gens/${gid}/adopt`, { method: 'POST' });
      await alertBox(rr.note || '已采用');
      if (STATE.chapter) await openChapter(STATE.chapter.id);
      await loadGens();
    } catch (e) { await alertBox('采用失败：' + e.message); }
  };
  $('#gen-retry').onclick = async () => {
    const rr = await api(`/api/gens/${gid}/retry`, { method: 'POST' });
    if (!rr.ok) { await alertBox('重试失败：' + (rr.error || '')); return; }
    $('#ch-gen-area').innerHTML = '<div class="hint">已按原提示词重试，生成中…</div>';
    pollGen(rr.gen_id, async (gg) => { $('#ch-gen-area').innerHTML = `<div class="hint">重试结果：${gg.status}</div>`; await loadGens(); });
  };
}

async function finishChapter() {
  if (!STATE.chapter) { await alertBox('先选一章'); return; }
  $('#ch-finish').disabled = true;
  $('#ch-task').innerHTML = '<div class="hint">已启动「起草 → 去 AI 味 → 摘要 → 伏笔台账 → 违禁词扫描」，跑完自动刷新…</div>';
  try {
    const r = await api(`/api/chapters/${STATE.chapter.id}/finish`, { method: 'POST' });
    if (!r.ok) { $('#ch-task').innerHTML = `<div class="err">${esc(r.error)}</div>`; return; }
    pollTask(r.task_id);
  } finally { $('#ch-finish').disabled = false; }
}

function pollTask(tid) {
  clearInterval(STATE.taskTimer);
  STATE.taskTimer = setInterval(async () => {
    try {
      const t = (await api('/api/tasks/' + tid)).task;
      const icons = { pending: '·', running: '⏳', success: '✓', failed: '✗', skipped: '—' };
      $('#ch-task').innerHTML = t.steps.map((s) =>
        `  ${icons[s.status] || '·'} ${esc(s.name)}${s.msg ? '：' + esc(s.msg) : ''}`).join('<br>')
        + (t.status !== 'running' ? `<br><b>${t.status === 'success' ? '全部完成' : '中断：' + esc(t.error || '')}</b>` : '');
      if (t.status !== 'running') {
        clearInterval(STATE.taskTimer);
        await openChapter(STATE.chapter.id);
      }
    } catch (e) { clearInterval(STATE.taskTimer); }
  }, 3000);
}

async function runSummary() {
  if (!STATE.chapter) { await alertBox('先选一章'); return; }
  const r = await api(`/api/chapters/${STATE.chapter.id}/summary`, { method: 'POST' });
  if (!r.ok) { await alertBox(r.error || '无法生成摘要'); return; }
  $('#ch-summary-badge').textContent = '生成中…';
  pollGen(r.gen_id, async () => { await openChapter(STATE.chapter.id); });
}

async function delChapter() {
  if (!STATE.chapter) { await alertBox('先选一章'); return; }
  if (!(await confirmBox(`删除第 ${STATE.chapter.no} 章？`))) return;
  await api('/api/chapters/' + STATE.chapter.id, { method: 'DELETE' });
  STATE.chapter = null;
  await loadChapters();
}

async function saveChapter() {
  if (!STATE.chapter) return;
  const r = await api('/api/chapters/' + STATE.chapter.id, { method: 'PUT', body: { content: $('#ch-text').value } });
  STATE.chapter = r.chapter;
  $('#ch-words').textContent = `${r.chapter.words} 字（不计标题行）`;
  await alertBox('已保存');
}

async function genChapter(kind) {
  const pid = $('#ch-book').value;
  if (!STATE.chapter) { await alertBox('先选一章'); return; }
  if (!STATE.book || STATE.book.id !== pid) await openBook(pid);
  const step = chapterStep(kind);
  if (!step) { await alertBox('这本书的流程里没有章节级步骤'); return; }
  // 让步骤面板与之同步（生成结果会直接写进本章）
  $('#ch-gen-area').innerHTML = `<div class="hint">用「${step.name}」生成中…</div>`;
  const body = { step_id: step.id, chapter_id: STATE.chapter.id, extra: '' };
  const r = await api(`/api/books/${pid}/generate`, { method: 'POST', body });
  if (r.awaiting) { alertBox('这本书的默认模型是网页版通道：请到「写作流程」的该步骤里复制提示词执行。'); return; }
  pollGen(r.gen_id, async (g) => {
    if (g.status === 'success') {
      $('#ch-gen-area').innerHTML = `<div class="hint okk">✓ 已写入本章（${g.chars} 字）</div>`;
      await openChapter(STATE.chapter.id);
    } else {
      $('#ch-gen-area').innerHTML = `<div class="err">✗ 失败：${esc(g.error)}</div>`;
    }
  });
}

async function exportBook() {
  const pid = $('#ch-book').value;
  if (!pid) { await alertBox('先选书'); return; }
  const dir = await showModal('导出到哪个目录？留空 = 平台目录 data/exports/<书名>\n（可填绝对路径，如 D:\\小说导出）',
    { withInput: true, value: '' });
  if (dir === false) return;
  const r = await api(`/api/books/${pid}/export`, { method: 'POST', body: { dir, mode: 'both', fmt: 'txt' } });
  await alertBox(`已导出 ${r.count} 章 / ${r.words} 字到：\n${r.dir}\n\n文件数：${r.files.length}`);
}

/* ==================== 合规检查 ==================== */
async function loadCheckBooks() {
  const r = await api('/api/books');
  const books = r.books || [];
  const opts = books.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
  const cur = $('#ck-book').value;
  $('#ck-book').innerHTML = opts || '<option value="">（还没有书）</option>';
  if (cur) $('#ck-book').value = cur;
  const pid = $('#ck-book').value;
  if (pid) {
    const ch = await api(`/api/books/${pid}/chapters`);
    $('#ck-chapter').innerHTML = '<option value="">（全书设定与产出）</option>' +
      ch.chapters.map((c) => `<option value="${c.id}">第 ${c.no} 章 ${esc(c.title || '')}</option>`).join('');
  }
}

async function runBlacklist() {
  const pid = $('#ck-book').value;
  if (!pid) { await alertBox('先建一本书'); return; }
  const r = await api(`/api/books/${pid}/check/blacklist`, { method: 'POST' });
  $('#ck-bl-badge').innerHTML = r.ok ? '<span class="okk">无高危命中</span>' : '<span class="err">命中高危词</span>';
  $('#ck-bl-out').textContent = r.report;
  $('#ck-bl-groups').innerHTML = (r.groups || []).map((g) => `
    <details class="g"><summary>${esc(g.title)}</summary>
      ${(g.subs || []).map((s) => `<div class="hint" style="margin-top:6px">${esc(s.title || '')}</div>
        <ul class="hint">${(s.items || []).map((it) => `<li>${esc(it)}</li>`).join('')}</ul>`).join('')}
    </details>`).join('');
}

async function runViolations() {
  const pid = $('#ck-book').value;
  if (!pid) { await alertBox('先建一本书'); return; }
  const body = { chapter_id: $('#ck-chapter').value || '' };
  try {
    const r = await api(`/api/books/${pid}/check/violations`, { method: 'POST', body });
    $('#ck-vb-badge').innerHTML = r.ok ? '<span class="okk">无一级违禁词</span>' : '<span class="err">有一级违禁词</span>';
    $('#ck-vb-out').textContent = r.report;
  } catch (e) { $('#ck-vb-out').textContent = '失败：' + e.message; }
}

async function runViolationsAll() {
  const pid = $('#ck-book').value;
  if (!pid) { await alertBox('先建一本书'); return; }
  $('#ck-vb-out').textContent = '扫描中…';
  try {
    const r = await api(`/api/books/${pid}/check/violations-all`, { method: 'POST' });
    $('#ck-vb-badge').innerHTML = r.ok ? '<span class="okk">全书无一级违禁词</span>'
      : `<span class="err">${r.bad_chapters.length} 章有问题</span>`;
    $('#ck-vb-out').textContent = r.report;
  } catch (e) { $('#ck-vb-out').textContent = '失败：' + e.message; }
}

async function runPresubmit(ticks) {
  const pid = $('#ck-book').value;
  if (!pid) { await alertBox('先建一本书'); return; }
  const r = await api(`/api/books/${pid}/check/presubmit`, { method: 'POST', body: { ticks: ticks || [] } });
  $('#ck-ps-badge').textContent = `${r.done}/${r.total}`;
  $('#ck-ps-out').textContent = r.report;
  $('#ck-ps-items').innerHTML = r.items.map((it, i) => `
    <label class="tick"><input type="checkbox" data-tick="${i}" ${it.ticked ? 'checked' : ''} />
      <span>${esc(it.text)}${it.auto ? `<div class="auto">↳ ${esc(it.auto)}</div>` : ''}</span></label>`).join('');
  const gather = () => Array.from(document.querySelectorAll('#ck-ps-items input'))
    .filter((x) => x.checked).map((x) => r.items[+x.dataset.tick].text);
  document.querySelectorAll('#ck-ps-items input').forEach((x) =>
    x.addEventListener('change', () => runPresubmit(gather())));
}

/* ==================== 模型 ==================== */
async function loadModels() {
  const r = await api('/api/models');
  STATE.models = r.models || [];
  $('#md-table').innerHTML = `<tr><th>名称</th><th>通道</th><th>模型名</th><th>密钥</th><th>默认</th><th>操作</th></tr>` +
    STATE.models.map((m) => `
      <tr><td>${esc(m.name)}${m.is_default ? ' ⭐' : ''}</td>
        <td>${m.channel === 'web' ? '网页人工' : 'API'}</td>
        <td>${esc(m.model_name || m.web_url || '')}</td>
        <td>${m.has_key ? '已填' : (m.channel === 'web' ? '—' : '未填')}</td>
        <td>${m.is_default ? '是' : ''}</td>
        <td>
          <button data-md-test="${m.id}">测试</button>
          <button data-md-edit="${m.id}">编辑</button>
          <button data-md-def="${m.id}">设默认</button>
          <button data-md-del="${m.id}">删除</button>
        </td></tr>`).join('');
  const opts = STATE.models.filter((m) => m.enabled).map((m) =>
    `<option value="${m.id}">${esc(m.name)}${m.is_default ? '（默认）' : ''}</option>`).join('');
  $('#gen-model').innerHTML = `<option value="">（用默认模型）</option>` + opts;
  await loadBindings();
  if (STATE.step) $('#step-model').value = STATE.stepModelId || '';
}

async function loadBindings() {
  try {
    const r = await api('/api/models/bindings');
    const rows = Object.entries(r.bindings).map(([mid, list]) => {
      const m = modelById(mid);
      const name = (m && m.name) || mid;
      const isDef = (r.defaults || []).includes(mid);
      return `<div class="item"><div class="n">${esc(name)}${isDef ? ' · 全局默认' : ''}</div>
        <div class="d">${list.length ? '绑定：' + list.map((x) => `${esc(x.book)}／${esc(x.step)}`).join('、')
                                      : '未绑定具体步骤（只作全局默认或临时选择）'}</div></div>`;
    }).join('');
    $('#md-bindings').innerHTML = rows || '（还没有模型）';
  } catch { /* 静默 */ }
}

function modelById(id) { return STATE.models.find((m) => m.id === id); }

function fillModelForm(m) {
  STATE.editId = m ? m.id : null;
  $('#md-name').value = m ? m.name : '';
  $('#md-channel').value = m ? m.channel : 'api';
  $('#md-purpose').value = m && m.purpose || '';
  $('#md-base').value = m && m.base_url || '';
  $('#md-model').value = m && m.model_name || '';
  $('#md-key').value = '';
  $('#md-temp').value = m && m.temperature || 0.8;
  $('#md-tokens').value = m && m.max_tokens || 4096;
  $('#md-timeout').value = m && m.timeout || 600;
  $('#md-thinkoff').checked = m ? !!m.thinking_off : true;
  $('#md-stream').checked = m ? !!m.stream : true;
  $('#md-noproxy').checked = m ? !!m.no_proxy : true;
  $('#md-default').checked = m ? !!m.is_default : (STATE.models.length === 0);
  $('#md-enabled').checked = m ? !!m.enabled : true;
  $('#md-weburl').value = m && m.web_url || '';
  toggleChannel();
}
function toggleChannel() {
  const web = $('#md-channel').value === 'web';
  $('#md-api').style.display = web ? 'none' : '';
  $('#md-web').style.display = web ? '' : 'none';
}

async function saveModel() {
  const body = {
    name: $('#md-name').value.trim() || '未命名模型',
    channel: $('#md-channel').value,
    purpose: $('#md-purpose').value.trim(),
    base_url: $('#md-base').value.trim(),
    model_name: $('#md-model').value.trim(),
    web_url: $('#md-weburl').value.trim(),
    temperature: parseFloat($('#md-temp').value) || 0.8,
    max_tokens: parseInt($('#md-tokens').value) || 4096,
    timeout: parseInt($('#md-timeout').value) || 600,
    thinking_off: $('#md-thinkoff').checked ? 1 : 0,
    stream: $('#md-stream').checked ? 1 : 0,
    no_proxy: $('#md-noproxy').checked ? 1 : 0,
    is_default: $('#md-default').checked ? 1 : 0,
    enabled: $('#md-enabled').checked ? 1 : 0,
  };
  const key = $('#md-key').value.trim();
  if (key) body.api_key = key;
  if (STATE.editId) await api('/api/models/' + STATE.editId, { method: 'PUT', body });
  else await api('/api/models', { method: 'POST', body });
  await loadModels();
  fillModelForm(null);
  await alertBox('已保存。点列表里的「测试」确认能连通再用。');
}

async function testModel(id) {
  const r = await api(`/api/models/${id}/test`, { method: 'POST', body: {} });
  await alertBox(`通道：${r.channel === 'web' ? '网页版' : 'API'}\n` + (r.message || `返回：${r.output}`));
}

async function upstreamModels() {
  const r = await api('/api/models/upstream', { method: 'POST', body: {
    base_url: $('#md-base').value.trim(), api_key: $('#md-key').value.trim() } });
  const pick = await showModal(`该网关共 ${r.count} 个模型，输入序号填入表单：\n\n` +
    r.ids.map((x, i) => `${i + 1}. ${x}`).join('\n'), { withInput: true, value: '' });
  const idx = parseInt(pick) - 1;
  if (r.ids[idx]) { $('#md-model').value = r.ids[idx]; await alertBox('已填入：' + r.ids[idx]); }
}

async function createPresets(tier) {
  const base = $('#md-base').value.trim();
  if (!base) { await alertBox('先填接口地址（如 https://tokenrhythm.studio/v1），再点这个按钮'); return; }
  const label = tier === 'quality' ? '质量档' : '省钱档';
  $('#md-presets-msg').textContent = `正在拉取网关模型列表并创建「${label}」配置…`;
  try {
    const r = await api('/api/models/presets', { method: 'POST', body: {
      base_url: base, api_key: $('#md-key').value.trim(), tier } });
    const lines = r.created.map((x) => `  ${x.role} → ${x.name}（${x.model_name}）`).join('\n') || '  （没有新建）';
    const skips = r.skipped.map((x) => `  ${x.role}：${x.reason}`).join('\n');
    $('#md-presets-msg').textContent = `${r.tier_label || label}：${r.message}`;
    await loadModels();
    const apply = await showModal(`已按「${r.tier_label || label}」创建：\n${lines}${skips ? '\n\n跳过：\n' + skips : ''}\n\n`
      + `（${r.tier_note || ''}）\n\n是否把这套分工应用到当前书（起草→章节步骤 / 改写→去AI味 / 大纲→大纲·人设 / 扫描→全文扫描）？`,
      { okText: '应用到当前书' });
    if (apply && STATE.book) {
      const a = await api(`/api/books/${STATE.book.id}/step-models/apply-presets`, { method: 'POST', body: { roles: r.roles } });
      await alertBox('已绑定 ' + Object.keys(a.bound).length + ' 个步骤：\n'
        + Object.entries(a.bound).map(([k]) => `  ${k}`).join('\n'));
      await refreshSteps();
      if (STATE.step) await openStep(STATE.step.id, STATE.chapterForStep);
    } else if (apply) {
      await alertBox('还没选书；等建好书后在「写作流程」页会自动带上这套分工（也可再点一次本按钮应用）。');
    }
  } catch (e) {
    $('#md-presets-msg').textContent = '失败：' + e.message;
    await alertBox('创建失败：' + e.message);
  }
}

/* ==================== 番茄短故事 ==================== */
const SHORT = { cats: [], book: null, meta: null, chapters: [], subs: new Set(), minWords: 6000 };

function shortCatName(key) {
  const c = SHORT.cats.find((x) => x.key === key);
  return c ? c.name : key;
}

async function shortLoad() {
  try {
    const c = await api('/api/short/categories');
    SHORT.cats = c.categories || [];
    SHORT.minWords = c.min_words || 6000;
    const opts = SHORT.cats.map((x) => '<option value="' + x.key + '">' + esc(x.name) + '（' + esc(x.channel) + '）</option>').join('');
    const cur1 = $('#short-main').value;
    $('#short-main').innerHTML = opts;   // 不再保留「主分类…」空选项，直接列分类
    if (cur1 && SHORT.cats.some((x) => x.key === cur1)) $('#short-main').value = cur1;
    else if (opts) $('#short-main').selectedIndex = 0;
    $('#short-msg').textContent = '一级分类 ' + SHORT.cats.length + ' 个 · 二级标签 78 个（合计 102）｜正文 ≥' + SHORT.minWords + ' 字即有机会签约｜单篇完结上传';
    const r = await api('/api/short/books');
    const books = r.books || [];
    $('#short-book').innerHTML = '<option value="">（选择短故事）</option>' +
      books.map((b) => '<option value="' + b.id + '">' + esc(b.name) + '｜' + b.chapters + ' 章 / ' + b.words + ' 字</option>').join('');
    $('#short-badge').textContent = books.length + ' 篇';
    if (SHORT.book && books.some((b) => b.id === SHORT.book.id)) {
      $('#short-book').value = SHORT.book.id;
      await shortOpenBook(SHORT.book.id);
    }
    await shortLoadTags();   // 即使还没选书，也先把标签列表填好
  } catch (e) { $('#short-badge').textContent = '失败：' + e.message; }
}

async function shortOpenBook(id) {
  // 切书：进度条属于某本书，切到别的书就隐藏（否则会一直显示上一本的进度/中断状态）
  if (PROG.bookId && PROG.bookId !== id) progReset();
  const r = await api('/api/short/books/' + id);
  SHORT.book = r.book;
  SHORT.meta = r.meta;
  SHORT.chapters = r.chapters || [];
  SHORT.subs = new Set(r.meta.sub_categories || []);
  $('#short-book').value = id;
  $('#short-ch-msg').textContent = '';
  // 明确显示「当前书」的分类：上面那个下拉是「新建用」，容易被误认成当前书的分类
  try {
    const cat = (SHORT.cats || []).find((c) => c.key === r.meta.main_category);
    $('#short-cur-cat').textContent = '当前书分类：' + (cat ? cat.name + '（' + cat.channel + '）' : '（未设置）')
      + '　｜　上方下拉只用于新建';
  } catch (e) { /* 忽略 */ }
  $('#short-checks').innerHTML = '';
  $('#short-text').value = '';
  $('#short-pv-msg').textContent = '';
  $('#short-task').innerHTML = '';
  shortRenderSubs();
  shortRenderChapters();
  $('#short-words').textContent = r.total_words + ' 字 / ' + SHORT.chapters.length + ' 章';
}

function shortRenderSubs() {
  // 发布设置已移除：次分类不再在界面上编辑（建书时只选主分类）
}

function shortRenderChapters() {
  const chs = SHORT.chapters || [];
  const words = chs.reduce((s, c) => s + (c.words || 0), 0);
  $('#short-ch-badge').textContent = chs.length ? chs.length + ' 章 / ' + words + ' 字' : '还没有章节';
  $('#short-chapters').innerHTML = chs.length ? chs.map((c) => {
    const done = (c.status || 'draft') === 'done';
    return '<div class="item" style="cursor:default">' +
      '<div class="n">第 ' + c.no + ' 章 ' + esc(c.title || '（无标题）') +
      ' <span class="hint">' + (c.words || 0) + ' 字 · ' + (done ? '<span class="okk">已成稿</span>' : '草稿') + '</span></div>' +
      '<div class="row"><button data-shgen="' + c.id + '">⚡ 一键写完本章</button>' +
      '<button data-shview="' + c.id + '">看正文</button></div></div>';
  }).join('') : '<div class="hint">点上面「建立 5 章骨架」开始（默认用黄金五段小标题，可在「章节」页改标题）</div>';
}

async function shortCreate() {
  const name = $('#short-name').value.trim() || '未命名短故事';   // 名称可空：先占位，之后一键生成会自动起名
  const main = $('#short-main').value;
  if (!main) { await alertBox('请选主分类（作品分类主分类必选）'); return; }
  try {
    const tags = [...($('#short-tags')?.selectedOptions || [])].map((o) => o.value).slice(0, 7);
    const r = await api('/api/short/books', { method: 'POST', body: {
      name, main_category: main, sub_categories: tags, brief: '' } });
    $('#short-name').value = '';
    SHORT.book = r.book;
    await shortLoad();
    await shortOpenBook(r.book.id);
    await alertBox('已创建《' + r.book.name + '》\n\n下一步：点「建立 5 章骨架」→ 逐章生成（或「一键写完所有章」）→ 预览 → 导出投稿包。');
  } catch (e) { await alertBox('创建失败：' + e.message); }
}

async function shortPreview() {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  try {
    const r = await api('/api/short/books/' + SHORT.book.id + '/preview');
    $('#short-text').value = r.text || '';
    $('#short-words').textContent = r.total_words + ' 字 / ' + r.chapters.length + ' 章';
    $('#short-checks').innerHTML = r.checks.map((c) =>
      '<div class="tick"><span>' + (c.ok ? '<span class="okk">✓</span>' : '<span class="err">✗</span>') + '</span>' +
      '<span>' + esc(c.label) + '<div class="auto">' + esc(c.detail) + '</div></span></div>').join('');
    $('#short-pv-msg').textContent = r.total_words >= r.min_words
      ? '已过 ' + r.min_words + ' 字签约机会线' : '还差 ' + (r.min_words - r.total_words) + ' 字到签约机会线';
  } catch (e) { await alertBox('预览失败：' + e.message); }
}

async function shortNameCheck() {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  const btn = $('#short-name-check');
  btn.disabled = true;
  $('#short-name-msg').textContent = '查询中（本地 + 联网，约 3–10 秒）…';
  try {
    const r = await api('/api/name/check', { method: 'POST', body: { name: SHORT.book.name } });
    const local = r.local_duplicates.length
      ? '<div class="err">平台内已有同名：' + r.local_duplicates.map((x) => esc(x.name)).join('、') + '</div>'
      : '<div class="okk">平台内无同名</div>';
    const web = r.web.hits.length
      ? '<div class="err">联网疑似命中 ' + r.web.hits.length + ' 条：<br>' + r.web.hits.map((h) => esc(h)).join('<br>') + '</div>'
      : '<div class="hint">联网未命中' + (r.web.checked ? '' : '（检索失败或没结果）') + '</div>';
    $('#short-name-out').innerHTML = local + web +
      '<div class="hint">' + esc(r.web.note) + '</div>' +
      '<div class="row"><input id="short-new-name" placeholder="新书名（≤15 字）" style="flex:1" />' +
      '<button id="short-rename">改用它</button><button id="short-name-suggest">让模型起 8 个</button></div>' +
      '<div id="short-name-sugg"></div>';
    $('#short-rename').onclick = async () => {
      const nm = $('#short-new-name').value.trim();
      if (!nm) { await alertBox('先填新书名'); return; }
      await api('/api/books/' + SHORT.book.id, { method: 'PUT', body: { name: nm } });
      SHORT.book.name = nm;
      await shortLoad();
      await alertBox('已改名为：' + nm + '（发布前再查一次重）');
    };
    $('#short-name-suggest').onclick = async () => {
      $('#short-name-sugg').textContent = '生成 20 个候选并打分中（约 10–20 秒）…';
      try {
        const s = await api('/api/name/suggest', { method: 'POST', body: { name: SHORT.book.name, book_id: SHORT.book.id } });
        if (!s.best) {
          $('#short-name-sugg').innerHTML = '<div class="err">没有合格候选：' + s.checked + ' 个候选全部被规则淘汰（超 15 字/英文标点/敏感词）</div>';
          return;
        }
        const b = s.best;
        $('#short-name-sugg').innerHTML =
          '<div class="item" style="cursor:default">' +
          '<div class="n" style="font-size:15px"><b>' + esc(b.name) + '</b> <span class="badge">' + b.score + '/100</span></div>' +
          '<div class="d">贴合 ' + (b.fit ?? "-") + ' · 冲突 ' + (b.conflict ?? "-") + ' · 差异 ' + (b.diff ?? "-") +
          ' · 记忆 ' + (b.memory ?? "-") + ' · 合规 ' + (b.compliance ?? "-") + '</div>' +
          '<div class="d">' + esc(b.reason) + '</div>' +
          '<div class="d">' + (b.web_hits && b.web_hits.length
            ? '⚠️ 联网命中：' + b.web_hits.map((h) => esc(h)).join('；')
            : '✓ ' + esc(b.web_note || '联网未命中同名')) + '</div>' +
          '<div class="row"><button id="short-use-best" class="primary">用这个书名</button>' +
          '<span class="hint">共 ' + s.checked + ' 个候选，规则合格 ' + s.valid + ' 个；按分数从高到低逐个联网查重</span></div></div>';
        $('#short-use-best').onclick = async () => {
          await api('/api/books/' + SHORT.book.id, { method: 'PUT', body: { name: b.name } });
          SHORT.book.name = b.name;
          await shortLoad();
          await alertBox('已改名为：' + b.name + '\n\n发布前建议再查一次重。');
        };
      } catch (e) { $('#short-name-sugg').textContent = '失败：' + e.message; }
    };
  } catch (e) {
    $('#short-name-msg').textContent = '失败：' + e.message;
  } finally {
    btn.disabled = false;
    if ($('#short-name-msg').textContent.startsWith('查询中')) $('#short-name-msg').textContent = '';
  }
}

async function shortRegenerateName() {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  const btn = $('#short-name-regen');
  btn.disabled = true;
  $('#short-name-msg').textContent = '生成 + 打分 + 联网查重中（约 15–25 秒）…';
  $('#short-name-out').innerHTML = '<div class="hint">正在一步完成：生成候选 → 五维打分 → 逐个联网查重…</div>';
  try {
    const s = await api('/api/name/suggest', { method: 'POST', body: { name: SHORT.book.name, book_id: SHORT.book.id } });
    if (!s.best) {
      $('#short-name-out').innerHTML = '<div class="err">没有合格候选（' + s.checked + ' 个候选全部被规则淘汰）</div>';
      return;
    }
    const b = s.best;
    $('#short-name-out').innerHTML =
      '<div class="item" style="cursor:default">' +
      '<div class="n" style="font-size:15px"><b>' + esc(b.name) + '</b> <span class="badge">' + b.score + '/100</span></div>' +
      '<div class="d">贴合 ' + (b.fit ?? "-") + ' · 冲突 ' + (b.conflict ?? "-") + ' · 差异 ' + (b.diff ?? "-") +
      ' · 记忆 ' + (b.memory ?? "-") + ' · 合规 ' + (b.compliance ?? "-") + '</div>' +
      '<div class="d">' + esc(b.reason) + '</div>' +
      '<div class="d">' + (b.web_hits && b.web_hits.length
        ? '⚠️ 联网命中：' + b.web_hits.map((h) => esc(h)).join('；')
        : '✓ ' + esc(b.web_note || '联网未命中同名')) + '</div>' +
      '<div class="row"><button id="short-use-best" class="primary">用这个书名</button>' +
      '<span class="hint">共 ' + s.checked + ' 个候选，合格 ' + s.valid + ' 个</span></div></div>';
    $('#short-use-best').onclick = async () => {
      await api('/api/books/' + SHORT.book.id, { method: 'PUT', body: { name: b.name } });
      SHORT.book.name = b.name;
      await shortLoad();
      await alertBox('已改名为：' + b.name + '\n\n发布前建议再查一次重。');
    };
  } catch (e) {
    $('#short-name-out').innerHTML = '<div class="err">失败：' + esc(e.message) + '</div>';
  } finally {
    btn.disabled = false;
    $('#short-name-msg').textContent = '';
  }
}

async function shortFullDraft() {
  if (!SHORT.book) { await alertBox('先选或建一篇短故事'); return; }
  if (!(await confirmBox('一次成稿：用当前「起草」模型把五章全文一次写出来（约 1–2 万字），' +
    '再按「第N章」切分落库。\n\n⚠️ 会覆盖已有章节正文；内容不合格时不会落库（结果留在生成记录里）。\n\n继续？'))) return;
  const btn = $('#short-full-draft');
  btn.disabled = true;
  $('#short-ch-msg').textContent = '正在一次生成五章全文…（可能需要 3–6 分钟）';
  $('#short-task').innerHTML = '一次成稿进行中，请勿关闭页面…';
  try {
    const r = await api('/api/short/books/' + SHORT.book.id + '/full-draft', { method: 'POST', body: {} });
    if (!r.ok) { $('#short-ch-msg').textContent = r.error || '启动失败'; return; }
    pollGen(r.gen_id, async (g) => {
      await shortRefreshChapters();
      const note = (g.error || '').replace('[note] ', '');
      $('#short-ch-msg').textContent = note || (g.status === 'success' ? '一次成稿完成' : '失败');
      $('#short-task').innerHTML = g.status === 'success'
        ? '一次成稿完成：' + esc(note) : '<span class="err">失败：' + esc(g.error || '') + '</span>';
      await alertBox(g.status === 'success' ? (note || '一次成稿完成') : ('失败：' + (g.error || '')));
    });
  } catch (e) {
    $('#short-ch-msg').textContent = '启动失败：' + e.message;
  } finally { btn.disabled = false; }
}

async function shortExport() {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  try {
    const r = await api('/api/short/books/' + SHORT.book.id + '/export', { method: 'POST', body: { fmt: 'both' } });
    await alertBox('已导出 ' + r.count + ' 章 / ' + r.words + ' 字' + (r.pass_line ? '（已过 6000 字线）' : '（还差 ' + (6000 - r.words) + ' 字到 6000 线）') +
      '\n\n目录：' + r.dir + '\n\n' + r.files.map((f) => '· ' + f.split('\\').pop()).join('\n'));
  } catch (e) { await alertBox('导出失败：' + e.message); }
}

async function shortCopy() {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  let t = $('#short-text').value;
  // 没预览过就自动拉一次合并正文——点一下就能拿到带「第N章 标题」的全文
  if (!t) {
    try {
      const r = await api('/api/short/books/' + SHORT.book.id + '/preview');
      t = r.text || '';
      $('#short-text').value = t;
      $('#short-words').textContent = r.total_words + ' 字 / ' + r.chapters.length + ' 章';
    } catch (e) { await alertBox('读取正文失败：' + e.message); return; }
  }
  if (!t) { await alertBox('这篇还没有正文'); return; }
  try {
    await navigator.clipboard.writeText(t);
    $('#short-pv-msg').textContent = '已复制全文（含「第N章 标题」行），可直接粘到番茄短故事正文框';
  } catch {
    await alertBox('浏览器拒绝了复制，请手动选中下面的文本复制。');
  }
}

$('#short-book').addEventListener('change', () => {
  const v = $('#short-book').value;
  if (v) shortOpenBook(v);
});
$('#short-create').addEventListener('click', shortCreate);

$('#short-skeleton').addEventListener('click', shortSkeleton);
$('#short-gen-all').addEventListener('click', shortGenAll);
$('#short-preset-unified').addEventListener('click', shortApplyModels);
$('#short-full-draft').addEventListener('click', shortFullDraft);

$('#short-auto-book').addEventListener('click', shortAutoBook);
$('#short-cost').addEventListener('click', shortCost);
$('#auto-chapters-run').addEventListener('click', autoChapters);
$('#auto-proposal').addEventListener('click', autoProposal);
$('#clear-cache').addEventListener('click', async () => {
  try {
    const r = await api('/api/admin/clear-cache', { method: 'POST', body: {} });
    try { localStorage.clear(); sessionStorage.clear(); } catch (e) { /* 忽略 */ }
    await alertBox('已清理：知识库缓存 ' + r.library_entries + ' 项、僵尸生成 ' + r.stale_gens + ' 条。\n\n页面将强制刷新。');
  } catch (e) { await alertBox('清理失败：' + e.message); }
  location.reload();
});
$('#short-quick-create').addEventListener('click', shortQuickCreate);
$('#short-preview').addEventListener('click', shortPreview);
$('#short-export').addEventListener('click', shortExport);
$('#short-copy').addEventListener('click', shortCopy);
$('#short-chapters').addEventListener('click', async (e) => {
  const g = e.target.closest && e.target.closest('[data-shgen]');
  const v = e.target.closest && e.target.closest('[data-shview]');
  try {
    if (g) await shortGenOne(g.dataset.shgen);
    else if (v) {
      const r = await api('/api/chapters/' + v.dataset.shview);
      $('#short-text').value = r.chapter.content || '';
      $('#short-words').textContent = r.chapter.words + ' 字 · 第 ' + r.chapter.no + ' 章';
    }
  } catch (err) { await alertBox('操作失败：' + err.message); }
});

/* ==================== 事件绑定 ==================== */
document.addEventListener('click', async (e) => {
  const sel = '[data-kb],[data-step],[data-ch],[data-md-test],[data-md-edit],[data-md-def],[data-md-del],[data-gen],[data-kbsec],[data-tool]';
  const t = e.target.closest && e.target.closest(sel);
  if (!t) return;
  try {
    if (t.dataset.kb) await kbRead(t.dataset.kb);
    else if (t.dataset.kbsec) await kbSection(t.dataset.kbsec);
    else if (t.dataset.step) await openStep(t.dataset.step);
    else if (t.dataset.tool) {
      const names = { diagnose: '① 卡文自检定位', ooc_fix: '② OOC 即时纠错', deai_fix: '③ 去 AI 化补强' };
      $('#tool-msg').textContent = `已打开：${names[t.dataset.tool]}（在下方步骤面板里看手册原文并生成）`;
      await openStep(t.dataset.tool, null, 'stuck');
    }
    else if (t.dataset.ch) { STATE.chapter = { id: t.dataset.ch }; await openChapter(t.dataset.ch); }
    else if (t.dataset.gen) await showGen(t.dataset.gen);
    else if (t.dataset.mdTest) await testModel(t.dataset.mdTest);
    else if (t.dataset.mdEdit) fillModelForm(modelById(t.dataset.mdEdit));
    else if (t.dataset.mdDef) { await api(`/api/models/${t.dataset.mdDef}/default`, { method: 'POST' }); await loadModels(); }
    else if (t.dataset.mdDel) {
      if (await confirmBox('删除这个模型？')) { await api('/api/models/' + t.dataset.mdDel, { method: 'DELETE' }); await loadModels(); }
    }
  } catch (err) { await alertBox('操作失败：' + err.message); }
});

$('#book-sel').addEventListener('change', (e) => e.target.value ? openBook(e.target.value) : null);
$('#book-create').addEventListener('click', createBook);
$('#book-del').addEventListener('click', deleteBook);
$('#gen-run').addEventListener('click', () => genStep(STATE.step && STATE.step.id, STATE.chapterForStep));
$('#art-save').addEventListener('click', saveArtifact);
$('#art-lock').addEventListener('change', async () => {
  if (!STATE.book || !STATE.step) return;
  await api(`/api/books/${STATE.book.id}/artifacts/${STATE.step.id}`, { method: 'PUT',
    body: { locked: $('#art-lock').checked ? 1 : 0 } });
  await refreshSteps();
});
$('#ch-book').addEventListener('change', async () => { STATE.chapter = null; await loadChapters(); });
$('#ch-create').addEventListener('click', createChapter);
$('#ch-drift').addEventListener('click', driftCheck);
$('#ch-save').addEventListener('click', saveChapter);
$('#ch-finish').addEventListener('click', finishChapter);
$('#ch-summary-run').addEventListener('click', runSummary);
$('#ch-del').addEventListener('click', delChapter);
$('#ch-gen').addEventListener('click', () => genChapter('chapter'));
$('#ch-deai').addEventListener('click', () => genChapter('deai'));
$('#ch-export').addEventListener('click', exportBook);
$('#kb-whole').addEventListener('click', kbWhole);
$('#ck-all').addEventListener('click', runViolationsAll);
$('#ch-check').addEventListener('click', async () => {
  if (!STATE.chapter) { await alertBox('先选一章'); return; }
  goTab('checks');
  await loadCheckBooks();
  $('#ck-chapter').value = STATE.chapter.id;
  await runViolations();
});
$('#kb-search').addEventListener('click', kbSearch);
$('#kb-blocks').addEventListener('click', kbBlocks);
$('#kb-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') kbSearch(); });
$('#ck-blacklist').addEventListener('click', runBlacklist);
$('#ck-violations').addEventListener('click', runViolations);
$('#ck-presubmit').addEventListener('click', () => runPresubmit([]));
$('#ck-book').addEventListener('change', loadCheckBooks);
$('#md-channel').addEventListener('change', toggleChannel);
$('#md-save').addEventListener('click', saveModel);
$('#md-reset').addEventListener('click', () => fillModelForm(null));
$('#md-upstream').addEventListener('click', upstreamModels);
$('#md-presets').addEventListener('click', () => createPresets('value'));
$('#md-presets-q').addEventListener('click', () => createPresets('quality'));
$('#step-model').addEventListener('change', async () => {
  if (!STATE.book || !STATE.step) return;
  try {
    const r = await api(`/api/books/${STATE.book.id}/step-models`, { method: 'PUT', body: {
      step_id: STATE.step.id, model_id: $('#step-model').value } });
    $('#step-model-msg').textContent = r.model_id ? '已绑定（生成时优先用它）' : '已解绑';
  } catch (e) { await alertBox('绑定失败：' + e.message); }
});

/* ==================== 启动 ==================== */
(async function boot() {
  try {
    const h = await api('/api/health');
    $('#health').textContent = '已连接 · ' + (h.skill_dir || '').split(/[\\/]/).slice(-2).join('/');
  } catch { $('#health').textContent = '后端未连接'; }
  try {
    await loadFlows();
    await loadModels();
    fillModelForm(null);
    await loadBooks();
    await kbLoad();
  } catch (e) { await alertBox('初始化失败：' + e.message); }
  // 支持用 URL 直接打开某个页签（#short / #kb / #chapters …），便于分享与截图
  const want = (location.hash || '').replace('#', '');
  if (want && document.querySelector('.tab[data-tab="' + want + '"]')) goTab(want, true);
})();


// ---------------------------------------------------------------- 一键成书 / 一键续写

function waitGen(gid, onTick) {
  return new Promise((resolve) => {
    const timer = setInterval(async () => {
      try {
        const r = await api('/api/gens/' + gid);
        const g = r.gen || r;                // 接口返回 {gen:{...}}，这里要取出来
        if (onTick) onTick(g);
        if (g.status !== 'running') { clearInterval(timer); resolve(g); }
      } catch (e) {
        clearInterval(timer);
        resolve({ status: 'failed', error: e.message });
      }
    }, 3000);
  });
}

function waitTask(tid, onTick) {
  return new Promise((resolve) => {
    const timer = setInterval(async () => {
      try {
        const r = await api('/api/tasks/' + tid);
        const t = r.task || r;               // 同样：接口包了一层
        if (onTick) onTick(t);
        if (t.status !== 'running') { clearInterval(timer); resolve(t); }
      } catch (e) {
        clearInterval(timer);
        resolve({ status: 'failed', error: e.message });
      }
    }, 3000);
  });
}

async function shortAutoBook() {
  if (!SHORT.book) { await alertBox('先选或建一篇短故事'); return; }
  if (!(await confirmBox('一键成书会依次执行：\n① 一次成稿（五章全文）\n② 逐章去 AI 味\n③ 全书违禁词扫描\n④ 导出\n\n⚠️ 会覆盖已有章节正文，继续？'))) return;
  const bid = SHORT.book.id;
  const btn = $('#short-auto-book');
  btn.disabled = true;
  const set = (m) => { $('#short-ch-msg').textContent = m; };
  try {
    set('① 一次成稿（约 3–5 分钟）…');
    const r = await api('/api/short/books/' + bid + '/full-draft', { method: 'POST', body: {} });
    if (!r.ok) { set(r.error || '启动失败'); return; }
    const g = await waitGen(r.gen_id, (x) => set('① 一次成稿：' + (x.chars || 0) + ' 字'));
    if (g.status !== 'success') { set('一次成稿失败：' + (g.error || '')); return; }
    await shortRefreshChapters();
    const chs = SHORT.chapters.slice().sort((a, b) => a.no - b.no);
    for (const c of chs) {
      set('② 第 ' + c.no + ' 章去 AI 味…');
      const gr = await api('/api/books/' + bid + '/generate', { method: 'POST', body: { step_id: 'polish', chapter_id: c.id } });
      if (gr.gen_id) await waitGen(gr.gen_id);
      await shortRefreshChapters();
    }
    set('③ 全书扫描…');
    await api('/api/books/' + bid + '/check/violations-all', { method: 'POST', body: {} });
    set('④ 导出…');
    const ex = await api('/api/short/books/' + bid + '/export', { method: 'POST', body: { fmt: 'both' } });
    set('成书完成：已导出 ' + (ex.count || '') + ' 章 / ' + (ex.words || '') + ' 字');
    await alertBox('一键成书完成。\n\n' + ((ex.files || []).join('\n')));
  } catch (e) {
    set('失败：' + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function autoChapters() {
  const n = Math.max(1, Math.min(20, parseInt($('#auto-chapters-n').value || '3', 10)));
  const bid = STATE.book && STATE.book.id;
  if (!bid) { await alertBox('先在「写作流程」选一本书'); return; }
  if (!(await confirmBox('一键续写 ' + n + ' 章？\n后台会连续跑「起草 → 改写 → 摘要+伏笔 → 扫描」；关掉页面也会继续。'))) return;
  const btn = $('#auto-chapters-run');
  btn.disabled = true;
  const set = (m) => { $('#auto-chapters-msg').textContent = m; };
  try {
    const r = await api('/api/books/' + bid + '/auto-chapters', { method: 'POST', body: { count: n } });
    if (!r.ok) { set(r.error || '启动失败'); return; }
    const t = await waitTask(r.task_id, (task) => {
      const running = (task.steps || []).find((s) => s.status === 'running');
      const done = (task.steps || []).filter((s) => s.status === 'success').length;
      set(running ? (running.name + '：' + (running.msg || '进行中')) : ('已完成 ' + done + '/' + n + ' 章'));
    });
    set(t.status === 'success' ? ('续写完成：' + n + ' 章') : ('已停止：' + (t.error || '')));
    await loadFlow();
  } catch (e) {
    set('失败：' + e.message);
  } finally {
    btn.disabled = false;
  }
}


async function shortCost() {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  try {
    const r = await api('/api/books/' + SHORT.book.id + '/cost');
    const rows = (r.rows || []).filter((x) => x.calls > 0);
    const body = rows.length ? rows.map((x) =>
      '<div class="d">' + esc(x.step) + ' · ' + esc(x.model) + ' · 调用 ' + x.calls +
      ' · 输入 ' + x.prompt_tokens + ' / 输出 ' + x.completion_tokens +
      ' · <b>¥' + Number(x.cost || 0).toFixed(4) + '</b></div>').join('')
      : '<div class="hint">还没有生成记录</div>';
    $('#short-cost-out').innerHTML = '<details class="g" open><summary>此书累计：<b>¥' +
      Number(r.total || 0).toFixed(4) + '</b></summary>' + body + '</details>';
    $('#short-cost-msg').textContent = '共 ' + rows.length + ' 组';
  } catch (e) {
    $('#short-cost-msg').textContent = '失败：' + e.message;
  }
}


async function loadCostOverview() {
  try {
    const r = await api('/api/cost/overview');
    const rows = r.rows || [];
    $('#cost-badge').textContent = '合计 ¥' + Number(r.total || 0).toFixed(4);
    if (!rows.length) {
      $('#cost-table').innerHTML = '<tr><td class="hint">还没有生成记录</td></tr>';
      return;
    }
    $('#cost-table').innerHTML =
      '<tr><th>书</th><th>调用</th><th>输入</th><th>输出</th><th>缓存命中</th><th>费用</th></tr>' +
      rows.map((x) => '<tr><td>' + esc(x.book) + '</td><td>' + x.calls + '</td><td>' + x.prompt_tokens +
        '</td><td>' + x.completion_tokens + '</td><td>' + x.cache_tokens +
        '</td><td>¥' + Number(x.cost || 0).toFixed(4) + '</td></tr>').join('');
  } catch (e) {
    $('#cost-badge').textContent = '加载失败：' + e.message;
  }
}


async function autoProposal() {
  const bid = STATE.book && STATE.book.id;
  if (!bid) { await alertBox('先在「写作流程」选一本书'); return; }
  const order = ['topic', 'compliance', 'title_intro', 'characters', 'outline', 'golden3'];
  const steps = (STATE.steps || []).filter((s) => order.includes(s.id));
  if (!steps.length) { await alertBox('当前流程没有可立项的步骤（短故事请用短故事页）'); return; }
  if (!(await confirmBox('一键立项会依次跑：' + steps.map((s) => s.name).join(' → ') +
    '\n\n已有产出的步骤会自动跳过。继续？'))) return;
  const btn = $('#auto-proposal');
  btn.disabled = true;
  const set = (m) => { $('#auto-proposal-msg').textContent = m; };
  try {
    for (const step of steps) {
      const d = await api('/api/books/' + bid + '/step/' + step.id);
      if (d.artifact && (d.artifact.content || '').trim()) { set('跳过 ' + step.name + '（已有产出）'); continue; }
      set(step.no + '. ' + step.name + '…');
      const r = await api('/api/books/' + bid + '/generate', { method: 'POST', body: { step_id: step.id } });
      if (!r.gen_id) { set('启动失败：' + (r.error || step.name)); break; }
      const g = await waitGen(r.gen_id, (x) => set(step.name + '：' + (x.chars || 0) + ' 字'));
      if (g.status !== 'success') { set(step.name + ' 失败：' + (g.error || '')); break; }
    }
    set('立项完成');
    await loadFlow();
  } catch (e) {
    set('失败：' + e.message);
  } finally {
    btn.disabled = false;
  }
}


async function driftCheck() {
  const pid = $('#ch-book').value;
  if (!pid) { await alertBox('先选一本书'); return; }
  const btn = $('#ch-drift');
  btn.disabled = true;
  $('#ch-drift-msg').textContent = '比对中…';
  try {
    const r = await api('/api/books/' + pid + '/drift-check', { method: 'POST', body: {} });
    if (!r.ok) { $('#ch-drift-msg').textContent = r.error || '失败'; return; }
    $('#ch-drift-out').innerHTML = '<pre>' + esc(r.report) + '</pre>';
    $('#ch-drift-msg').textContent = '完成 · ' + r.chars + ' 字';
  } catch (e) {
    $('#ch-drift-msg').textContent = '失败：' + e.message;
  } finally {
    btn.disabled = false;
  }
}


async function genStepApi(bid, stepId) {
  const r = await api('/api/books/' + bid + '/generate', { method: 'POST', body: { step_id: stepId } });
  if (!r.gen_id) throw new Error(r.error || '启动失败');
  return waitGen(r.gen_id);
}

async function shortQuickCreate() {
  const main = $('#short-main').value;
  let brief = '';
  const name = $('#short-name').value.trim();
  if (!main) { await alertBox('先选主分类'); return; }
  const btn = $('#short-quick-create');
  // 断点续跑：当前选中的书已有设定表、但还没有正文 → 直接从一次成稿继续（前端中断后可恢复）
  const cur = SHORT.book;
  if (cur && cur.id) {
    try {
      const d = await api('/api/books/' + cur.id + '/step/setup');
      const chs = (await api('/api/books/' + cur.id + '/chapters')).chapters || [];
      const hasBody = chs.some((c) => (c.words || 0) > 0);
      // 只有「有设定表、且确实没有任何正文」才提示续跑；有正文就不会误弹
      if (!hasBody && d.artifact && (d.artifact.content || '').trim()) {
        if (!(await confirmBox('检测到《' + cur.name + '》已有设定表和大纲，但还没有正文。\n\n从「一次成稿」继续跑完剩余流程？约 10 分钟。'))) return;
        btn.disabled = true;
        progInit(['一次成稿', '逐章改写', '全书扫描', '导出']);
        try {
          const ex = await runFromFullDraft(cur.id, 0);
          if (ex) {
            progEnd(true, '完成：' + (ex.count || '') + ' 章 / ' + (ex.words || '') + ' 字，已导出');
            await alertBox('续跑完成。\n\n' + ((ex.files || []).join('\n')));
          }
        } catch (e) {
          progEnd(false, '中断：' + e.message);
        } finally {
          btn.disabled = false;
        }
        return;
      }
    } catch (e) { /* 没有设定表就走新建流程 */ }
  }

  if (!(await confirmBox('一键生成整篇：构思开篇冲突 → 建书 → 起书名 → 设定表 → 五章大纲 → 一次成稿 → 逐章改写 → 扫描 → 导出。\n\n约 12–18 分钟，进度条会显示到哪一步。继续？'))) return;
  btn.disabled = true;
  let ok = false;
  progInit(['构思开篇冲突', '建书', '起书名', '设定表', '五章大纲', '一次成稿', '逐章改写', '全书扫描', '导出']);
  try {
    progStart(0, '按分类自动构思开篇冲突…');
    try {
      const idea = await api('/api/short/idea', { method: 'POST', body: { main_category: main } });
      brief = idea.brief || '';
    } catch (e) { /* 构思失败就让模型按分类自由发挥 */ }
    progDone(0);

    progStart(1, '建书…');
    const tags = [...($('#short-tags')?.selectedOptions || [])].map((o) => o.value).slice(0, 7);
    const c = await api('/api/short/books', { method: 'POST', body: {
      name: name || '未命名短故事', main_category: main, sub_categories: tags, brief } });
    const bid = c.book.id;
    SHORT.book = c.book;
    progDone(1);

    if (!name) {
      progStart(2, '起书名（生成 → 打分 → 联网查重）…');
      try {
        const s = await api('/api/name/suggest', { method: 'POST', body: { name: '短故事', book_id: bid } });
        if (s.best) await api('/api/books/' + bid, { method: 'PUT', body: { name: s.best.name } });
      } catch (e) { /* 起名失败就用占位名，不阻断正文 */ }
    }
    progDone(2);

    await shortLoad();
    await shortOpenBook(bid);

    progStart(3, '生成三份设定表…');
    await genStepApi(bid, 'setup');
    progDone(3);

    progStart(4, '生成五章大纲…');
    await genStepApi(bid, 'outline');
    progDone(4);

    const ex = await runFromFullDraft(bid, 5);
    if (ex) {
      ok = true;
      progEnd(true, '完成：' + (ex.count || '') + ' 章 / ' + (ex.words || '') + ' 字，已导出');
      await alertBox('一键生成完成。\n\n' + ((ex.files || []).join('\n')));
    }
  } catch (e) {
    progEnd(false, '中断：' + e.message);
  } finally {
    btn.disabled = false;
    if (!ok && !$('#short-progress-msg').textContent) progEnd(false, '已中断');
  }
}


// 下面三个函数在清理发布设置时被误删，导致 boot 绑定失败、整页不加载；现补回
async function shortSkeleton() {
  if (!SHORT.book) { await alertBox('先选或建一篇短故事'); return; }
  try {
    const r = await api('/api/short/books/' + SHORT.book.id + '/skeleton', { method: 'POST', body: {} });
    await shortLoad();
    await shortOpenBook(SHORT.book.id);
    await alertBox('已按黄金五段建好骨架' + (r.created ? '：' + r.created + ' 章' : ''));
  } catch (e) {
    await alertBox('建骨架失败：' + e.message);
  }
}

async function shortGenAll() {
  if (!SHORT.book) { await alertBox('先选或建一篇短故事'); return; }
  if (!(await confirmBox('一键写完所有章？会依次跑「起草 → 改写 → 摘要+伏笔 → 扫描」。'))) return;
  const todo = (SHORT.chapters || []).filter((c) => c.status !== 'done').sort((a, b) => a.no - b.no);
  if (!todo.length) { await alertBox('所有章节都已成稿'); return; }
  for (const c of todo) {
    $('#short-ch-msg').textContent = '正在写第 ' + c.no + ' 章…';
    try {
      const r = await api('/api/chapters/' + c.id + '/finish', { method: 'POST' });
      if (!r.ok) { $('#short-ch-msg').textContent = '第 ' + c.no + ' 章失败：' + (r.error || ''); break; }
      await waitTask(r.task_id);
      await shortRefreshChapters();
    } catch (e) {
      $('#short-ch-msg').textContent = '第 ' + c.no + ' 章失败：' + e.message;
      break;
    }
  }
  $('#short-ch-msg').textContent = '全部完成';
}

async function shortApplyModels() {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  try {
    const r = await api('/api/books/' + SHORT.book.id + '/apply-models', { method: 'POST', body: {} });
    const lines = Object.keys(r.bound || {}).map((k) => k + ' → ' + r.bound[k]).join('\n');
    await alertBox('已应用「' + r.label + '」：\n' + (lines || '（没有可绑定的模型）'));
  } catch (e) {
    await alertBox('失败：' + e.message);
  }
}


// ---------------------------------------------------------------- 一键生成进度条

const PROG = { steps: [], cur: -1, t0: 0, stepT0: 0, timer: null };

const EXPECT_SEC = {
  '建书': 6, '构思开篇冲突': 20, '起书名': 30, '设定表': 80, '五章大纲': 80, '一次成稿': 300,
  '全书扫描': 15, '导出': 15,
};

function fmtSec(s) {
  s = Math.max(0, Math.round(s));
  return s >= 60 ? Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒' : s + ' 秒';
}

function progRender() {
  const box = $('#short-progress-steps');
  if (!box) return;
  box.innerHTML = PROG.steps.map((s, i) =>
    '<span class="pstep' + (s.done ? ' done' : (i === PROG.cur ? ' now' : '')) + '">' +
    (s.done ? '✓ ' : (i === PROG.cur ? '⏳ ' : '· ')) + esc(s.label) +
    (s.done && s.sec ? '（' + fmtSec(s.sec) + '）' : '') + '</span>').join('');
}

function progReset() {
  if (PROG.active) return;   // 正在跑的时候不重置，否则切书/建书会把进度条弄没
  clearInterval(PROG.timer);
  PROG.steps = [];
  PROG.cur = -1;
  PROG.bookId = '';
  const card = $('#short-progress-card');
  if (card) card.hidden = true;
}

function progInit(labels) {
  PROG.active = true;
  PROG.bookId = (typeof SHORT !== 'undefined' && SHORT.book) ? SHORT.book.id : '';
  PROG.steps = labels.map((l) => ({ label: l, done: false, sec: 0 }));
  PROG.cur = -1;
  PROG.t0 = Date.now();
  PROG.stepT0 = Date.now();
  const card = $('#short-progress-card');
  if (card) card.hidden = false;
  $('#short-progress-wrap')?.classList.remove('warn');
  $('#short-progress-bar').style.width = '0%';
  $('#short-progress-badge').textContent = '0%';
  $('#short-progress-msg').textContent = '准备中…';
  progRender();
  clearInterval(PROG.timer);
  PROG.timer = setInterval(progTick, 1000);
}

function progStart(i, hint) {
  PROG.cur = i;
  PROG.stepT0 = Date.now();
  if (hint) $('#short-progress-msg').textContent = hint;
  progRender();
}

function progDone(i) {
  if (PROG.steps[i]) {
    PROG.steps[i].done = true;
    PROG.steps[i].sec = Math.round((Date.now() - PROG.stepT0) / 1000);
  }
  progRender();
}

function progTick() {
  if (!PROG.steps.length) return;
  const now = Date.now();
  const total = (now - PROG.t0) / 1000;
  const done = PROG.steps.filter((s) => s.done).length;
  const pct = Math.round(done / PROG.steps.length * 100);
  $('#short-progress-bar').style.width = pct + '%';
  $('#short-progress-badge').textContent = pct + '%';
  const cur = PROG.steps[PROG.cur];
  if (!cur) return;
  const stepSec = (now - PROG.stepT0) / 1000;
  const exp = EXPECT_SEC[cur.label] || 100;
  const warn = stepSec > exp * 1.8;
  $('#short-progress-wrap')?.classList.toggle('warn', warn);
  $('#short-progress-msg').innerHTML = '当前：<b>' + esc(cur.label) + '</b> · 本步已用 ' + fmtSec(stepSec) +
    ' · 总计 ' + fmtSec(total) + (warn
      ? ' · <span class="err">⚠️ 已超过预计（' + fmtSec(exp) + '），可能卡住</span>'
      : ' · 预计本步约 ' + fmtSec(exp));
}

function progEnd(ok, msg) {
  PROG.active = false;
  clearInterval(PROG.timer);
  const bar = $('#short-progress-bar');
  if (bar) bar.style.width = '100%';
  const badge = $('#short-progress-badge');
  if (badge) badge.textContent = ok ? '完成' : '中断';
  const m = $('#short-progress-msg');
  if (m) m.textContent = msg || '';
}


// 从「一次成稿」开始跑到底（逐章改写 → 扫描 → 导出）。新建流程和断点续跑共用。
async function runFromFullDraft(bid, progBase) {
  const idx = progBase || 0;
  let fallback = false;
  let why = '';
  progStart(idx, '一次成稿（约 3–5 分钟）…');
  try {
    const fd = await api('/api/short/books/' + bid + '/full-draft', { method: 'POST', body: {} });
    if (!fd.ok) throw new Error(fd.error || '一次成稿启动失败');
    const g = await waitGen(fd.gen_id, (x) => {
      $('#short-progress-msg').textContent = '一次成稿：已生成 ' + (x.chars || 0) + ' 字…';
    });
    if (!g || g.status !== 'success' || (g.error || '').includes('未落库')) {
      fallback = true;
      why = ((g && g.error) || '').replace('[note] ', '').slice(0, 100);
    }
    await shortRefreshChapters();
    if (!SHORT.chapters.length) fallback = true;
  } catch (e) {
    fallback = true;
    why = e.message;
  }

  if (fallback) {
    // 一次成稿不达标（后段缩水/只切出 1 章）→ 自动降级为逐章生成，不再让流程中断
    $('#short-progress-msg').textContent = '一次成稿未达标' + (why ? '（' + why + '）' : '') + '，自动切换逐章生成…';
    await api('/api/short/books/' + bid + '/skeleton', { method: 'POST', body: {} });
    await shortRefreshChapters();
    const chs = SHORT.chapters.slice().sort((a, b) => a.no - b.no);
    if (!chs.length) { progEnd(false, '逐章生成也没建出章节，请检查该书是否已有大纲'); return null; }
    for (const ch of chs) {
      $('#short-progress-msg').textContent = '逐章生成：第 ' + ch.no + '/' + chs.length + ' 章（起草 → 改写 → 摘要+伏笔 → 扫描）…';
      const r = await api('/api/chapters/' + ch.id + '/finish', { method: 'POST' });
      if (!r.ok) { progEnd(false, '第 ' + ch.no + ' 章失败：' + (r.error || '')); return null; }
      await waitTask(r.task_id);
      await shortRefreshChapters();
    }
  } else {
    progDone(idx);
    progStart(idx + 1, '逐章改写…');
    const chs = SHORT.chapters.slice().sort((a, b) => a.no - b.no);
    for (const ch of chs) {
      $('#short-progress-msg').textContent = '逐章改写：第 ' + ch.no + '/' + chs.length + ' 章…';
      const g = await api('/api/books/' + bid + '/generate', { method: 'POST', body: { step_id: 'polish', chapter_id: ch.id } });
      if (g.gen_id) await waitGen(g.gen_id);
      await shortRefreshChapters();
    }
  }
  progDone(idx);
  progDone(idx + 1);
  progStart(idx + 2, '全书违禁词扫描…');
  await shortRefreshChapters();
  let hasBody = (SHORT.chapters || []).some((c) => (c.words || 0) > 0);
  if (!hasBody) {
    // 章节刚建/正文还没落库时，等 5 秒再刷一次
    await new Promise((r) => setTimeout(r, 5000));
    await shortRefreshChapters();
    hasBody = (SHORT.chapters || []).some((c) => (c.words || 0) > 0);
  }
  if (!hasBody) {
    progEnd(false, '还没有章节正文，无法扫描（可点「⚡ 一键写完所有章」逐章生成）');
    return null;
  }
  await api('/api/books/' + bid + '/check/violations-all', { method: 'POST', body: {} });
  progDone(idx + 2);
  progStart(idx + 3, '导出投稿包…');
  const ex = await api('/api/short/books/' + bid + '/export', { method: 'POST', body: { fmt: 'both' } });
  progDone(idx + 3);
  const w = ex.words || 0;
  if (w < 6000) {
    progEnd(false, '导出完成但只有 ' + w + ' 字（低于 6000 签约线），建议补写章节后再导出');
  } else if (w > 30000) {
    progEnd(false, '导出完成但 ' + w + ' 字（超过 30000 上限），建议删减');
  }
  return ex;
}


// 补回被误删的三个函数（清理发布设置时连带删掉）
async function loadFlow() {
  if (STATE.book && STATE.book.id) await openBook(STATE.book.id);
}

async function shortRefreshChapters() {
  if (!SHORT.book) return;
  const r = await api('/api/short/books/' + SHORT.book.id);
  SHORT.book = r.book;
  SHORT.chapters = r.chapters || [];
  shortRenderChapters();
  $('#short-words').textContent = (r.total_words || 0) + ' 字 / ' + SHORT.chapters.length + ' 章';
  try { await shortLoadTags(); } catch (e) { /* 标签加载失败不影响主流程 */ }
}

async function shortGenOne(cid) {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  try {
    const r = await api('/api/chapters/' + cid + '/finish', { method: 'POST' });
    if (!r.ok) { $('#short-ch-msg').textContent = r.error || '启动失败'; return; }
    $('#short-ch-msg').textContent = '正在写…';
    await waitTask(r.task_id, (t) => {
      const done = (t.steps || []).filter((s) => s.status === 'success').length;
      $('#short-ch-msg').textContent = '进度 ' + done + '/5';
    });
    await shortRefreshChapters();
  } catch (e) {
    $('#short-ch-msg').textContent = '失败：' + e.message;
  }
}


// ---------------------------------------------------------------- 作品标签（番茄分类标签）

async function shortLoadTags() {
  try {
    const r = await api('/api/short/tags');
    const sel = $('#short-tags');
    if (!sel) return;
    const cur = new Set((SHORT.meta && SHORT.meta.sub_categories) || []);
    sel.innerHTML = (r.subtags || []).map((t) => '<option value="' + esc(t) + '"' + (cur.has(t) ? ' selected' : '') + '>' + esc(t) + '</option>').join('');
    $('#short-tags-msg').textContent = '共 ' + (r.tags || []).length + ' 个分类标签（一级 ' + (r.primary || []).length + ' / 二级 ' + (r.subtags || []).length + '）';
  } catch (e) { $('#short-tags-msg').textContent = '加载失败：' + e.message; }
}

async function shortSaveTags() {
  if (!SHORT.book) { await alertBox('先选一篇短故事'); return; }
  const sel = $('#short-tags');
  const picked = [...sel.selectedOptions].map((o) => o.value);
  if (picked.length > 7) { await alertBox('二级标签最多选 7 个（加主分类合计 8 个）'); return; }
  try {
    await api('/api/short/books/' + SHORT.book.id + '/meta', { method: 'PUT', body: { sub_categories: picked } });
    await shortOpenBook(SHORT.book.id);
    $('#short-tags-msg').textContent = '已保存 ' + picked.length + ' 个标签';
  } catch (e) { $('#short-tags-msg').textContent = '保存失败：' + e.message; }
}

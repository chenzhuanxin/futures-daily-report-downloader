/* 期货公司日研报下载器 —— 前端逻辑 */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  let COMPANIES = [];
  let jobId = null;
  let pollTimer = null;
  let busy = false;
  let renderedKeys = new Set();

  // ---------- 初始化 ----------
  function setToday() {
    const d = new Date();
    const iso = d.toISOString().slice(0, 10);
    $('#reportDate').value = iso;
    $('#todayPill').textContent = '今天 ' + iso;
  }

  function fmtBytes(n) {
    if (!n && n !== 0) return '—';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }

  function esc(s) {
    return (s || '').replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function toast(msg, isErr) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = 'toast show' + (isErr ? ' err' : '');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.className = 'toast', 2800);
  }

  // ---------- 公司列表渲染 ----------
  async function loadCompanies() {
    try {
      const res = await fetch('/api/companies');
      COMPANIES = await res.json();
      const grid = $('#companyGrid');
      grid.innerHTML = '';
      COMPANIES.forEach((c) => {
        const el = document.createElement('div');
        el.className = 'company-card';
        el.dataset.id = c.id;
        el.innerHTML = `
          <span class="ck"></span>
          <div class="company-name">${esc(c.company)}</div>
          <div class="company-method">${esc(c.method)}</div>
          <div class="company-desc">${esc(c.desc)}</div>`;
        el.addEventListener('click', () => {
          el.classList.toggle('checked');
          syncSelectAll();
        });
        grid.appendChild(el);
      });
      // 默认全选
      selectAll(true);
    } catch (e) {
      toast('加载公司列表失败：' + e.message, true);
    }
  }

  function selectedIds() {
    return $$('.company-card.checked').map((el) => el.dataset.id);
  }

  function syncSelectAll() {
    const all = $$('.company-card');
    const sel = $$('.company-card.checked');
    $('#selectAll').checked = all.length > 0 && sel.length === all.length;
    updateCount();
  }

  function selectAll(on) {
    $$('.company-card').forEach((el) => el.classList.toggle('checked', on));
    $('#selectAll').checked = on;
    updateCount();
  }

  function updateCount() {
    const n = selectedIds().length;
    $('#selectedCount').textContent = `已选 ${n} / ${COMPANIES.length} 家`;
  }

  // ---------- 事件绑定 ----------
  function bindEvents() {
    $('#selectAll').addEventListener('change', (e) => selectAll(e.target.checked));
    $('#clearAll').addEventListener('click', () => selectAll(false));

    $('#browseBtn').addEventListener('click', async () => {
      $('#browseBtn').disabled = true;
      try {
        const res = await fetch('/api/browse', { method: 'POST' });
        const data = await res.json();
        if (data.path) {
          $('#outDir').value = data.path;
          $('#outDir').title = data.path;
        }
      } catch (e) {
        toast('无法打开文件夹选择框：' + e.message, true);
      } finally {
        $('#browseBtn').disabled = false;
      }
    });

    $('#openBtn').addEventListener('click', () => {
      const p = $('#outDir').value.trim();
      if (!p) { toast('请先选择保存目录', true); return; }
      fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: p }) }).then((r) => r.json()).then((d) => {
        if (!d.ok) toast(d.error || '无法打开目录', true);
      });
    });

    $('#runBtn').addEventListener('click', run);
    $('#stopBtn').addEventListener('click', stop);
  }

  // ---------- 执行 ----------
  async function run() {
    if (busy) return;
    const ids = selectedIds();
    if (!ids.length) { toast('请至少勾选一家期货公司', true); return; }
    const outDir = $('#outDir').value.trim();
    if (!outDir) { toast('请选择保存目录', true); return; }

    busy = true;
    renderedKeys = new Set();
    $('#runBtn').disabled = true;
    $('#stopBtn').disabled = false;
    $('#logCard').hidden = false;
    $('#resultCard').hidden = true;
    $('#log').innerHTML = '';
    setStatus('running');
    appendLog('开始下载：共 ' + ids.length + ' 家公司', 'info');
    appendLog('保存目录：' + esc(outDir), 'dim');
    setProgress(0, ids.length, '');

    try {
      const res = await fetch('/api/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ companies: ids, date: $('#reportDate').value, out_dir: outDir }),
      });
      const data = await res.json();
      if (!res.ok) { toast(data.error || '启动失败', true); busy = false; $('#runBtn').disabled = false; $('#stopBtn').disabled = true; return; }
      jobId = data.job_id;
      pollTimer = setInterval(poll, 1000);
    } catch (e) {
      toast('请求失败：' + e.message, true);
      busy = false; $('#runBtn').disabled = false; $('#stopBtn').disabled = true;
    }
  }

  function appendLog(text, cls) {
    const div = document.createElement('div');
    div.className = 'ln ' + (cls || '');
    div.innerHTML = text;
    const log = $('#log');
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function setStatus(state) {
    const chip = $('#statusChip');
    chip.textContent = { running: '进行中', done: '已完成', error: '出错', stopping: '停止中' }[state] || state;
    chip.className = 'status-chip ' + (state === 'done' ? 'done' : state === 'error' ? 'error' : 'running');
  }

  function setProgress(done, total, current) {
    $('#progressFill').style.width = total ? Math.round((done / total) * 100) + '%' : '0%';
    $('#progressText').textContent = done + ' / ' + total;
    if (current) $('#statusChip').textContent = '进行中 · ' + current;
  }

  async function poll() {
    if (!jobId) return;
    try {
      const res = await fetch('/api/jobs/' + jobId);
      const job = await res.json();
      if (!job || job.error) { stopPolling(); return; }

      const done = job.steps.length;
      const running = job.state === 'running' || job.state === 'stopping';
      setProgress(done, job.total, job.current_company);
      if (job.state === 'stopping') setStatus('stopping');

      renderSteps(job.steps);

      if (job.state === 'done' || job.state === 'error') {
        stopPolling();
        busy = false;
        $('#runBtn').disabled = false;
        $('#stopBtn').disabled = true;
        renderResult(job);
        setStatus(job.state);
        if (job.state === 'done') {
          appendLog('全部完成 ✔ 输出目录：' + esc(job.out_dir), 'ok');
        } else {
          appendLog('任务出错：' + (job.steps[job.steps.length - 1]?.message || ''), 'fail');
        }
      }
    } catch (e) {
      stopPolling();
      busy = false; $('#runBtn').disabled = false; $('#stopBtn').disabled = true;
      toast('进度查询失败：' + e.message, true);
    }
  }

  function renderSteps(steps) {
    // 增量渲染日志：只追加新出现的“公司+状态”行，避免轮询重复
    steps.forEach((s) => {
      const key = s.company + '|' + s.status;
      if (renderedKeys.has(key)) return;
      renderedKeys.add(key);
      if (s.status === 'success') {
        appendLog(`[成功] ${esc(s.company)} → ${esc(s.message)}（${fmtBytes(s.size)}）`, 'ok');
      } else if (s.status === 'fail' && s.company) {
        appendLog(`[失败] ${esc(s.company)}：${esc(s.message)}`, 'fail');
      }
    });
  }

  function renderResult(job) {
    const sum = job.steps.filter((s) => s.status === 'success').length;
    $('#resultSum').textContent = `${sum} 家成功 / ${job.steps.length} 家完成`;
    const body = $('#resultBody');
    body.innerHTML = '';
    job.steps.forEach((s) => {
      const tr = document.createElement('tr');
      const fileCell = s.path
        ? `<a href="#" class="file-cell" onclick="window.open('file:///${encodeURI(s.path.replace(/\\\\/g, '/'))}');return false;">${esc(s.message)}</a>`
        : esc(s.message || '—');
      tr.innerHTML = `
        <td>${esc(s.company || '—')}</td>
        <td><span class="badge ${s.status === 'success' ? 'ok' : 'fail'}">${s.status === 'success' ? '成功' : '失败'}</span></td>
        <td class="file-cell">${fileCell}</td>
        <td class="size-cell">${fmtBytes(s.size)}</td>`;
      body.appendChild(tr);
    });
    $('#resultCard').hidden = false;
    $('#footTip').textContent = `研报已保存到：${esc(job.out_dir)}`;
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function stop() {
    if (!jobId) return;
    const res = await fetch('/api/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId }) });
    const d = await res.json();
    if (d.ok) { appendLog('已请求停止，正在中断当前任务…', 'dim'); setStatus('stopping'); }
  }

  // ---------- 启动 ----------
  setToday();
  loadCompanies();
  bindEvents();
})();
